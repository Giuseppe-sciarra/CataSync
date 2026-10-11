"""Login, logout, profilo."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing
from ..db import get_db
from ..models import User
from ..security import (
    clear_attempts,
    client_ip,
    current_user,
    dummy_verify,
    hash_password,
    login_user,
    logout_user,
    record_attempt,
    require_user,
    too_many_attempts,
    verify_password,
)
from ..store import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_payload(db: Session, user: User) -> dict:
    data = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "client_id": user.client_id,
        "client_name": user.client.name if user.client else "",
        "brand": None,
        "subscription": None,
    }
    if user.client:
        data["brand"] = {
            "name": user.client.legal_name or user.client.name,
            "logo": f"/media/logo/{user.client.id}" if user.client.logo_path else "",
            "accent": user.client.accent_color or "",
        }
        data["subscription"] = billing.as_json(billing.check(db, user.client))
    return data


@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    email = data.email.lower()

    # Due contatori: uno sull'indirizzo di rete, uno sull'account. Il primo ferma
    # chi prova mille password, il secondo chi prova la stessa email da più IP.
    if too_many_attempts("ip:" + ip) or too_many_attempts("user:" + email):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Troppi tentativi. Riprova fra qualche minuto.",
        )

    user = db.scalar(select(User).where(User.email == email))
    if not user:
        dummy_verify()  # stesso tempo di risposta di un'email esistente
        record_attempt("ip:" + ip)
        record_attempt("user:" + email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email o password non corrette.")

    if not verify_password(data.password, user.password_hash):
        record_attempt("ip:" + ip)
        record_attempt("user:" + email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email o password non corrette.")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disattivato. Scrivi all'assistenza.")
    if user.client and not user.client.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account cliente sospeso.")

    clear_attempts("ip:" + ip)
    clear_attempts("user:" + email)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    login_user(request, user)
    audit(db, user.email, "login", ip)
    return user_payload(db, user)


@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return {"ok": True}


@router.get("/me")
def me(db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, **user_payload(db, user)}


@router.post("/password")
def change_password(
    data: PasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La password attuale non corrisponde.")
    if len(data.new_password) < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Usa almeno 10 caratteri.")
    user.password_hash = hash_password(data.new_password)
    db.commit()
    audit(db, user.email, "password_change")
    return {"ok": True}
