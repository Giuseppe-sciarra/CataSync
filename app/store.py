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
    }


def salva_ricerca(
    db: Session, motore: str, chiave: str | None, cx: str, url: str = "",
    lt_url: str | None = None, lt_chiave: str | None = None,
    render_url: str | None = None, motori: str | None = None,
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
