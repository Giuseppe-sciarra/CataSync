"""Dialogo con WooCommerce tramite le sue REST API.

Nessun plugin da installare sul sito del cliente: bastano una chiave e un segreto
generati in WooCommerce → Impostazioni → Avanzate → API REST, con permesso di
lettura e scrittura. Si incollano nella scheda del sito e il portale fa tutto da lì.

Autenticazione: Basic su HTTPS. Diversi server Apache mangiano l'intestazione
Authorization, quindi al primo 401 si riprova con le chiavi nell'indirizzo, che è
la modalità che WooCommerce prevede proprio per quel caso.
"""
import asyncio
import html
import logging
from urllib.parse import urlparse

import httpx

from . import config
from .html_sicuro import scheda_sicura
from .security import decrypt

log = logging.getLogger("catasync.wc")

BASE = "/wp-json/wc/v3"
# Rotte del mu-plugin «CataSync – Menu delle categorie» (wordpress/catasync-category-menu.php):
# il prefisso «wc-» fa accettare a WooCommerce le stesse chiavi REST.
PLUGIN_MENU = "/wp-json/wc-catasync/v1"
PER_PAGE = 100
BATCH_MAX = 100   # tetto di WooCommerce per le chiamate /batch

# Plugin TD Product Specs: chiave e tetto di righe scritte.
TD_SPECS_META_KEY = "_td_product_specs"
TD_SPECS_MAX = 60


class SiteError(Exception):
    pass


class PluginMancante(SiteError):
    """Il sito risponde, ma il mu-plugin del menu delle categorie non c'è."""


def validate_base_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("L'indirizzo deve iniziare con http:// o https://")
    if not parsed.hostname:
        raise ValueError("Indirizzo del sito non valido.")
    if parsed.username or parsed.password:
        raise ValueError("Non mettere utente e password dentro l'indirizzo.")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _creds(site) -> tuple[str, str]:
    key, secret = decrypt(site.wc_key_enc), decrypt(site.wc_secret_enc)
    if not key or not secret:
        raise SiteError("Chiave REST mancante per questo sito.")
    return key, secret


async def _request(
    site, method: str, path: str, params: dict | None = None, json_body=None, base: str = BASE
) -> tuple:
    key, secret = _creds(site)
    url = site.base_url.rstrip("/") + base + path
    params = dict(params or {})
    headers = {"Accept": "application/json", "User-Agent": "TDSyncCatalog/2.0"}

    async def call(use_query: bool):
        auth = None if use_query else (key, secret)
        query = dict(params)
        if use_query:
            query["consumer_key"] = key
            query["consumer_secret"] = secret
        async with httpx.AsyncClient(timeout=config.SITE_TIMEOUT, follow_redirects=True) as client:
            return await client.request(method, url, params=query, json=json_body, auth=auth, headers=headers)

    try:
        resp = await call(False)
        if resp.status_code == 401:
            # Authorization mangiata dal server: si riprova con le chiavi nell'indirizzo.
            resp = await call(True)
    except httpx.RequestError as exc:
        raise SiteError(f"Sito non raggiungibile: {exc.__class__.__name__}")

    if resp.status_code == 401:
        raise SiteError("Chiavi REST rifiutate: controlla che abbiano permesso di lettura e scrittura.")
    if resp.status_code == 404:
        if base != BASE:
            raise PluginMancante("Sul sito manca il mu-plugin «CataSync – Menu delle categorie».")
        raise SiteError("Endpoint non trovato: WooCommerce non risponde o i permalink non sono attivi.")
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = resp.text[:200]
        if base != BASE:
            if resp.status_code == 403:
                # O la chiave è di un utente che non può toccare i menu, o un firewall o un plugin di
                # sicurezza blocca l'indirizzo prima di WordPress: da qui non si distingue.
                raise SiteError("Il sito ha rifiutato la richiesta al mu-plugin del menu (HTTP 403): la chiave REST "
                                "dev'essere di un amministratore o di un gestore del negozio, e nessun firewall o "
                                "plugin di sicurezza deve bloccare /wp-json/wc-catasync/.")
            if resp.status_code == 400 and detail:
                raise SiteError(detail)   # il plugin spiega già cosa non va, in italiano
        raise SiteError(f"HTTP {resp.status_code} dal sito. {detail}".strip())

    try:
        return resp.json(), resp.headers
    except ValueError:
        raise SiteError("Il sito non ha risposto in JSON: probabile output sporco di un altro plugin.")


