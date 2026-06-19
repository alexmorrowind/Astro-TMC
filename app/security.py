import base64
import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings


PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    salt = base64.b64decode(salt_b64)
    expected_digest = base64.b64decode(digest_b64)
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        int(iterations),
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().app_secret_key, salt="tmc-session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"user_id": user_id})


def read_session_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        return None
    return payload.get("user_id")
