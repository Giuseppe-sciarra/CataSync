"""Worker della coda. Gira dentro lo stesso processo: nessun Redis, nessun Celery."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from . import billing, config, providers, quality, scheduler, wc_rest
from .db import SessionLocal
from .models import CategoryMap, Job, JobItem, Site

log = logging.getLogger("catasync.worker")

# Attese fra un rinvio e l'altro quando i motori ci bloccano: le sospensioni
# di SearXNG durano da qualche minuto a un'ora, Google a volte di più.
RINVII_MINUTI = (10, 30, 90, 240)

MAX_ATTEMPTS = 3


# Gli attributi globali del sito cambiano di rado: si rileggono ogni cinque minuti,
# non a ogni prodotto, altrimenti una coda da 500 EAN fa 500 chiamate in più.
_attr_cache: dict[int, tuple[float, list]] = {}


async def _site_attributes(site) -> list:
    import time

    cached = _attr_cache.get(site.id)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        data = await wc_rest.taxonomy(site)
        attributes = data.get("attributes", [])
    except wc_rest.SiteError:
        attributes = []
    _attr_cache[site.id] = (time.monotonic(), attributes)
    return attributes


def _empty_sheet(gtin: str) -> dict:
    """Scheda vuota da compilare a mano quando nessuna fonte conosce il codice.

    Se il codice cercato è un codice produttore e non un EAN, va nel campo
    «codice produttore» (diventerà lo SKU), non nel campo EAN."""
    from . import codici
    e_ean = codici.e_ean(gtin)
    return {
        "gtin": gtin if e_ean else "", "source_ref": "", "title": "", "brand": "",
        "mpn": "" if e_ean else gtin,
        "category": {"id": "", "name": ""}, "description": "", "short_description": "",
        "bullets": [], "images": [], "attributes": [], "documents": [],
        "regular_price": "", "sale_price": "", "warnings": [],
    }


def _store_product(
    db: Session, item: JobItem, site: Site, normalized: dict, source: str, job: Job | None = None
) -> None:
    """Salva la scheda e decide se è pubblicabile o se manca qualcosa."""
    job_kind = job.kind if job else "manual"
    # I prezzi stanno solo sulla riga, scritti nel portale. La fonte non li tocca:
    # se una scheda viene riscaricata, il prezzo già impostato resta dov'è.
    normalized["regular_price"] = item.regular_price
    normalized["sale_price"] = item.sale_price

    item.normalized = json.dumps(normalized, ensure_ascii=False)
    item.title = (normalized.get("title") or "")[:255]
    item.brand = (normalized.get("brand") or "")[:120]
    item.source_ref = (normalized.get("source_ref") or "")[:32]
    item.source = (source or "")[:60]

    # La categoria si risolve subito, non al push: se c'è una corrispondenza
    # salvata (o la categoria di riserva del job) la riga se la trova già
    # scelta e in revisione il cliente non deve fare niente.
    if not item.wc_category_id:
        risolta = _resolve_category(db, site.id, normalized, job.fallback_category_id if job else None)
        if risolta:
            item.wc_category_id = risolta[0]
    normalized["category_id"] = item.wc_category_id

    missing = quality.check(normalized, site, scheduler.policy_for(site, job_kind))
    item.issues = json.dumps(missing)

    notes = list(normalized.get("warnings") or [])
    sale_issue = quality.sale_price_issue(normalized)
    if sale_issue:
        notes.append(sale_issue)

    if missing:
        item.status = "incomplete"
        notes.insert(0, "Da completare: " + quality.labels(missing))
    else:
        item.status = "ready"

    item.error = "; ".join(notes)[:900]
    db.commit()


def _resolve_category(db: Session, site_id: int, normalized: dict, fallback: int | None) -> list[int]:
    cat = normalized.get("category") or {}
    keys = [str(cat.get("id") or "").strip(), (cat.get("name") or "").strip().lower()]
    for key in keys:
        if not key:
            continue
        row = db.scalar(
            select(CategoryMap).where(
                CategoryMap.site_id == site_id, CategoryMap.source_category == key
            )
        )
        if row:
            return [row.wc_term_id]
    return [fallback] if fallback else []


async def _delete_item(db: Session, item: JobItem, site: Site) -> None:
    """Toglie dal sito un prodotto creato da questa importazione.

    Si cancella solo quello che il portale ha CREATO: se la riga aveva soltanto
    aggiornato una scheda già presente, il prodotto era del cliente prima che
    arrivassimo noi e non si tocca.
    """
    if not item.wc_product_id or item.wc_action != "created":
        item.status = "deleted"
        item.error = "Non creato da questa importazione: lasciato sul sito."
        db.commit()
        return

    immagini: list[int] = []
    try:
        prodotto = await wc_rest.get_product(site, item.wc_product_id)
        immagini = [img["id"] for img in (prodotto.get("images") or []) if img.get("id")]
    except wc_rest.SiteError:
        pass  # se non si legge più, si prova comunque a cancellarlo

    try:
        await wc_rest.delete_product(site, item.wc_product_id)
    except wc_rest.SiteError as exc:
        item.attempts += 1
        item.error = str(exc)
        item.status = "error" if item.attempts >= MAX_ATTEMPTS else "to_delete"
        db.commit()
        return

    note = ["Prodotto eliminato dal sito."]
    if immagini:
        quante, problema = await wc_rest.delete_media(site, immagini)
        if quante:
            note.append(f"{quante} immagini eliminate.")
        if problema:
            note.append(f"Immagini rimaste in libreria: {problema}.")

    item.status = "deleted"
    item.wc_product_id = None
    item.error = " ".join(note)
    db.commit()


def _salva_diagnosi(item: JobItem, diagnosi: dict | None) -> None:
    if diagnosi:
        try:
            item.diagnosi = json.dumps(diagnosi, ensure_ascii=False)[:60_000]
        except (TypeError, ValueError):
            pass


async def _scarica(db: Session, item: JobItem, job: Job, site: Site) -> None:
    """Fase 1: cerca la scheda nelle fonti del cliente."""
    client = site.client
    item.status = "fetching"
    db.commit()
    try:
        normalized, source = await providers.fetch(db, client, item.gtin, job.lang)
    except providers.RicercaRimandata as exc:
        # I motori ci stanno bloccando: il codice non è stato cercato davvero.
        # Si rimette in coda più tardi, e solo dopo qualche rinvio si arrende.
        _salva_diagnosi(item, exc.diagnosi)
        item.rinvii = (item.rinvii or 0) + 1
        if item.rinvii > config.RINVII_MASSIMI:
            item.status = "not_found"
            item.error = ("Motori di ricerca bloccati per troppo tempo: riprova più tardi con «Riprova». "
                          + str(exc))[:600]
            item.normalized = item.normalized or json.dumps(_empty_sheet(item.gtin), ensure_ascii=False)
            item.retry_after = None
        else:
            minuti = RINVII_MINUTI[min(item.rinvii, len(RINVII_MINUTI)) - 1]
            item.retry_after = datetime.now(timezone.utc) + timedelta(minutes=minuti)
            item.status = "pending"
            item.error = (f"Motori di ricerca momentaneamente bloccati: riprovo da solo fra {minuti} minuti. "
                          + str(exc))[:600]
        db.commit()
        return
    except providers.NoSourceFound as exc:
        # Nessuna fonte lo conosce: non è un errore, è una scheda da riempire a mano.
        _salva_diagnosi(item, exc.diagnosi)
        item.status = "not_found"
        item.source = ""
        item.error = str(exc)[:600]
        item.retry_after = None
        item.normalized = json.dumps(_empty_sheet(item.gtin), ensure_ascii=False)
        db.commit()
        return
    except providers.FonteErrore as exc:
        _salva_diagnosi(item, exc.diagnosi)
        item.attempts += 1
        item.error = str(exc)[:900]
        item.status = "error" if item.attempts >= MAX_ATTEMPTS else "pending"
        db.commit()
        return

    _salva_diagnosi(item, normalized.pop("_diagnosi", None))
    normalized.pop("_esiti", None)
    item.retry_after = None
    item.rinvii = 0
    _store_product(db, item, site, normalized, source, job)


async def _pubblica(db: Session, item: JobItem, job: Job, site: Site) -> None:
    """Fase 2: manda la scheda pronta sul sito WooCommerce."""
    client = site.client
    # Il blocco vero: si controlla riga per riga, non solo all'avvio della coda.
    entitlement = billing.check(db, client)
    if not entitlement.allowed:
        item.status = "blocked"
        item.error = entitlement.reason
        db.commit()
        return
    if item.status != "ready":
        return

    item.status = "pushing"
    db.commit()
    normalized = json.loads(item.normalized or "{}")
    normalized["regular_price"] = item.regular_price
    normalized["sale_price"] = item.sale_price
    normalized["source"] = item.source
    options = {
        "write_policy": scheduler.policy_for(site, job.kind),
        "status": job.product_status or site.default_status,
        "categories": (
            [item.wc_category_id]
            if item.wc_category_id
            else _resolve_category(db, site.id, normalized, job.fallback_category_id)
        ),
        "site_attributes": await _site_attributes(site),
        "specs_meta": site.specs_meta,
        "specs_limit": site.specs_limit,
        "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        result = await wc_rest.push_product(site, normalized, options)
    except wc_rest.SiteError as exc:
        item.attempts += 1
        item.error = str(exc)
        item.status = "error" if item.attempts >= MAX_ATTEMPTS else "ready"
        db.commit()
        return

    item.wc_product_id = result.get("product_id")
    item.wc_action = result.get("action", "")
    item.status = "pushed"
    item.error = "; ".join(result.get("warnings") or [])
    db.commit()


async def _process_item(db: Session, item: JobItem, job: Job, site: Site) -> None:
    # --- 1. scarico dalle fonti configurate -----------------------------
    if item.status == "pending":
        await _scarica(db, item, job, site)

    if not job.auto_push:
        return  # si ferma in anteprima, il push lo lancia il cliente

    # --- 2. push sul sito ------------------------------------------------
    if item.status == "ready":
        await _pubblica(db, item, job, site)


async def pubblica_riga(db: Session, item: JobItem, job: Job, site: Site) -> None:
    """Il pulsante «Pubblica» sulla riga: manda QUESTA scheda adesso.

    Prima passava da _process_item, che sui carichi normali (senza
    pubblicazione automatica) si fermava subito: la riga restava «pronta» e
    il pulsante sembrava non fare niente."""
    await _pubblica(db, item, job, site)


def _finalize(db: Session, job: Job) -> None:
    pending = db.scalar(
        select(JobItem).where(
            JobItem.job_id == job.id,
            JobItem.status.in_(
                ["pending", "fetching", "pushing", "to_delete"]
                + (["ready"] if job.auto_push else [])
            ),
        )
    )
    if pending:
        return

    if job.status == "purging":
        # La pulizia è finita: sparisce anche l'importazione, che è quello che
        # si intende premendo "elimina anche i prodotti dal sito".
        rimasti = db.scalar(
            select(func.count(JobItem.id)).where(JobItem.job_id == job.id, JobItem.status == "error")
        )
        if not rimasti:
            log.info("Importazione %s svuotata ed eliminata", job.id)
            db.delete(job)
            db.commit()
            return
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    job.status = "done"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


async def _tick() -> None:
    db = SessionLocal()
    try:
        statuses = ["pending"]
        jobs = db.scalars(
            select(Job).where(Job.status.in_(["queued", "running", "purging"])).order_by(Job.id)
        ).all()
        for job in jobs:
            site = db.get(Site, job.site_id)
            if not site or not site.is_active or not site.client.is_active:
                job.status = "paused"
                db.commit()
                continue

            wanted = statuses + (["ready"] if job.auto_push else []) + ["to_delete"]
            adesso = datetime.now(timezone.utc)
            items = db.scalars(
                select(JobItem)
                .where(JobItem.job_id == job.id, JobItem.status.in_(wanted),
                       or_(JobItem.retry_after.is_(None), JobItem.retry_after <= adesso))
                .order_by(JobItem.id)
                .limit(config.WORKER_BATCH)
            ).all()

            if not items:
                # Righe rimandate (motori bloccati): il carico non è finito, aspetta.
                in_attesa = db.scalar(
                    select(func.count(JobItem.id)).where(
                        JobItem.job_id == job.id, JobItem.status == "pending", JobItem.retry_after.is_not(None))
                )
                if not in_attesa:
                    _finalize(db, job)
                continue

            if job.status not in ("running", "purging"):
                job.status = "running"
                db.commit()

            for item in items:
                try:
                    if item.status == "to_delete":
                        await _delete_item(db, item, site)
                    else:
                        await _process_item(db, item, job, site)
                except Exception as exc:  # non far morire il worker per un singolo EAN
                    log.exception("Errore su EAN %s", item.gtin)
                    item.attempts += 1
                    item.error = f"Errore interno: {exc}"
                    item.status = "error" if item.attempts >= MAX_ATTEMPTS else item.status
                    db.commit()
    finally:
        db.close()


async def run_forever() -> None:
    log.info("Worker avviato (batch=%s, poll=%ss)", config.WORKER_BATCH, config.WORKER_POLL_SECONDS)
    while True:
        try:
            await _tick()
        except Exception:
            log.exception("Ciclo worker fallito")
        await asyncio.sleep(config.WORKER_POLL_SECONDS)


async def run_scheduler_forever() -> None:
    """Controlla ogni minuto se qualche sito è arrivato alla sua ora."""
    log.info("Pianificatore notturno avviato (fuso %s)", config.TIMEZONE)
    while True:
        try:
            await scheduler.tick()
        except Exception:
            log.exception("Ciclo pianificatore fallito")
        await asyncio.sleep(config.SCHEDULER_POLL_SECONDS)


def requeue_ready(db: Session, job_id: int) -> int:
    """Lancia il push per un job fermo in anteprima."""
    job = db.get(Job, job_id)
    if not job:
        return 0
    job.auto_push = True
    job.status = "queued"
    db.execute(
        update(JobItem)
        .where(JobItem.job_id == job_id, JobItem.status.in_(["error", "blocked"]))
        .values(status="ready", attempts=0)
    )
    db.commit()
    return db.scalar(
        select(JobItem).where(JobItem.job_id == job_id, JobItem.status == "ready").limit(1)
    ) is not None
