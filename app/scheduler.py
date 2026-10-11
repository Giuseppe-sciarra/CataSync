"""Ri-sincronizzazione notturna.

Ogni sito ha il suo orario. A quell'ora il portale chiede al sito quali prodotti ha
con un EAN valorizzato, prende i più stantii e li ricerca di nuovo applicando le
politiche di ri-sincronizzazione, che sono diverse da quelle dell'importazione manuale:
di norma si aggiornano descrizioni e specifiche e non si tocca il nome del prodotto,
perché rinominare a notte fonda le schede che il cliente ha sistemato a mano è il modo
più rapido per farsi telefonare la mattina dopo.

Il pianificatore non tiene stato in memoria: guarda `last_resync_at` sul sito e
l'orologio. Se il container si riavvia a metà notte, al giro dopo riprende senza
saltare né raddoppiare.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing, config, lingue, wc_rest
from .db import SessionLocal
from .models import Job, JobItem, Site

log = logging.getLogger("catasync.resync")

# Cosa fa un'importazione manuale se il sito non ha impostazioni sue.
DEFAULT_WRITE_POLICY = {
    "title": "fill",
    "description": "fill",
    "short_description": "fill",
    "attributes": "overwrite",
    "images": "fill",
    "brand": "fill",
    "categories": "fill",
    "gtin": "overwrite",
    "prices": "overwrite",       # il prezzo lo scrive il cliente nel portale, e va sul sito
}

# Cosa fa il giro notturno per impostazione predefinita.
DEFAULT_RESYNC_POLICY = {
    "title": "skip",             # il nome non si tocca: spesso è personalizzato
    "description": "overwrite",
    "short_description": "overwrite",
    "attributes": "overwrite",
    "images": "fill",            # solo se mancano: non si rimescola la galleria
    "brand": "fill",
    "categories": "skip",        # le categorie le decide il cliente
    "gtin": "skip",
    "prices": "skip",            # i prezzi sono del cliente: la notte non si toccano mai
}


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def policy_for(site: Site, kind: str) -> dict:
    """Politiche effettive, coi valori predefiniti sotto a quelli impostati sul sito."""
    if kind != "resync":
        return {**DEFAULT_WRITE_POLICY, **json.loads(site.write_policy or "{}")}
    return {**DEFAULT_RESYNC_POLICY, **json.loads(site.resync_policy or "{}")}


def is_due(site: Site, now: datetime | None = None) -> bool:
    """Vero se per questo sito è ora e oggi non è ancora girato."""
    if not site.resync_enabled or not site.is_active:
        return False

    now = (now or datetime.now(timezone.utc)).astimezone(tz())

    if site.resync_days:
        # 0 = lunedì, come weekday() di Python
        if str(now.weekday()) not in site.resync_days:
            return False

    scheduled = now.replace(
        hour=max(0, min(23, site.resync_hour)),
        minute=max(0, min(59, site.resync_minute)),
        second=0,
        microsecond=0,
    )
    if now < scheduled:
        return False

    # Finestra di recupero: se il portale era spento all'orario esatto, parte
    # comunque entro le due ore successive invece di saltare la notte.
    if now - scheduled > timedelta(hours=2):
        return False

    last = site.last_resync_at
    if last:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last.astimezone(tz()).date() == now.date():
            return False

    return True


async def candidates(db: Session, site: Site, limit: int) -> list[str]:
    """Quali EAN ripassare stanotte.

    Per i prodotti che il portale ha già pubblicato non serve chiedere niente al sito:
    la data dell'ultimo passaggio è nei dati del portale. Si interroga WooCommerce solo
    in modalità "tutti", per pescare i prodotti con EAN che il portale non ha mai
    toccato — quelli arrivati dal gestionale o da import precedenti."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, site.resync_min_age_days))

    rows = db.execute(
        select(JobItem.gtin, func.max(JobItem.updated_at))
        .join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status == "pushed")
        .group_by(JobItem.gtin)
        .order_by(func.max(JobItem.updated_at).asc())
    ).all()

    known: set[str] = set()
    gtins: list[str] = []
    for gtin, last in rows:
        known.add(gtin)
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or last < cutoff:
            gtins.append(gtin)

    if len(gtins) >= limit or (site.resync_scope or "hub") == "hub":
        return gtins[:limit]

    # Modalità "tutti": si sfoglia il catalogo per trovare gli EAN che il portale non conosce.
    for row in await wc_rest.scan_catalog(site, max_pages=config.WC_MAX_SCAN_PAGES):
        gtin = row["gtin"]
        if gtin in known:
            continue
        known.add(gtin)
        gtins.append(gtin)
        if len(gtins) >= limit:
            break

    return gtins[:limit]


async def build_job(db: Session, site: Site) -> tuple[Job | None, str]:
    """Interroga il sito, crea il job di aggiornamento. Non lo esegue: ci pensa il worker."""
    entitlement = billing.check(db, site.client)
    if not entitlement.allowed:
        return None, f"Saltato: {entitlement.reason}"

    limit = max(1, min(2000, site.resync_batch))
    try:
        gtins = await candidates(db, site, limit)
    except wc_rest.SiteError as exc:
        return None, f"Sito non interrogabile: {exc}"

    if not gtins:
        return None, "Nessun prodotto da aggiornare."

    now = datetime.now(tz())
    job = Job(
        site_id=site.id,
        created_by_id=None,
        label=f"Aggiornamento notturno {now.strftime('%d/%m/%Y')}",
        lang=lingue.normalizza(site.content_lang or site.client.content_lang or config.DEFAULT_LANG),
        kind="resync",
        product_status=site.default_status,
        auto_push=True,
        total=len(gtins),
        status="queued",
    )
    db.add(job)
    db.flush()
    db.add_all([JobItem(job_id=job.id, gtin=g) for g in gtins])
    db.commit()
    return job, f"{len(gtins)} prodotti messi in coda."


async def tick() -> None:
    db = SessionLocal()
    try:
        sites = db.scalars(select(Site).where(Site.resync_enabled.is_(True))).all()
        for site in sites:
            if not is_due(site):
                continue
            log.info("Aggiornamento notturno per %s", site.base_url)
            job, message = await build_job(db, site)
            site.last_resync_at = datetime.now(timezone.utc)
            site.last_resync_msg = message
            db.commit()
            log.info("%s: %s", site.base_url, message)
    except Exception:
        log.exception("Ciclo di ri-sincronizzazione fallito")
    finally:
        db.close()


async def run_now(db: Session, site: Site) -> tuple[Job | None, str]:
    """Avvio manuale dal pannello, senza aspettare la notte."""
    job, message = await build_job(db, site)
    site.last_resync_at = datetime.now(timezone.utc)
    site.last_resync_msg = message
    db.commit()
    return job, message
