"""TD Sync Catalog — portale multi-cliente per l'import schede prodotto da EAN."""
import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import config, worker
from .db import Base, SessionLocal, engine, ensure_columns, get_db, migra_dati, migra_schema
from .models import Client, Site, User
from .routers import admin, auth, client, magazzino
from .security import current_user, hash_password, require_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("catasync")

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

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def bootstrap_admin() -> None:
    """Crea l'admin al primo avvio se ADMIN_PASSWORD è impostata."""
    if not config.ADMIN_PASSWORD:
        return
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.email == config.ADMIN_EMAIL.lower())):
            return
        db.add(
            User(
                email=config.ADMIN_EMAIL.lower(),
                full_name="Amministratore",
                password_hash=hash_password(config.ADMIN_PASSWORD),
                role="admin",
            )
        )
        db.commit()
        log.info("Creato utente amministratore %s", config.ADMIN_EMAIL)
    finally:
        db.close()


def pubblica_di_default() -> None:
    """I prodotti nascono pubblicati, non in bozza.

    Girata una volta sola: i siti già configurati passano da bozza a pubblicato,
    poi il flag impedisce di rifarlo, così se un domani rimetti bozza su un sito
    la scelta resta tua e nessuno te la ribalta al riavvio."""
    from .store import get_setting, set_setting
    from sqlalchemy import update

    db = SessionLocal()
    try:
        if get_setting(db, "default_status_migrato") == "1":
            return
        cambiati = db.execute(
            update(Site).where(Site.default_status == "draft").values(default_status="publish")
        ).rowcount
        set_setting(db, "default_status_migrato", "1")
        db.commit()
        if cambiati:
            log.info("Siti passati da bozza a pubblicato: %s", cambiati)
    finally:
        db.close()


