from contextlib import contextmanager
import base64

from django.db import connections, transaction, connection
from django.utils.encoding import force_str

from common.utils import get_logger, signer, crypto

logger = get_logger(__file__)


def default_ip_group():
    return ["*"]


def get_object_if_need(model, pk):
    if not isinstance(pk, model):
        try:
            return model.objects.get(id=pk)
        except model.DoesNotExist as e:
            logger.error(f"DoesNotExist: <{model.__name__}:{pk}> not exist")
            raise e
    return pk


def get_objects_if_need(model, pks):
    if not pks:
        return pks
    if not isinstance(pks[0], model):
        objs = list(model.objects.filter(id__in=pks))
        if len(objs) != len(pks):
            pks = set(pks)
            exists_pks = {o.id for o in objs}
            not_found_pks = ",".join(pks - exists_pks)
            logger.error(f"DoesNotExist: <{model.__name__}: {not_found_pks}>")
        return objs
    return pks


def get_objects(model, pks):
    if not pks:
        return pks

    objs = list(model.objects.filter(id__in=pks))
    if len(objs) != len(pks):
        pks = set(pks)
        exists_pks = {o.id for o in objs}
        not_found_pks = pks - exists_pks
        logger.error(f"DoesNotExist: <{model.__name__}: {not_found_pks}>")
    return objs


# Copied from django.db.close_old_connections, because it's not exported and the IDE flags it as an issue
def close_old_connections(**kwargs):
    for conn in connections.all(initialized_only=True):
        conn.close_if_unusable_or_obsolete()


# This is meant to be used outside the Django request cycle; it must not affect Django's transaction management — using it inside an api would affect the api transaction
@contextmanager
def safe_db_connection():
    close_old_connections()
    yield
    close_old_connections()


@contextmanager
def safe_atomic_db_connection(auto_close=False):
    """
    Generic database connection manager (thread-safe, transaction-aware):
    - Proactively rebuild the connection when it is unusable
    - Automatically close the connection outside of a transaction (optional)
    - Does not affect the Django request/transaction cycle
    """
    in_atomic = connection.in_atomic_block  # Whether we are currently inside a transaction
    autocommit = transaction.get_autocommit()
    recreated = False

    try:
        if not connection.is_usable():
            connection.close()
            connection.connect()
            recreated = True
        yield
    finally:
        # Only consider proactively cleaning up the connection when outside a transaction and in autocommit mode
        if auto_close or (recreated and not in_atomic and autocommit):
            close_old_connections()


@contextmanager
def open_db_connection(alias="default"):
    connection = transaction.get_connection(alias)
    try:
        connection.connect()
        with transaction.atomic():
            yield connection
    finally:
        connection.close()


class Encryptor:
    def __init__(self, value):
        self.value = force_str(value)

    def is_encrypted_data(self):
        """
        Detect whether the data is in encrypted format
        Returns True if it is encrypted data, False if it is raw data
        """
        if not self.value:
            return False

        # Detect base64-encoded format (output of crypto.encrypt)
        try:
            # Try different base64 decoding methods
            # 1. Standard base64
            try:
                base64.b64decode(self.value)
                return True
            except Exception:
                pass

            # 2. URL-safe base64
            try:
                # Add the necessary padding
                missing_padding = len(self.value) % 4
                if missing_padding:
                    padded_value = self.value + '=' * (4 - missing_padding)
                else:
                    padded_value = self.value
                base64.urlsafe_b64decode(padded_value)
                return True
            except Exception:
                pass
                
        except Exception:
            pass
        
        # Detect AES GCM format (fixed 72-character metadata)
        if len(self.value) > 72:
            try:
                # The first 72 characters should be 3 groups of 24-character base64 encoding
                metadata = self.value[:72]
                for i in range(0, 72, 24):
                    part = metadata[i:i+24]
                    base64.b64decode(part)
                return True
            except Exception:
                pass
        
        return False

    def decrypt(self):
        plain_value = crypto.decrypt(self.value)

        # If it wasn't decrypted, fall back to decrypting with the original signer
        if not plain_value:
            plain_value = signer.unsign(self.value) or ""
        return plain_value

    def encrypt(self):
        return crypto.encrypt(self.value)