async def _get(site, path: str, params: dict | None = None):
    data, _ = await _request(site, "GET", path, params=params)
    return data


async def _paged(site, path: str, params: dict | None = None, max_pages: int = 20) -> list:
    """Scorre le pagine fino a esaurimento o al tetto indicato."""
    out: list = []
    for page in range(1, max_pages + 1):
        query = dict(params or {})
        query.update({"per_page": PER_PAGE, "page": page})
        data, headers = await _request(site, "GET", path, params=query)
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        total_pages = int(headers.get("X-WP-TotalPages") or 0)
        if total_pages and page >= total_pages:
            break
        if len(data) < PER_PAGE:
            break
    return out


# ------------------------------------------------------------------- stato
async def ping(site) -> dict:
    """Verifica chiavi e permessi. Un solo prodotto basta a sapere se si legge."""
    data, headers = await _request(site, "GET", "/products", params={"per_page": 1})
    info = {
        "ok": True,
        "products": int(headers.get("X-WP-Total") or 0),
        "woocommerce": "?",
        "wordpress": "?",
        "has_brands": False,
    }
    try:
        status = await _get(site, "/system_status")
        info["woocommerce"] = (status.get("environment") or {}).get("version", "?")
        info["wordpress"] = (status.get("environment") or {}).get("wp_version", "?")
        # system_status richiede permessi di lettura elevati: se risponde, la chiave è buona.
    except SiteError:
        pass
    try:
        await _get(site, "/products/brands", {"per_page": 1})
        info["has_brands"] = True
    except SiteError:
        pass
    return info


# -------------------------------------------------------------- tassonomia
def _term_path(term: dict, by_id: dict) -> str:
    parts = [term.get("name", "")]
    parent = term.get("parent") or 0
    guard = 0
    while parent and guard < 6:
        ancestor = by_id.get(parent)
        if not ancestor:
            break
        parts.insert(0, ancestor.get("name", ""))
        parent = ancestor.get("parent") or 0
        guard += 1
    return " › ".join(p for p in parts if p)


def e_riserva(categoria: dict) -> bool:
    """«Senza categoria» non è una scelta: è il cestino di WooCommerce."""
    return (categoria.get("slug") or "").lower() in ("uncategorized", "senza-categoria")


def _nome_in_chiaro(categoria):
    """WordPress salva «&» come «&amp;» e WooCommerce lo rende così: nel portale serve il
    testo vero, sia per mostrarlo sia per confrontare i nomi."""
    if isinstance(categoria, dict) and isinstance(categoria.get("name"), str):
        categoria["name"] = html.unescape(categoria["name"])
    return categoria


async def categorie(site) -> list[dict]:
    """Le categorie del sito così come le dà WooCommerce, compresa «Senza categoria»."""
    cats = await _paged(site, "/products/categories", {"orderby": "name", "order": "asc"}, max_pages=20)
    return [_nome_in_chiaro(c) for c in cats]


async def taxonomy(site) -> dict:
    # Senza la categoria di riserva, così nessuno può metterci dentro i prodotti dal portale.
    cats = [c for c in await categorie(site) if not e_riserva(c)]
    by_id = {c["id"]: c for c in cats}
    categories = [
        {
            "id": c["id"],
            "name": _term_path(c, by_id),   # col percorso, per le tendine: «Caldaie › Condensazione»
            "nome": c.get("name", ""),       # il nome da solo, per l'albero delle categorie
            "slug": c.get("slug", ""),
            "parent": c.get("parent", 0),
            "menu_order": c.get("menu_order", 0) or 0,
            "count": c.get("count", 0),
            "image": (c.get("image") or {}).get("src", ""),
            "description": c.get("description", ""),
        }
        for c in cats
    ]

    attributes = []
    try:
        for a in await _get(site, "/products/attributes", {"per_page": PER_PAGE}) or []:
            attributes.append(
                {"id": a["id"], "name": a.get("name", ""), "slug": a.get("slug", ""), "taxonomy": "pa_" + a.get("slug", "")}
            )
    except SiteError:
        pass

    brands = []
    try:
        for b in await _paged(site, "/products/brands", max_pages=5):
            brands.append({"id": b["id"], "name": b.get("name", ""), "slug": b.get("slug", "")})
    except SiteError:
        pass

    return {"categories": categories, "attributes": attributes, "brands": brands}


