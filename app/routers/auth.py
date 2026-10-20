"""Login (con la verifica in due passaggi), logout, profilo, sicurezza dell'account."""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing, due_fattori, posta
from ..db import get_db
from ..models import Passkey, User
from ..security import (
    clear_attempts,
    client_ip,
    current_user,
    decrypt,
    dummy_verify,
    encrypt,
    hash_password,
    login_user,
    logout_user,
    record_attempt,
    require_user,
    too_many_attempts,
    verify_password,
)
from ..store import audit, portal_brand

log = logging.getLogger("catasync.accesso")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Fra la password giusta e il secondo passaggio: dieci minuti, poi si ricomincia dalla password.
ATTESA_SECONDO_PASSO = 600
# Dopo aver riscritto la password per un'operazione delicata, per dieci minuti non la si richiede.
CONFERMA_VALE = 600


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
        "due_fattori": due_fattori.attiva(user),
    }
    if user.client:
        data["brand"] = {
            "name": user.client.legal_name or user.client.name,
            "logo": f"/media/logo/{user.client.id}" if user.client.logo_path else "",
            "accent": user.client.accent_color or "",
        }
        data["subscription"] = billing.as_json(billing.check(db, user.client))
    return data


# ========================================================== accesso
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

    metodi = due_fattori.metodi(user)
    if not metodi:
        return _completa_accesso(request, db, user, "password")

    # Password giusta, ma l'accesso non c'è ancora: la sessione ricorda solo chi sta
    # facendo il secondo passaggio, e current_user() non lo considera entrato.
    logout_user(request)
    request.session["mfa_uid"] = user.id
    request.session["mfa_t"] = int(time.time())
    # Se l'unico modo attivo è l'email, la pagina chiede subito il codice (/2fa/email/invia).
    return {
        "due_fattori": True,
        "metodi": metodi,
        "email": due_fattori.email_mascherata(user.email),
        "riserva": due_fattori.riserva_rimasti(user) > 0,
    }


def _completa_accesso(request: Request, db: Session, user: User, metodo: str) -> dict:
    request.session.pop("mfa_uid", None)
    request.session.pop("mfa_t", None)
    due_fattori.azzera_tentativi(user)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    login_user(request, user)
    ip = client_ip(request)
    audit(db, user.email, "login", ip if metodo == "password" else f"{ip} · {metodo}")
    dati = user_payload(db, user)
    if metodo == "riserva":
        dati["riserva_rimasti"] = due_fattori.riserva_rimasti(user)
    return dati


