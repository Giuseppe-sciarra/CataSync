"""Area cliente: carica EAN, controlla l'anteprima, pubblica sul proprio sito."""
import csv
import io
import json

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import billing, codici, config, lingue, providers, quality, scheduler, wc_rest
from ..html_sicuro import scheda_sicura, svg_attivo
from ..db import get_db
from ..models import CategoryMap, Client, Job, JobItem, Site, User
from ..security import require_user
from ..store import audit
from ..worker import pubblica_riga, requeue_ready

router = APIRouter(prefix="/api", tags=["client"])

MAX_EAN_PER_JOB = 5000
# Il mu-plugin di WordPress per il menu delle categorie (cartella wordpress/ del progetto).
MU_PLUGIN = Path(__file__).resolve().parents[2] / "wordpress" / "catasync-category-menu.php"


def _visible_site(db: Session, site_id: int, user: User) -> Site:
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    if not user.is_admin and site.client_id != user.client_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Questo sito non è collegato al tuo account.")
    return site


def _visible_job(db: Session, job_id: int, user: User) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Importazione non trovata.")
    _visible_site(db, job.site_id, user)
    return job


# --------------------------------------------------------------------- schemi
class JobIn(BaseModel):
    site_id: int
    eans: str = ""
    label: str = ""
    lang: str = "IT"
    product_status: str = "publish"
    fallback_category_id: int | None = None
    auto_push: bool = False


class MapIn(BaseModel):
    source_category: str = Field(min_length=1)
    source_label: str = ""
    wc_term_id: int
    wc_label: str = ""


