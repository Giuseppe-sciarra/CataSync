"""Avviso di manutenzione programmata.

L'amministratore lo accende da Impostazioni con giorno, ora d'inizio e ora di fine: compare
nella pagina di accesso, sopra il modulo, finché la manutenzione non è finita. Passata l'ora di
fine sparisce da solo, anche se nessuno spegne l'interruttore. Gli orari sono quelli del fuso
del portale (TZ nel .env, di serie Europe/Rome).

Tutto sta in un'unica impostazione JSON, come la pagina pubblica."""
import json
import re
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from .store import get_setting, set_setting

CHIAVE = "manutenzione"
LIMITE_NOTA = 240

DI_SERIE = {
    "attiva": False,
    "giorno": "",     # AAAA-MM-GG
    "dalle": "",      # HH:MM
    "alle": "",       # HH:MM; se viene prima di «dalle» vale il giorno dopo
    "nota": "",       # facoltativa, scritta dall'amministratore così com'è
}

GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre")

# Le frasi dell'avviso. Sono anche le chiavi del catalogo delle traduzioni: la pagina di
# accesso le traduce nella lingua di chi la apre e ci mette la data scritta in quella lingua.
FRASE_STESSO_GIORNO = "{0}, dalle {1} alle {2}, il portale sarà fermo per manutenzione: in quelle ore i servizi non funzioneranno"
FRASE_A_CAVALLO = "{0}, dalle {1} alle {2} del giorno dopo, il portale sarà fermo per manutenzione: in quelle ore i servizi non funzioneranno"
FRASE_IN_CORSO = "Fino alle {0} il portale è fermo per manutenzione: i servizi potrebbero non funzionare"

_ORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def leggi(db: Session) -> dict:
    try:
        salvati = json.loads(get_setting(db, CHIAVE) or "{}")
    except ValueError:
        salvati = {}
    dati = dict(DI_SERIE)
    if isinstance(salvati, dict):
        dati.update({k: v for k, v in salvati.items() if k in DI_SERIE})
    return dati


def salva(db: Session, nuovi: dict) -> dict:
    dati = dict(DI_SERIE)
    dati.update({k: v for k, v in nuovi.items() if k in DI_SERIE})
    set_setting(db, CHIAVE, json.dumps(dati, ensure_ascii=False))
    return dati


def _fuso():
    from .scheduler import tz

    return tz()


def adesso() -> datetime:
    return datetime.now(_fuso())


def giorno_valido(valore: str) -> date | None:
    try:
        return date.fromisoformat(str(valore or "").strip())
    except ValueError:
        return None


def ora_valida(valore: str) -> time | None:
    valore = str(valore or "").strip()
    if not _ORA.match(valore):
        return None
    ore, minuti = valore.split(":")
    return time(int(ore), int(minuti))


def finestra(dati: dict) -> tuple[datetime, datetime] | None:
    """Inizio e fine della manutenzione nel fuso del portale, o None se i dati non bastano.

    Un'ora di fine che viene prima (o è uguale) di quella d'inizio vale il giorno dopo:
    dalle 23:00 alle 02:00 è una notte, non un errore."""
    giorno, dalle, alle = giorno_valido(dati.get("giorno")), ora_valida(dati.get("dalle")), ora_valida(dati.get("alle"))
    if not (giorno and dalle and alle):
        return None
    fuso = _fuso()
    inizio = datetime.combine(giorno, dalle, tzinfo=fuso)
    fine = datetime.combine(giorno, alle, tzinfo=fuso)
    if fine <= inizio:
        fine += timedelta(days=1)
    return inizio, fine


def stato(dati: dict, quando: datetime | None = None) -> str:
    """spenta | incompleta | programmata | in_corso | finita."""
    if not dati.get("attiva"):
        return "spenta"
    orari = finestra(dati)
    if not orari:
        return "incompleta"
    quando = quando or adesso()
    inizio, fine = orari
    if quando >= fine:
        return "finita"
    return "in_corso" if quando >= inizio else "programmata"


def giorno_esteso(giorno: date) -> str:
    """«giovedì 23 ottobre»: senza anno, la manutenzione si annuncia con pochi giorni d'anticipo."""
    return f"{GIORNI[giorno.weekday()]} {giorno.day} {MESI[giorno.month - 1]}"


def frase(dati: dict, quando: datetime | None = None) -> str:
    """La frase dell'avviso in italiano (la pagina la riscrive nella lingua di chi legge)."""
    orari = finestra(dati)
    if not orari:
        return ""
    inizio, fine = orari
    if stato(dati, quando) == "in_corso":
        return FRASE_IN_CORSO.replace("{0}", fine.strftime("%H:%M"))
    modello = FRASE_STESSO_GIORNO if fine.date() == inizio.date() else FRASE_A_CAVALLO
    testo = (modello.replace("{0}", giorno_esteso(inizio.date()))
             .replace("{1}", inizio.strftime("%H:%M")).replace("{2}", fine.strftime("%H:%M")))
    return testo[:1].upper() + testo[1:]


def per_la_pagina(db: Session, quando: datetime | None = None) -> dict | None:
    """Quello che serve alla pagina di accesso, oppure None se l'avviso non va mostrato."""
    dati = leggi(db)
    attuale = stato(dati, quando)
    if attuale not in ("programmata", "in_corso"):
        return None
    inizio, fine = finestra(dati)
    return {
        "stato": attuale,
        "giorno": inizio.date().isoformat(),
        "dalle": inizio.strftime("%H:%M"),
        "alle": fine.strftime("%H:%M"),
        "a_cavallo": fine.date() != inizio.date(),
        "frase": frase(dati, quando),
        "nota": (dati.get("nota") or "").strip(),
    }


def per_admin(dati: dict) -> dict:
    """I dati salvati, più lo stato di adesso e la frase così come la leggerà chi accede."""
    from . import config

    return {**dati, "stato": stato(dati), "frase": frase(dati), "fuso": config.TIMEZONE}