# ------------------------------------------------------------------ categorie
async def create_category(
    site, name: str, parent: int = 0, description: str = "", image_url: str = "", menu_order: int | None = None
) -> dict:
    """Crea una categoria sul sito. `parent` la rende sottocategoria, `menu_order` la mette
    al suo posto fra le sorelle (è l'ordine di Prodotti → Categorie).

    L'immagine si passa come indirizzo: la scarica WooCommerce nella libreria media
    del cliente, esattamente come fa con le immagini dei prodotti."""
    body: dict = {"name": name.strip()}
    if parent:
        body["parent"] = int(parent)
    if menu_order is not None:
        body["menu_order"] = int(menu_order)
    if description:
        body["description"] = description
    if image_url.startswith("http"):
        body["image"] = {"src": image_url}
    created, _ = await _request(site, "POST", "/products/categories", json_body=body)
    return _nome_in_chiaro(created)


async def update_category(
    site,
    term_id: int,
    name: str | None = None,
    parent: int | None = None,
    description: str | None = None,
    image_url: str | None = None,
) -> dict:
    body: dict = {}
    if name is not None:
        body["name"] = name.strip()
    if parent is not None:
        body["parent"] = int(parent)
    if description is not None:
        body["description"] = description
    if image_url is not None:
        # Stringa vuota = togli l'immagine.
        body["image"] = {"src": image_url} if image_url.startswith("http") else {}
    if not body:
        return {}
    updated, _ = await _request(site, "PUT", f"/products/categories/{term_id}", json_body=body)
    return _nome_in_chiaro(updated)


async def get_category(site, term_id: int) -> dict:
    return _nome_in_chiaro(await _get(site, f"/products/categories/{term_id}"))


async def delete_category(site, term_id: int) -> dict:
    """WooCommerce cancella i termini solo con force. Il controllo su quante cose
    ci sono dentro lo fa il portale prima di arrivare qui."""
    data, _ = await _request(site, "DELETE", f"/products/categories/{term_id}", params={"force": "true"})
    return data


async def order_categories(site, ordini: list[tuple[int, int]]) -> int:
    """Scrive l'ordine delle categorie (menu_order, quello di Prodotti → Categorie) a lotti
    da cento. Ritorna quante ne ha cambiate; un errore anche su una sola si fa sentire."""
    cambiate = 0
    errori: list[str] = []
    for inizio in range(0, len(ordini), BATCH_MAX):
        lotto = [{"id": int(i), "menu_order": int(o)} for i, o in ordini[inizio:inizio + BATCH_MAX]]
        data, _ = await _request(site, "POST", "/products/categories/batch", json_body={"update": lotto})
        for voce in (data or {}).get("update", []) if isinstance(data, dict) else []:
            errore = voce.get("error") if isinstance(voce, dict) else None
            if errore:
                errori.append(str(errore.get("message") or errore) if isinstance(errore, dict) else str(errore))
            else:
                cambiate += 1
    if errori:
        raise SiteError("Ordine non salvato per tutte le categorie: " + "; ".join(errori[:3]))
    return cambiate


# ------------------------------------------------- menu delle categorie (mu-plugin)
async def menu_stato(site) -> dict:
    data, _ = await _request(site, "GET", "/menu", base=PLUGIN_MENU)
    return data if isinstance(data, dict) else {}


async def menu_salva(site, scelta: dict) -> dict:
    data, _ = await _request(site, "POST", "/menu", json_body=scelta, base=PLUGIN_MENU)
    return data if isinstance(data, dict) else {}


