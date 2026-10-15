"""Pannello amministratore: clienti, utenti, siti, fonti, ricerca sul web."""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import billing, config, lingue, providers as providers_mod, quality, scheduler, wc_rest
from ..html_sicuro import scheda_sicura, svg_attivo, tipo_immagine
from ..db import get_db
from ..models import AuditLog, Client, Job, JobItem, Provider, Site, User
from ..security import decrypt, encrypt, hash_password, mask, random_token, require_admin
from ..store import (
    BRAND_FAVICON_KEY,
    BRAND_FAVICON_VER,
    BRAND_LOGO_KEY,
    BRAND_NAME_KEY,
    audit,
    portal_brand,
    ricerca_config,
    salva_ricerca,
    set_setting,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

SUB_STATES = ("active", "trial", "suspended", "expired")


# --------------------------------------------------------------------- schemi
class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    notes: str = ""
    is_active: bool = True
    content_lang: str = "IT"
    # abbonamento
    subscription_status: str = "trial"
    plan_name: str = "Prova"
    subscription_until: datetime | None = None
    monthly_quota: int = 0
    # anagrafica
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


class UserIn(BaseModel):
    email: EmailStr
    full_name: str = ""
    password: str | None = None
    role: str = "client"
    client_id: int | None = None
    is_active: bool = True


class SiteIn(BaseModel):
    client_id: int
    name: str = Field(min_length=2, max_length=160)
    base_url: str
    is_active: bool = True
    default_status: str = "publish"
    wc_key: str | None = None
    wc_secret: str | None = None
    wp_user: str | None = None
    wp_app_password: str | None = None
    write_policy: dict | None = None
    required_fields: list[str] | None = None
    specs_meta: bool = True
    specs_limit: int = 6
    content_lang: str = ""
    # ri-sincronizzazione notturna
    resync_enabled: bool = False
    resync_hour: int = 3
    resync_minute: int = 0
    resync_days: str = ""
    resync_scope: str = "hub"
    resync_batch: int = 200
    resync_min_age_days: int = 25
    resync_policy: dict | None = None
    giorni_cestino: int = 30


# --------------------------------------------------------------------- clienti
def _client_json(db: Session, c: Client) -> dict:
    sites = db.scalars(select(Site).where(Site.client_id == c.id)).all()
    used = db.scalar(
        select(func.count(JobItem.id))
        .join(Job, Job.id == JobItem.job_id)
        .join(Site, Site.id == Job.site_id)
        .where(Site.client_id == c.id, JobItem.status == "pushed")
    )
    return {
        "id": c.id,
        "name": c.name,
        "notes": c.notes,
        "is_active": c.is_active,
        "content_lang": c.content_lang or "IT",
        "users": [{"id": u.id, "email": u.email, "is_active": u.is_active} for u in c.users],
        "sites": [{"id": s.id, "name": s.name, "base_url": s.base_url, "is_active": s.is_active} for s in sites],
        "pushed_total": used or 0,
        "subscription": billing.as_json(billing.check(db, c)),
        "subscription_status": c.subscription_status,
        "plan_name": c.plan_name,
        "subscription_until": c.subscription_until.isoformat() if c.subscription_until else None,
        "monthly_quota": c.monthly_quota,
        "has_logo": bool(c.logo_path),
        "profile": _profile_json(c),
    }


PROFILE_FIELDS = (
    "legal_name", "vat_number", "tax_code", "sdi_code", "pec", "address",
    "zip_code", "city", "province", "country", "phone", "contact_email",
    "website", "accent_color",
)


def _profile_json(c: Client) -> dict:
    return {field: getattr(c, field) for field in PROFILE_FIELDS}


def _apply_client_fields(client: Client, data: "ClientIn") -> None:
    client.name = data.name
    client.notes = data.notes
    client.is_active = data.is_active
    client.content_lang = lingue.normalizza(data.content_lang)
    client.subscription_status = (
        data.subscription_status if data.subscription_status in SUB_STATES else "trial"
    )
    client.plan_name = data.plan_name
    client.subscription_until = data.subscription_until
    client.monthly_quota = max(0, data.monthly_quota)
    for field in PROFILE_FIELDS:
        setattr(client, field, getattr(data, field, "") or "")


@router.get("/clients")
def list_clients(db: Session = Depends(get_db)):
    rows = db.scalars(select(Client).order_by(Client.name)).all()
    return {"clients": [_client_json(db, c) for c in rows]}


@router.post("/clients", status_code=201)
def create_client(data: ClientIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    client = Client()
    _apply_client_fields(client, data)
    db.add(client)
    db.commit()
    audit(db, admin.email, "client_create", client.name)
    return _client_json(db, client)


@router.put("/clients/{client_id}")
def update_client(
    client_id: int, data: ClientIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    _apply_client_fields(client, data)
    db.commit()
    audit(db, admin.email, "client_update", client.name)
    return _client_json(db, client)


@router.delete("/clients/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    name = client.name
    db.delete(client)
    db.commit()
    audit(db, admin.email, "client_delete", name)
    return {"ok": True}


# --------------------------------------------------------------------- utenti
@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.email)).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
            "client_id": u.client_id,
            "client_name": u.client.name if u.client else "",
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        }
        for u in rows
    ]


