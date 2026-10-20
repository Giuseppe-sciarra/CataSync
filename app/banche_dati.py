"""Banche dati pubbliche e gratuite interrogate per EAN, senza chiavi né abbonamenti.

Danno l'identità del prodotto (nome, marca, codice produttore, foto): con quella la ricerca
sul web trova la pagina giusta anche quando i motori col solo EAN non trovano niente.
La descrizione la si prende solo se è nella lingua della scheda; il resto lo riempie il web.

- UPCitemdb (api.upcitemdb.com, piano «trial»): prodotti di ogni tipo, 100 richieste al
  giorno per indirizzo IP. Oltre, risponde 429 e si passa avanti senza segnare il buco.
- Open Food / Beauty / Pet Food / Products Facts: alimentari, cosmetici, cibo per animali e
  oggetti vari, senza limiti dichiarati.
"""
import asyncio
import logging
import time

import httpx

from . import lingue

log = logging.getLogger("catasync.banche_dati")

UPCITEMDB = "https://api.upcitemdb.com/prod/trial/lookup"
OPEN_FACTS = ("world.openfoodfacts.org", "world.openbeautyfacts.org",
              "world.openpetfoodfacts.org", "world.openproductsfacts.org")
TIMEOUT = 15.0
INTESTAZIONI = {"Accept": "application/json", "User-Agent": "CataSync (https://github.com/Giuseppe-sciarra/CataSync)"}

# UPCitemdb trial: anche 6 richieste al minuto. Una ogni 10 s le rispetta.
PAUSA = 10.0
_turno = asyncio.Lock()
_ultima = 0.0


class NonDisponibile(Exception):
    """La banca dati non ha risposto o ci limita: non è un «non c'è»."""


async def _upcitemdb(gtin: str, lang: str) -> dict | None:
    global _ultima
    async with _turno:
        attesa = PAUSA - (time.monotonic() - _ultima)
        if attesa > 0:
            await asyncio.sleep(attesa)
        _ultima = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(UPCITEMDB, params={"upc": gtin}, headers=INTESTAZIONI)
    if resp.status_code == 404:
        return None
    if resp.status_code == 429:
        raise NonDisponibile("UPCitemdb: limite giornaliero raggiunto")
    if resp.status_code >= 400:
        raise NonDisponibile(f"UPCitemdb: HTTP {resp.status_code}")
    voci = (resp.json() or {}).get("items") or []
    if not voci:
        return None
    v = voci[0]
    titolo = (v.get("title") or "").strip()
    if not titolo:
        return None
    return {
        "title": titolo[:200],
        "brand": (v.get("brand") or "").strip()[:120],
        "mpn": (v.get("model") or v.get("mpn") or "").strip()[:120],
        # Testi in inglese: valgono come descrizione solo per le schede in inglese.
        "description": (v.get("description") or "").strip() if lang == "EN" else "",
        "images": [u for u in (v.get("images") or []) if isinstance(u, str) and u.startswith("http")][:6],
        "source_ref": "upcitemdb",
    }


async def _open_facts(gtin: str, lang: str) -> dict | None:
    campi = "product_name,product_name_" + lang.lower() + ",brands,generic_name,generic_name_" + lang.lower() + ",image_url,image_front_url"
    giu = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for host in OPEN_FACTS:
            try:
                resp = await client.get(f"https://{host}/api/v2/product/{gtin}.json", params={"fields": campi},
                                        headers=INTESTAZIONI)
            except httpx.HTTPError as exc:
                log.info("%s non risponde (%s)", host, exc.__class__.__name__)
                giu += 1
                continue
            if resp.status_code != 200:
                continue
            p = (resp.json() or {}).get("product") or {}
            titolo = (p.get("product_name_" + lang.lower()) or p.get("product_name") or "").strip()
            if not titolo:
                continue
            marca = (p.get("brands") or "").split(",")[0].strip()
            return {
                "title": titolo[:200],
                "brand": marca[:120],
                "mpn": "",
                "description": (p.get("generic_name_" + lang.lower()) or "").strip(),
                "images": [u for u in (p.get("image_url"), p.get("image_front_url")) if u],
                "source_ref": host.split(".")[1],
            }
    if giu == len(OPEN_FACTS):
        raise NonDisponibile("Open Facts non rispondono")
    return None


async def scheda(gtin: str, lang: str) -> dict | None:
    """La prima banca dati che conosce il codice, o None. NonDisponibile se nessuna ha
    potuto rispondere (limiti, rete): in quel caso non si ricorda il buco."""
    lang = lingue.normalizza(lang)
    guasti = []
    for fonte in (_upcitemdb, _open_facts):
        try:
            trovato = await fonte(gtin, lang)
        except NonDisponibile as exc:
            guasti.append(str(exc))
            continue
        except (httpx.HTTPError, ValueError) as exc:
            guasti.append(f"{fonte.__name__.strip('_')}: {exc.__class__.__name__}")
            continue
        if trovato:
            trovato["gtin"] = gtin
            return trovato
    if len(guasti) == 2:
        raise NonDisponibile(" · ".join(guasti))
    return None