async def menu_allinea(site) -> dict:
    data, _ = await _request(site, "POST", "/menu/allinea", base=PLUGIN_MENU)
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ ricerca
async def find_by_gtin(site, gtin: str) -> dict | None:
    """Cerca per campo GTIN nativo, poi come ripiego per SKU."""
    if not gtin:
        return None
    try:
        rows = await _get(site, "/products", {"global_unique_id": gtin, "per_page": 1})
        if rows:
            return rows[0]
    except SiteError:
        pass  # WooCommerce sotto la 9.2 non conosce il parametro
    try:
        rows = await _get(site, "/products", {"sku": gtin, "per_page": 1})
        if rows:
            return rows[0]
    except SiteError:
        pass
    return None


async def lookup(site, gtin: str) -> dict:
    found = await find_by_gtin(site, gtin)
    if not found:
        return {"found": False}
    return {
        "found": True,
        "product_id": found["id"],
        "title": found.get("name", ""),
        "sku": found.get("sku", ""),
        "status": found.get("status", ""),
        "permalink": found.get("permalink", ""),
    }


async def ensure_brand(site, name: str) -> int | None:
    if not name:
        return None
    try:
        rows = await _get(site, "/products/brands", {"search": name, "per_page": 10})
        for row in rows or []:
            if row.get("name", "").strip().lower() == name.strip().lower():
                return row["id"]
        created, _ = await _request(site, "POST", "/products/brands", json_body={"name": name})
        return created.get("id")
    except SiteError:
        return None


# -------------------------------------------------------------- scrittura
def _may_write(policy: dict, field: str, is_new: bool, current) -> bool:
    mode = policy.get(field, "fill")
    if mode == "skip":
        return False
    if mode == "overwrite":
        return True
    if is_new:
        return True
    if isinstance(current, (list, dict)):
        return not current
    return not str(current or "").strip()


def taglia_specifiche(rows: list, limite: int) -> list:
    """Sceglie le caratteristiche che contano davvero, non le prime a caso.

    Le fonti marcano «chiave» (`key`) quelle su cui si costruiscono i filtri di ricerca: per un
    televisore sono diagonale, risoluzione, Smart TV, Wi-Fi. Sono le stesse su cui
    ragiona chi compra. Prendendo invece le prime nell'ordine grezzo si finisce con
    voci come «Regolazioni formato schermo: 16:9, Auto, Zoom», che non dicono niente.

    Quindi: prima quelle da confronto, poi si riempie coi restanti nell'ordine
    originale, che tiene comunque insieme le voci dello stesso gruppo."""
    if not limite or limite <= 0:
        return rows
    principali = [r for r in rows if r.get("key")]
    resto = [r for r in rows if not r.get("key")]
    return (principali + resto)[:limite]


def _build_attributes(rows: list, site_attributes: list) -> list:
    """Attributo globale se sul sito esiste già con quel nome, altrimenti locale.

    Una scheda può avere 150 caratteristiche: creare 150 tassonomie affossa il sito."""
    by_name = {a["name"].strip().lower(): a for a in site_attributes}
    out, position = [], 0
    for row in rows[:60]:
        name = str(row.get("name") or "").strip()
        value = str(row.get("value") or "").strip()
        if not name or not value:
            continue
        existing = by_name.get(name.lower())
        out.append(
            {
                "id": existing["id"] if existing else 0,
                "name": existing["name"] if existing else name,
                "position": position,
                "visible": True,
                "variation": False,
                "options": [value],
            }
        )
        position += 1
    return out