@router.post("/users", status_code=201)
def create_user(data: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Esiste già un utente con questa email.")
    if data.password and len(data.password) < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La password deve avere almeno 10 caratteri.")
    password = data.password or random_token(9)
    user = User(
        email=data.email.lower(),
        full_name=data.full_name,
        password_hash=hash_password(password),
        role=data.role if data.role in ("admin", "client") else "client",
        client_id=data.client_id,
        is_active=data.is_active,
    )
    db.add(user)
    db.commit()
    audit(db, admin.email, "user_create", user.email)
    # La password in chiaro torna una volta sola: è il momento in cui gliela mandi.
    return {"id": user.id, "email": user.email, "password": password}


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utente non trovato.")
    nuova_email = data.email.lower()
    if nuova_email != user.email:
        if db.scalar(select(User).where(User.email == nuova_email, User.id != user.id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Esiste già un utente con questa email.")
        user.email = nuova_email
    user.full_name = data.full_name
    user.role = data.role if data.role in ("admin", "client") else user.role
    user.client_id = data.client_id
    user.is_active = data.is_active
    if data.password:
        if len(data.password) < 10:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "La password deve avere almeno 10 caratteri.")
        user.password_hash = hash_password(data.password)
    db.commit()
    audit(db, admin.email, "user_update", user.email)
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utente non trovato.")
    password = random_token(9)
    user.password_hash = hash_password(password)
    db.commit()
    audit(db, admin.email, "user_reset_password", user.email)
    return {"email": user.email, "password": password}


# --------------------------------------------------------------------- siti
def _site_json(s: Site) -> dict:
    return {
        "id": s.id,
        "client_id": s.client_id,
        "client_name": s.client.name if s.client else "",
        "name": s.name,
        "base_url": s.base_url,
        "is_active": s.is_active,
        "default_status": s.default_status,
        "write_policy": json.loads(s.write_policy or "{}"),
        "required_fields": quality.required_for(s),
        "specs_meta": s.specs_meta,
        "specs_limit": s.specs_limit,
        "giorni_cestino": s.giorni_cestino or 30,
        "content_lang": s.content_lang or "",
        # Nemmeno nel pannello si mostrano per intero: bastano le ultime cifre per
        # riconoscere quale chiave è. Il cliente non le vede in nessun caso.
        "wc_key_masked": mask(decrypt(s.wc_key_enc), keep=6),
        "wc_secret_masked": mask(decrypt(s.wc_secret_enc)),
        "wp_user": decrypt(s.wp_user_enc),
        "wp_app_masked": mask(decrypt(s.wp_app_password_enc)),
        "puo_cancellare_immagini": bool(s.wp_user_enc and s.wp_app_password_enc),
        "has_keys": bool(s.wc_key_enc and s.wc_secret_enc),
        "last_ping_at": s.last_ping_at.isoformat() if s.last_ping_at else None,
        "last_ping_ok": s.last_ping_ok,
        "last_ping_msg": s.last_ping_msg,
        "resync_enabled": s.resync_enabled,
        "resync_hour": s.resync_hour,
        "resync_minute": s.resync_minute,
        "resync_days": s.resync_days,
        "resync_scope": s.resync_scope,
        "resync_batch": s.resync_batch,
        "resync_min_age_days": s.resync_min_age_days,
        "resync_policy": {**scheduler.DEFAULT_RESYNC_POLICY, **json.loads(s.resync_policy or "{}")},
        "last_resync_at": s.last_resync_at.isoformat() if s.last_resync_at else None,
        "last_resync_msg": s.last_resync_msg,
    }


def _apply_resync(site: Site, data: "SiteIn") -> None:
    site.resync_enabled = data.resync_enabled
    site.resync_hour = max(0, min(23, data.resync_hour))
    site.resync_minute = max(0, min(59, data.resync_minute))
    site.resync_days = "".join(sorted(c for c in (data.resync_days or "") if c in "0123456"))
    site.resync_scope = data.resync_scope if data.resync_scope in ("hub", "all") else "hub"
    site.resync_batch = max(1, min(2000, data.resync_batch))
    site.resync_min_age_days = max(0, min(365, data.resync_min_age_days))
    if data.resync_policy is not None:
        site.resync_policy = json.dumps(data.resync_policy)


@router.get("/sites")
def list_sites(db: Session = Depends(get_db)):
    return [_site_json(s) for s in db.scalars(select(Site).order_by(Site.name)).all()]


@router.post("/sites", status_code=201)
def create_site(data: SiteIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if not db.get(Client, data.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    try:
        base_url = wc_rest.validate_base_url(data.base_url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    site = Site(
        client_id=data.client_id,
        name=data.name,
        base_url=base_url,
        is_active=data.is_active,
        default_status=data.default_status,
        write_policy=json.dumps(data.write_policy or {}),
        wc_key_enc=encrypt(data.wc_key or ""),
        wc_secret_enc=encrypt(data.wc_secret or ""),
        wp_user_enc=encrypt(data.wp_user or ""),
        wp_app_password_enc=encrypt(data.wp_app_password or ""),
    )
    _apply_resync(site, data)
    site.specs_meta = data.specs_meta
    site.specs_limit = max(0, min(80, data.specs_limit))
    site.giorni_cestino = max(1, min(365, data.giorni_cestino))
    site.content_lang = lingue.normalizza(data.content_lang, "") if data.content_lang else ""
    if data.required_fields is not None:
        site.required_fields = json.dumps([f for f in data.required_fields if f in quality.FIELDS])
    db.add(site)
    db.commit()
    audit(db, admin.email, "site_create", site.base_url)
    return _site_json(site)


@router.put("/sites/{site_id}")
def update_site(site_id: int, data: SiteIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    try:
        site.base_url = wc_rest.validate_base_url(data.base_url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    site.client_id = data.client_id
    site.name = data.name
    site.is_active = data.is_active
    site.default_status = data.default_status
    # Le chiavi si toccano solo se arrivano valorizzate: la UI manda null se non le hai
    # scritte, così non si cancellano per sbaglio salvando altre impostazioni.
    if data.wc_key is not None:
        site.wc_key_enc = encrypt(data.wc_key)
    if data.wc_secret is not None:
        site.wc_secret_enc = encrypt(data.wc_secret)
    if data.wp_user is not None:
        site.wp_user_enc = encrypt(data.wp_user)
    if data.wp_app_password is not None:
        site.wp_app_password_enc = encrypt(data.wp_app_password)
    if data.write_policy is not None:
        site.write_policy = json.dumps(data.write_policy)
    _apply_resync(site, data)
    site.specs_meta = data.specs_meta
    site.specs_limit = max(0, min(80, data.specs_limit))
    site.giorni_cestino = max(1, min(365, data.giorni_cestino))
    site.content_lang = lingue.normalizza(data.content_lang, "") if data.content_lang else ""
    if data.required_fields is not None:
        site.required_fields = json.dumps([f for f in data.required_fields if f in quality.FIELDS])
    db.commit()
    audit(db, admin.email, "site_update", site.base_url)
    return _site_json(site)


@router.post("/sites/{site_id}/ping")
async def ping_site(site_id: int, db: Session = Depends(get_db)):
    from datetime import datetime, timezone

    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    site.last_ping_at = datetime.now(timezone.utc)
    try:
        info = await wc_rest.ping(site)
        site.last_ping_ok = True
        site.last_ping_msg = (
            f"WooCommerce {info.get('woocommerce', '?')} · WP {info.get('wordpress', '?')} · "
            f"{info.get('products', 0)} prodotti"
            + (" · Marche attive" if info.get("has_brands") else "")
        )
        db.commit()
        return {"ok": True, "info": info}
    except wc_rest.SiteError as exc:
        site.last_ping_ok = False
        site.last_ping_msg = str(exc)
        db.commit()
        return {"ok": False, "error": str(exc)}


@router.post("/sites/{site_id}/resync-now")
async def resync_now(site_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Lancia subito il giro di aggiornamento, senza aspettare la notte."""
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    job, message = await scheduler.run_now(db, site)
    audit(db, admin.email, "resync_now", f"{site.base_url} · {message}")
    return {"ok": job is not None, "message": message, "job_id": job.id if job else None}


@router.delete("/sites/{site_id}")
def delete_site(site_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sito non trovato.")
    url = site.base_url
    db.delete(site)
    db.commit()
    audit(db, admin.email, "site_delete", url)
    return {"ok": True}


# --------------------------------------------------------------------- log
@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()
    return [
        {"at": r.at.isoformat(), "user": r.user_email, "action": r.action, "detail": r.detail}
        for r in rows
    ]


# --------------------------------------------------------------- fonti dati
class ProviderIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = "web"
    priority: int = 10
    is_active: bool = True
    lang: str = ""
    username: str | None = None
    secret: str | None = None
    secret2: str | None = None
    endpoint: str = ""
    auth_mode: str = "none"
    auth_name: str = ""
    extra_params: dict | None = None
    field_map: dict | None = None
    root_path: str = ""
    notes: str = ""


def _provider_json(p: Provider) -> dict:
    return {
        "id": p.id,
        "client_id": p.client_id,
        "name": p.name,
        "kind": p.kind,
        "priority": p.priority,
        "is_active": p.is_active,
        "lang": p.lang,
        "username": decrypt(p.username_enc),
        "secret_masked": mask(decrypt(p.secret_enc)),
        "has_secret": bool(p.secret_enc),
        "endpoint": p.endpoint,
        "auth_mode": p.auth_mode,
        "auth_name": p.auth_name,
        "extra_params": json.loads(p.extra_params or "{}"),
        "field_map": json.loads(p.field_map or "{}"),
        "root_path": p.root_path,
        "notes": p.notes,
        "last_ok_at": p.last_ok_at.isoformat() if p.last_ok_at else None,
        "last_error": p.last_error,
    }


def _apply_provider(row: Provider, data: ProviderIn) -> None:
    row.name = data.name
    row.kind = data.kind if data.kind in providers_mod.KINDS else "http_json"
    row.priority = max(0, min(999, data.priority))
    row.is_active = data.is_active
    row.lang = lingue.normalizza(data.lang, "") if data.lang else ""
    row.endpoint = data.endpoint.strip()
    row.auth_mode = data.auth_mode if data.auth_mode in ("none", "header", "query", "basic", "bearer") else "none"
    row.auth_name = data.auth_name.strip()[:80]
    row.root_path = data.root_path.strip()[:190]
    row.notes = data.notes
    if data.extra_params is not None:
        row.extra_params = json.dumps(data.extra_params)
    if data.field_map is not None:
        row.field_map = json.dumps(data.field_map)
    # I segreti si toccano solo se arrivano valorizzati.
    if data.username is not None:
        row.username_enc = encrypt(data.username)
    if data.secret is not None:
        row.secret_enc = encrypt(data.secret)
    if data.secret2 is not None:
        row.secret2_enc = encrypt(data.secret2)


@router.get("/clients/{client_id}/providers")
def list_providers(client_id: int, db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    rows = db.scalars(
        select(Provider).where(Provider.client_id == client_id).order_by(Provider.priority, Provider.id)
    ).all()
    return {
        "providers": [_provider_json(p) for p in rows],
        "fields": list(quality.FIELDS.items()),
        "preset": providers_mod.PRESET,
    }


@router.post("/clients/{client_id}/providers", status_code=201)
def create_provider(
    client_id: int, data: ProviderIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    if not db.get(Client, client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    row = Provider(client_id=client_id)
    _apply_provider(row, data)
    db.add(row)
    db.commit()
    audit(db, admin.email, "provider_create", f"{client_id} · {row.name}")
    return _provider_json(row)


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: int, data: ProviderIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    row = db.get(Provider, provider_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fonte non trovata.")
    _apply_provider(row, data)
    db.commit()
    audit(db, admin.email, "provider_update", row.name)
    return _provider_json(row)


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = db.get(Provider, provider_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fonte non trovata.")
    name = row.name
    db.delete(row)
    db.commit()
    audit(db, admin.email, "provider_delete", name)
    return {"ok": True}


def _codice_valido(gtin: str) -> str:
    from .. import codici

    esito = codici.classifica(gtin)
    if not esito or esito[0] == "interno":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Serve un EAN valido o un codice produttore.")
    return esito[1]


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int, gtin: str, lang: str = "", db: Session = Depends(get_db)):
    """Prova la fonte su un codice e restituisce cosa ha capito, per tarare la mappa."""
    from .. import web_source

    row = db.get(Provider, provider_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fonte non trovata.")
    client = db.get(Client, row.client_id)
    clean = _codice_valido(gtin)
    lingua = lingue.normalizza(lang or row.lang or client.content_lang)
    diag = None
    try:
        if row.kind == "web":
            modelli = [r.strip() for r in (row.endpoint or "").splitlines() if r.strip()]
            cfg = ricerca_config(db)
            if modelli:
                product = await web_source.cerca(modelli, clean, lingua=lingua, render_url=cfg["render_url"])
            else:
                if not cfg["motore"]:
                    return {"ok": False, "error": "Motore di ricerca non impostato: vai in Impostazioni → "
                                                  "Ricerca automatica sul web."}
                product = await web_source.cerca_automatica(
                    clean, cfg["motore"], cfg["chiave"], providers_mod.extra_ricerca(cfg), lingua=lingua)
                if product.get("title"):
                    providers_mod.salva_cache_web(db, clean, lingua, json.dumps(product, ensure_ascii=False), True)
            diag = product.pop("_diagnosi", None)
            product.pop("_esiti", None)
            raw = {"origine": product.get("origine_url", "")}
        else:
            raw = await providers_mod._fetch_http_json(row, clean, lingua)
            product = providers_mod.map_generic(raw, clean, json.loads(row.field_map or "{}"), row.root_path)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "diagnosi": getattr(exc, "diagnosi", None)}

    sample = raw if isinstance(raw, dict) else {}
    return {
        "ok": bool(product.get("title")),
        "product": scheda_sicura(product),
        "diagnosi": diag,
        "raw_keys": sorted(sample.keys())[:40],
        "raw_sample": json.dumps(sample, ensure_ascii=False)[:4000],
    }


# ------------------------------------------------------ marca del portale
class BrandIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}


@router.get("/brand")
def get_brand(db: Session = Depends(get_db)):
    return portal_brand(db)


@router.put("/brand")
def save_brand(data: BrandIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    set_setting(db, BRAND_NAME_KEY, data.name.strip())
    audit(db, admin.email, "brand_update", data.name)
    return portal_brand(db)


@router.post("/brand/logo")
async def upload_brand_logo(
    file: UploadFile = File(...), db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    suffix = LOGO_TYPES.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Formati accettati: PNG, JPG, WebP, SVG.")

    blob = await file.read()
    if len(blob) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Il logo non deve superare i 2 MB.")
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File vuoto.")
    if suffix == ".svg" and svg_attivo(blob):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG con contenuto attivo: usane uno pulito o un PNG.")

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for vecchio in config.UPLOAD_DIR.glob("portale-logo.*"):
        vecchio.unlink(missing_ok=True)
    (config.UPLOAD_DIR / f"portale-logo{suffix}").write_bytes(blob)
    set_setting(db, BRAND_LOGO_KEY, f"portale-logo{suffix}")
    audit(db, admin.email, "brand_logo")
    return portal_brand(db)


@router.delete("/brand/logo")
def delete_brand_logo(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    for vecchio in config.UPLOAD_DIR.glob("portale-logo.*"):
        vecchio.unlink(missing_ok=True)
    set_setting(db, BRAND_LOGO_KEY, "")
    audit(db, admin.email, "brand_logo_delete")
    return portal_brand(db)


FAVICON_MAX_BYTES = 1024 * 1024


@router.post("/brand/favicon")
async def upload_brand_favicon(
    file: UploadFile = File(...), db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """La favicon del portale: scheda del browser, preferiti, pagina di accesso e,
    se è un PNG/WebP/JPG, anche l'icona sul telefono. Il formato si riconosce dai
    byte: i browser mandano i .ico con tipi diversi a seconda del sistema."""
    from ..web_source import _dimensioni_immagine

    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File vuoto.")
    if len(blob) > FAVICON_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "La favicon non deve superare 1 MB.")
    suffix = tipo_immagine(blob)
    if not suffix:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Formati accettati: PNG, SVG, ICO, WebP, JPG.")
    if suffix == ".svg" and svg_attivo(blob):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG con contenuto attivo: usane uno pulito o un PNG.")

    avviso = ""
    if suffix in (".png", ".jpg", ".webp"):
        dimensioni = _dimensioni_immagine(blob)
        if dimensioni:
            larga, alta = dimensioni
            if min(larga, alta) < 16:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "Troppo piccola: serve almeno 16×16 pixel (meglio 512×512).")
            if max(larga, alta) > 1.15 * min(larga, alta):
                avviso = "Non è quadrata: nella scheda del browser verrà rimpicciolita. Meglio un'immagine quadrata."
            elif min(larga, alta) < 180:
                avviso = "Va bene per il browser; per l'icona sul telefono servono almeno 180×180 pixel."

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for vecchio in config.UPLOAD_DIR.glob("portale-favicon.*"):
        vecchio.unlink(missing_ok=True)
    (config.UPLOAD_DIR / f"portale-favicon{suffix}").write_bytes(blob)
    set_setting(db, BRAND_FAVICON_KEY, f"portale-favicon{suffix}")
    set_setting(db, BRAND_FAVICON_VER, str(int(datetime.now().timestamp())))
    audit(db, admin.email, "brand_favicon", suffix.lstrip("."))
    return {**portal_brand(db), "avviso": avviso}


@router.delete("/brand/favicon")
def delete_brand_favicon(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Si torna alla favicon di serie del portale."""
    for vecchio in config.UPLOAD_DIR.glob("portale-favicon.*"):
        vecchio.unlink(missing_ok=True)
    set_setting(db, BRAND_FAVICON_KEY, "")
    set_setting(db, BRAND_FAVICON_VER, str(int(datetime.now().timestamp())))
    audit(db, admin.email, "brand_favicon_delete")
    return portal_brand(db)


# --------------------------------------------------- motore di ricerca web
class RicercaIn(BaseModel):
    motore: str = ""
    chiave: str | None = None
    cx: str = ""
    url: str = ""
    motori: str | None = None
    lt_url: str = ""
    lt_chiave: str | None = None
    render_url: str = ""


@router.get("/ricerca")
def get_ricerca(db: Session = Depends(get_db)):
    from .. import web_search

    cfg = ricerca_config(db)
    senza_chiave = cfg["motore"] in ("searxng", "duckduckgo")
    pronto = bool(cfg["motore"]) and (
        bool(cfg["url"]) if cfg["motore"] == "searxng" else (senza_chiave or bool(cfg["chiave"]))
    )
    return {
        "motore": cfg["motore"],
        "cx": cfg["cx"],
        "url": cfg["url"],
        "motori": cfg["motori"],
        "motori_predefiniti": web_search.MOTORI_SEARXNG_PREDEFINITI,
        "lt_url": cfg["lt_url"],
        "lt_chiave_masked": mask(cfg["lt_chiave"]),
        "render_url": cfg["render_url"],
        "chiave_masked": mask(cfg["chiave"]),
        "attiva": pronto,
        "pausa": config.RICERCA_PAUSA,
        "lingue": [{"codice": c, "nome": lingue.NOMI[c]} for c in lingue.LINGUE],
        "motori_disponibili": {
            k: {"nome": v["nome"], "campi": v["campi"], "note": v["note"]}
            for k, v in web_search.MOTORI.items()
        },
    }


@router.put("/ricerca")
def save_ricerca(data: RicercaIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from .. import web_search

    if data.motore and data.motore not in web_search.MOTORI:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Motore non riconosciuto.")
    salva_ricerca(db, data.motore, data.chiave, data.cx.strip(), data.url.strip(),
                  lt_url=data.lt_url.strip(), lt_chiave=data.lt_chiave,
                  render_url=data.render_url.strip(), motori=data.motori)
    audit(db, admin.email, "ricerca_config", data.motore or "spenta")
    return get_ricerca(db)


@router.delete("/ricerca/cache")
def svuota_cache_ricerca(tutto: bool = False, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Svuota la memoria della ricerca.

    Di base butta solo i tentativi a vuoto: utile dopo aver sistemato SearXNG o
    cambiato motore, per rifare davvero le ricerche fallite. Con tutto=true butta
    anche le schede già trovate, così al prossimo giro si riscaricano da capo.
    """
    from ..models import EanCache

    if tutto:
        righe = db.scalars(select(EanCache)).all()
    else:
        righe = db.scalars(select(EanCache).where(EanCache.found.is_(False))).all()
    quante = len(righe)
    for riga in righe:
        db.delete(riga)
    db.commit()
    audit(db, admin.email, "ricerca_cache_svuotata", f"{'tutta la cache' if tutto else 'solo i buchi'}: {quante} righe")
    return {"cancellate": quante}


def _candidati_json(diag: dict | None) -> list[dict]:
    return list((diag or {}).get("candidati") or [])


@router.post("/ricerca/prova")
async def prova_ricerca(gtin: str, lang: str = "IT", db: Session = Depends(get_db)):
    """Cerca davvero un codice e mostra cosa ha trovato, con il diario completo."""
    from .. import web_search, web_source

    clean = _codice_valido(gtin)
    lingua = lingue.normalizza(lang)
    cfg = ricerca_config(db)
    try:
        scheda = await web_source.cerca_automatica(clean, cfg["motore"], cfg["chiave"],
                                                   providers_mod.extra_ricerca(cfg), lingua=lingua)
    except web_search.MotoriBloccati as exc:
        diag = getattr(exc, "diagnosi", None) or {}
        return {"ok": False, "error": diag.get("spiegazione") or str(exc), "diagnosi": diag,
                "candidati": _candidati_json(diag), "esiti": []}
    except (web_search.RicercaSpenta, web_search.RicercaError) as exc:
        diag = getattr(exc, "diagnosi", None) or {}
        return {"ok": False, "error": str(exc), "diagnosi": diag, "candidati": [], "esiti": []}
    except web_source.NonTrovato as exc:
        diag = getattr(exc, "diagnosi", None) or {}
        return {
            "ok": False,
            "error": diag.get("spiegazione") or f"Nessuna pagina ha confermato il codice. {exc}",
            "diagnosi": diag,
            "candidati": _candidati_json(diag),
            "esiti": list(getattr(exc, "esiti", []) or []),
        }

    # La scheda trovata si mette in cache: se un'importazione aveva lasciato un
    # «non trovato», alla prossima riprova la trova subito.
    diag = scheda.pop("_diagnosi", None) or {}
    esiti = list(scheda.pop("_esiti", []) or [])
    providers_mod.salva_cache_web(db, clean, lingua, json.dumps(scheda, ensure_ascii=False), True)
    return {
        "ok": True,
        "product": scheda,
        "origine": scheda.get("origine_dominio", ""),
        "conferma": scheda.get("conferma", ""),
        "candidati": _candidati_json(diag),
        "esiti": esiti,
        "diagnosi": diag,
    }


@router.get("/ricerca/salute")
def salute_ricerca(giorni: int = 7, db: Session = Depends(get_db)):
    """Com'è andata la ricerca negli ultimi giorni: motori, cause, siti che bloccano.

    È la risposta a «perché trovo 3 su 10?», con i numeri veri di QUESTO server."""
    from datetime import timedelta, timezone

    from .. import web_search

    dal = datetime.now(timezone.utc) - timedelta(days=max(1, min(60, giorni)))
    righe = db.scalars(
        select(JobItem).where(JobItem.updated_at >= dal, JobItem.diagnosi != "")
        .order_by(JobItem.id.desc()).limit(3000)
    ).all()
    cause: dict[str, int] = {}
    siti: dict[str, dict] = {}
    motori_ko: dict[str, dict] = {}
    totale = trovati = 0
    for r in righe:
        try:
            d = json.loads(r.diagnosi or "{}")
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        totale += 1
        causa = d.get("causa") or "?"
        if causa == "trovato":
            trovati += 1
        cause[causa] = cause.get(causa, 0) + 1
        for p in d.get("pagine") or []:
            esito = p.get("esito")
            if esito in ("bloccata", "robots", "http", "rete"):
                voce = siti.setdefault(p.get("dominio") or "?", {"dominio": p.get("dominio") or "?", "volte": 0,
                                                                 "motivo": p.get("dettaglio") or esito})
                voce["volte"] += 1
        for q in d.get("ricerche") or []:
            for ko in q.get("ko") or []:
                if isinstance(ko, (list, tuple)) and ko:
                    voce = motori_ko.setdefault(str(ko[0]), {"motore": str(ko[0]), "volte": 0, "motivo": ""})
                    voce["volte"] += 1
                    voce["motivo"] = str(ko[1]) if len(ko) > 1 else voce["motivo"]
    return {
        "giorni": giorni,
        "ricerche": totale,
        "trovati": trovati,
        "cause": [{"causa": k, "volte": v, "testo": _testo_causa(k)} for k, v in
                  sorted(cause.items(), key=lambda x: -x[1])],
        "siti_bloccati": sorted(siti.values(), key=lambda x: -x["volte"])[:15],
        "motori_bloccati": sorted(motori_ko.values(), key=lambda x: -x["volte"]),
        "motori_ora": web_search.stato_motori(),
    }


def _testo_causa(causa: str) -> str:
    from .. import diagnosi

    return diagnosi.CAUSE.get(causa, causa)


# ------------------------------------------------------------ prove a lotti
class LottoIn(BaseModel):
    codici: str
    lang: str = "IT"


@router.post("/ricerca/lotto")
async def avvia_lotto(data: LottoIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Prova la ricerca su un elenco di codici, in sottofondo, uno alla volta come
    farebbe un'importazione vera. Non tocca né i clienti né i siti."""
    import asyncio
    import secrets

    from .. import codici, diagnosi, web_search, web_source

    buoni, scarti = codici.parse_codici(data.codici)
    buoni = buoni[:40]
    if not buoni:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nessun codice valido nell'elenco.")
    for vecchio in [k for k, v in diagnosi.LOTTI.items() if v.get("finito")][:-5]:
        diagnosi.LOTTI.pop(vecchio, None)
    lingua = lingue.normalizza(data.lang)
    cfg = ricerca_config(db)
    lotto_id = secrets.token_hex(6)
    stato = {"id": lotto_id, "lingua": lingua, "totale": len(buoni), "fatti": 0, "righe": [],
             "scartati": scarti, "finito": False, "avviato": datetime.now().isoformat(timespec="seconds")}
    diagnosi.LOTTI[lotto_id] = stato

    async def gira():
        for codice in buoni:
            riga = {"codice": codice}
            try:
                scheda = await web_source.cerca_automatica(codice, cfg["motore"], cfg["chiave"],
                                                           providers_mod.extra_ricerca(cfg), lingua=lingua)
                diag = scheda.pop("_diagnosi", None) or {}
                riga.update(trovato=True, titolo=scheda.get("title", "")[:120],
                            origine=scheda.get("origine_dominio", ""), conferma=scheda.get("conferma", ""),
                            foto=len(scheda.get("images") or []), descrizione=len(scheda.get("description") or ""),
                            specifiche=len(scheda.get("attributes") or []), diagnosi=diag)
            except Exception as exc:  # noqa: BLE001 - ogni esito va nel resoconto
                diag = getattr(exc, "diagnosi", None) or {}
                riga.update(trovato=False, errore=diag.get("spiegazione") or str(exc)[:300], diagnosi=diag)
            stato["righe"].append(riga)
            stato["fatti"] += 1
        stato["finito"] = True

    asyncio.get_running_loop().create_task(gira())
    audit(db, admin.email, "ricerca_lotto", f"{len(buoni)} codici · {lingua}")
    return stato


@router.get("/ricerca/lotto/{lotto_id}")
def stato_lotto(lotto_id: str):
    from .. import diagnosi

    stato = diagnosi.LOTTI.get(lotto_id)
    if not stato:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prova non trovata (il portale è stato riavviato?).")
    return stato


@router.post("/clients/{client_id}/providers/confronta")
async def confronta_fonti(client_id: int, gtin: str, db: Session = Depends(get_db)):
    """Interroga OGNI fonte separatamente sullo stesso codice e mette a confronto."""
    from .. import web_source

    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente non trovato.")
    clean = _codice_valido(gtin)
    lingua = lingue.normalizza(client.content_lang)
    esiti = []

    for provider in providers_mod.chain(db, client):
        if provider.kind == "manual":
            continue
        voce = {"fonte": provider.name or provider.kind, "kind": provider.kind,
                "priorita": provider.priority, "ok": False, "errore": ""}
        try:
            if provider.kind == "web":
                cfg = ricerca_config(db)
                prodotto = await web_source.cerca_automatica(
                    clean, cfg["motore"], cfg["chiave"], providers_mod.extra_ricerca(cfg), lingua=lingua)
                prodotto.pop("_diagnosi", None)
                prodotto.pop("_esiti", None)
                if prodotto.get("title"):
                    providers_mod.salva_cache_web(db, clean, lingua, json.dumps(prodotto, ensure_ascii=False), True)
            else:
                grezzo = await providers_mod._fetch_http_json(provider, clean, lingua)
                prodotto = providers_mod.map_generic(
                    grezzo, clean, json.loads(provider.field_map or "{}"), provider.root_path)
        except Exception as exc:
            diag = getattr(exc, "diagnosi", None) or {}
            voce["errore"] = (diag.get("spiegazione") or str(exc))[:220] or exc.__class__.__name__
            esiti.append(voce)
            continue

        voce.update({
            "ok": bool(prodotto.get("title")),
            "titolo": prodotto.get("title", ""),
            "marca": prodotto.get("brand", ""),
            "mpn": prodotto.get("mpn", ""),
            "categoria": (prodotto.get("category") or {}).get("name", ""),
            "descrizione": len(prodotto.get("description") or ""),
            "immagini": len(prodotto.get("images") or []),
            "caratteristiche": len(prodotto.get("attributes") or []),
            "origine": prodotto.get("origine_dominio", ""),
        })
        esiti.append(voce)

    return {"gtin": clean, "esiti": esiti}
