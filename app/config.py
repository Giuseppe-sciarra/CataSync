"""Configurazione letta da variabili d'ambiente."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# --- Database -------------------------------------------------------------
# SQLite di default, Postgres cambiando la sola DATABASE_URL:
#   postgresql+psycopg://user:pass@host:5432/hub
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'hub.db'}")

# --- Sicurezza ------------------------------------------------------------
# SECRET_KEY firma i cookie di sessione.
SECRET_KEY = os.getenv("SECRET_KEY", "cambiami-in-produzione")
# FERNET_KEY cifra a riposo le chiavi dei siti WooCommerce e le credenziali delle fonti.
# Generala con:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY = os.getenv("FERNET_KEY", "")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "43200"))  # 12h
COOKIE_SECURE = _bool("COOKIE_SECURE", "1")

# --- Bootstrap admin ------------------------------------------------------
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# --- Ricerca e fonti -----------------------------------------------------
# Giorni di validità della cache delle schede trovate sul web, per (codice, lingua).
CACHE_GIORNI = int(os.getenv("CACHE_DAYS", "30"))
# Lingua delle schede quando nessuno la sceglie. IT, EN, FR, DE, ES.
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "IT")
# Secondi fra una ricerca e la successiva sui motori gratuiti (SearXNG,
# DuckDuckGo). È ciò che evita i CAPTCHA: un carico da cento codici ci mette
# qualche minuto in più, ma Google non ci blocca per la giornata.
RICERCA_PAUSA = float(os.getenv("SEARCH_PAUSE", "4"))
# Quante volte rimandare un codice quando i motori ci bloccano, prima di
# chiuderlo come «non trovato» spiegando il perché.
RINVII_MASSIMI = int(os.getenv("SEARCH_MAX_DEFERRALS", "4"))
# Timeout delle chiamate ai connettori JSON dei fornitori.
FONTE_TIMEOUT = float(os.getenv("SOURCE_TIMEOUT", "25"))

# --- Worker ---------------------------------------------------------------
WORKER_ENABLED = _bool("WORKER_ENABLED", "1")
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))
WORKER_BATCH = int(os.getenv("WORKER_BATCH", "5"))

# --- Ri-sincronizzazione notturna ----------------------------------------
SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", "1")
SCHEDULER_POLL_SECONDS = float(os.getenv("SCHEDULER_POLL_SECONDS", "60"))
# Fuso usato per interpretare l'orario impostato su ogni sito.
TIMEZONE = os.getenv("TZ", "Europe/Rome")

# --- Sito ----------------------------------------------------------------
SITE_TIMEOUT = float(os.getenv("SITE_TIMEOUT", "60"))
# Pagine massime da sfogliare quando si cerca nel catalogo del sito (100 prodotti a pagina).
WC_MAX_SCAN_PAGES = int(os.getenv("WC_MAX_SCAN_PAGES", "30"))
APP_NAME = os.getenv("APP_NAME", "TD Sync Catalog")


def _versione() -> str:
    """Versione del programma: sta in VERSION.txt, accanto alla cartella app/.
    La aggiorna il workflow delle release, a ogni modifica pubblicata."""
    try:
        numero = (Path(__file__).resolve().parent.parent / "VERSION.txt").read_text(encoding="utf-8").strip()
    except OSError:
        numero = ""
    return numero or "0.0.0"


APP_VERSION = _versione()
# Dove sta il codice del programma. AGPL-3.0: chi modifica il portale e lo offre
# in rete ad altri deve indicare qui il PROPRIO sorgente.
SOURCE_URL = os.getenv("SOURCE_URL", "https://github.com/Giuseppe-sciarra/CataSync").strip()

# Indirizzo pubblico del portale, es. https://catalogo.example.com
# Serve al controllo di provenienza sulle richieste che scrivono.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# --- File caricati (loghi) ------------------------------------------------
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))   # 2 MB
MAX_CSV_BYTES = int(os.getenv("MAX_CSV_BYTES", str(5 * 1024 * 1024)))        # 5 MB
