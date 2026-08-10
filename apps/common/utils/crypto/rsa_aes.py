import base64
import hashlib
from django.conf import settings

from Cryptodome import Random
from Cryptodome.Cipher import AES, PKCS1_v1_5
from Cryptodome.PublicKey import RSA
from Cryptodome.Random import get_random_bytes
from Cryptodome.Util.Padding import pad

from .base import BaseCryptoSuite

from .common import padding_key


def gen_key_pair(length=2048):
    """ Generates an encryption key
    Used to encrypt (frontend)/decrypt (backend) the password when
    submitting a username/password on the login page
    """
    random_generator = Random.new().read
    rsa = RSA.generate(length, random_generator)
    rsa_private_key = rsa.exportKey().decode()
    rsa_public_key = rsa.publickey().exportKey().decode()
    return rsa_private_key, rsa_public_key


class AESCrypto:
    """
    AES
    Except for MODE_SIV mode, where the key length is: 32, 48, or 64,
    other modes use a key length of 16, 24 or 32
    See the AES internal documentation for details
    CBC mode requires passing an iv parameter
    This example uses the commonly used ECB mode
    """

    def __init__(self, key):
        self.key = padding_key(key, 32)
        self.aes = AES.new(self.key, AES.MODE_ECB)

    @staticmethod
    def to_16(key):
        """
        Converts to bytes data whose length is a multiple of 16
        :param key:
        :return:
        """
        key = bytes(key, encoding="utf8")
        while len(key) % 16 != 0:
            key += b'\0'
        return key  # returns bytes

    def aes(self):
        return AES.new(self.key, AES.MODE_ECB)

    def encrypt(self, text):
        cipher = base64.encodebytes(self.aes.encrypt(self.to_16(text)))
        return str(cipher, encoding='utf8').replace('\n', '')  # encrypt

    def decrypt(self, text):
        text_decoded = base64.decodebytes(bytes(text, encoding='utf8'))
        return str(self.aes.decrypt(text_decoded).rstrip(b'\0').decode("utf8"))


class AESCryptoGCM:
    """
    Uses AES GCM mode
    """

    def __init__(self, key):
        self.key = self.process_key(key)

    @staticmethod
    def process_key(key):
        if not isinstance(key, bytes):
            key = bytes(key, encoding='utf-8')
        if len(key) >= 32:
            return key[:32]
        return pad(key, 32)

    def encrypt(self, text):
        """
        Encrypts text, and prepends header, nonce, tag (3*16 bytes, which
        becomes 3*24 bytes after base64 encoding) to the ciphertext.
        These are needed during decryption.
        """
        header = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_GCM)
        cipher.update(header)
        ciphertext, tag = cipher.encrypt_and_digest(bytes(text, encoding='utf-8'))

        result = []
        for byte_data in (header, cipher.nonce, tag, ciphertext):
            result.append(base64.b64encode(byte_data).decode('utf-8'))

        return ''.join(result)

    def decrypt(self, text):
        """
        Extracts header, nonce, tag and decrypts text.
        """
        metadata = text[:72]
        header = base64.b64decode(metadata[:24])
        nonce = base64.b64decode(metadata[24:48])
        tag = base64.b64decode(metadata[48:])
        ciphertext = base64.b64decode(text[72:])
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)

        cipher.update(header)
        plain_text_bytes = cipher.decrypt_and_verify(ciphertext, tag)
        return plain_text_bytes.decode('utf-8')


def get_aes_crypto(key=None, mode='GCM'):
    if key is None:
        key = settings.SECRET_KEY
    if mode == 'GCM':
        return AESCryptoGCM(key)
    else:
        return AESCrypto(key)


class Hasher:
    name = 'sha256'
    block_size = 64
    digest_size = 32

    def __init__(self, data):
        self.__data = data

    def hexdigest(self):
        return hashlib.sha256(self.__data).hexdigest()

    def digest(self):
        return bytes.fromhex(self.hexdigest())

    @staticmethod
    def hash(cls, msg=b''):
        return cls(msg)

    def update(self, data):
        self.__data += data

    @classmethod
    def copy(cls, data):
        return cls(data)


class RsaAesCryptoSuite(BaseCryptoSuite):
    crypto = None
    _key = None

    def __init__(self, key, mode='ECB'):
        self.mode = mode
        if key:
            self.key = key

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, value):
        self._key = value
        self.crypto = get_aes_crypto(value, self.mode)
    
    def encrypt(self, text):
        return self.crypto.encrypt(text)
    
    def decrypt(self, cipher_text):
        return self.crypto.decrypt(cipher_text)

    def gen_key_pair(self, length=2048):
        return gen_key_pair(length)

    def encrypt_with_key_pair(self, message, rsa_public_key):
        """ Encrypts the login password """
        key = RSA.importKey(rsa_public_key)
        cipher = PKCS1_v1_5.new(key)
        cipher_text = base64.b64encode(cipher.encrypt(message.encode())).decode()
        return cipher_text
    
    def decrypt_with_key_pair(self, cipher_text, rsa_private_key):
        """ Decrypts the login password """
        if rsa_private_key is None:
            # rsa_private_key is None, this may be API request authentication, no need to decrypt
            return cipher_text

        key = RSA.importKey(rsa_private_key)
        cipher = PKCS1_v1_5.new(key)
        cipher_decoded = base64.b64decode(cipher_text.encode())
        # Todo: figure out why this needs to be written this way, https://xbuba.com/questions/57035263
        if len(cipher_decoded) == 127:
            hex_fixed = '00' + cipher_decoded.hex()
            cipher_decoded = base64.b16decode(hex_fixed.upper())
        message = cipher.decrypt(cipher_decoded, b'error').decode()
        return message
    
    def hash(self, msg):
        return Hasher.hash(Hasher, msg)


aes_ecb_crypto = get_aes_crypto(mode='ECB')
aes_crypto = get_aes_crypto(mode='GCM')