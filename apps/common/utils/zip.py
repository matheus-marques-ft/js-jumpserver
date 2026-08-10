import os
import stat
import hashlib
from pathlib import Path
from rest_framework.exceptions import ValidationError
from zipfile import ZipFile, BadZipFile


# -------------------------
# Adjust to fit your security policy
# -------------------------
MAX_FILES = 1000
MAX_SINGLE_FILE_SIZE = 200 * 1024 * 1024      # 200MB
MAX_TOTAL_SIZE = 1 * 1024 * 1024 * 1024        # 1GB
MAX_COMPRESSION_RATIO = 100                   # extracted / compressed


class ZipSecurityError(ValidationError):
    pass


# -------------------------
# Utility functions
# -------------------------
def _is_symlink(zip_info):
    return stat.S_ISLNK(zip_info.external_attr >> 16)


def _is_safe_path(base_dir: Path, target: Path) -> bool:
    try:
        return target.resolve().is_relative_to(base_dir.resolve())
    except AttributeError:
        # Python < 3.9
        return str(target.resolve()).startswith(str(base_dir.resolve()))


def _verify_signature(zip_path: Path, sig_path: Path, public_key_pem: bytes):
    """
    Example: RSA + SHA256
    You can replace this with your own signature verification logic
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    data = zip_path.read_bytes()
    signature = sig_path.read_bytes()

    public_key = serialization.load_pem_public_key(public_key_pem)

    public_key.verify(
        signature,
        data,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


# -------------------------
# Main function
# -------------------------
def safe_extract_zip(
    zip_path: str | Path,
    extract_dir: str | Path,
    zip_sign_path: str | Path | None = None,
    *,
    public_key_pem: bytes | None = None,
):
    """
    Safely extract a zip file

    :param zip_path: path to the zip file
    :param extract_dir: destination directory for extraction
    :param zip_sign_path: optional, path to the zip signature file
    :param public_key_pem: optional, public key used for signature verification
    """

    zip_path = Path(zip_path)
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    # 1. Signature verification
    if zip_sign_path:
        if not public_key_pem:
            raise ZipSecurityError("Signature provided but public key missing")

        _verify_signature(
            zip_path,
            Path(zip_sign_path),
            public_key_pem,
        )

    try:
        with ZipFile(zip_path) as zf:
            infos = zf.infolist()

            # 2. Entry count limit
            if len(infos) > MAX_FILES:
                raise ZipSecurityError("Too many files in zip")

            total_size = 0

            for info in infos:
                name = info.filename

                # 3. Basic filename validation
                if name.startswith(("/", "\\")):
                    raise ZipSecurityError(f"Absolute path not allowed: {name}")

                if ".." in Path(name).parts:
                    raise ZipSecurityError(f"Path traversal detected: {name}")

                # 4. Symlink detection
                if _is_symlink(info):
                    raise ZipSecurityError(f"Symlink not allowed: {name}")

                # 5. File size limit
                if info.file_size > MAX_SINGLE_FILE_SIZE:
                    raise ZipSecurityError(f"File too large: {name}")

                total_size += info.file_size
                if total_size > MAX_TOTAL_SIZE:
                    raise ZipSecurityError("Total extracted size exceeded")

                # 6. Compression ratio check (prevents zip bombs)
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise ZipSecurityError(
                            f"Suspicious compression ratio ({ratio:.1f}): {name}"
                        )

                # 7. Final path validation
                target_path = extract_dir / name
                if not _is_safe_path(extract_dir, target_path):
                    raise ZipSecurityError(f"Unsafe extract path: {name}")

                # 8. Extraction (manual)
                if info.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target_path, "wb") as dst:
                        dst.write(src.read())

    except BadZipFile:
        raise ZipSecurityError("Invalid zip file")