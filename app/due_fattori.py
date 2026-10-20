"""Verifica in due passaggi: dopo la password, una seconda prova che sei proprio tu.

Tre modi, attivabili anche tutti insieme (all'accesso si sceglie quello che si ha sotto mano):

* app di autenticazione (TOTP, RFC 6238): Google o Microsoft Authenticator, 1Password, Aegis…
  Scritto qui con hmac e base32 della libreria standard: sono venti righe e non serve altro.
* codice via email: sei cifre spedite col server SMTP del .env, valide dieci minuti.
* passkey (WebAuthn): impronta, volto o PIN del dispositivo, oppure una chiave di sicurezza.
  La verifica crittografica la fa py_webauthn, che è la parte da non scriversi a mano.

Con il primo modo attivato arrivano dieci codici di riserva, ognuno buono una volta, per chi
perde il telefono. Se si perde tutto, l'amministratore toglie la verifica dalla scheda Utenti;
se a restare fuori è l'unico amministratore, da riga di comando:

    docker exec -it catasync-hub python -m app.due_fattori togli admin@example.com
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import quote, urlparse

from . import config

# --- app di autenticazione -----------------------------------------------
PASSO = 30          # secondi di validità di un codice
CIFRE = 6
# Un intervallo prima e uno dopo: l'orologio del telefono può essere avanti o indietro.
TOLLERANZA = 1

# --- codici sbagliati nel secondo passaggio (tutti i modi insieme) -----------
ERRORI_MASSIMI = 6
ERRORI_FINESTRA = 600   # secondi

# --- codice via email ------------------------------------------------------
EMAIL_MINUTI = 10
EMAIL_TENTATIVI = 5
EMAIL_PAUSA = 30    # secondi prima di poterne chiedere un altro
EMAIL_ORA = 6       # codici spediti al massimo in un'ora

# --- codici di riserva -----------------------------------------------------
RISERVA_QUANTI = 10
# Niente 0/o, 1/l/i: si ricopiano da un foglio senza dubbi.
_ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"

# --- passkey ---------------------------------------------------------------
SFIDA_MINUTI = 5

METODI = ("passkey", "app", "email")


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _utc(valore: datetime | None) -> datetime | None:
    """SQLite restituisce le date senza fuso: sono scritte in UTC, si rimette il fuso."""
    if valore is None:
        return None
    return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)


def _impronta(tipo: str, user_id: int, valore: str) -> str:
    """Impronta legata alla SECRET_KEY e all'utente: un database copiato non basta a provarli."""
    messaggio = f"{tipo}:{user_id}:{valore}".encode()
    return hmac.new(config.SECRET_KEY.encode(), messaggio, hashlib.sha256).hexdigest()


def solo_cifre(codice: str) -> str:
    return "".join(c for c in str(codice or "") if c.isdigit())


# ================================================================ TOTP
def nuovo_segreto() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _chiave(segreto: str) -> bytes:
    pulito = segreto.replace(" ", "").upper()
    return base64.b32decode(pulito + "=" * (-len(pulito) % 8))


def codice_totp(segreto: str, passo: int) -> str:
    digest = hmac.new(_chiave(segreto), struct.pack(">Q", passo), hashlib.sha1).digest()
    inizio = digest[-1] & 0x0F
    numero = (struct.unpack(">I", digest[inizio:inizio + 4])[0] & 0x7FFFFFFF) % (10 ** CIFRE)
    return str(numero).zfill(CIFRE)


def _aggiorna(db, user, *condizioni, **valori) -> bool:
    """UPDATE atomico sull'utente: vero se la riga rispettava le condizioni ed è cambiata.

    Due richieste in parallelo con lo stesso codice: una sola trova la riga com'era e passa,
    l'altra trova zero righe. Poi l'oggetto si rilegge, per non scrivere sopra valori vecchi."""
    from sqlalchemy import update

    from .models import User

    righe = db.execute(update(User).where(User.id == user.id, *condizioni).values(**valori)).rowcount
    db.commit()
    db.refresh(user)
    return righe == 1


