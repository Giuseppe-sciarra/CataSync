"""Template Jinja condivisi dalle pagine (pannello, accesso, pagina pubblica)."""
from fastapi.templating import Jinja2Templates

from . import config

templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


# Versione dei file statici: cambia da sola a ogni build, perché è l'ora di
# modifica più recente di ciò che sta in /static. Appesa come ?v=... agli
# indirizzi di css e js, costringe i browser a riscaricare i file dopo un
# aggiornamento: senza, restava in giro lo hub.js vecchio e i pulsanti nuovi
# «non esistevano» finché qualcuno non faceva Ctrl+F5.
def _versione_statici() -> int:
    cartella = config.BASE_DIR / "static"
    try:
        # Anche le sottocartelle (i18n): un catalogo di traduzioni vecchio in
        # cache lasciava in italiano le scritte nuove.
        return int(max(f.stat().st_mtime for f in cartella.rglob("*") if f.is_file()))
    except (OSError, ValueError):
        return 0


templates.env.globals["static_v"] = _versione_statici()
templates.env.globals["app_version"] = config.APP_VERSION
templates.env.globals["source_url"] = config.SOURCE_URL


def contesto_favicon(marca: dict) -> dict:
    return {
        "portal_favicon": marca.get("favicon", ""),
        "portal_favicon_type": marca.get("favicon_type", ""),
        "portal_favicon_raster": marca.get("favicon_raster", False),
    }
