"""Modelli dati del portale."""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Client(Base):
    """Un cliente dell'agenzia: anagrafica, abbonamento, siti collegati."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    # Lingua predefinita delle schede per questo cliente (IT, EN, FR, DE, ES).
    content_lang: Mapped[str] = mapped_column(String(8), default="IT")

    # --- abbonamento -----------------------------------------------------
    # active | trial | suspended | expired
    subscription_status: Mapped[str] = mapped_column(String(16), default="trial", index=True)
    plan_name: Mapped[str] = mapped_column(String(80), default="Prova")
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Quota mensile di prodotti esportabili sul sito. 0 = illimitata.
    monthly_quota: Mapped[int] = mapped_column(Integer, default=0)

    # --- anagrafica, usata solo dentro il portale -------------------------
    legal_name: Mapped[str] = mapped_column(String(190), default="")
    vat_number: Mapped[str] = mapped_column(String(32), default="")
    tax_code: Mapped[str] = mapped_column(String(32), default="")
    sdi_code: Mapped[str] = mapped_column(String(16), default="")
    pec: Mapped[str] = mapped_column(String(190), default="")
    address: Mapped[str] = mapped_column(String(190), default="")
    zip_code: Mapped[str] = mapped_column(String(16), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    province: Mapped[str] = mapped_column(String(8), default="")
    country: Mapped[str] = mapped_column(String(64), default="Italia")
    phone: Mapped[str] = mapped_column(String(40), default="")
    contact_email: Mapped[str] = mapped_column(String(190), default="")
    website: Mapped[str] = mapped_column(String(190), default="")
    logo_path: Mapped[str] = mapped_column(String(255), default="")
    accent_color: Mapped[str] = mapped_column(String(16), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    sites: Mapped[list["Site"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class User(Base):
    """Utente del portale. role = 'admin' (tu) oppure 'client'."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(16), default="client")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    client: Mapped[Client | None] = relationship(back_populates="users")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Site(Base):
    """Un sito WordPress/WooCommerce di un cliente."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)  # es. https://www.gebinformatica.it
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Chiavi REST di WooCommerce (WooCommerce → Impostazioni → Avanzate → API REST).
    wc_key_enc: Mapped[str] = mapped_column(Text, default="")
    wc_secret_enc: Mapped[str] = mapped_column(Text, default="")

    # Facoltative: utente WordPress + Password per applicazioni. Servono solo a
    # cancellare le immagini dalla libreria media, cosa che le chiavi WooCommerce
    # non possono fare perché non autenticano le rotte /wp/v2.
    wp_user_enc: Mapped[str] = mapped_column(Text, default="")
    wp_app_password_enc: Mapped[str] = mapped_column(Text, default="")

    # Stato ultimo handshake.
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ping_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ping_msg: Mapped[str] = mapped_column(Text, default="")

    # Politiche di scrittura per campo, JSON:
    # {"title":"fill","description":"fill","short_description":"fill",
    #  "images":"fill","attributes":"overwrite","brand":"fill","gtin":"overwrite"}
    # valori ammessi: skip | fill (solo se vuoto) | overwrite
    write_policy: Mapped[str] = mapped_column(Text, default="")

    default_status: Mapped[str] = mapped_column(String(16), default="publish")  # publish | draft

    # Campi senza i quali un prodotto non si pubblica. JSON, lista di nomi.
    required_fields: Mapped[str] = mapped_column(Text, default="")

    # Scrive le caratteristiche anche nel meta letto dal plugin TD Product Specs.
    specs_meta: Mapped[bool] = mapped_column(Boolean, default=True)
    # Quante caratteristiche tenere. Certe schede ne hanno anche novanta: le prime
    # sono quelle che contano, il resto allunga la scheda e basta. 0 = tutte.
    specs_limit: Mapped[int] = mapped_column(Integer, default=6)

    # Lingua delle schede per questo negozio (IT, EN, FR, DE, ES). Vuota = quella
    # scelta da chi carica i codici (di norma la lingua dell'interfaccia).
    content_lang: Mapped[str] = mapped_column(String(8), default="")

    # --- ri-sincronizzazione notturna -----------------------------------
    resync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    resync_hour: Mapped[int] = mapped_column(Integer, default=3)        # ora locale
    resync_minute: Mapped[int] = mapped_column(Integer, default=0)
    # Giorni della settimana, 0=lunedì. Vuoto = tutti i giorni.
    resync_days: Mapped[str] = mapped_column(String(16), default="")
    # hub = solo i prodotti importati da qui; all = tutti quelli con un EAN valorizzato
    resync_scope: Mapped[str] = mapped_column(String(8), default="hub")
    resync_batch: Mapped[int] = mapped_column(Integer, default=200)
    resync_min_age_days: Mapped[int] = mapped_column(Integer, default=25)
    # Politiche usate solo dal giro notturno: di norma più aggressive di quelle manuali.
    resync_policy: Mapped[str] = mapped_column(Text, default="")
    last_resync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resync_msg: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    client: Mapped[Client] = relationship(back_populates="sites")
    jobs: Mapped[list["Job"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Job(Base):
    """Un batch di EAN caricato dal cliente."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(190), default="")
    lang: Mapped[str] = mapped_column(String(8), default="IT")
    # manual = caricato dal cliente, resync = giro notturno automatico
    kind: Mapped[str] = mapped_column(String(16), default="manual", index=True)

    # queued | running | paused | done | error
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)

    # Categoria Woo di destinazione se le corrispondenze non la risolvono.
    fallback_category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_status: Mapped[str] = mapped_column(String(16), default="publish")

    # false = solo scarico e ti fermi in anteprima; true = spinge in automatico
    auto_push: Mapped[bool] = mapped_column(Boolean, default=False)

    total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    site: Mapped[Site] = relationship(back_populates="jobs")
    items: Mapped[list["JobItem"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobItem(Base):
    """Un singolo EAN dentro un job."""

    __tablename__ = "job_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    gtin: Mapped[str] = mapped_column(String(32), index=True)

    # pending | fetching | ready | pushing | pushed | not_found | error | skipped
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    source_ref: Mapped[str] = mapped_column(String(32), default="")   # id del prodotto presso la fonte
    title: Mapped[str] = mapped_column(String(255), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")

    # Prezzi: si scrivono nel portale prima dell'export, non arrivano dalle fonti.
    regular_price: Mapped[str] = mapped_column(String(24), default="")
    sale_price: Mapped[str] = mapped_column(String(24), default="")

    # Quale fonte ha risposto, e cosa manca per poter pubblicare.
    source: Mapped[str] = mapped_column(String(60), default="")
    # Categoria scelta a mano per questa riga: vince su mappatura e ripiego.
    wc_category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues: Mapped[str] = mapped_column(Text, default="")   # JSON, lista di campi mancanti
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized: Mapped[str] = mapped_column(Text, default="")  # JSON canonico pronto per il push
    wc_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wc_action: Mapped[str] = mapped_column(String(16), default="")  # created | updated
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    # Diario dell'ultima ricerca (JSON): motori interrogati e bloccati, siti aperti
    # con l'esito di ognuno, causa del «non trovato». È il «Perché?» in tabella.
    diagnosi: Mapped[str] = mapped_column(Text, default="")
    # Ricerca rimandata perché i motori ci bloccavano: non prima di quest'ora.
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rinvii: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="items")


class EanCache(Base):
    """Cache delle schede trovate per (codice, lingua): una ricerca per codice, non una per cliente."""

    __tablename__ = "ean_cache"
    __table_args__ = (UniqueConstraint("gtin", "lang", name="uq_ean_lang"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gtin: Mapped[str] = mapped_column(String(32), index=True)
    lang: Mapped[str] = mapped_column(String(8))
    found: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[str] = mapped_column(Text, default="")  # JSON della scheda (o del perché non c'è)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryMap(Base):
    """Mappa categoria della fonte -> term_id WooCommerce, per singolo sito."""

    __tablename__ = "category_map"
    __table_args__ = (UniqueConstraint("site_id", "source_category", name="uq_site_cat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    source_category: Mapped[str] = mapped_column(String(190))  # id o nome normalizzato
    source_label: Mapped[str] = mapped_column(String(190), default="")
    wc_term_id: Mapped[int] = mapped_column(Integer)
    wc_label: Mapped[str] = mapped_column(String(190), default="")


class Provider(Base):
    """Una fonte dati per un cliente. Si provano in ordine di priorità finché una risponde.

    kind='web'       → ricerca automatica sul web partendo dal codice
    kind='http_json' → qualunque servizio che risponde JSON su una URL, con mappatura
                       dei campi impostata dall'admin: il portale di un distributore, un gestionale,
                       il feed di un distributore
    kind='manual'    → nessuna chiamata: i campi li riempie l'utente a mano
    """

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(16), default="web")
    priority: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lang: Mapped[str] = mapped_column(String(8), default="")

    # Credenziali, cifrate a riposo.
    username_enc: Mapped[str] = mapped_column(Text, default="")
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    secret2_enc: Mapped[str] = mapped_column(Text, default="")

    # Solo per http_json.
    endpoint: Mapped[str] = mapped_column(Text, default="")     # con {gtin} e {lang}
    auth_mode: Mapped[str] = mapped_column(String(16), default="none")  # none|header|query|basic|bearer
    auth_name: Mapped[str] = mapped_column(String(80), default="")
    extra_params: Mapped[str] = mapped_column(Text, default="")  # JSON
    field_map: Mapped[str] = mapped_column(Text, default="")     # JSON
    root_path: Mapped[str] = mapped_column(String(190), default="")

    notes: Mapped[str] = mapped_column(Text, default="")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Impostazioni globali: motore di ricerca, traduzione, aspetto del portale."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_email: Mapped[str] = mapped_column(String(190), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
