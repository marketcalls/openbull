import hashlib
import base64
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from jose import jwt, JWTError

from backend.config import get_settings

settings = get_settings()

# -- Argon2 password hashing --

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    peppered = password + settings.encryption_pepper
    return _ph.hash(peppered)


def verify_password(password: str, password_hash: str) -> bool:
    peppered = password + settings.encryption_pepper
    try:
        return _ph.verify(password_hash, peppered)
    except VerifyMismatchError:
        return False


def check_needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


# -- Fernet encryption (for tokens, broker secrets) --

def _derive_fernet_key() -> bytes:
    pepper = settings.encryption_pepper.encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"openbull_static_salt",
        iterations=100_000,
    )
    key = kdf.derive(pepper)
    return base64.urlsafe_b64encode(key)


_fernet = Fernet(_derive_fernet_key())


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


# -- API key hashing (Argon2 for verification) --

def hash_api_key(api_key: str) -> str:
    peppered = api_key + settings.encryption_pepper
    return _ph.hash(peppered)


def verify_api_key(api_key: str, api_key_hash: str) -> bool:
    peppered = api_key + settings.encryption_pepper
    try:
        return _ph.verify(api_key_hash, peppered)
    except VerifyMismatchError:
        return False


def generate_api_key() -> str:
    return secrets.token_hex(32)


# -- JWT --

ALGORITHM = "HS256"


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # Default: expire at 3:00 AM IST next day
        now_utc = datetime.now(timezone.utc)
        ist_offset = timedelta(hours=5, minutes=30)
        now_ist = now_utc + ist_offset

        parts = settings.session_expiry_time.split(":")
        expiry_hour, expiry_min = int(parts[0]), int(parts[1])

        expiry_ist = now_ist.replace(hour=expiry_hour, minute=expiry_min, second=0, microsecond=0)
        if expiry_ist <= now_ist:
            expiry_ist += timedelta(days=1)

        expire = expiry_ist - ist_offset  # convert back to UTC

    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.app_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
