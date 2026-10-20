"""Impostazioni globali del portale."""
from sqlalchemy.orm import Session

from .models import AuditLog, Setting


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def audit(db: Session, user_email: str, action: str, detail: str = "") -> None:
    db.add(AuditLog(user_email=user_email, action=action, detail=detail))
    db.commit()


# --------------------------------------------------------- marca del portale
RICERCA_MOTORE = "ricerca_motore"
RICERCA_CHIAVE = "ricerca_chiave"      # cifrata
RICERCA_CX = "ricerca_cx"
RICERCA_URL = "ricerca_url"            # indirizzo di SearXNG
RICERCA_MOTORI = "ricerca_motori"      # motori di SearXNG da interrogare (vuoto = predefiniti)
TRAD_URL = "traduzione_url"            # indirizzo di LibreTranslate (vuoto = spenta)
TRAD_CHIAVE = "traduzione_chiave"      # cifrata, facoltativa
RENDER_URL = "render_url"              # browser headless per i siti solo-JavaScript
GIRO_CODICI = "ricerca_giro_codici"    # codici cercati di fila prima di una pausa
GIRO_PAUSA = "ricerca_giro_pausa"      # minuti di pausa fra un giro e l'altro
RISERVA_CHIAVE = "ricerca_riserva_brave"  # chiave Brave Search API di riserva a SearXNG
CATALOGHI_FORZA = "ricerca_cataloghi_forza"  # "1" = cataloghi letti anche se robots.txt lo vieta
# Icecat: una sola utenza per tutto il portale, prima fonte per ogni cliente.
ICECAT_ATTIVO = "icecat_attivo"
ICECAT_UTENTE = "icecat_utente"
ICECAT_API_TOKEN = "icecat_api_token"
ICECAT_CONTENT_TOKEN = "icecat_content_token"

BRAND_NAME_KEY = "portal_name"
BRAND_LOGO_KEY = "portal_logo"
BRAND_FAVICON_KEY = "portal_favicon"       # nome del file caricato, in UPLOAD_DIR
BRAND_FAVICON_VER = "portal_favicon_v"     # cambia a ogni caricamento: i browser la riscaricano