def prenota_tentativo(db, user) -> bool:
    """Conta un tentativo PRIMA di controllare il codice: anche con cento richieste in parallelo
    se ne valutano al massimo ERRORI_MASSIMI ogni dieci minuti. Falso = troppi, fermarsi."""
    from .models import User

    dal = _utc(user.errori_2fa_dal)
    if not dal or (_adesso() - dal).total_seconds() >= ERRORI_FINESTRA:
        _aggiorna(db, user, errori_2fa=0, errori_2fa_dal=_adesso())
    return _aggiorna(db, user, User.errori_2fa < ERRORI_MASSIMI, errori_2fa=User.errori_2fa + 1)


def azzera_tentativi(user) -> None:
    user.errori_2fa = 0
    user.errori_2fa_dal = None


def consuma_passo(db, user, passo: int) -> bool:
    """Segna come usato l'intervallo del codice, solo se nessun altro l'ha appena fatto."""
    from .models import User

    return _aggiorna(db, user, User.totp_ultimo_passo < passo, totp_ultimo_passo=passo)


def verifica_totp(segreto: str, codice: str, ultimo_passo: int = 0, quando: float | None = None) -> int | None:
    """L'intervallo a cui appartiene il codice, se è giusto e non già usato; altrimenti None."""
    codice = solo_cifre(codice)
    if not segreto or len(codice) != CIFRE:
        return None
    attuale = int((time.time() if quando is None else quando) // PASSO)
    for scarto in (0, -1, 1)[: 1 + 2 * TOLLERANZA]:
        passo = attuale + scarto
        if passo <= ultimo_passo:
            continue
        if hmac.compare_digest(codice_totp(segreto, passo), codice):
            return passo
    return None


def uri_otpauth(segreto: str, account: str, emittente: str) -> str:
    """L'indirizzo che il QR contiene: le app lo leggono e si configurano da sole."""
    emittente = emittente.replace(":", " ").strip() or "CataSync"
    etichetta = quote(f"{emittente}:{account}")
    return (f"otpauth://totp/{etichetta}?secret={segreto}&issuer={quote(emittente)}"
            f"&algorithm=SHA1&digits={CIFRE}&period={PASSO}")


def qr_svg(testo: str) -> str:
    """Il QR come immagine SVG in un data: URI, generato qui (niente servizi esterni)."""
    import segno

    return segno.make(testo, error="m").svg_data_uri(scale=6, border=2, dark="#111111", light="#ffffff")


def segreto_leggibile(segreto: str) -> str:
    """A gruppi di quattro, per chi lo ricopia a mano nell'app."""
    return " ".join(segreto[i:i + 4] for i in range(0, len(segreto), 4))


# ========================================================= codice via email
def prepara_codice_email(user, scopo: str) -> str:
    """Sei cifre nuove per l'utente: si salva solo l'impronta, il codice va nella mail."""
    codice = str(secrets.randbelow(10 ** CIFRE)).zfill(CIFRE)
    user.codice_email = _impronta("email:" + scopo, user.id, codice)
    user.codice_email_scopo = scopo
    user.codice_email_scade = _adesso() + timedelta(minutes=EMAIL_MINUTI)
    user.codice_email_tentativi = 0
    user.codice_email_inviato = _adesso()
    dal = _utc(user.invii_email_dal)
    if not dal or (_adesso() - dal).total_seconds() >= 3600:
        user.invii_email, user.invii_email_dal = 1, _adesso()
    else:
        user.invii_email = (user.invii_email or 0) + 1
    return codice


def troppi_invii(user) -> bool:
    """Vero se nell'ultima ora sono già partiti EMAIL_ORA codici: niente raffiche di email."""
    dal = _utc(user.invii_email_dal)
    return bool(dal) and (_adesso() - dal).total_seconds() < 3600 and (user.invii_email or 0) >= EMAIL_ORA


def attesa_reinvio(user, scopo: str) -> int:
    """Secondi da aspettare prima di poter spedire un altro codice (0 = si può).

    La pausa vale fra due codici per lo stesso scopo ancora in sospeso: chi ha appena attivato il
    codice via email e prova subito a rientrare il suo codice di accesso lo riceve senza aspettare."""
    inviato = _utc(user.codice_email_inviato)
    if not inviato or not user.codice_email or user.codice_email_scopo != scopo:
        return 0
    trascorsi = (_adesso() - inviato).total_seconds()
    return max(0, int(EMAIL_PAUSA - trascorsi + 0.999))


def verifica_codice_email(db, user, codice: str, scopo: str) -> str:
    """'ok', oppure il motivo del rifiuto: 'nessuno', 'scaduto', 'troppi', 'errato'.

    Il codice giusto si cancella con un UPDATE atomico: usato due volte insieme, passa una volta."""
    from .models import User

    codice = solo_cifre(codice)
    impronta = user.codice_email
    if not impronta or user.codice_email_scopo != scopo:
        return "nessuno"
    scade = _utc(user.codice_email_scade)
    if not scade or scade < _adesso():
        return "scaduto"
    if (user.codice_email_tentativi or 0) >= EMAIL_TENTATIVI:
        return "troppi"
    if len(codice) != CIFRE or not hmac.compare_digest(impronta, _impronta("email:" + scopo, user.id, codice)):
        _aggiorna(db, user, codice_email_tentativi=User.codice_email_tentativi + 1)
        return "errato"
    usato = _aggiorna(db, user, User.codice_email == impronta, codice_email="", codice_email_scopo="",
                      codice_email_scade=None, codice_email_tentativi=0)
    return "ok" if usato else "nessuno"


def cancella_codice_email(user) -> None:
    user.codice_email = ""
    user.codice_email_scopo = ""
    user.codice_email_scade = None
    user.codice_email_tentativi = 0


def email_mascherata(email: str) -> str:
    """g•••••e@example.com: abbastanza per riconoscerla, non per leggerla a chi non la sa."""
    nome, _, dominio = (email or "").partition("@")
    if not dominio:
        return email
    if len(nome) <= 2:
        return nome[:1] + "•••@" + dominio
    return nome[0] + "•" * min(6, len(nome) - 2) + nome[-1] + "@" + dominio


# ========================================================= codici di riserva
def normalizza_riserva(codice: str) -> str:
    return "".join(c for c in str(codice or "").lower() if c.isalnum())


def nuovi_codici_riserva(user) -> list[str]:
    codici = []
    for _ in range(RISERVA_QUANTI):
        grezzo = "".join(secrets.choice(_ALFABETO) for _ in range(10))
        codici.append(grezzo[:5] + "-" + grezzo[5:])
    user.codici_riserva = json.dumps([_impronta("riserva", user.id, normalizza_riserva(c)) for c in codici])
    return codici


def _impronte_riserva(user) -> list[str]:
    try:
        valori = json.loads(user.codici_riserva or "[]")
    except ValueError:
        return []
    return [v for v in valori if isinstance(v, str)] if isinstance(valori, list) else []


def riserva_rimasti(user) -> int:
    return len(_impronte_riserva(user))


def usa_codice_riserva(db, user, codice: str) -> bool:
    """Vero se il codice è uno di quelli rimasti: da quel momento non vale più.

    L'elenco si riscrive solo se nel frattempo nessuno l'ha cambiato (confronta e scambia):
    lo stesso codice usato da due parti insieme passa una volta sola."""
    from .models import User

    pulito = normalizza_riserva(codice)
    if len(pulito) != 10:
        return False
    cercata = _impronta("riserva", user.id, pulito)
    prima = user.codici_riserva or ""
    impronte = _impronte_riserva(user)
    trovata = next((i for i in impronte if hmac.compare_digest(i, cercata)), None)
    if trovata is None:
        return False
    impronte.remove(trovata)
    return _aggiorna(db, user, User.codici_riserva == prima, codici_riserva=json.dumps(impronte))


# ================================================================ stato
def metodi(user) -> list[str]:
    """I modi attivi, nell'ordine in cui la pagina di accesso li propone."""
    attivi = []
    if user.passkey:
        attivi.append("passkey")
    if user.totp_segreto:
        attivi.append("app")
    if user.email_2fa:
        attivi.append("email")
    return attivi


def attiva(user) -> bool:
    return bool(metodi(user))


def togli_tutto(user) -> None:
    """Spegne la verifica in due passaggi dell'utente: tutti i modi e i codici di riserva."""
    user.totp_segreto = ""
    user.totp_in_attesa = ""
    user.totp_ultimo_passo = 0
    user.email_2fa = False
    cancella_codice_email(user)
    user.codice_email_inviato = None
    user.codici_riserva = ""
    user.sfida_passkey = ""
    user.sfida_passkey_scade = None
    azzera_tentativi(user)
    user.passkey.clear()


def dopo_una_disattivazione(user) -> None:
    """Tolto l'ultimo modo attivo, i codici di riserva non servono più a niente."""
    if not attiva(user):
        user.codici_riserva = ""


# ================================================================ passkey
def rp_e_origine(request) -> tuple[str, str]:
    """Dominio (rp_id) e origine attesi dalle passkey: quelli di PUBLIC_URL se c'è, come il
    controllo di provenienza del portale; altrimenti quelli con cui si sta parlando."""
    if config.PUBLIC_URL:
        indirizzo = urlparse(config.PUBLIC_URL)
        return indirizzo.hostname or "", f"{indirizzo.scheme}://{indirizzo.netloc}"
    return request.url.hostname or "", f"{request.url.scheme}://{request.url.netloc}"


def maniglia_utente(user) -> bytes:
    """Identificativo dell'utente per l'autenticatore: stabile, senza dati personali dentro."""
    return hashlib.sha256(f"{config.SECRET_KEY}:passkey:{user.id}".encode()).digest()[:16]


def salva_sfida(user, sfida: bytes) -> None:
    from webauthn.helpers import bytes_to_base64url

    user.sfida_passkey = bytes_to_base64url(sfida)
    user.sfida_passkey_scade = _adesso() + timedelta(minutes=SFIDA_MINUTI)


def prendi_sfida(db, user) -> bytes | None:
    """La sfida in sospeso, una volta sola: letta, si cancella (anche fra richieste parallele)."""
    from webauthn.helpers import base64url_to_bytes

    from .models import User

    sfida, scade = user.sfida_passkey, _utc(user.sfida_passkey_scade)
    if not sfida:
        return None
    presa = _aggiorna(db, user, User.sfida_passkey == sfida, sfida_passkey="", sfida_passkey_scade=None)
    if not presa or not scade or scade < _adesso():
        return None
    return base64url_to_bytes(sfida)


# ============================================================ traduzioni
@lru_cache(maxsize=8)
def _catalogo(lingua: str) -> dict:
    try:
        with open(config.BASE_DIR / "static" / "i18n" / f"{lingua}.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def traduci(testo: str, lingua: str, *valori) -> str:
    """Per le email: la stessa traduzione dell'interfaccia, presa dal catalogo (static/i18n)."""
    lingua = (lingua or "it").lower()[:2]
    if lingua not in ("en", "fr", "de"):
        lingua = "it"
    tradotto = testo if lingua == "it" else (_catalogo(lingua).get(testo) or testo)
    for i, valore in enumerate(valori):
        tradotto = tradotto.replace("{" + str(i) + "}", str(valore))
    return tradotto


# ======================================================= riga di comando
def _togli_da_riga_di_comando(email: str) -> int:
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import User
    from .store import audit

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if not user:
            print(f"Nessun utente con email {email}.")
            return 1
        togli_tutto(user)
        db.commit()
        audit(db, "riga di comando", "2fa_reset", user.email)
        print(f"Verifica in due passaggi tolta a {user.email}: ora entra con la sola password.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "togli":
        print("Uso: python -m app.due_fattori togli <email>")
        sys.exit(2)
    sys.exit(_togli_da_riga_di_comando(sys.argv[2]))
