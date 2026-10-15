"""Login, logout, profilo."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
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


# Gli avvisi della guida che si possono chiudere: per ora l'invito alla Guida che i nuovi
# utenti trovano in cima alla pagina Prodotti.
GUIDE = ("benvenuto",)


def guide_chiuse(user: User) -> list[str]:
    return [g for g in (user.guide_chiuse or "").split(",") if g in GUIDE]


class GuidaIn(BaseModel):
    guida: str = Field(pattern="^(" + "|".join(GUIDE) + ")$")
    aperta: bool


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
        "guide_chiuse": guide_chiuse(user),
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


@router.put("/me/guide")
def ricorda_guida(data: GuidaIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Ricorda se l'utente ha chiuso o riaperto una guida, per tutti i suoi dispositivi."""
    chiuse = [g for g in guide_chiuse(user) if g != data.guida]
    if not data.aperta:
        chiuse.append(data.guida)
    user.guide_chiuse = ",".join(chiuse)
    db.commit()
    return {"ok": True, "guide_chiuse": chiuse}


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
