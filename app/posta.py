"""Posta in uscita (codici della verifica in due passaggi), spedita col server SMTP del .env."""
import asyncio
import logging
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from . import config

log = logging.getLogger("catasync.posta")


class PostaErrore(Exception):
    """L'email non è partita: il messaggio è già in italiano, da mostrare all'amministratore."""


def _porta() -> int:
    if config.SMTP_PORT:
        return config.SMTP_PORT
    return {"ssl": 465, "none": 25}.get(config.SMTP_SECURITY, 587)


def mittente() -> str:
    """L'indirizzo che spedisce: SMTP_FROM, altrimenti l'utente SMTP se è un'email."""
    if config.SMTP_FROM:
        return config.SMTP_FROM
    return config.SMTP_USER if "@" in config.SMTP_USER else ""


def manca() -> list[str]:
    """Le variabili del .env che servono e non ci sono: vuoto = la posta è pronta."""
    assenti = []
    if not config.SMTP_HOST:
        assenti.append("SMTP_HOST")
    if not mittente():
        assenti.append("SMTP_FROM")
    if config.SMTP_SECURITY not in ("starttls", "ssl", "none"):
        assenti.append("SMTP_SECURITY")
    return assenti


def configurata() -> bool:
    return not manca()



def _spedisci(messaggio: EmailMessage) -> None:
    contesto = ssl.create_default_context()
    try:
        if config.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(config.SMTP_HOST, _porta(), timeout=config.SMTP_TIMEOUT, context=contesto)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, _porta(), timeout=config.SMTP_TIMEOUT)
        with server:
            server.ehlo()
            if config.SMTP_SECURITY == "starttls":
                server.starttls(context=contesto)
                server.ehlo()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(messaggio)
    except smtplib.SMTPAuthenticationError as exc:
        raise PostaErrore("Il server di posta ha rifiutato utente e password (SMTP_USER, SMTP_PASSWORD).") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise PostaErrore("Il server di posta ha rifiutato il destinatario.") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise PostaErrore("Il server di posta non accetta questo mittente (SMTP_FROM).") from exc
    except (smtplib.SMTPException, ssl.SSLError) as exc:
        raise PostaErrore(f"Il server di posta ha risposto con un errore: {exc}") from exc
    except OSError as exc:
        raise PostaErrore(f"Server di posta non raggiungibile ({config.SMTP_HOST}:{_porta()}): {exc}") from exc


def _intestazione_pulita(valore: str) -> str:
    """Niente a capo dentro oggetto e nomi: è così che si infilano intestazioni finte."""
    return " ".join(str(valore or "").split())


async def invia(destinatario: str, oggetto: str, testo: str, *, rispondi_a: str = "", nome_mittente: str = "") -> None:
    """Manda un'email di solo testo. Solleva PostaErrore se non parte."""
    if not configurata():
        raise PostaErrore("La posta non è configurata: nel .env mancano " + ", ".join(manca()) + ".")
    if not destinatario or "@" not in destinatario:
        raise PostaErrore("Manca l'indirizzo che riceve le richieste.")
    messaggio = EmailMessage()
    nome, indirizzo = parseaddr(mittente())
    messaggio["From"] = formataddr((_intestazione_pulita(nome or nome_mittente), indirizzo))
    messaggio["To"] = destinatario
    messaggio["Subject"] = _intestazione_pulita(oggetto)
    messaggio["Date"] = formatdate(localtime=True)
    messaggio["Message-ID"] = make_msgid(domain=indirizzo.split("@")[-1] if "@" in indirizzo else None)
    if rispondi_a:
        messaggio["Reply-To"] = str(Address(addr_spec=rispondi_a))
    messaggio.set_content(testo)
    await asyncio.to_thread(_spedisci, messaggio)
    log.info("Email spedita a %s: %s", destinatario, messaggio["Subject"])