FAVICON_TIPI = {".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".webp": "image/webp", ".jpg": "image/jpeg"}


def portal_brand(db: Session) -> dict:
    """Nome, logo e favicon del portale. Il nome impostato qui vince su APP_NAME.

    ``favicon`` vuota = quella di serie (static/favicon.*)."""
    from pathlib import Path

    from . import config

    favicon = get_setting(db, BRAND_FAVICON_KEY)
    estensione = Path(favicon).suffix.lower() if favicon else ""
    versione = get_setting(db, BRAND_FAVICON_VER) or "1"
    return {
        "name": get_setting(db, BRAND_NAME_KEY) or config.APP_NAME,
        "logo": "/media/portale/logo" if get_setting(db, BRAND_LOGO_KEY) else "",
        "favicon": f"/media/portale/favicon?v={versione}" if favicon else "",
        "favicon_type": FAVICON_TIPI.get(estensione, ""),
        # Solo un'immagine «a pixel» fa anche da icona sul telefono (apple-touch-icon).
        "favicon_raster": estensione in (".png", ".webp", ".jpg"),
    }


# ------------------------------------------------------- motore di ricerca
def ricerca_config(db: Session) -> dict:
    """Motore e credenziali per la ricerca automatica sul web. Globali, non per cliente."""
    from .security import decrypt

    return {
        "motore": get_setting(db, RICERCA_MOTORE) or "",
        "chiave": decrypt(get_setting(db, RICERCA_CHIAVE) or ""),
        "cx": get_setting(db, RICERCA_CX) or "",
        "url": get_setting(db, RICERCA_URL) or "",
        "motori": get_setting(db, RICERCA_MOTORI) or "",
        "lt_url": get_setting(db, TRAD_URL) or "",
        "lt_chiave": decrypt(get_setting(db, TRAD_CHIAVE) or ""),
        "render_url": get_setting(db, RENDER_URL) or "",
        "giro_codici": _intero(get_setting(db, GIRO_CODICI), 5),
        "giro_pausa": _decimale(get_setting(db, GIRO_PAUSA), 2.0),
        "riserva_chiave": decrypt(get_setting(db, RISERVA_CHIAVE) or ""),
        "cataloghi_forza": get_setting(db, CATALOGHI_FORZA) == "1",
    }


def icecat_config(db: Session) -> dict:
    """L'utenza Icecat del portale: vale per tutti i clienti."""
    from .security import decrypt

    utente = (get_setting(db, ICECAT_UTENTE) or "").strip()
    return {
        "attivo": get_setting(db, ICECAT_ATTIVO) == "1" and bool(utente),
        "acceso": get_setting(db, ICECAT_ATTIVO) == "1",
        "utente": utente,
        "api_token": decrypt(get_setting(db, ICECAT_API_TOKEN) or ""),
        "content_token": decrypt(get_setting(db, ICECAT_CONTENT_TOKEN) or ""),
    }


def salva_icecat(db: Session, attivo: bool, utente: str, api_token: str | None = None,
                 content_token: str | None = None, togli_token: bool = False) -> None:
    """Token vuoti = non toccare quelli salvati; ``togli_token`` li cancella (si torna a Open Icecat)."""
    from .security import encrypt

    set_setting(db, ICECAT_ATTIVO, "1" if attivo else "0")
    set_setting(db, ICECAT_UTENTE, (utente or "").strip())
    if togli_token:
        set_setting(db, ICECAT_API_TOKEN, "")
        set_setting(db, ICECAT_CONTENT_TOKEN, "")
        return
    if api_token:
        set_setting(db, ICECAT_API_TOKEN, encrypt(api_token.strip()))
    if content_token:
        set_setting(db, ICECAT_CONTENT_TOKEN, encrypt(content_token.strip()))


def _intero(valore: str, predefinito: int) -> int:
    try:
        return int(valore)
    except (TypeError, ValueError):
        return predefinito


def _decimale(valore: str, predefinito: float) -> float:
    try:
        return float(valore)
    except (TypeError, ValueError):
        return predefinito


def salva_ricerca(
    db: Session, motore: str, chiave: str | None, cx: str, url: str = "",
    lt_url: str | None = None, lt_chiave: str | None = None,
    render_url: str | None = None, motori: str | None = None,
    giro_codici: int | None = None, giro_pausa: float | None = None,
    riserva_chiave: str | None = None, togli_riserva: bool = False,
    cataloghi_forza: bool | None = None,
) -> None:
    from .security import encrypt

    set_setting(db, RICERCA_MOTORE, motore)
    set_setting(db, RICERCA_CX, cx)
    set_setting(db, RICERCA_URL, url.rstrip("/"))
    if chiave:  # vuota = non toccare quella già salvata
        set_setting(db, RICERCA_CHIAVE, encrypt(chiave))
    if lt_url is not None:
        set_setting(db, TRAD_URL, lt_url.rstrip("/"))
    if lt_chiave:
        set_setting(db, TRAD_CHIAVE, encrypt(lt_chiave))
    if render_url is not None:
        set_setting(db, RENDER_URL, render_url.rstrip("/"))
    if motori is not None:
        pulito = ", ".join(p.strip().lower() for p in motori.split(",") if p.strip())
        set_setting(db, RICERCA_MOTORI, pulito)
    if giro_codici is not None:
        set_setting(db, GIRO_CODICI, str(int(giro_codici)))
    if giro_pausa is not None:
        set_setting(db, GIRO_PAUSA, f"{float(giro_pausa):g}")
    if togli_riserva:
        set_setting(db, RISERVA_CHIAVE, "")
    elif riserva_chiave:
        set_setting(db, RISERVA_CHIAVE, encrypt(riserva_chiave.strip()))
    if cataloghi_forza is not None:
        set_setting(db, CATALOGHI_FORZA, "1" if cataloghi_forza else "0")
