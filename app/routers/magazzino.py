"""Magazzino: i prodotti che sono già sul sito, per categoria e con la ricerca, e il cestino
del sito. Eliminare sposta nel cestino di WooCommerce (lo stesso di Prodotti → Cestino in
WordPress); da lì si ripristina o si elimina per sempre. WordPress svuota il cestino da solo
dopo i giorni impostati sul sito (EMPTY_TRASH_DAYS, di serie 30)."""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import quality, wc_rest
from ..db import get_db
from ..models import Job, JobItem, ProdottoCestinato, Site, User
from ..security import require_user
from ..store import audit
from .client import _catalog_key, _clean_price, _scrittura_sul_sito, _visible_site

router = APIRouter(prefix="/api/sites", tags=["magazzino"])

PER_PAGINA = (15, 20, 30, 50, 100)
STATI_RIPRISTINABILI = ("publish", "draft", "pending", "private")


def _data(testo: str) -> datetime | None:
    """Le date di WooCommerce arrivano senza fuso (ora del sito): bastano per il giorno."""
    try:
        return datetime.fromisoformat((testo or "")[:19])
    except ValueError:
        return None


def _riga(p: dict, giorni_cestino: int) -> dict:
    immagini = p.get("images") or []
    stock = p.get("stock_quantity") if p.get("manage_stock") else None
    riga = {
        "id": p.get("id"),
        "nome": p.get("name") or "",
        "sku": p.get("sku") or "",
        "ean": p.get("global_unique_id") or "",
        "categorie": [{"id": c.get("id"), "nome": wc_rest.html.unescape(c.get("name") or "")}
                      for c in (p.get("categories") or [])],
        "prezzo": p.get("price") or "",
        "prezzo_pieno": p.get("regular_price") or "",
        "scontato": p.get("sale_price") or "",
        "stato": p.get("status") or "",
        "disponibilita": p.get("stock_status") or "",
        "quantita": stock,
        # La miniatura se WooCommerce la dà (pesa poco), altrimenti l'immagine intera.
        "immagine": ((immagini[0] or {}).get("thumbnail") or (immagini[0] or {}).get("src", "")) if immagini else "",
        "link": p.get("permalink") or "",
        "tipo": p.get("type") or "simple",
    }
    if riga["stato"] == "trash":
        dal = _data(p.get("date_modified") or "")
        riga["nel_cestino_dal"] = dal.date().isoformat() if dal else ""
        riga["si_svuota_il"] = (dal + timedelta(days=giorni_cestino)).date().isoformat() if dal else ""
    return riga