async def push_product(site, product: dict, options: dict) -> dict:
    """Crea o aggiorna il prodotto. Le politiche si applicano qui: per i campi
    'solo se vuoto' si guarda com'è messo il prodotto sul sito prima di scrivere."""
    policy = options.get("write_policy") or {}
    warnings: list[str] = []
    # Anche le schede salvate prima della pulizia (o scritte a mano) passano di
    # qui: sul sito del cliente non arriva mai HTML attivo.
    product = scheda_sicura(dict(product))
    gtin = "".join(ch for ch in str(product.get("gtin") or "") if ch.isdigit())
    # Un codice produttore di 8 o 13 cifre sembra un EAN ma non lo è: sul
    # prodotto va solo un GTIN con la cifra di controllo giusta.
    from . import codici
    if gtin and not codici.checksum_gtin(gtin):
        gtin = ""

    existing = await find_by_gtin(site, gtin) if gtin else None
    # Niente EAN (prodotto cercato per codice produttore)? Si riconosce il
    # prodotto già esistente dallo SKU, così un ricaricamento aggiorna invece
    # di duplicare.
    if existing is None and product.get("mpn"):
        try:
            rows = await _get(site, "/products", {"sku": str(product["mpn"]), "per_page": 1})
            existing = rows[0] if rows else None
        except SiteError:
            existing = None
    is_new = existing is None
    payload: dict = {}

    if is_new and not product.get("title"):
        raise SiteError("Prodotto nuovo senza nome: non lo creo.")

    def current(field, default=""):
        return (existing or {}).get(field, default)

    if product.get("title") and _may_write(policy, "title", is_new, current("name")):
        payload["name"] = product["title"]
    if product.get("description") and _may_write(policy, "description", is_new, current("description")):
        payload["description"] = product["description"]
    if product.get("short_description") and _may_write(policy, "short_description", is_new, current("short_description")):
        payload["short_description"] = product["short_description"]

    if product.get("mpn") and not current("sku"):
        payload["sku"] = product["mpn"]
    if gtin and _may_write(policy, "gtin", is_new, current("global_unique_id")):
        payload["global_unique_id"] = gtin

    # Prezzi: li scrive il cliente nel portale, mai le fonti dati.
    regular = str(product.get("regular_price") or "").strip()
    sale = str(product.get("sale_price") or "").strip()
    if regular and _may_write(policy, "prices", is_new, current("regular_price")):
        payload["regular_price"] = regular
        if sale:
            try:
                if float(sale) < float(regular):
                    payload["sale_price"] = sale
                else:
                    payload["sale_price"] = ""
                    warnings.append("Prezzo scontato non inferiore a quello pieno: non applicato.")
            except ValueError:
                payload["sale_price"] = ""
        else:
            payload["sale_price"] = ""
    elif is_new and not regular:
        warnings.append("Prodotto creato senza prezzo: resta non acquistabile finché non lo imposti.")

    categories = [int(c) for c in (options.get("categories") or []) if c]
    if categories and _may_write(policy, "categories", is_new, current("categories", [])):
        payload["categories"] = [{"id": c} for c in categories]

    if product.get("images") and _may_write(policy, "images", is_new, current("images", [])):
        # WooCommerce scarica le immagini da sé: si passano gli indirizzi e basta.
        payload["images"] = [
            {"src": url, "alt": product.get("title", "")} for url in product["images"][:8] if str(url).startswith("http")
        ]

    scrivi_specifiche = product.get("attributes") and _may_write(
        policy, "attributes", is_new, current("attributes", [])
    )
    specifiche = taglia_specifiche(product.get("attributes") or [], options.get("specs_limit", 6))
    if scrivi_specifiche:
        site_attributes = options.get("site_attributes") or []
        payload["attributes"] = _build_attributes(specifiche, site_attributes)

    if product.get("brand") and _may_write(policy, "brand", is_new, current("brands", [])):
        brand_id = await ensure_brand(site, product["brand"])
        if brand_id:
            payload["brands"] = [{"id": brand_id}]
        else:
            warnings.append("Marca non assegnata: il sito non ha la funzione Marche attiva.")

    meta = [
        {"key": "_tdic_gtin", "value": gtin},
        {"key": "_tdic_synced", "value": options.get("now", "")},
    ]

    # TD Product Specs: stesso elenco di caratteristiche, nel formato che il plugin
    # legge per lo shortcode [td_specifiche] e per i campi YOOtheme. Se il plugin non
    # c'è, resta un meta inutilizzato e non fa danno.
    if scrivi_specifiche and options.get("specs_meta", True):
        righe = [
            {"label": a.get("name", ""), "value": a.get("value", "")}
            for a in specifiche[:TD_SPECS_MAX]
            if a.get("name") and a.get("value")
        ]
        if righe:
            meta.append({"key": TD_SPECS_META_KEY, "value": righe})
    if product.get("mpn"):
        meta.append({"key": "_tdic_mpn", "value": product["mpn"]})
    if product.get("source_ref"):
        meta.append({"key": "_tdic_source_id", "value": str(product["source_ref"])})
    if product.get("source"):
        meta.append({"key": "_tdic_source", "value": str(product["source"])})
    payload["meta_data"] = meta

    if is_new:
        payload["type"] = "simple"
        payload["status"] = options.get("status", "publish")
        payload["catalog_visibility"] = "visible"

    if is_new:
        created, _ = await _request(site, "POST", "/products", json_body=payload)
        return {
            "product_id": created.get("id"),
            "action": "created",
            "permalink": created.get("permalink", ""),
            "warnings": warnings,
        }

    if not payload or list(payload.keys()) == ["meta_data"]:
        return {
            "product_id": existing["id"],
            "action": "updated",
            "permalink": existing.get("permalink", ""),
            "warnings": warnings + ["Niente da aggiornare con le politiche impostate."],
        }

    updated, _ = await _request(site, "PUT", f"/products/{existing['id']}", json_body=payload)
    return {
        "product_id": updated.get("id"),
        "action": "updated",
        "permalink": updated.get("permalink", ""),
        "warnings": warnings,
    }