# --------------------------------------------------------------------- siti
@router.get("/sites")
def my_sites(db: Session = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(Site).where(Site.is_active.is_(True))
    if not user.is_admin:
        stmt = stmt.where(Site.client_id == user.client_id)
    return [
        {
            "id": s.id,
            "name": s.name,
            "base_url": s.base_url,
            "default_status": s.default_status,
            "last_ping_ok": s.last_ping_ok,
            "last_ping_msg": s.last_ping_msg,
            "resync_enabled": s.resync_enabled,
            "resync_hour": s.resync_hour,
            "resync_minute": s.resync_minute,
            "resync_days": s.resync_days,
            "last_resync_at": s.last_resync_at.isoformat() if s.last_resync_at else None,
            "last_resync_msg": s.last_resync_msg,
            # Lingua delle schede fissata per questo negozio ('' = quella scelta al caricamento).
            "content_lang": s.content_lang or "",
        }
        for s in db.scalars(stmt.order_by(Site.name)).all()
    ]


@router.get("/sites/{site_id}/taxonomy")
async def site_taxonomy(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Categorie, attributi e brand già presenti sul sito: si sceglie da lì, non si inventa."""
    site = _visible_site(db, site_id, user)
    try:
        data = await wc_rest.taxonomy(site)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    rows = db.scalars(select(CategoryMap).where(CategoryMap.site_id == site_id)).all()
    data["mapping"] = [
        {
            "id": r.id,
            "source_category": r.source_category,
            "source_label": r.source_label,
            "wc_term_id": r.wc_term_id,
            "wc_label": r.wc_label,
        }
        for r in rows
    ]
    return data


@router.post("/sites/{site_id}/mapping", status_code=201)
def upsert_mapping(
    site_id: int, data: MapIn, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    _visible_site(db, site_id, user)
    key = data.source_category.strip().lower()
    row = db.scalar(
        select(CategoryMap).where(CategoryMap.site_id == site_id, CategoryMap.source_category == key)
    )
    if row:
        row.wc_term_id = data.wc_term_id
        row.wc_label = data.wc_label
        row.source_label = data.source_label
    else:
        row = CategoryMap(
            site_id=site_id,
            source_category=key,
            source_label=data.source_label,
            wc_term_id=data.wc_term_id,
            wc_label=data.wc_label,
        )
        db.add(row)
    db.commit()
    return {"ok": True, "id": row.id}


@router.delete("/sites/{site_id}/mapping/{map_id}")
def delete_mapping(site_id: int, map_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    _visible_site(db, site_id, user)
    db.execute(delete(CategoryMap).where(CategoryMap.id == map_id, CategoryMap.site_id == site_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------- job
def _parse_eans(raw: str) -> tuple[list[str], list[str]]:
    """EAN o codici produttore, nello stesso riquadro: li distingue codici.py."""
    return codici.parse_codici(raw)


def _job_json(db: Session, job: Job) -> dict:
    counts = dict(
        db.execute(
            select(JobItem.status, func.count(JobItem.id))
            .where(JobItem.job_id == job.id)
            .group_by(JobItem.status)
        ).all()
    )
    site = db.get(Site, job.site_id)
    return {
        "id": job.id,
        "label": job.label,
        "site_id": job.site_id,
        "site_name": site.name if site else "",
        "kind": job.kind,
        "status": job.status,
        "lang": job.lang,
        "auto_push": job.auto_push,
        "product_status": job.product_status,
        "total": job.total,
        "counts": counts,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/jobs", status_code=201)
def create_job(data: JobIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    site = _visible_site(db, data.site_id, user)
    fonti_attive = [p for p in providers.chain(db, site.client) if p.kind != "manual"]
    if not fonti_attive:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nessuna fonte dati attiva per questo account: attiva la ricerca sul web o un connettore.",
        )

    if data.auto_push:
        entitlement = billing.check(db, site.client)
        if not entitlement.allowed:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, entitlement.reason)

    eans, invalid = _parse_eans(data.eans)
    if not eans:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nessun codice valido nell'elenco: servono EAN o codici produttore.")
    if len(eans) > MAX_EAN_PER_JOB:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Massimo {MAX_EAN_PER_JOB} EAN per importazione."
        )

    job = Job(
        site_id=site.id,
        created_by_id=user.id,
        label=data.label or f"{len(eans)} EAN",
        # La lingua fissata sul negozio vince; altrimenti quella scelta da chi carica.
        lang=lingue.normalizza(site.content_lang or data.lang or site.client.content_lang),
        product_status=data.product_status,
        fallback_category_id=data.fallback_category_id,
        auto_push=data.auto_push,
        total=len(eans),
        status="queued",
    )
    db.add(job)
    db.flush()
    db.add_all([JobItem(job_id=job.id, gtin=g) for g in eans])
    db.commit()
    audit(db, user.email, "job_create", f"{site.base_url} · {len(eans)} EAN")

    # Quanti di questi codici stanno già sul sito? Si dice subito, così un
    # ricaricamento non sembra un errore: quelli verranno aggiornati, non duplicati.
    gia = db.scalar(
        select(func.count(func.distinct(JobItem.gtin)))
        .select_from(JobItem).join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status == "pushed",
               JobItem.gtin.in_(eans), JobItem.job_id != job.id)
    ) or 0
    return {**_job_json(db, job), "invalid": invalid, "gia_sul_sito": int(gia)}


@router.post("/jobs/upload")
async def upload_csv(
    site_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Accetta un CSV: legge la prima colonna che contiene EAN validi."""
    _visible_site(db, site_id, user)
    blob = await file.read()
    if len(blob) > config.MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File troppo grande: massimo 5 MB.")
    raw = blob.decode("utf-8-sig", errors="ignore")
    # Lo sniffer non sa riconoscere un file a colonna singola — che è il caso più
    # comune: un elenco di soli EAN, uno per riga. In quel caso va bene qualunque
    # dialetto, tanto non c'è niente da separare.
    try:
        dialect = csv.Sniffer().sniff(raw[:2000], delimiters=",;\t") if raw.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    found: list[str] = []
    for row in reader:
        for cell in row:
            esito = codici.classifica(cell)
            gtin = esito[1] if esito and esito[0] != "interno" else ""
            if gtin:
                found.append(gtin)
                break
    return {"eans": found, "count": len(found)}


@router.get("/jobs")
def list_jobs(site_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(Job).order_by(Job.id.desc()).limit(100)
    if site_id:
        _visible_site(db, site_id, user)
        stmt = stmt.where(Job.site_id == site_id)
    elif not user.is_admin:
        stmt = stmt.join(Site, Site.id == Job.site_id).where(Site.client_id == user.client_id)
    return [_job_json(db, j) for j in db.scalars(stmt).all()]


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    job = _visible_job(db, job_id, user)
    return _job_json(db, job)


@router.get("/jobs/{job_id}/items")
def job_items(
    job_id: int,
    status_filter: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    _visible_job(db, job_id, user)
    stmt = select(JobItem).where(JobItem.job_id == job_id)
    if status_filter:
        stmt = stmt.where(JobItem.status == status_filter)
    else:
        # Le righe eliminate restano nel DB per tracciabilità ma non devono
        # continuare a comparire nell'interfaccia come se fossero ancora lì.
        stmt = stmt.where(JobItem.status != "deleted")
    rows = db.scalars(stmt.order_by(JobItem.id).offset(offset).limit(min(limit, 200))).all()
    return [
        {
            "id": r.id,
            "gtin": r.gtin,
            "status": r.status,
            "title": r.title,
            "brand": r.brand,
            "source_ref": r.source_ref,
            "wc_product_id": r.wc_product_id,
            "wc_action": r.wc_action,
            "error": r.error,
            "regular_price": r.regular_price,
            "sale_price": r.sale_price,
            "source": r.source,
            "category_id": r.wc_category_id,
            "issues": json.loads(r.issues or "[]"),
            "edited": r.edited,
            # Si può togliere dal catalogo solo se NON è già stato pubblicato
            # (o agganciato a un prodotto esistente sul sito) e non è in lavorazione.
            "can_delete": (
                not r.wc_product_id
                and r.status not in ("pushed", "pushing", "fetching", "pending", "to_delete", "deleted")
            ),
        }
        for r in rows
    ]


@router.get("/items/{item_id}/preview")
def item_preview(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    _visible_job(db, item.job_id, user)
    site = db.get(Site, db.get(Job, item.job_id).site_id)
    product = json.loads(item.normalized or "{}")
    product["regular_price"] = item.regular_price
    product["sale_price"] = item.sale_price
    # Si mostrano solo le caratteristiche che finiranno davvero sul sito: vederne
    # novanta per poi scriverne sei confonde e basta.
    tutte = product.get("attributes") or []
    product["attributes"] = wc_rest.taglia_specifiche(tutte, site.specs_limit)
    product["attributes_totali"] = len(tutte)
    return {
        "id": item.id,
        "gtin": item.gtin,
        "status": item.status,
        "error": item.error,
        "source": item.source,
        "category_id": item.wc_category_id,
        "issues": json.loads(item.issues or "[]"),
        "required": quality.effective(site, scheduler.policy_for(site, db.get(Job, item.job_id).kind)),
        "field_labels": quality.FIELDS,
        "can_delete": (
            not item.wc_product_id
            and item.status not in ("pushed", "pushing", "fetching", "pending", "to_delete", "deleted")
        ),
        "product": product,
    }


@router.get("/items/{item_id}/diagnosi")
def item_diagnosi(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Il diario completo dell'ultima ricerca di una riga: il «Perché?» in tabella."""
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    _visible_job(db, item.job_id, user)
    try:
        d = json.loads(item.diagnosi or "{}")
    except ValueError:
        d = {}
    if not user.is_admin and isinstance(d, dict):
        # Al cliente servono il perché, le ricerche e i siti: la classifica
        # interna dei candidati è roba da tarature.
        d.pop("candidati", None)
    return {"gtin": item.gtin, "status": item.status, "error": item.error, "diagnosi": d,
            "riprova_alle": item.retry_after.isoformat() if item.retry_after else None}


@router.post("/jobs/{job_id}/publish")
def publish_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Sblocca il push per un'importazione ferma in anteprima."""
    job = _visible_job(db, job_id, user)
    site = db.get(Site, job.site_id)
    entitlement = billing.check(db, site.client)
    if not entitlement.allowed:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, entitlement.reason)
    requeue_ready(db, job.id)
    audit(db, user.email, "job_publish", f"job {job.id}")
    return _job_json(db, job)


def _rimetti_in_coda(row: JobItem) -> None:
    """Riga da ricercare da capo: via tentativi, rinvii e diario vecchio."""
    row.attempts = 0
    row.rinvii = 0
    row.retry_after = None
    row.error = ""
    row.diagnosi = ""
    row.status = "pending"


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Rimette in coda le righe in errore e i 'non trovato', ripartendo da zero.

    Premere «Riprova» vuol dire «cerca di nuovo adesso»: si elimina la cache web
    di quelle righe, anche quando conteneva una scheda parziale. Altrimenti una
    foto/descrizione mancante resterebbe identica a ogni tentativo.
    """
    job = _visible_job(db, job_id, user)
    rows = db.scalars(
        select(JobItem).where(JobItem.job_id == job.id, JobItem.status.in_(["error", "not_found"]))
    ).all()
    providers.svuota_cache_web(db, [r.gtin for r in rows])
    for row in rows:
        _rimetti_in_coda(row)
    job.status = "queued"
    job.finished_at = None
    db.commit()
    return {"requeued": len(rows)}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    job = _visible_job(db, job_id, user)
    db.delete(job)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------- singolo EAN
@router.get("/lookup")
async def lookup_single(
    site_id: int, gtin: str, lang: str = "IT", db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Prova un codice al volo, senza creare un'importazione.

    Usa la STESSA catena delle importazioni (connettori, indirizzi fissi,
    ricerca web) e la stessa lingua delle schede del negozio.
    """
    site = _visible_site(db, site_id, user)
    esito = codici.classifica(gtin)
    if not esito:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codice non valido: un EAN (8, 12, 13 o 14 cifre) o un codice produttore di almeno 4 caratteri.")
    if esito[0] == "interno":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Questo è un EAN-13 a circolazione limitata (prefisso 20-29): di solito è un codice interno "
            "del grossista/punto vendita e non identifica una scheda globale. Usa l'EAN del produttore o il suo codice articolo.",
        )
    clean = esito[1]
    lingua = lingue.normalizza(site.content_lang or lang)
    try:
        product, fonte = await providers.fetch(db, site.client, clean, lingua)
    except providers.RicercaRimandata as exc:
        diag = exc.diagnosi or {}
        return {"found": False, "message": diag.get("spiegazione") or str(exc), "diagnosi": diag}
    except providers.NoSourceFound as exc:
        diag = exc.diagnosi or {}
        return {"found": False, "message": diag.get("spiegazione") or "Nessuna fonte conosce questo codice.",
                "diagnosi": diag}
    except providers.FonteErrore as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    diag = product.pop("_diagnosi", None)
    product.pop("_esiti", None)

    existing = None
    try:
        existing = await wc_rest.lookup(site, clean)
    except wc_rest.SiteError:
        pass
    return {
        "found": True,
        "product": product,
        "fonte": fonte,
        "on_site": existing,
        "lingua": lingua,
        "diagnosi": diag,
    }


# ------------------------------------------------------- anagrafica e logo
PROFILE_FIELDS = (
    "legal_name", "vat_number", "tax_code", "sdi_code", "pec", "address",
    "zip_code", "city", "province", "country", "phone", "contact_email",
    "website", "accent_color",
)


class ProfileIn(BaseModel):
    legal_name: str = ""
    vat_number: str = ""
    tax_code: str = ""
    sdi_code: str = ""
    pec: str = ""
    address: str = ""
    zip_code: str = ""
    city: str = ""
    province: str = ""
    country: str = "Italia"
    phone: str = ""
    contact_email: str = ""
    website: str = ""
    accent_color: str = ""


def _own_client(db: Session, user: User, client_id: int | None = None) -> Client:
    """L'admin può agire su qualunque cliente, il cliente solo sul proprio.

    Se un cliente passa l'id di un altro non gli si restituisce in silenzio il
    proprio: si risponde 403, così il comportamento è quello che sembra."""
    if client_id and not user.is_admin and client_id != user.client_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Non è il tuo account.")
    target = client_id if (user.is_admin and client_id) else user.client_id
    if not target:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nessun cliente collegato a questo account.")
    row = db.get(Client, target)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    if not user.is_admin and row.id != user.client_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Non è il tuo account.")
    return row


@router.get("/profile")
def get_profile(client_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _own_client(db, user, client_id)
    data = {field: getattr(row, field) for field in PROFILE_FIELDS}
    data["name"] = row.name
    data["logo"] = f"/media/logo/{row.id}" if row.logo_path else ""
    data["subscription"] = billing.as_json(billing.check(db, row))
    return data


@router.put("/profile")
def save_profile(
    data: ProfileIn,
    client_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = _own_client(db, user, client_id)
    for field in PROFILE_FIELDS:
        setattr(row, field, (getattr(data, field) or "").strip()[:190])
    db.commit()
    audit(db, user.email, "profile_update", row.name)
    return get_profile(client_id, db, user)


ALLOWED_LOGO_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


@router.post("/profile/logo")
async def upload_logo(
    file: UploadFile = File(...),
    client_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    row = _own_client(db, user, client_id)

    suffix = ALLOWED_LOGO_TYPES.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Formati accettati: PNG, JPG, WebP, SVG.")

    blob = await file.read()
    if len(blob) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Il logo non deve superare i 2 MB.")
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File vuoto.")

    # Un SVG è codice: lo si accetta solo se non contiene script, gestori di
    # eventi o contenuti incorporati.
    if suffix == ".svg" and svg_attivo(blob):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG con contenuto attivo: caricane uno pulito o usa un PNG.")

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Nome derivato dall'id, mai dal nome del file caricato.
    filename = f"logo-{row.id}{suffix}"
    for old in config.UPLOAD_DIR.glob(f"logo-{row.id}.*"):
        old.unlink(missing_ok=True)
    (config.UPLOAD_DIR / filename).write_bytes(blob)

    row.logo_path = filename
    db.commit()
    audit(db, user.email, "logo_update", row.name)
    return {"logo": f"/media/logo/{row.id}"}


@router.delete("/profile/logo")
def delete_logo(client_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _own_client(db, user, client_id)
    for old in config.UPLOAD_DIR.glob(f"logo-{row.id}.*"):
        old.unlink(missing_ok=True)
    row.logo_path = ""
    db.commit()
    return {"ok": True}


@router.get("/subscription")
def subscription(db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not user.client_id:
        return {"allowed": True, "status": "admin", "status_label": "Amministratore",
                "reason": "", "plan": "", "expires_at": None, "quota": 0, "used": 0, "remaining": None}
    row = db.get(Client, user.client_id)
    return billing.as_json(billing.check(db, row))


# ------------------------------------------------------ correzione manuale
class ItemIn(BaseModel):
    """Quello che si può correggere a mano su una riga, prima di mandarla sul sito."""

    title: str = ""
    brand: str = ""
    mpn: str = ""
    description: str = ""
    short_description: str = ""
    category_name: str = ""
    images: list[str] = []
    attributes: list[dict] = []
    regular_price: str = ""
    sale_price: str = ""
    category_id: int | None = None


class PricesIn(BaseModel):
    # Testo incollato: una riga per prodotto, "EAN prezzo [prezzo_scontato]".
    text: str = ""


def _clean_price(raw: str) -> str:
    raw = (raw or "").strip().replace("€", "").replace(" ", "")
    if not raw:
        return ""
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return ""
    return f"{value:.2f}" if value >= 0 else ""


def _revalidate(db: Session, item: JobItem, site: Site, kind: str = "manual") -> dict:
    """Ricalcola cosa manca e aggiorna lo stato della riga."""
    product = json.loads(item.normalized or "{}")
    product["regular_price"] = item.regular_price
    product["sale_price"] = item.sale_price
    product["category_id"] = item.wc_category_id

    missing = quality.check(product, site, scheduler.policy_for(site, kind))
    item.issues = json.dumps(missing)

    notes = []
    if missing:
        notes.append("Da completare: " + quality.labels(missing))
    sale_issue = quality.sale_price_issue(product)
    if sale_issue:
        notes.append(sale_issue)

    if item.status in ("ready", "incomplete", "not_found"):
        item.status = "incomplete" if missing else "ready"
    item.error = "; ".join(notes)[:900]
    db.commit()
    return product


@router.put("/items/{item_id}")
def edit_item(
    item_id: int, data: ItemIn, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Riempie o corregge a mano una scheda: serve soprattutto quando nessuna fonte
    conosce il codice e il prodotto va scritto da zero."""
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    job = _visible_job(db, item.job_id, user)
    site = db.get(Site, job.site_id)

    if item.status in ("pushing", "fetching"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Riga in lavorazione: riprova fra un istante.")

    product = json.loads(item.normalized or "{}") or {"gtin": item.gtin}
    product["title"] = data.title.strip()[:255]
    product["brand"] = data.brand.strip()[:120]
    product["mpn"] = data.mpn.strip()[:80]
    product["description"] = data.description
    product["short_description"] = data.short_description
    product["category"] = {"id": "", "name": data.category_name.strip()[:190]}
    product["images"] = [u.strip() for u in data.images if u.strip().startswith("http")][:12]
    product["attributes"] = [
        {"group": "", "name": str(a.get("name", ""))[:120], "value": str(a.get("value", ""))[:255]}
        for a in data.attributes
        if str(a.get("name", "")).strip() and str(a.get("value", "")).strip()
    ][:80]

    item.regular_price = _clean_price(data.regular_price)
    item.sale_price = _clean_price(data.sale_price)
    item.wc_category_id = data.category_id or None
    scheda_sicura(product)   # chi compila a mano non deve poter iniettare HTML attivo
    item.normalized = json.dumps(product, ensure_ascii=False)
    item.title = product["title"]
    item.brand = product["brand"]
    item.edited = True
    if not item.source:
        item.source = "manuale"

    _revalidate(db, item, site, job.kind)
    audit(db, user.email, "item_edit", f"{item.gtin} · {item.status}")
    return {
        "id": item.id,
        "status": item.status,
        "issues": json.loads(item.issues or "[]"),
        "error": item.error,
    }


@router.post("/jobs/{job_id}/prices")
def bulk_prices(
    job_id: int, data: PricesIn, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Prezzi in blocco. Una riga per prodotto: EAN, prezzo, prezzo scontato.
    Separatori accettati: spazi, virgole, punto e virgola, tabulazioni."""
    job = _visible_job(db, job_id, user)
    site = db.get(Site, job.site_id)

    updated, unknown = 0, []
    for line in (data.text or "").splitlines():
        parts = [p for p in line.replace(";", " ").replace("\t", " ").split() if p]
        if len(parts) < 2:
            continue
        esito = codici.classifica(parts[0])
        gtin = esito[1] if esito else ""
        if not gtin:
            continue
        item = db.scalar(select(JobItem).where(JobItem.job_id == job.id, JobItem.gtin == gtin))
        if not item:
            unknown.append(gtin)
            continue
        item.regular_price = _clean_price(parts[1])
        if len(parts) > 2:
            item.sale_price = _clean_price(parts[2])
        _revalidate(db, item, site, job.kind)
        updated += 1

    return {"updated": updated, "unknown": unknown}


@router.get("/jobs/{job_id}/prices/template")
def prices_template(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Elenco pronto da copiare, incollare in un foglio e riportare indietro coi prezzi."""
    job = _visible_job(db, job_id, user)
    rows = db.scalars(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.id)).all()
    lines = [
        f"{r.gtin} {r.regular_price or ''} {r.sale_price or ''}".rstrip()
        for r in rows
    ]
    return {"text": "\n".join(lines)}


# --------------------------------------------------------------- categorie
# Le categorie stanno sul sito: qui si creano, si eliminano e si mettono in ordine
# (l'ordine di Prodotti → Categorie in WooCommerce, che il mu-plugin del menu segue).

class CategoriaNuova(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    parent: int = Field(default=0, ge=0)   # 0 = categoria principale


class NomeCategoria(BaseModel):
    name: str = Field(min_length=1, max_length=190)


class OrdineCategorie(BaseModel):
    # Gli id nell'ordine voluto, dall'alto in basso: conta l'ordine fra sorelle.
    ids: list[int] = Field(default_factory=list, max_length=5000)


class MenuScelta(BaseModel):
    attivo: bool | None = None
    menu_id: int | None = Field(default=None, ge=0)
    voce_id: int | None = Field(default=None, ge=0)
    sottocategorie: bool | None = None


def _scrittura_sul_sito(site: Site) -> None:
    """Categorie e menu scrivono sul sito: servono account e abbonamento attivi, come per
    pubblicare. La quota dei prodotti qui non conta."""
    motivo = billing.scrittura_bloccata(site.client)
    if motivo:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, motivo)


async def _categorie_del_sito(site: Site) -> list[dict]:
    try:
        return await wc_rest.categorie(site)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.post("/sites/{site_id}/categories", status_code=201)
async def crea_categoria(
    site_id: int, data: CategoriaNuova, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Nuova categoria sul sito (principale o dentro un'altra), in fondo alle sorelle."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    nome = " ".join(data.name.split())
    if not nome:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Scrivi il nome della categoria.")

    esistenti = await _categorie_del_sito(site)
    # La madre dev'essere ancora sul sito, e non «Senza categoria», che non si mostra.
    if data.parent and not any(c.get("id") == data.parent and not wc_rest.e_riserva(c) for c in esistenti):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La categoria madre non c'è più sul sito: rileggi le categorie.")
    sorelle = [c for c in esistenti if (c.get("parent") or 0) == data.parent]
    if any((c.get("name") or "").strip().lower() == nome.lower() for c in sorelle):
        raise HTTPException(status.HTTP_409_CONFLICT, f"C'è già una categoria «{nome}» a questo livello.")
    in_fondo = max(((c.get("menu_order") or 0) for c in sorelle), default=-1) + 1

    try:
        creata = await wc_rest.create_category(site, nome, parent=data.parent, menu_order=in_fondo)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit(db, user.email, "category_create", f"{site.name} · {nome}")
    return {"ok": True, "id": creata.get("id"), "name": creata.get("name", nome)}


@router.patch("/sites/{site_id}/categories/{term_id}")
async def rinomina_categoria(
    site_id: int, term_id: int, data: NomeCategoria, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Nuovo nome della categoria, sul sito e nel portale. L'indirizzo (slug) resta quello di
    prima: i link alla categoria non si rompono."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    nome = " ".join(data.name.split())
    if not nome:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Scrivi il nome della categoria.")

    esistenti = await _categorie_del_sito(site)
    categoria = next((c for c in esistenti if c.get("id") == term_id), None)
    if not categoria:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "La categoria non c'è più sul sito: rileggi le categorie.")
    if wc_rest.e_riserva(categoria):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "«Senza categoria» è quella di riserva di WooCommerce: non si rinomina.")
    prima = categoria.get("name") or ""
    if nome == prima:
        return {"ok": True, "id": term_id, "name": nome, "cambiata": False}
    padre = categoria.get("parent") or 0
    if any(c.get("id") != term_id and (c.get("parent") or 0) == padre
           and (c.get("name") or "").strip().lower() == nome.lower() for c in esistenti):
        raise HTTPException(status.HTTP_409_CONFLICT, f"C'è già una categoria «{nome}» a questo livello.")

    try:
        aggiornata = await wc_rest.update_category(site, term_id, name=nome)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    nuovo = aggiornata.get("name") or nome
    db.execute(
        update(CategoryMap).where(CategoryMap.site_id == site.id, CategoryMap.wc_term_id == term_id).values(wc_label=nuovo)
    )
    db.commit()
    audit(db, user.email, "category_rename", f"{site.name} · {prima} → {nuovo}")
    return {"ok": True, "id": term_id, "name": nuovo, "cambiata": True}


@router.delete("/sites/{site_id}/categories/{term_id}")
async def elimina_categoria(
    site_id: int, term_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Elimina la categoria dal sito. I prodotti restano (WooCommerce mette in «Senza
    categoria» quelli che non ne hanno altre), le sottocategorie salgono di un livello.
    Nel portale si tolgono le corrispondenze verso questa categoria e le righe che la
    usavano tornano da completare."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    try:
        categoria = await wc_rest.get_category(site, term_id)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if wc_rest.e_riserva(categoria):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "«Senza categoria» è quella di riserva di WooCommerce: non si elimina.")
    try:
        await wc_rest.delete_category(site, term_id)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    corrispondenze = db.execute(
        delete(CategoryMap).where(CategoryMap.site_id == site.id, CategoryMap.wc_term_id == term_id)
    ).rowcount
    db.execute(
        update(Job).where(Job.site_id == site.id, Job.fallback_category_id == term_id).values(fallback_category_id=None)
    )
    righe = 0
    for item in db.scalars(
        select(JobItem).join(Job, Job.id == JobItem.job_id).where(Job.site_id == site.id, JobItem.wc_category_id == term_id)
    ).all():
        item.wc_category_id = None
        righe += 1
        if item.status in ("ready", "incomplete", "not_found"):
            _revalidate(db, item, site, db.get(Job, item.job_id).kind)
    db.commit()
    audit(db, user.email, "category_delete", f"{site.name} · {categoria.get('name', term_id)}")
    return {
        "ok": True,
        "name": categoria.get("name", ""),
        "prodotti": categoria.get("count", 0),
        "corrispondenze": corrispondenze,
        "righe": righe,
    }


@router.put("/sites/{site_id}/categories/order")
async def ordina_categorie(
    site_id: int, data: OrdineCategorie, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Salva l'ordine: ogni categoria prende la sua posizione fra le sorelle (0, 1, 2…).
    Le sorelle che non sono nell'elenco vanno in fondo, nell'ordine che avevano."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    esistenti = await _categorie_del_sito(site)
    per_id = {c["id"]: c for c in esistenti}

    posizioni: dict[int, int] = {}
    contatori: dict[int, int] = {}
    for cid in data.ids:
        c = per_id.get(cid)
        if not c or cid in posizioni:
            continue
        padre = c.get("parent") or 0
        posizioni[cid] = contatori.get(padre, 0)
        contatori[padre] = posizioni[cid] + 1
    for padre in list(contatori):
        resto = sorted(
            (c for c in esistenti if (c.get("parent") or 0) == padre and c["id"] not in posizioni),
            key=lambda c: ((c.get("menu_order") or 0), (c.get("name") or "").lower()),
        )
        for c in resto:
            posizioni[c["id"]] = contatori[padre]
            contatori[padre] += 1

    cambi = [(cid, pos) for cid, pos in posizioni.items() if (per_id[cid].get("menu_order") or 0) != pos]
    if cambi:
        try:
            await wc_rest.order_categories(site, cambi)
        except wc_rest.SiteError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit(db, user.email, "category_order", f"{site.name} · {len(cambi)} categorie")
    return {"ok": True, "cambiate": len(cambi)}


# ------------------------------------------------ menu del sito (mu-plugin)
@router.get("/sites/{site_id}/menu")
async def menu_del_sito(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Dove finiscono le categorie nel menu del sito. plugin=false: il mu-plugin non c'è."""
    site = _visible_site(db, site_id, user)
    try:
        stato = await wc_rest.menu_stato(site)
    except wc_rest.PluginMancante:
        return {"plugin": False}
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return {**stato, "plugin": True}


@router.put("/sites/{site_id}/menu")
async def salva_menu_del_sito(
    site_id: int, data: MenuScelta, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    try:
        stato = await wc_rest.menu_salva(site, data.model_dump(exclude_none=True))
    except wc_rest.PluginMancante:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sul sito manca il mu-plugin «CataSync – Menu delle categorie».")
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit(db, user.email, "menu_set", f"{site.name} · menu {data.menu_id} voce {data.voce_id} attivo {data.attivo}")
    return {**stato, "plugin": True}


@router.post("/sites/{site_id}/menu/allinea")
async def allinea_menu_del_sito(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    try:
        stato = await wc_rest.menu_allinea(site)
    except wc_rest.PluginMancante:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sul sito manca il mu-plugin «CataSync – Menu delle categorie».")
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit(db, user.email, "menu_align", site.name)
    return {**stato, "plugin": True}


@router.get("/wordpress/catasync-category-menu.php")
def scarica_mu_plugin(user: User = Depends(require_user)):
    """Il mu-plugin da mettere in wp-content/mu-plugins/ del negozio."""
    return FileResponse(MU_PLUGIN, media_type="application/x-httpd-php", filename=MU_PLUGIN.name)


# ------------------------------------------------- eliminazione con i prodotti
@router.get("/jobs/{job_id}/purge-preview")
def purge_preview(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Cosa succederebbe eliminando anche dal sito, prima di farlo davvero."""
    job = _visible_job(db, job_id, user)
    site = db.get(Site, job.site_id)

    da_eliminare = db.scalar(
        select(func.count(JobItem.id)).where(
            JobItem.job_id == job.id,
            JobItem.status == "pushed",
            JobItem.wc_action == "created",
            JobItem.wc_product_id.isnot(None),
        )
    ) or 0
    aggiornati = db.scalar(
        select(func.count(JobItem.id)).where(
            JobItem.job_id == job.id, JobItem.status == "pushed", JobItem.wc_action == "updated"
        )
    ) or 0

    return {
        "da_eliminare": da_eliminare,
        "aggiornati": aggiornati,
        "immagini": bool(site.wp_user_enc and site.wp_app_password_enc),
        "site_name": site.name,
    }


@router.post("/jobs/{job_id}/purge")
def purge_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Marca per l'eliminazione i prodotti CREATI da questa importazione.

    Non tocca quelli che erano già sul sito e che l'importazione ha solo
    aggiornato: quelli sono roba del cliente, non nostra."""
    job = _visible_job(db, job_id, user)
    if job.status == "purging":
        raise HTTPException(status.HTTP_409_CONFLICT, "Pulizia già in corso.")

    quanti = db.execute(
        update(JobItem)
        .where(
            JobItem.job_id == job.id,
            JobItem.status == "pushed",
            JobItem.wc_action == "created",
            JobItem.wc_product_id.isnot(None),
        )
        .values(status="to_delete", attempts=0, error="")
    ).rowcount

    job.status = "purging"
    db.commit()
    audit(db, user.email, "job_purge", f"{job.label} · {quanti} prodotti")
    return {"ok": True, "marcati": quanti}


# ================================================= catalogo del cliente
# Il cliente ragiona per PRODOTTI («questo EAN è sul sito o no?»), non per
# importazioni. Qui ogni EAN compare una volta sola — l'ultima versione — con
# uno stato in parole povere e un'azione. Le importazioni restano come storico.

STATO_CLIENTE = {
    "pushed": "sul_sito",
    "ready": "pronto",
    "incomplete": "da_completare",
    "skipped": "da_completare",
    "not_found": "non_trovato",
    "pending": "in_corso",
    "fetching": "in_corso",
    "pushing": "in_corso",
    "error": "problema",
    "blocked": "problema",
}
_ORDINE_STATI = ["da_completare", "non_trovato", "problema", "pronto", "in_corso", "sul_sito"]


def _catalog_key(r: JobItem) -> str:
    """Chiave stabile del prodotto nel catalogo (EAN recuperato compreso)."""
    if codici.e_ean(r.gtin):
        return r.gtin
    try:
        ean = str(json.loads(r.normalized or "{}").get("gtin") or "")
    except (ValueError, TypeError):
        ean = ""
    return ean if codici.e_ean(ean) else "mpn:" + r.gtin.lower()


def _perche_breve(r: JobItem) -> dict | None:
    """Il riassunto del diario per la tabella: causa, frase e siti coinvolti.
    Il dettaglio completo lo dà /api/items/{id}/diagnosi quando si apre «Perché?»."""
    if not r.diagnosi:
        return None
    try:
        d = json.loads(r.diagnosi)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    return {
        "causa": d.get("causa", ""),
        "spiegazione": d.get("spiegazione", ""),
        "siti": [{"dominio": p.get("dominio", ""), "esito": p.get("esito", ""), "dettaglio": p.get("dettaglio", "")}
                 for p in (d.get("pagine") or [])[:8]],
        "quando": d.get("quando", ""),
        "lingua": d.get("lingua", ""),
    }


def _riga_catalogo(r: JobItem, site: Site, wc_id: int | None) -> dict:
    norm = json.loads(r.normalized or "{}")
    immagini = norm.get("images") or []
    stato = STATO_CLIENTE.get(r.status, "in_corso")
    url_sito = f"{site.base_url.rstrip('/')}/?p={wc_id}" if wc_id else ""
    issues = json.loads(r.issues or "[]")
    campi_fonte = {"title", "description", "images", "attributes", "brand", "mpn"}
    campi_cliente = {"category", "regular_price"}
    return {
        "item_id": r.id,
        "job_id": r.job_id,
        "gtin": r.gtin,
        "tipo_codice": "ean" if codici.e_ean(r.gtin) else "mpn",
        "ean_recuperato": (str(norm.get("gtin") or "") if not codici.e_ean(r.gtin)
                           and codici.e_ean(str(norm.get("gtin") or "")) else ""),
        "title": r.title or "",
        "brand": r.brand or "",
        "image": immagini[0] if immagini else "",
        "regular_price": r.regular_price or "",
        "sale_price": r.sale_price or "",
        "category_id": r.wc_category_id,
        "stato": stato,
        "stato_raw": r.status,
        # «debole» = trovata solo per somiglianza del nome: va controllata a occhio
        "conferma": norm.get("conferma", ""),
        "issues": issues,
        "missing_source": [x for x in issues if x in campi_fonte],
        "missing_user": [x for x in issues if x in campi_cliente],
        "source": r.source or "",
        "wc_product_id": wc_id,
        "url_sito": url_sito,
        "gia_sul_sito": bool(wc_id) and r.status != "pushed",
        # Il pulsante Elimina rimuove dal portale, NON da WooCommerce. Perciò
        # non lo mostriamo mai quando esiste già un prodotto collegato sul sito.
        "can_delete": (
            not wc_id
            and r.status not in ("pushed", "pushing", "fetching", "pending", "to_delete", "deleted")
        ),
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        # Ricerca rimandata perché i motori ci bloccavano: quando riprova da sola.
        "riprova_alle": r.retry_after.isoformat() if r.retry_after and r.status == "pending" else None,
        "lingua": norm.get("lingua", ""),
        "perche": _perche_breve(r) if stato in ("non_trovato", "problema") or r.retry_after else None,
        "ha_diagnosi": bool(r.diagnosi),
    }


@router.get("/sites/{site_id}/catalogo")
def catalogo(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Tutti i prodotti caricati su un sito, uno per EAN (l'ultima versione)."""
    site = _visible_site(db, site_id, user)
    rows = db.scalars(
        select(JobItem)
        .join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status.notin_(["deleted", "to_delete"]))
        .order_by(JobItem.id.desc())
    ).all()

    # Chiave del prodotto: l'EAN, anche quando il cliente ha cercato per codice
    # produttore e l'EAN l'abbiamo recuperato dalla pagina. Così «97924465» e
    # «5710626495436» caricati in due momenti diversi stanno su una riga sola.
    ultimo: dict[str, JobItem] = {}
    pubblicato: dict[str, int] = {}
    for r in rows:
        k = _catalog_key(r)
        if k not in ultimo:
            ultimo[k] = r
        if r.status == "pushed" and r.wc_product_id and k not in pubblicato:
            pubblicato[k] = r.wc_product_id

    righe = []
    conteggi: dict[str, int] = {}
    for k, r in ultimo.items():
        riga = _riga_catalogo(r, site, r.wc_product_id or pubblicato.get(k))
        righe.append(riga)
        conteggi[riga["stato"]] = conteggi.get(riga["stato"], 0) + 1

    righe.sort(key=lambda x: (_ORDINE_STATI.index(x["stato"]) if x["stato"] in _ORDINE_STATI else 9,
                              (x["title"] or "~").lower()))
    da_arricchire = sum(1 for x in righe if x.get("missing_source") or x.get("stato") in ("non_trovato", "problema"))
    return {"righe": righe, "conteggi": conteggi, "totale": len(righe), "da_arricchire": da_arricchire}


@router.post("/sites/{site_id}/catalogo/retry-missing")
def catalogo_retry_missing(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Rifà in un colpo le ricerche che hanno davvero buchi di FONTE.

    Non rimette in coda le righe a cui mancano solo prezzo/categoria: quelli sono
    dati del cliente e cercarli di nuovo sul web sarebbe solo tempo sprecato.
    """
    site = _visible_site(db, site_id, user)
    rows = db.scalars(
        select(JobItem)
        .join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status.notin_(["deleted", "to_delete"]))
        .order_by(JobItem.id.desc())
    ).all()

    # Ultima riga per codice: lo storico non va riaperto.
    ultime: dict[str, JobItem] = {}
    for r in rows:
        ultime.setdefault(r.gtin.lower(), r)

    da_rifare: list[JobItem] = []
    for r in ultime.values():
        issues = set(json.loads(r.issues or "[]"))
        manca_fonte = bool(issues & {"title", "description", "images", "attributes", "brand", "mpn"})
        if manca_fonte or r.status in ("not_found", "error", "blocked"):
            if r.status not in ("fetching", "pushing", "pending"):
                da_rifare.append(r)

    if da_rifare:
        providers.svuota_cache_web(db, [r.gtin for r in da_rifare])
        job_ids = set()
        for r in da_rifare:
            _rimetti_in_coda(r)
            job_ids.add(r.job_id)
        for jid in job_ids:
            job = db.get(Job, jid)
            if job:
                job.status = "queued"
                job.finished_at = None
        db.commit()
    return {"requeued": len(da_rifare)}


class CatalogDeleteIn(BaseModel):
    item_ids: list[int]


def _delete_catalog_item(db: Session, site: Site, item: JobItem) -> int:
    """Rimuove dal portale tutte le copie NON pubblicate dello stesso prodotto.

    Se lo stesso EAN/MPN è stato importato più volte, cancellare solo l'ultima
    riga farebbe riapparire quella vecchia nel catalogo. Per questo si marcano
    come eliminate tutte le versioni locali dello stesso prodotto. Le righe già
    collegate a WooCommerce non vengono mai toccate qui.
    """
    job = db.get(Job, item.job_id)
    if not job or job.site_id != site.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prodotto non trovato in questo sito.")
    if item.status in ("pending", "fetching", "pushing", "to_delete"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Il prodotto è in lavorazione: aspetta che finisca e poi eliminalo.")
    if item.status == "pushed" or item.wc_product_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Questo prodotto è già collegato al sito. Da qui puoi eliminare solo le schede locali non pubblicate.",
        )

    key = _catalog_key(item)
    rows = db.scalars(
        select(JobItem)
        .join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id)
        .order_by(JobItem.id.desc())
    ).all()

    eliminate = 0
    gtins_da_svuotare: set[str] = set()
    for row in rows:
        if _catalog_key(row) != key:
            continue
        # Mai cancellare/occultare dal portale qualcosa che corrisponde a un
        # prodotto vivo su WooCommerce o che è in esecuzione in questo momento.
        if row.wc_product_id or row.status in ("pushed", "pushing", "fetching", "pending", "to_delete"):
            continue
        if row.status != "deleted":
            row.status = "deleted"
            row.error = "Rimosso dal catalogo."
            row.issues = "[]"
            eliminate += 1
            gtins_da_svuotare.add(row.gtin)

    # Se in futuro viene reinserito, non deve riesumare una vecchia cache web
    # parziale/non trovata: ripartirà davvero da zero.
    if gtins_da_svuotare:
        providers.svuota_cache_web(db, list(gtins_da_svuotare))
    return eliminate


@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    job = _visible_job(db, item.job_id, user)
    site = db.get(Site, job.site_id)
    eliminate = _delete_catalog_item(db, site, item)
    audit(db, user.email, "catalogo_delete", f"site {site.id} · item {item.id} · {eliminate} righe")
    db.commit()
    return {"eliminate": eliminate}


@router.post("/sites/{site_id}/catalogo/delete")
def catalogo_delete_bulk(
    site_id: int, data: CatalogDeleteIn, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Elimina in blocco dal portale le schede selezionate che non sono sul sito."""
    site = _visible_site(db, site_id, user)
    eliminate = 0
    prodotti = 0
    gia_visti: set[str] = set()
    for iid in data.item_ids[:500]:
        item = db.get(JobItem, iid)
        if not item:
            continue
        job = db.get(Job, item.job_id)
        if not job or job.site_id != site.id:
            continue
        key = _catalog_key(item)
        if key in gia_visti:
            continue
        gia_visti.add(key)
        if item.status in ("pending", "fetching", "pushing", "to_delete", "pushed") or item.wc_product_id:
            continue
        n = _delete_catalog_item(db, site, item)
        if n:
            prodotti += 1
            eliminate += n
    audit(db, user.email, "catalogo_delete_bulk", f"site {site.id} · {prodotti} prodotti · {eliminate} righe")
    db.commit()
    return {"prodotti": prodotti, "eliminate": eliminate}


class BulkIn(BaseModel):
    item_ids: list[int]
    category_id: int | None = None     # None = non toccare
    regular_price: str | None = None   # None = non toccare, "" = svuota
    sale_price: str | None = None


@router.post("/sites/{site_id}/catalogo/bulk")
def catalogo_bulk(site_id: int, data: BulkIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Categoria e/o prezzi su più righe in un colpo: è la modifica in tabella."""
    site = _visible_site(db, site_id, user)
    aggiornate = 0
    for iid in data.item_ids[:500]:
        item = db.get(JobItem, iid)
        if not item:
            continue
        job = db.get(Job, item.job_id)
        if not job or job.site_id != site.id:
            continue
        if item.status in ("pushing", "fetching", "pending"):
            continue
        if data.category_id:
            item.wc_category_id = data.category_id
        if data.regular_price is not None:
            item.regular_price = _clean_price(data.regular_price)
        if data.sale_price is not None:
            item.sale_price = _clean_price(data.sale_price)
        item.edited = True
        # Una riga già pubblicata che viene ritoccata torna «pronta»: si ripubblica.
        if item.status == "pushed":
            item.status = "ready"
        _revalidate(db, item, site, job.kind)
        aggiornate += 1
    audit(db, user.email, "catalogo_bulk", f"site {site.id} · {aggiornate} righe")
    return {"aggiornate": aggiornate}


@router.post("/sites/{site_id}/catalogo/pubblica")
def catalogo_pubblica(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Manda sul sito tutto ciò che è pronto, in qualunque importazione stia."""
    site = _visible_site(db, site_id, user)
    entitlement = billing.check(db, site.client)
    if not entitlement.allowed:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, entitlement.reason)
    job_ids = db.scalars(
        select(func.distinct(JobItem.job_id))
        .select_from(JobItem).join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status == "ready")
    ).all()
    pronte = db.scalar(
        select(func.count(JobItem.id)).select_from(JobItem).join(Job, Job.id == JobItem.job_id)
        .where(Job.site_id == site.id, JobItem.status == "ready")
    ) or 0
    for jid in job_ids:
        requeue_ready(db, jid)
    audit(db, user.email, "catalogo_publish", f"site {site.id} · {pronte} righe in {len(job_ids)} importazioni")
    return {"pronte": int(pronte), "importazioni": len(job_ids)}


@router.post("/items/{item_id}/retry")
def item_retry(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Ricerca di nuovo UN prodotto da zero, scartando anche eventuali schede web parziali."""
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    job = _visible_job(db, item.job_id, user)
    if item.status in ("fetching", "pushing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Riga in lavorazione: aspetta un istante.")
    providers.svuota_cache_web(db, [item.gtin])
    _rimetti_in_coda(item)
    job.status = "queued"
    job.finished_at = None
    db.commit()
    return {"ok": True}


@router.post("/items/{item_id}/pubblica")
async def item_pubblica(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Pubblica UNA sola riga, subito, senza toccare il resto del carico.

    Il pulsante in riga compare solo quando la scheda è pronta (categoria e
    prezzo compilati). Non si usa il meccanismo del job (che spingerebbe tutte
    le righe pronte insieme): la singola riga si manda sul sito qui, adesso.
    """
    item = db.get(JobItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Riga non trovata.")
    job = _visible_job(db, item.job_id, user)
    site = db.get(Site, job.site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    if item.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "Compila prima categoria e prezzo: la scheda non è pronta.")
    entitlement = billing.check(db, site.client)
    if not entitlement.allowed:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, entitlement.reason)

    try:
        await pubblica_riga(db, item, job, site)
    except Exception as exc:  # il worker lo gestirebbe allo stesso modo
        item.error = f"Errore nell'invio: {exc}"
        item.status = "error"
        db.commit()
    db.refresh(item)
    audit(db, user.email, "item_publish", f"{item.gtin} → {item.status}")
    return {"stato": item.status, "errore": item.error or "", "wc_product_id": item.wc_product_id}
