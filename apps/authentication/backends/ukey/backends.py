# -*- coding: utf-8 -*-
#

import base64
import os
import tempfile

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from users.models import User
from common.utils import get_logger
from ..base import JMSBaseAuthBackend
from .sdk import ukey_sdk_config
from .exceptions import (
    UKeyAuthError,
    UKeyUserNotFoundError,
    UkeySNMismatchError,
    UKeyCertNormalizationError,
    UKeyCertChainError,
    UKeyCertCNMismatchError,
    UKeySignatureError,
    UKeyCertExpiredError,
    UKeyCertUnsupportedAlgorithmError,
)
from .utils import is_sm2_pem
from authentication.errors.const import reason_user_inactive, reason_choices


__all__ = ['UKeyBackend']

logger = get_logger(__name__)


class UKeyBackend(JMSBaseAuthBackend):
    backend = settings.AUTH_BACKEND_UKEY

    @staticmethod
    def is_enabled():
        return settings.AUTH_UKEY

    # ── Main entry point ────────────────────────────────────────────────────────

    def authenticate(self, request, username, cert, signature, challenge, ukey_sn=None):
        try:
            user = self._check_user_and_ukey_sn(username, ukey_sn)
            cert_pem = self._load_cert_pem(cert)
            if self._is_sm2_cert(cert_pem):
                user = self._authenticate_sm2(cert_pem, username, signature, challenge, user)
            else:
                user = self._authenticate_other(cert_pem, username, signature, challenge, user)
            if self.user_can_authenticate(user):
                return user
            else:
                error = reason_choices[reason_user_inactive]
                raise PermissionDenied(error)
        except Exception as e:
            if request:
                request.error_message = str(e)
            raise PermissionDenied(str(e))

    # ── Part 1: User and UKey SN pre-validation ─────────────────────────────────

    def _check_user_and_ukey_sn(self, username, ukey_sn):
        """Look up the user and validate the ukey_sn binding relationship, returning a User instance."""
        ukey_sn = (ukey_sn or '').strip()
        user = User.objects.filter(username=username).first()
        if user is None:
            logger.error('UKeyBackend: user %r not found', username)
            raise UKeyUserNotFoundError()
        user_ukey_sn = (user.ukey_sn or '').strip()
        if not user_ukey_sn or not ukey_sn or ukey_sn != user_ukey_sn:
            logger.error('UKeyBackend: ukey_sn mismatch for user %r', username)
            raise UkeySNMismatchError()
        return user

    # ── Part 2: SM2 certificate verification flow ───────────────────────────────

    def _authenticate_sm2(self, cert_pem, username, signature, challenge, user):
        """SM2 certificate verification: load -> chain verification -> validity -> CN comparison -> signature verification."""
        sm2_cert = self._load_sm2_cert(cert_pem)
        self._verify_sm2_cert_chain(sm2_cert)
        self._verify_sm2_cert_validity(sm2_cert)
        self._verify_cert_cn(sm2_cert.get_subject().get('commonName'), username)
        self._verify_sm2_signature(sm2_cert.get_subject_public_key(), signature, challenge)
        return user

    @staticmethod
    def _load_sm2_cert(cert_pem):
        """Write the PEM string to a temporary file, load it as an Sm2Certificate object, then immediately delete the temporary file."""
        from common.utils.gmssl_python import Sm2Certificate

        fd, cert_file = tempfile.mkstemp(suffix='.crt')
        try:
            os.close(fd)
            with open(cert_file, 'w', encoding='utf-8') as f:
                f.write(cert_pem)
            sm2_cert = Sm2Certificate()
            sm2_cert.import_pem(cert_file)
        except Exception as e:
            logger.error('UKeyBackend: failed to load SM2 cert: %s', e)
            raise UKeyCertNormalizationError()
        finally:
            if os.path.exists(cert_file):
                os.unlink(cert_file)
        return sm2_cert

    @staticmethod
    def _verify_sm2_cert_validity(sm2_cert):
        """Validate the SM2 certificate's validity period (not_before / not_after)."""
        try:
            validity = sm2_cert.get_validity()
        except Exception as e:
            logger.error('UKeyBackend: failed to get SM2 cert validity: %s', e)
            raise UKeyCertExpiredError()
        UKeyBackend._check_validity_period(validity.not_before, validity.not_after, 'SM2')

    @staticmethod
    def _verify_sm2_cert_chain(sm2_cert):
        """Call Sm2Certificate.verify_by_ca_certificate to verify the SM2 certificate chain."""
        from common.utils.gmssl_python import Sm2Certificate, SM2_DEFAULT_ID

        ca_cert_content = ukey_sdk_config.ca_cert_content
        if not ca_cert_content:
            raise UKeyCertChainError()

        fd, ca_cert_file = tempfile.mkstemp(suffix='.crt')
        try:
            os.close(fd)
            with open(ca_cert_file, 'w', encoding='utf-8') as f:
                f.write(ca_cert_content)
            ca_cert = Sm2Certificate()
            ca_cert.import_pem(ca_cert_file)
            ok = sm2_cert.verify_by_ca_certificate(ca_cert, SM2_DEFAULT_ID)
        except UKeyAuthError:
            raise
        except Exception as e:
            logger.error('UKeyBackend: SM2 cert chain verification error: %s', e)
            raise UKeyCertChainError()
        finally:
            if os.path.exists(ca_cert_file):
                os.unlink(ca_cert_file)

        if not ok:
            logger.error('UKeyBackend: SM2 cert chain verification failed')
            raise UKeyCertChainError()

    @staticmethod
    def _verify_sm2_signature(sm2_key, signature, challenge):
        """Use gmssl_python's Sm2Signature to perform SM2withSM3 signature verification."""
        from common.utils.gmssl_python import Sm2Signature, DO_VERIFY, SM2_DEFAULT_ID

        sig_bytes = UKeyBackend._decode_signature(signature)
        signed_data = UKeyBackend._challenge_as_bytes(challenge)
        try:
            verifier = Sm2Signature(sm2_key, SM2_DEFAULT_ID, DO_VERIFY)
            verifier.update(signed_data)
            ok = bool(verifier.verify(sig_bytes))
        except Exception as e:
            logger.error('UKeyBackend: SM2 signature verification error: %s', e)
            raise UKeySignatureError()
        if not ok:
            logger.error('UKeyBackend: SM2 signature mismatch')
            raise UKeySignatureError()

    # ── Part 3: RSA / other certificate verification flow ──────────────────────

    def _authenticate_other(self, cert_pem, username, signature, challenge, user):
        """RSA certificate verification: load -> chain verification -> validity -> CN comparison -> signature verification."""
        cert, pub_key = self._load_rsa_cert(cert_pem)
        self._verify_rsa_cert_chain(cert)
        self._verify_rsa_cert_validity(cert)
        self._verify_cert_cn(self._extract_rsa_cert_cn(cert), username)
        self._verify_rsa_signature(pub_key, signature, challenge)
        return user

    @staticmethod
    def _load_rsa_cert(cert_pem):
        """Load the RSA PEM certificate, validate the public key algorithm type, and return (cert, pub_key)."""
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
        except Exception as e:
            logger.error('UKeyBackend: failed to load certificate: %s', e)
            raise UKeyCertNormalizationError()

        pub_key = cert.public_key()
        if isinstance(pub_key, ec.EllipticCurvePublicKey):
            logger.error('UKeyBackend: ECDSA certificate verification is not supported')
            raise UKeyCertUnsupportedAlgorithmError()
        if not isinstance(pub_key, rsa.RSAPublicKey):
            logger.error('UKeyBackend: unsupported key type: %s', type(pub_key).__name__)
            raise UKeyCertUnsupportedAlgorithmError()
        return cert, pub_key

    @staticmethod
    def _verify_rsa_cert_validity(cert):
        """Validate the RSA certificate's validity period (not_valid_before_utc / not_valid_after_utc)."""
        UKeyBackend._check_validity_period(
            cert.not_valid_before_utc, cert.not_valid_after_utc, 'RSA'
        )

    @staticmethod
    def _verify_rsa_cert_chain(cert):
        """Verify the RSA certificate chain using the CA root certificate."""
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import padding

        ca_cert_content = ukey_sdk_config.ca_cert_content
        if not ca_cert_content:
            logger.error('UKeyBackend: AUTH_UKEY_CA_CERT_CONTENT not configured')
            raise UKeyCertChainError()
        try:
            ca_cert = x509.load_pem_x509_certificate(ca_cert_content.encode())
            ca_cert.public_key().verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                cert.signature_hash_algorithm,
            )
        except InvalidSignature:
            logger.error('UKeyBackend: RSA cert chain verification failed')
            raise UKeyCertChainError()
        except UKeyAuthError:
            raise
        except Exception as e:
            logger.error('UKeyBackend: RSA cert chain verification error: %s', e)
            raise UKeyCertChainError()

    @staticmethod
    def _extract_rsa_cert_cn(cert):
        """Extract the CN from the RSA certificate subject, returning None on failure."""
        from cryptography import x509

        try:
            return cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        except Exception:
            return None

    @staticmethod
    def _verify_rsa_signature(pub_key, signature, challenge):
        """Verify the signature using RSA PKCS1v15 + SHA256."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        sig_bytes = UKeyBackend._decode_signature(signature)
        signed_data = UKeyBackend._challenge_as_bytes(challenge)
        try:
            pub_key.verify(sig_bytes, signed_data, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            logger.error('UKeyBackend: RSA signature mismatch')
            raise UKeySignatureError()
        except UKeyAuthError:
            raise
        except Exception as e:
            logger.error('UKeyBackend: RSA signature verification error: %s', e)
            raise UKeySignatureError()

    # ── Common utility methods ──────────────────────────────────────────────────

    @staticmethod
    def _check_validity_period(not_before, not_after, label=''):
        """Validate the certificate's validity period (shared by SM2 and RSA).

        not_before / not_after can be naive (local time) or aware (timezone-aware) datetimes;
        now is kept the same type as them to ensure they are comparable.
        """
        import datetime

        if not_before.tzinfo is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            now = datetime.datetime.now()

        if now < not_before:
            logger.error(
                'UKeyBackend: %s certificate not yet valid, valid from %s', label, not_before
            )
            raise UKeyCertExpiredError()
        if now > not_after:
            logger.error(
                'UKeyBackend: %s certificate has expired at %s', label, not_after
            )
            raise UKeyCertExpiredError()

    @staticmethod
    def _verify_cert_cn(cert_cn, username):
        """Validate whether the certificate CN matches the username (shared by SM2 and RSA flows)."""
        if cert_cn != username:
            logger.error(
                'UKeyBackend: cert CN %r does not match username %r', cert_cn, username
            )
            raise UKeyCertCNMismatchError()

    @staticmethod
    def _challenge_as_bytes(challenge):
        """Convert challenge to bytes uniformly (shared by SM2 and RSA signature verification)."""
        return challenge if isinstance(challenge, bytes) else challenge.encode('utf-8')

    @staticmethod
    def _load_cert_pem(cert_data):
        """Convert the raw certificate data into a PEM string, raising CertNormalizationError if the format is invalid."""
        try:
            return UKeyBackend._normalize_cert_to_pem(cert_data)
        except Exception as e:
            logger.error('UKeyBackend: cert normalization failed: %s', e)
            raise UKeyCertNormalizationError()

    @staticmethod
    def _is_sm2_cert(cert_pem):
        """Determine whether the certificate uses the SM2 algorithm by its OID byte sequence."""
        return is_sm2_pem(cert_pem)

    @staticmethod
    def _normalize_cert_to_pem(cert_data):
        """
        Convert the certificate uniformly into standard PEM format.
        Supports: PEM that already has a header/footer, raw base64 strings, and DER bytes.
        """
        if isinstance(cert_data, bytes):
            if cert_data.lstrip().startswith(b'-----BEGIN'):
                return cert_data.decode('utf-8')
            b64 = base64.b64encode(cert_data).decode('ascii')
        else:
            cert_data = cert_data.strip()
            if cert_data.startswith('-----BEGIN'):
                return cert_data
            b64 = ''.join(cert_data.split())
            base64.b64decode(b64, validate=True)  # Validate that it is legal base64

        lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
        return (
            '-----BEGIN CERTIFICATE-----\n'
            + '\n'.join(lines)
            + '\n-----END CERTIFICATE-----\n'
        )

    @staticmethod
    def _decode_signature(signature):
        """
        Convert the signature value into bytes.
        Tried in order: already bytes -> hexadecimal string -> base64 string.
        """
        if isinstance(signature, bytes):
            return signature
        sig = signature.strip()
        try:
            return bytes.fromhex(sig)
        except ValueError:
            pass
        try:
            return base64.b64decode(sig)
        except Exception:
            pass
        raise ValueError('Cannot decode signature: unknown format')