def _in_attesa(request: Request, db: Session) -> User:
    """Chi ha dato la password giusta e sta facendo il secondo passaggio."""
    uid, quando = request.session.get("mfa_uid"), request.session.get("mfa_t") or 0
    user = db.get(User, uid) if uid else None
    if not user or time.time() - quando > ATTESA_SECONDO_PASSO or not user.is_active:
        request.session.pop("mfa_uid", None)
        request.session.pop("mfa_t", None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Il tempo per il secondo passaggio è scaduto: rientra con email e password.")
    return user


def _prenota(db: Session, user: User) -> None:
    """Ogni codice provato conta, nel database: al massimo sei ogni dieci minuti per utente,
    da qualunque indirizzo e anche in parallelo."""
    if not due_fattori.prenota_tentativo(db, user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Troppi codici sbagliati. Riprova fra qualche minuto.")


def _sbagliato(request: Request, db: Session, user: User, metodo: str, messaggio: str):
    db.commit()
    audit(db, user.email, "2fa_errato", f"{client_ip(request)} · {metodo}")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, messaggio)


MESSAGGI_EMAIL = {
    "nessuno": "Il codice è scaduto: fatti mandare un codice nuovo.",
    "scaduto": "Il codice è scaduto: fatti mandare un codice nuovo.",
    "troppi": "Troppi tentativi con questo codice: fatti mandare un codice nuovo.",
    "errato": "Codice non valido.",
}


class LinguaIn(BaseModel):
    lingua: str = Field(default="it", max_length=8)


class CodiceIn(BaseModel):
    metodo: Literal["app", "email", "riserva"]
    codice: str = Field(max_length=40)


class PasskeyIn(BaseModel):
    credenziale: dict
    nome: str = Field(default="", max_length=80)


@router.post("/2fa/email/invia")
async def invia_codice_accesso(data: LinguaIn, request: Request, db: Session = Depends(get_db)):
    user = _in_attesa(request, db)
    if not user.email_2fa:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Il codice via email non è attivo per questo account.")
    await _spedisci_codice(db, user, "accesso", data.lingua)
    return {"ok": True, "email": due_fattori.email_mascherata(user.email)}


@router.post("/2fa/verifica")
def verifica_codice(data: CodiceIn, request: Request, db: Session = Depends(get_db)):
    user = _in_attesa(request, db)
    _prenota(db, user)

    if data.metodo == "app":
        segreto = decrypt(user.totp_segreto) if user.totp_segreto else ""
        passo = due_fattori.verifica_totp(segreto, data.codice, user.totp_ultimo_passo or 0)
        # Giusto e non ancora usato, nemmeno da una richiesta partita nello stesso istante.
        if not passo or not due_fattori.consuma_passo(db, user, passo):
            _sbagliato(request, db, user, "app",
                       "Codice non valido. Controlla l'app e riprova: ogni codice vale 30 secondi.")
    elif data.metodo == "email":
        esito = due_fattori.verifica_codice_email(db, user, data.codice, "accesso") if user.email_2fa else "nessuno"
        if esito != "ok":
            _sbagliato(request, db, user, "email", MESSAGGI_EMAIL[esito])
    else:
        if not due_fattori.usa_codice_riserva(db, user, data.codice):
            _sbagliato(request, db, user, "riserva", "Codice di riserva non valido o già usato.")

    return _completa_accesso(request, db, user, data.metodo)


@router.post("/2fa/passkey/opzioni")
def opzioni_passkey_accesso(request: Request, db: Session = Depends(get_db)):
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    user = _in_attesa(request, db)
    if not user.passkey:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nessuna passkey registrata su questo account.")
    rp_id, _ = due_fattori.rp_e_origine(request)
    opzioni = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[_descrittore(p) for p in user.passkey],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    due_fattori.salva_sfida(user, opzioni.challenge)
    db.commit()
    return json.loads(options_to_json(opzioni))


@router.post("/2fa/passkey/verifica")
def verifica_passkey_accesso(data: PasskeyIn, request: Request, db: Session = Depends(get_db)):
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    user = _in_attesa(request, db)
    _prenota(db, user)
    sfida = due_fattori.prendi_sfida(db, user)
    if not sfida:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La richiesta della passkey è scaduta: riprova.")
    id_credenziale = str(data.credenziale.get("id") or "")
    passkey = next((p for p in user.passkey if p.credential_id == id_credenziale), None)
    if not passkey:
        _sbagliato(request, db, user, "passkey", "Questa passkey non è registrata sul tuo account.")
    rp_id, origine = due_fattori.rp_e_origine(request)
    try:
        verificata = verify_authentication_response(
            credential=data.credenziale,
            expected_challenge=sfida,
            expected_rp_id=rp_id,
            expected_origin=origine,
            credential_public_key=base64url_to_bytes(passkey.chiave_pubblica),
            credential_current_sign_count=passkey.contatore or 0,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 - qualunque difetto della risposta è un rifiuto
        log.info("Passkey rifiutata per %s: %s", user.email, exc)
        _sbagliato(request, db, user, "passkey", "Passkey non riconosciuta. Riprova.")
    passkey.contatore = verificata.new_sign_count
    passkey.ultimo_uso = datetime.now(timezone.utc)
    return _completa_accesso(request, db, user, "passkey")


@router.post("/2fa/annulla")
def annulla_secondo_passo(request: Request):
    request.session.pop("mfa_uid", None)
    request.session.pop("mfa_t", None)
    return {"ok": True}


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


def _controlla_password(user: User, password: str) -> None:
    """Le operazioni delicate chiedono di nuovo la password: una sessione lasciata aperta
    su un computer non basta a spegnere la verifica in due passaggi."""
    chiave = f"pwd:{user.id}"
    if too_many_attempts(chiave):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Troppi tentativi. Riprova fra qualche minuto.")
    if not verify_password(password or "", user.password_hash):
        record_attempt(chiave)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La password attuale non corrisponde.")
    clear_attempts(chiave)


@router.post("/password")
def change_password(
    data: PasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _controlla_password(user, data.current_password)
    if len(data.new_password) < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Usa almeno 10 caratteri.")
    user.password_hash = hash_password(data.new_password)
    db.commit()
    audit(db, user.email, "password_change")
    return {"ok": True}


# ===================================================== sicurezza dell'account
def _descrittore(passkey: Passkey):
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import AuthenticatorTransport, PublicKeyCredentialDescriptor

    validi = {t.value for t in AuthenticatorTransport}
    trasporti = [AuthenticatorTransport(t) for t in (passkey.trasporti or "").split(",") if t in validi]
    return PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id), transports=trasporti or None)


def _data(valore: datetime | None) -> str | None:
    return valore.isoformat() if valore else None


def stato_sicurezza(user: User) -> dict:
    return {
        "attiva": due_fattori.attiva(user),
        "metodi": due_fattori.metodi(user),
        "app": bool(user.totp_segreto),
        "email": bool(user.email_2fa),
        "email_indirizzo": user.email,
        "posta_pronta": posta.configurata(),
        "passkey": [
            {"id": p.id, "nome": p.nome, "creata": _data(p.creata), "ultimo_uso": _data(p.ultimo_uso)}
            for p in user.passkey
        ],
        "riserva_rimasti": due_fattori.riserva_rimasti(user),
    }


def _dopo_attivazione(db: Session, user: User, azione: str) -> dict:
    """Primo modo attivato: arrivano i codici di riserva, da mostrare una volta sola."""
    codici = due_fattori.nuovi_codici_riserva(user) if due_fattori.riserva_rimasti(user) == 0 else []
    db.commit()
    audit(db, user.email, azione)
    return {**stato_sicurezza(user), "codici_riserva": codici}


async def _spedisci_codice(db: Session, user: User, scopo: str, lingua: str) -> None:
    if not posta.configurata():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "La posta del portale non è configurata: usa un altro modo o scrivi all'assistenza.")
    if due_fattori.troppi_invii(user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Troppi codici spediti nell'ultima ora: riprova più tardi o usa un altro modo.")
    attesa = due_fattori.attesa_reinvio(user, scopo)
    if attesa:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Aspetta {attesa} secondi prima di chiedere un altro codice.")
    codice = due_fattori.prepara_codice_email(user, scopo)
    db.commit()
    portale = portal_brand(db)["name"]
    t = lambda testo, *valori: due_fattori.traduci(testo, lingua, *valori)  # noqa: E731
    righe = [
        t("Il codice per entrare in {0}:", portale) if scopo == "accesso"
        else t("Il codice per attivare la verifica via email in {0}:", portale),
        "",
        f"    {codice}",
        "",
        t("Vale {0} minuti.", due_fattori.EMAIL_MINUTI),
    ]
    if scopo == "accesso":
        righe += ["", t("Se non l'hai chiesto tu, qualcuno conosce la tua password: cambiala appena puoi.")]
    try:
        await posta.invia(user.email, t("{0} è il tuo codice per {1}", codice, portale), "\n".join(righe),
                          nome_mittente=portale)
    except posta.PostaErrore as exc:
        log.warning("Codice di verifica non spedito a %s: %s", user.email, exc)
        user.codice_email_inviato = None   # si può riprovare subito
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Il codice non è partito: riprova fra poco o usa un altro modo.")


@router.get("/2fa")
def leggi_sicurezza(user: User = Depends(require_user)):
    return stato_sicurezza(user)


class ConfermaIn(BaseModel):
    codice: str = Field(max_length=40)


class ConPasswordIn(BaseModel):
    # Vuota = va bene la conferma di pochi minuti fa (vedi _conferma).
    password: str = Field(default="", max_length=200)
    lingua: str = Field(default="it", max_length=8)


def _conferma(request: Request, user: User, password: str) -> None:
    """Attivare, cambiare o spegnere la verifica in due passaggi chiede di nuovo la password:
    con una sessione rubata o un computer lasciato aperto non si toglie l'account al titolare,
    né lo si lega al telefono di qualcun altro. Riscritta, vale dieci minuti (nella sessione,
    firmata dal server: non si falsifica)."""
    if password:
        _controlla_password(user, password)
        request.session["conferma_uid"] = user.id
        request.session["conferma_t"] = int(time.time())
        return
    recente = time.time() - (request.session.get("conferma_t") or 0) < CONFERMA_VALE
    if request.session.get("conferma_uid") == user.id and recente:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Per sicurezza, scrivi di nuovo la tua password.")


# --- app di autenticazione --------------------------------------------------
@router.post("/2fa/app/inizia")
def inizia_app(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    segreto = due_fattori.nuovo_segreto()
    user.totp_in_attesa = encrypt(segreto)
    db.commit()
    uri = due_fattori.uri_otpauth(segreto, user.email, portal_brand(db)["name"])
    return {"qr": due_fattori.qr_svg(uri), "segreto": due_fattori.segreto_leggibile(segreto), "uri": uri}


@router.post("/2fa/app/conferma")
def conferma_app(data: ConfermaIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    segreto = decrypt(user.totp_in_attesa) if user.totp_in_attesa else ""
    if not segreto:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ricomincia dalla scansione del codice QR.")
    _prenota(db, user)
    passo = due_fattori.verifica_totp(segreto, data.codice)
    if not passo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Codice non valido: controlla che l'ora del telefono sia giusta e riprova.")
    due_fattori.azzera_tentativi(user)
    user.totp_segreto = user.totp_in_attesa
    user.totp_in_attesa = ""
    user.totp_ultimo_passo = passo
    return _dopo_attivazione(db, user, "2fa_app_attivata")


@router.post("/2fa/app/disattiva")
def disattiva_app(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    user.totp_segreto = ""
    user.totp_in_attesa = ""
    user.totp_ultimo_passo = 0
    due_fattori.dopo_una_disattivazione(user)
    db.commit()
    audit(db, user.email, "2fa_app_disattivata")
    return stato_sicurezza(user)


# --- codice via email --------------------------------------------------------
@router.post("/2fa/email/inizia")
async def inizia_email(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    await _spedisci_codice(db, user, "attiva", data.lingua)
    return {"ok": True, "email": user.email}


@router.post("/2fa/email/conferma")
def conferma_email(data: ConfermaIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _prenota(db, user)
    esito = due_fattori.verifica_codice_email(db, user, data.codice, "attiva")
    if esito != "ok":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MESSAGGI_EMAIL[esito])
    due_fattori.azzera_tentativi(user)
    user.email_2fa = True
    return _dopo_attivazione(db, user, "2fa_email_attivata")


@router.post("/2fa/email/disattiva")
def disattiva_email(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    user.email_2fa = False
    due_fattori.cancella_codice_email(user)
    due_fattori.dopo_una_disattivazione(user)
    db.commit()
    audit(db, user.email, "2fa_email_disattivata")
    return stato_sicurezza(user)


# --- passkey ---------------------------------------------------------------
@router.post("/2fa/passkey/inizia")
def inizia_passkey(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    _conferma(request, user, data.password)
    rp_id, _ = due_fattori.rp_e_origine(request)
    opzioni = generate_registration_options(
        rp_id=rp_id,
        rp_name=portal_brand(db)["name"],
        user_id=due_fattori.maniglia_utente(user),
        user_name=user.email,
        user_display_name=user.full_name or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[_descrittore(p) for p in user.passkey],
    )
    due_fattori.salva_sfida(user, opzioni.challenge)
    db.commit()
    return json.loads(options_to_json(opzioni))


@router.post("/2fa/passkey/conferma")
def conferma_passkey(data: PasskeyIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    # La sfida nasce solo da /2fa/passkey/inizia, che chiede la password.
    sfida = due_fattori.prendi_sfida(db, user)
    if not sfida:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La richiesta della passkey è scaduta: riprova.")
    rp_id, origine = due_fattori.rp_e_origine(request)
    try:
        verificata = verify_registration_response(
            credential=data.credenziale,
            expected_challenge=sfida,
            expected_rp_id=rp_id,
            expected_origin=origine,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("Registrazione passkey rifiutata per %s: %s", user.email, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La passkey non è stata registrata. Riprova.")
    id_credenziale = bytes_to_base64url(verificata.credential_id)
    if db.scalar(select(Passkey).where(Passkey.credential_id == id_credenziale)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Questa passkey è già registrata.")
    risposta = data.credenziale.get("response") if isinstance(data.credenziale.get("response"), dict) else {}
    trasporti = [t for t in (risposta.get("transports") or []) if isinstance(t, str)][:6]
    nome = " ".join(data.nome.split())[:80] or f"Passkey {len(user.passkey) + 1}"
    user.passkey.append(Passkey(
        credential_id=id_credenziale,
        chiave_pubblica=bytes_to_base64url(verificata.credential_public_key),
        contatore=verificata.sign_count,
        trasporti=",".join(trasporti)[:120],
        nome=nome,
    ))
    return _dopo_attivazione(db, user, "2fa_passkey_aggiunta")


@router.post("/2fa/passkey/{passkey_id}/rimuovi")
def rimuovi_passkey(passkey_id: int, data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    passkey = next((p for p in user.passkey if p.id == passkey_id), None)
    if not passkey:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Passkey non trovata.")
    user.passkey.remove(passkey)
    due_fattori.dopo_una_disattivazione(user)
    db.commit()
    audit(db, user.email, "2fa_passkey_rimossa", passkey.nome)
    return stato_sicurezza(user)


# --- codici di riserva ---------------------------------------------------------
@router.post("/2fa/riserva")
def nuovi_codici_riserva(data: ConPasswordIn, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_user)):
    _conferma(request, user, data.password)
    if not due_fattori.attiva(user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Prima attiva almeno un modo di verifica.")
    codici = due_fattori.nuovi_codici_riserva(user)
    db.commit()
    audit(db, user.email, "2fa_riserva_rigenerati")
    return {**stato_sicurezza(user), "codici_riserva": codici}