@router.get("/{site_id}/magazzino")
async def magazzino(
    site_id: int,
    categoria: int = 0,
    q: str = Query("", max_length=120),
    pagina: int = Query(1, ge=1),
    per_pagina: int = 20,
    cestino: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Una pagina di prodotti del sito, filtrati per categoria e testo (nome, SKU, EAN)."""
    site = _visible_site(db, site_id, user)
    per_pagina = per_pagina if per_pagina in PER_PAGINA else 20
    testo = " ".join(q.split())
    try:
        try:
            prodotti, totale, pagine = await wc_rest.prodotti_magazzino(
                site, categoria=categoria, testo=testo, pagina=pagina, per_pagina=per_pagina, cestino=cestino)
        except wc_rest.SiteError:
            if pagina == 1:
                raise
            # Pagina sparita (si è appena eliminato l'ultimo prodotto in fondo): si torna alla prima.
            pagina = 1
            prodotti, totale, pagine = await wc_rest.prodotti_magazzino(
                site, categoria=categoria, testo=testo, pagina=1, per_pagina=per_pagina, cestino=cestino)
        nel_cestino = totale if cestino and not (categoria or testo) else await wc_rest.quanti_nel_cestino(site)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return {
        "prodotti": [_riga(p, site.giorni_cestino or 30) for p in prodotti],
        "totale": totale,
        "pagine": max(1, pagine),
        "pagina": pagina,
        "per_pagina": per_pagina,
        "nel_cestino": nel_cestino,
        "giorni_cestino": site.giorni_cestino or 30,
    }


@router.get("/{site_id}/magazzino/categorie")
async def categorie_magazzino(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Tutte le categorie, «Senza categoria» compresa: anche quella può avere dei prodotti."""
    site = _visible_site(db, site_id, user)
    try:
        cats = await wc_rest.categorie(site)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return [
        {"id": c.get("id"), "nome": c.get("name") or "", "parent": c.get("parent") or 0,
         "menu_order": c.get("menu_order") or 0, "count": c.get("count") or 0, "riserva": wc_rest.e_riserva(c)}
        for c in cats
    ]


class ModificaProdotto(BaseModel):
    # None = non toccare. Le categorie sono l'elenco completo che il prodotto deve avere.
    categorie: list[int] | None = Field(default=None, max_length=20)
    prezzo: str | None = Field(default=None, max_length=24)
    scontato: str | None = Field(default=None, max_length=24)


async def _prodotto(site: Site, product_id: int) -> dict:
    try:
        return await wc_rest.get_product(site, product_id)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.delete("/{site_id}/magazzino/{product_id}")
async def sposta_nel_cestino(
    site_id: int, product_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Il prodotto va nel cestino del sito: sparisce dal negozio, ma si può ancora ripristinare."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    prodotto = await _prodotto(site, product_id)
    if prodotto.get("status") == "trash":
        raise HTTPException(status.HTTP_409_CONFLICT, "Il prodotto è già nel cestino.")
    try:
        await wc_rest.cestina_prodotto(site, product_id)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    nome = wc_rest.html.unescape(prodotto.get("name") or "")
    nota = db.scalar(select(ProdottoCestinato).where(
        ProdottoCestinato.site_id == site.id, ProdottoCestinato.product_id == product_id))
    if not nota:
        nota = ProdottoCestinato(site_id=site.id, product_id=product_id)
        db.add(nota)
    nota.stato_prima = prodotto.get("status") if prodotto.get("status") in STATI_RIPRISTINABILI else "publish"
    nota.nome, nota.da_chi, nota.quando = nome[:255], user.email, datetime.now(timezone.utc)
    db.commit()
    audit(db, user.email, "magazzino_cestino", f"{site.name} · {nome} (#{product_id})")
    return {"ok": True, "id": product_id, "nome": nome, "giorni_cestino": site.giorni_cestino or 30}


@router.patch("/{site_id}/magazzino/{product_id}")
async def modifica_prodotto(
    site_id: int, product_id: int, data: ModificaProdotto, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Categoria e prezzi di un prodotto che è già sul sito. Anche le righe del portale
    collegate a quel prodotto si allineano, così una nuova pubblicazione non torna indietro."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    prodotto = await _prodotto(site, product_id)
    if prodotto.get("status") == "trash":
        raise HTTPException(status.HTTP_409_CONFLICT, "Il prodotto è nel cestino: prima ripristinalo.")
    corpo: dict = {}
    if data.categorie is not None:
        ids = list(dict.fromkeys(int(i) for i in data.categorie if i))
        if not ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Scegli almeno una categoria.")
        try:
            esistenti = {c.get("id") for c in await wc_rest.categorie(site)}
        except wc_rest.SiteError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
        if any(i not in esistenti for i in ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "La categoria non c'è più sul sito: rileggi le categorie.")
        corpo["categories"] = [{"id": i} for i in ids]
    if data.prezzo is not None or data.scontato is not None:
        if (prodotto.get("type") or "simple") == "variable":
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "È un prodotto con varianti: i prezzi stanno sulle varianti e si cambiano dal sito.")
        prezzo = _clean_price(data.prezzo) if data.prezzo is not None else (prodotto.get("regular_price") or "")
        scontato = _clean_price(data.scontato) if data.scontato is not None else (prodotto.get("sale_price") or "")
        if data.prezzo and not prezzo:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Prezzo non valido: scrivi un numero, per esempio 129,90.")
        if data.scontato and not scontato:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Prezzo scontato non valido: scrivi un numero, per esempio 99,90.")
        problema = quality.sale_price_issue({"regular_price": prezzo, "sale_price": scontato})
        if problema:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, problema)
        if data.prezzo is not None:
            corpo["regular_price"] = prezzo
        if data.scontato is not None:
            corpo["sale_price"] = scontato
    if not corpo:
        return {"ok": True, "prodotto": _riga(prodotto, site.giorni_cestino or 30), "cambiato": False}
    try:
        aggiornato, _ = await wc_rest._request(site, "PUT", f"/products/{product_id}", json_body=corpo)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    aggiornato = wc_rest._nome_in_chiaro(aggiornato or {})

    righe = db.scalars(select(JobItem).join(Job, Job.id == JobItem.job_id)
                       .where(Job.site_id == site.id, JobItem.wc_product_id == product_id)).all()
    for r in righe:
        if "categories" in corpo:
            r.wc_category_id = corpo["categories"][0]["id"]
        if "regular_price" in corpo:
            r.regular_price = corpo["regular_price"]
        if "sale_price" in corpo:
            r.sale_price = corpo["sale_price"]
    db.commit()
    nome = aggiornato.get("name") or wc_rest.html.unescape(prodotto.get("name") or "")
    audit(db, user.email, "magazzino_modifica", f"{site.name} · {nome} (#{product_id}) · {', '.join(sorted(corpo))}")
    return {"ok": True, "prodotto": _riga(aggiornato, site.giorni_cestino or 30), "cambiato": True}


@router.post("/{site_id}/magazzino/{product_id}/ripristina")
async def ripristina(site_id: int, product_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Fuori dal cestino, com'era prima: stato di prima (se l'ha cestinato il portale,
    altrimenti pubblicato) e indirizzo di prima, senza il «__trashed» che WordPress aggiunge."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    prodotto = await _prodotto(site, product_id)
    if prodotto.get("status") != "trash":
        raise HTTPException(status.HTTP_409_CONFLICT, "Il prodotto non è più nel cestino: rileggi l'elenco.")
    nota = db.scalar(select(ProdottoCestinato).where(
        ProdottoCestinato.site_id == site.id, ProdottoCestinato.product_id == product_id))
    stato = nota.stato_prima if nota and nota.stato_prima in STATI_RIPRISTINABILI else "publish"
    slug = re.sub(r"__trashed(-\d+)?$", "", prodotto.get("slug") or "")
    try:
        ripristinato = await wc_rest.ripristina_prodotto(site, product_id, stato, slug)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if nota:
        db.delete(nota)
        db.commit()
    nome = ripristinato.get("name") or wc_rest.html.unescape(prodotto.get("name") or "")
    audit(db, user.email, "magazzino_ripristina", f"{site.name} · {nome} (#{product_id})")
    return {"ok": True, "id": product_id, "nome": nome, "stato": stato}


def _dimentica(db: Session, site: Site, ids: set[int]) -> int:
    """Nel portale, i prodotti eliminati per sempre non sono più «sul sito»: le righe collegate
    (e le altre copie dello stesso codice non pubblicate) si tolgono dall'elenco Prodotti, così
    nessuno le ripubblica per sbaglio. Le righe in lavorazione non si toccano."""
    if not ids:
        return 0
    righe = db.scalars(select(JobItem).join(Job, Job.id == JobItem.job_id).where(Job.site_id == site.id)).all()
    chiavi = {_catalog_key(r) for r in righe if r.wc_product_id in ids}
    tolte = 0
    for r in righe:
        if r.status in ("pending", "fetching", "pushing", "to_delete", "deleted"):
            continue
        if r.wc_product_id in ids or (not r.wc_product_id and _catalog_key(r) in chiavi):
            r.status, r.wc_product_id, r.error = "deleted", None, "Eliminato per sempre dal Magazzino."
            tolte += 1
    db.query(ProdottoCestinato).filter(
        ProdottoCestinato.site_id == site.id, ProdottoCestinato.product_id.in_(list(ids))).delete(synchronize_session=False)
    db.commit()
    return tolte


@router.delete("/{site_id}/magazzino/{product_id}/per-sempre")
async def elimina_per_sempre(
    site_id: int, product_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Dal cestino, per sempre. Le immagini restano nella libreria media del sito."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    prodotto = await _prodotto(site, product_id)
    if prodotto.get("status") != "trash":
        raise HTTPException(status.HTTP_409_CONFLICT, "Si elimina per sempre solo dal cestino: prima spostalo lì.")
    try:
        await wc_rest.delete_product(site, product_id)
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    _dimentica(db, site, {product_id})
    nome = wc_rest.html.unescape(prodotto.get("name") or "")
    audit(db, user.email, "magazzino_elimina", f"{site.name} · {nome} (#{product_id})")
    return {"ok": True, "id": product_id, "nome": nome}


@router.post("/{site_id}/magazzino/cestino/svuota")
async def svuota_cestino(site_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Tutto il cestino del sito, per sempre (anche quello cestinato da WordPress)."""
    site = _visible_site(db, site_id, user)
    _scrittura_sul_sito(site)
    ids: list[int] = []
    try:
        pagina, pagine = 1, 1
        while pagina <= pagine and pagina <= 50:
            prodotti, _, pagine = await wc_rest.prodotti_magazzino(site, pagina=pagina, per_pagina=100, cestino=True)
            ids += [int(p["id"]) for p in prodotti if p.get("id")]
            pagina += 1
        eliminati = await wc_rest.elimina_prodotti(site, ids) if ids else 0
    except wc_rest.SiteError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    _dimentica(db, site, set(ids))
    audit(db, user.email, "magazzino_svuota", f"{site.name} · {eliminati} prodotti")
    return {"ok": True, "eliminati": eliminati}