def check_secrets() -> None:
    if config.SECRET_KEY == "cambiami-in-produzione":
        log.warning("SECRET_KEY è quella di esempio: le sessioni sono falsificabili. Cambiala.")
    if not config.FERNET_KEY:
        log.warning(
            "FERNET_KEY non impostata: le credenziali sono cifrate con una chiave derivata "
            "da SECRET_KEY. Se cambi SECRET_KEY diventano illeggibili."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migra_schema()      # prima delle colonne nuove: rinomina e toglie quelle vecchie
    ensure_columns()
    migra_dati()        # dopo: servono le colonne nuove
    pubblica_di_default()
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    check_secrets()
    bootstrap_admin()
    tasks = []
    if config.WORKER_ENABLED:
        tasks.append(asyncio.create_task(worker.run_forever()))
    if config.SCHEDULER_ENABLED:
        tasks.append(asyncio.create_task(worker.run_scheduler_forever()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    max_age=config.SESSION_MAX_AGE,
    same_site="strict",
    https_only=config.COOKIE_SECURE,
)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Controllo di provenienza sulle richieste che scrivono, più intestazioni di sicurezza.

    Il cookie è già SameSite=strict, quindi un sito terzo non se lo porta dietro;
    questo è il secondo giro di chiave, per i browser che si comportano male.
    """
    if request.method in WRITE_METHODS and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin:
            host = urlparse(origin).netloc
            expected = urlparse(config.PUBLIC_URL).netloc if config.PUBLIC_URL else request.url.netloc
            if host and expected and host != expected:
                return JSONResponse({"detail": "Richiesta con provenienza non valida."}, status_code=403)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(client.router)
app.include_router(magazzino.router)

static_dir = config.BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.exception_handler(500)
async def server_error(_request: Request, exc: Exception):
    log.exception("Errore non gestito", exc_info=exc)
    return JSONResponse({"detail": "Errore interno. Il dettaglio è nei log del container."}, status_code=500)


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


def _favicon_caricata(db) -> tuple:
    """(percorso, tipo) della favicon caricata da Impostazioni, o (None, "")."""
    from pathlib import Path

    from .store import BRAND_FAVICON_KEY, FAVICON_TIPI, get_setting

    nome = get_setting(db, BRAND_FAVICON_KEY)
    if not nome:
        return None, ""
    percorso = config.UPLOAD_DIR / nome
    if not percorso.is_file():
        return None, ""
    return percorso, FAVICON_TIPI.get(Path(nome).suffix.lower(), "application/octet-stream")


def _risposta_immagine(percorso, tipo: str, cache: str) -> FileResponse:
    from .html_sicuro import INTESTAZIONI_SVG

    intestazioni = {"Cache-Control": cache}
    if tipo == "image/svg+xml" or str(percorso).lower().endswith(".svg"):
        intestazioni.update(INTESTAZIONI_SVG)    # aperto da solo, un SVG non esegue niente
    return FileResponse(percorso, media_type=tipo or None, headers=intestazioni)


@app.get("/favicon.ico", include_in_schema=False)
def favicon(db=Depends(get_db)):
    """I browser la chiedono sempre qui, anche senza il <link> nella pagina:
    la favicon caricata da Impostazioni, altrimenti quella di serie."""
    percorso, tipo = _favicon_caricata(db)
    if percorso:
        return _risposta_immagine(percorso, tipo, "public, max-age=3600")
    return FileResponse(static_dir / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/media/portale/favicon", include_in_schema=False)
def portal_favicon(db=Depends(get_db)):
    """Favicon caricata da Impostazioni: pubblica come il logo, serve anche alla
    pagina di accesso. L'indirizzo nelle pagine porta ?v=… e cambia a ogni
    caricamento, quindi la si può tenere in cache a lungo."""
    percorso, tipo = _favicon_caricata(db)
    if not percorso:
        return _risposta_immagine(static_dir / "favicon.svg", "image/svg+xml", "public, max-age=300")
    return _risposta_immagine(percorso, tipo, "public, max-age=604800")


@app.get("/media/logo/{client_id}", include_in_schema=False)
def logo(client_id: int, db=Depends(get_db), user: User = Depends(require_user)):
    """Il logo lo vede solo chi ha diritto a quel cliente: non è un file pubblico."""
    if not user.is_admin and user.client_id != client_id:
        return JSONResponse({"detail": "Non disponibile."}, status_code=404)
    row = db.get(Client, client_id)
    if not row or not row.logo_path:
        return JSONResponse({"detail": "Nessun logo."}, status_code=404)
    path = config.UPLOAD_DIR / row.logo_path
    if not path.is_file():
        return JSONResponse({"detail": "Nessun logo."}, status_code=404)
    return _risposta_immagine(path, "", "private, max-age=300")


@app.get("/media/portale/logo", include_in_schema=False)
def portal_logo(db=Depends(get_db)):
    """Logo dell'agenzia: pubblico perché serve alla pagina di accesso,
    dove per definizione nessuno è ancora autenticato."""
    from .store import BRAND_LOGO_KEY, get_setting

    nome = get_setting(db, BRAND_LOGO_KEY)
    if not nome:
        return JSONResponse({"detail": "Nessun logo."}, status_code=404)
    path = config.UPLOAD_DIR / nome
    if not path.is_file():
        return JSONResponse({"detail": "Nessun logo."}, status_code=404)
    return _risposta_immagine(path, "", "public, max-age=300")


def _contesto_favicon(marca: dict) -> dict:
    return {
        "portal_favicon": marca.get("favicon", ""),
        "portal_favicon_type": marca.get("favicon_type", ""),
        "portal_favicon_raster": marca.get("favicon_raster", False),
    }


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, db=Depends(get_db)):
    from .store import portal_brand

    marca = portal_brand(db)
    return templates.TemplateResponse(
        request, "login.html", {"app_name": marca["name"], "portal_logo": marca["logo"], **_contesto_favicon(marca)}
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, db=Depends(get_db), user: User | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=302)
    from .store import portal_brand

    marca = portal_brand(db)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "app_name": marca["name"],
            "portal_logo": marca["logo"],
            **_contesto_favicon(marca),
            "is_admin": user.is_admin,
            "user_email": user.email,
        },
    )
