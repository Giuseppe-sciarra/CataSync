"""Connessione al database."""
import logging
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

# Il driver installato è psycopg 3: SQLAlchemy con "postgresql://" cercherebbe
# psycopg2 e cadrebbe all'avvio. L'URL si scrive normale, la correzione sta qui.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgres://"):  # forma vecchia, la usano certi pannelli
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

if DATABASE_URL.startswith("sqlite"):
    db_path = DATABASE_URL.split("///", 1)[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns() -> None:
    """Aggiunge alle tabelle esistenti le colonne comparse nelle versioni successive.

    create_all crea le tabelle nuove ma non tocca quelle già presenti: senza questo,
    avviare una versione nuova sopra un database vecchio esplode alla prima query.
    Niente sistema di migrazioni: un ADD COLUMN col valore predefinito basta, e le
    colonne rimaste orfane (es. il vecchio token del connettore) restano lì innocue.
    """
    from sqlalchemy import inspect, text

    log = logging.getLogger("catasync.db")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"{col.name} {col.type.compile(engine.dialect)}"
                default = getattr(col.default, "arg", None)
                if default is not None and not callable(default):
                    if isinstance(default, bool):
                        # TRUE/FALSE vale su Postgres e su SQLite; 1/0 su Postgres no.
                        ddl += f" DEFAULT {'TRUE' if default else 'FALSE'}"
                    elif isinstance(default, (int, float)):
                        ddl += f" DEFAULT {default}"
                    elif isinstance(default, str):
                        ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                try:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                    log.info("Aggiunta colonna %s.%s", table.name, col.name)
                except Exception:
                    log.warning("Colonna %s.%s non aggiunta automaticamente", table.name, col.name)


# Colonne rinominate o tolte rispetto alle versioni precedenti. Le rinomine
# conservano i dati (lingua dei clienti, corrispondenze di categoria); le
# colonne tolte erano le credenziali di un archivio esterno non più usato.
RINOMINE = [
    ("clients", "icecat_lang", "content_lang"),
    ("job_items", "icecat_id", "source_ref"),
    ("category_map", "icecat_category", "source_category"),
    ("category_map", "icecat_label", "source_label"),
]
DA_TOGLIERE = [
    ("clients", "icecat_username_enc"),
    ("clients", "icecat_api_token_enc"),
    ("clients", "icecat_content_token_enc"),
]
CHIAVI_DISMESSE = ("partner_mode", "partner_username", "partner_api_token", "partner_content_token")
FONTE_DISMESSA = "icecat"   # l'archivio esterno tolto dal portale: qui solo per ripulire i dati vecchi
PULIZIA_FATTA = "pulizia_fonti_v2"


def _esegui(sql: str, parametri: dict | None = None) -> bool:
    """Un'istruzione per transazione: su Postgres un errore annulla tutta la
    transazione, e una rinomina fallita non deve portarsi dietro le altre."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(sql), parametri or {})
        return True
    except Exception as exc:  # noqa: BLE001 - si registra e si va avanti
        logging.getLogger("catasync.db").warning("Migrazione: «%s» non eseguita (%s)", sql[:80], exc)
        return False


def migra_schema() -> None:
    """Porta un database delle versioni precedenti alla forma attuale.

    Va chiamata DOPO create_all e PRIMA di ensure_columns: se ensure_columns
    aggiungesse prima le colonne nuove, la rinomina di quelle vecchie fallirebbe."""
    from sqlalchemy import inspect, text

    log = logging.getLogger("catasync.db")
    inspector = inspect(engine)
    tabelle = set(inspector.get_table_names())

    def colonne(tabella: str) -> set[str]:
        return {c["name"] for c in inspect(engine).get_columns(tabella)} if tabella in tabelle else set()

    for tabella, vecchia, nuova in RINOMINE:
        cols = colonne(tabella)
        if vecchia not in cols:
            continue
        if nuova not in cols:
            if _esegui(f"ALTER TABLE {tabella} RENAME COLUMN {vecchia} TO {nuova}"):
                log.info("Colonna %s.%s rinominata in %s", tabella, vecchia, nuova)
        else:
            # Entrambe presenti (aggiornamento a metà): si travasano i dati e si toglie la vecchia.
            _esegui(f"UPDATE {tabella} SET {nuova} = {vecchia} WHERE {nuova} IS NULL OR {nuova} = ''")
            _esegui(f"ALTER TABLE {tabella} DROP COLUMN {vecchia}")

    for tabella, colonna in DA_TOGLIERE:
        if colonna in colonne(tabella):
            if _esegui(f"ALTER TABLE {tabella} DROP COLUMN {colonna}"):
                log.info("Colonna %s.%s eliminata", tabella, colonna)

    if "settings" not in tabelle:
        return
    with engine.connect() as conn:
        fatta = conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": PULIZIA_FATTA}).scalar()
    if fatta == "1":
        return

    # Fonti, impostazioni e cache dell'archivio esterno dismesso.
    if "providers" in tabelle:
        _esegui("DELETE FROM providers WHERE kind = :k", {"k": FONTE_DISMESSA})
    for chiave in CHIAVI_DISMESSE:
        _esegui("DELETE FROM settings WHERE key = :k", {"k": chiave})
    if "ean_cache" in tabelle:
        _esegui("DELETE FROM ean_cache WHERE lang NOT LIKE :p ESCAPE '!'", {"p": "!_WEB%"})

    # Etichette e messaggi vecchi sulle righe: via ogni riferimento all'archivio.
    if "job_items" in tabelle:
        _pulisci_righe()

    _esegui("INSERT INTO settings (key, value) VALUES (:k, '1')", {"k": PULIZIA_FATTA})
    log.info("Pulizia delle fonti dismesse completata")


def _pulisci_righe() -> None:
    import json
    import re

    from sqlalchemy import text

    parola = FONTE_DISMESSA
    with engine.connect() as conn:
        righe = conn.execute(text(
            "SELECT id, source, error, normalized FROM job_items "
            "WHERE lower(source) LIKE :p OR lower(error) LIKE :p OR lower(normalized) LIKE :p"
        ), {"p": f"%{parola}%"}).all()
    for rid, source, error, normalized in righe:
        fonti = [f for f in re.split(r"\s*\+\s*", source or "") if f and parola not in f.lower()]
        nuova_fonte = " + ".join(fonti)
        pezzi = [x for x in re.split(r"\s*·\s*|;\s*", error or "") if x and parola not in x.lower()]
        nuovo_errore = " · ".join(pezzi)
        nuovo_norm = normalized or ""
        try:
            dati = json.loads(normalized or "{}")
            if isinstance(dati, dict):
                vecchio_id = dati.pop(parola + "_id", None)
                if vecchio_id is not None and not dati.get("source_ref"):
                    dati["source_ref"] = vecchio_id
                dati["warnings"] = [w for w in (dati.get("warnings") or []) if parola not in str(w).lower()]
                cat = dati.get("category") or {}
                if isinstance(cat, dict) and parola in str(cat.get("name") or "").lower():
                    cat["name"] = ""
                nuovo_norm = json.dumps(dati, ensure_ascii=False)
        except ValueError:
            pass
        _esegui("UPDATE job_items SET source = :s, error = :e, normalized = :n WHERE id = :i",
                {"s": nuova_fonte[:60], "e": nuovo_errore[:900], "n": nuovo_norm, "i": rid})


LINGUA_SITI_FATTA = "lingua_siti_v2"


def migra_dati() -> None:
    """Sistemazioni dei dati da fare DOPO ensure_columns (servono le colonne nuove).

    Prima della v2 un negozio lavorava solo nella lingua del suo cliente
    (italiano, in pratica). Ora la lingua delle schede segue di partenza quella
    dell'interfaccia: perché nei negozi esistenti non cambi niente, la loro lingua
    si fissa sul negozio, una volta sola. Si toglie da Siti → Modifica se si
    vuole scegliere carico per carico."""
    from sqlalchemy import inspect, text

    tabelle = set(inspect(engine).get_table_names())
    if not {"settings", "sites", "clients"} <= tabelle:
        return
    with engine.connect() as conn:
        fatta = conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": LINGUA_SITI_FATTA}).scalar()
    if fatta == "1":
        return
    _esegui(
        "UPDATE sites SET content_lang = COALESCE("
        "(SELECT NULLIF(upper(c.content_lang), '') FROM clients c WHERE c.id = sites.client_id), 'IT') "
        "WHERE content_lang IS NULL OR content_lang = ''"
    )
    _esegui("INSERT INTO settings (key, value) VALUES (:k, '1')", {"k": LINGUA_SITI_FATTA})

