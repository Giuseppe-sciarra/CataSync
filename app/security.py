"""Password, cifratura a riposo, sessione."""
import base64
import hashlib
import hmac
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User

_PBKDF2_ROUNDS = 240_000

# Hash valido di una password che nessuno userà mai: serve a far costare uguale
# il login di un'email inesistente e di una esistente, così non si possono
# indovinare gli account guardando i tempi di risposta.
_DUMMY_HASH = (
    "pbkdf2_sha256$240000$YWJjZGVmZ2hpamtsbW5vcA==$"
    "Q2hpIGxlZ2dlIHF1ZXN0byBub24gdHJvdmEgbnVsbGEgZGkgdXRpbGUu"
)


def dummy_verify() -> None:
    """Brucia lo stesso tempo di una verifica vera."""
    verify_password("password-inesistente", _DUMMY_HASH)


# --- password -------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# --- cifratura credenziali ------------------------------------------------
def _fernet() -> Fernet:
    key = config.FERNET_KEY.strip()
    if not key:
        # Fallback derivato dalla SECRET_KEY: funziona, ma imposta FERNET_KEY in produzione.
        digest = hashlib.sha256(config.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


# --- limite ai tentativi di accesso --------------------------------------
_attempts: dict[str, list[float]] = {}


def too_many_attempts(key: str, limit: int = 8, window: int = 300) -> bool:
    """Finestra scorrevole in memoria. Basta e avanza per un portale con
    poche decine di utenti; se un giorno serve distribuito, si sposta su Redis."""
    now = time.monotonic()
    hits = [t for t in _attempts.get(key, []) if now - t < window]
    _attempts[key] = hits
    if len(_attempts) > 5000:  # non far crescere il dizionario all'infinito
        _attempts.clear()
    return len(hits) >= limit


def record_attempt(key: str) -> None:
    _attempts.setdefault(key, []).append(time.monotonic())


def clear_attempts(key: str) -> None:
    _attempts.pop(key, None)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def mask(value: str, keep: int = 3) -> str:
    """Mostra solo la coda di un segreto, per la UI."""
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * (len(value) - keep) + value[-keep:]


# --- sessione -------------------------------------------------------------
def login_user(request: Request, user: User) -> None:
    request.session["uid"] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if user and user.is_active:
        return user
    return None


def require_user(user: User | None = Depends(current_user)) -> User:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessione scaduta. Rientra.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve un account amministratore.")
    return user