# --------------------------------------------------------------- catalogo
async def scan_catalog(site, max_pages: int = 30) -> list[dict]:
    """Prodotti del sito che hanno un EAN, con la data dell'ultimo passaggio del portale.

    WooCommerce non permette di filtrare per meta dalle REST, quindi si sfoglia. Serve
    solo per i prodotti che il portale non ha mai toccato, tipo quelli caricati dal
    gestionale: per gli altri il portale sa già tutto dai propri dati."""
    rows = await _paged(site, "/products", {"orderby": "id", "order": "asc", "status": "any"}, max_pages=max_pages)
    out = []
    for row in rows:
        gtin = "".join(ch for ch in str(row.get("global_unique_id") or "") if ch.isdigit())
        synced = ""
        for meta in row.get("meta_data") or []:
            if meta.get("key") == "_tdic_gtin" and not gtin:
                gtin = "".join(ch for ch in str(meta.get("value") or "") if ch.isdigit())
            if meta.get("key") == "_tdic_synced":
                synced = str(meta.get("value") or "")
        if len(gtin) in (8, 12, 13, 14):
            out.append({"product_id": row["id"], "gtin": gtin, "title": row.get("name", ""), "synced_at": synced})
    return out


# ------------------------------------------------------------ eliminazione
async def get_product(site, product_id: int) -> dict:
    return await _get(site, f"/products/{product_id}")


async def delete_product(site, product_id: int) -> dict:
    """Elimina il prodotto. force=true perché senza finirebbe nel cestino."""
    data, _ = await _request(site, "DELETE", f"/products/{product_id}", params={"force": "true"})
    return data


async def delete_media(site, media_ids: list[int]) -> tuple[int, str]:
    """Elimina gli allegati dalla libreria media.

    Le chiavi WooCommerce qui non valgono: /wp/v2 è del core WordPress e la
    loro autenticazione copre solo le rotte wc/. Servono utente e Password per
    applicazioni. Senza, si restituisce il motivo e le immagini restano dove sono.
    """
    utente = decrypt(site.wp_user_enc)
    password = decrypt(site.wp_app_password_enc)
    if not (utente and password):
        return 0, "credenziali WordPress non impostate"
    if not media_ids:
        return 0, ""

    base = site.base_url.rstrip("/") + "/wp-json/wp/v2/media/"
    eliminate, errore = 0, ""
    async with httpx.AsyncClient(timeout=config.SITE_TIMEOUT, follow_redirects=True) as client:
        for mid in media_ids[:40]:
            try:
                resp = await client.delete(
                    f"{base}{mid}", params={"force": "true"}, auth=(utente, password)
                )
                if resp.status_code < 300:
                    eliminate += 1
                elif resp.status_code in (401, 403) and not errore:
                    errore = "credenziali WordPress rifiutate"
                elif resp.status_code == 404:
                    eliminate += 1  # già sparita, va bene così
            except httpx.RequestError:
                if not errore:
                    errore = "sito non raggiungibile"
    return eliminate, errore
