# -*- coding: utf-8 -*-
#
import base64

# SM2 curve OID: 1.2.156.10197.1.301
# DER encoding: 06 08 2a 81 1c cf 55 01 82 2d
SM2_OID_DER = bytes([0x06, 0x08, 0x2a, 0x81, 0x1c, 0xcf, 0x55, 0x01, 0x82, 0x2d])


def is_sm2_pem(pem_content):
    """
    Determine whether the PEM data (certificate / CSR / public key, etc.) uses the SM2 algorithm by searching for the SM2 curve OID byte sequence.

    pem_content: a standard PEM string (with -----BEGIN ... ----- header/footer).
    Returns True if the SM2 OID is present, False otherwise.
    """
    pem_lines = pem_content.strip().splitlines()
    b64 = ''.join(ln for ln in pem_lines if not ln.startswith('-----'))
    try:
        der = base64.b64decode(b64)
    except Exception:
        return False
    return SM2_OID_DER in der


def detect_cert_algorithm(pem_content):
    """
    Detect the public key algorithm from the PEM content, returning a string such as 'SM2' / 'RSA-1024' / 'RSA-2048' / 'ECDSA-256',
    or an empty string if it cannot be recognized. Supports any PEM format, including certificates, CSRs, and public keys.
    """
    if not pem_content:
        return ''

    try:
        if is_sm2_pem(pem_content):
            return 'SM2'
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
        cert = x509.load_pem_x509_certificate(pem_content.encode())
        pub = cert.public_key()
        if isinstance(pub, rsa.RSAPublicKey):
            return 'RSA-{}'.format(pub.key_size)
        if isinstance(pub, ec.EllipticCurvePublicKey):
            return 'ECDSA-{}'.format(pub.key_size)
        return ''
    except Exception:
        return ''
