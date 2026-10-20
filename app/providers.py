"""Catena delle fonti dati.

Ogni cliente ha una lista ordinata di fonti: la **ricerca sul web** (trova da sola
la pagina del prodotto partendo dal codice, su qualunque marca), i **connettori
JSON** verso i servizi dei suoi fornitori (il portale di un distributore, un PIM,
il gestionale) e la **compilazione a mano**. Si prova la prima e, se la scheda è
parziale, le successive la completano: una risposta senza foto o senza
descrizione non deve impedire alle altre fonti di arricchirla.

Un cliente senza fonti configurate usa comunque la ricerca sul web: è sempre
disponibile e non chiede credenziali.

Le fonti `http_json` non hanno codice dedicato: l'admin imposta URL, autenticazione e
una mappa che dice dove stanno i campi dentro la risposta. Così si attacca un servizio
nuovo senza toccare il portale.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import codici, config, lingue, web_search, web_source
from .html_sicuro import scheda_sicura
from .models import Client, EanCache, Provider
from .security import decrypt
from .store import icecat_config, ricerca_config

log = logging.getLogger("catasync.providers")

KINDS = ("web", "http_json", "manual")

# Presconfigurazioni pronte: l'admin sceglie la voce e riempie solo quello che serve.
PRESET = {
    "web": {
        "name": "Ricerca sul web",
        "kind": "web",
        "priority": 20,
        "descrizione": "Cerca da sola la pagina del prodotto partendo dall'EAN o dal codice "
                       "produttore e ne legge i dati. Nessuna marca da configurare: funziona "
                       "anche su produttori mai visti prima. Il motore si imposta una volta nel "
                       "pannello Impostazioni.",
        "campi": [],
        "endpoint": "",
    },
    "altro": {
        "name": "Altro connettore",
        "kind": "http_json",
        "priority": 30,
        "descrizione": "Qualunque servizio che risponda in JSON: il portale di un "
                       "distributore, un PIM, il gestionale. Si imposta a mano indirizzo, "
                       "autenticazione e mappa dei campi.",
        "campi": ["username", "secret", "endpoint"],
        "endpoint": "",
        "auth_mode": "basic",
        "root_path": "",
        "field_map": {
            "title": "descrizione",
            "brand": "marca.nome",
            "mpn": "codice",
            "description": "testo",
            "category": "categoria.descrizione",
            "images": {"path": "media.immagini[]", "url": "url"},
            "attributes": {"path": "caratteristiche[]", "name": "nome", "value": "valore"},
        },
    },
}


class NoSourceFound(Exception):
    """Nessuna fonte conosce questo codice."""

    def __init__(self, messaggio: str, diagnosi: dict | None = None):
        super().__init__(messaggio)
        self.diagnosi = diagnosi


class FonteErrore(Exception):
    """Una fonte ha risposto male (credenziali, rete, formato): si riprova."""

    def __init__(self, messaggio: str, diagnosi: dict | None = None):
        super().__init__(messaggio)
        self.diagnosi = diagnosi


class FonteNonTrovata(Exception):
    """La fonte ha risposto: questo codice non ce l'ha."""


class RicercaRimandata(Exception):
    """I motori di ricerca ci stanno bloccando: il codice non è stato cercato
    davvero. Il worker rimette la riga in coda più tardi invece di chiuderla."""

    def __init__(self, messaggio: str, diagnosi: dict | None = None, riprova_alle: float | None = None):
        super().__init__(messaggio)
        self.diagnosi = diagnosi
        # Motori tutti in pausa e nessuna domanda fatta: si riprende a quell'ora (epoch).
        self.riprova_alle = riprova_alle


def chain(db: Session, client: Client) -> list[Provider]:
    """Fonti attive del cliente, in ordine. Senza fonti: la ricerca sul web."""
    rows = db.scalars(
        select(Provider)
        .where(Provider.client_id == client.id, Provider.is_active.is_(True))
        .order_by(Provider.priority, Provider.id)
    ).all()
    if rows:
        return list(rows)
    predefinita = Provider(client_id=client.id, name="Ricerca sul web", kind="web", priority=20,
                           is_active=True, lang="")
    predefinita.id = 0  # non salvata: esiste solo per questa chiamata
    return [predefinita]


# --------------------------------------------------------------- lettura JSON
def dig(data: Any, path: str) -> Any:
    """Percorso con punti. `campo[]` scende dentro una lista e restituisce la lista."""
    if not path:
        return None
    node = data
    for part in path.split("."):
        wants_list = part.endswith("[]")
        key = part[:-2] if wants_list else part
        if key:
            if isinstance(node, dict):
                node = node.get(key)
            elif isinstance(node, list) and key.isdigit():
                idx = int(key)
                node = node[idx] if idx < len(node) else None
            else:
                return None
        if node is None:
            return None
        if wants_list and isinstance(node, dict):
            node = list(node.values())
    return node


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("value", "Value", "text", "name", "Name"):
            if key in value:
                return _text(value[key])
    if isinstance(value, list) and value:
        return _text(value[0])
    return ""


def map_generic(payload: dict, gtin: str, field_map: dict, root_path: str = "") -> dict:
    """Applica la mappa dell'admin alla risposta della fonte."""
    root = dig(payload, root_path) if root_path else payload
    if not isinstance(root, (dict, list)):
        root = payload

    def one(name: str) -> str:
        return _text(dig(root, field_map.get(name, "")))

    images: list[str] = []
    img_spec = field_map.get("images")
    if isinstance(img_spec, dict):
        rows = dig(root, img_spec.get("path", "")) or []
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            url = _text(dig(row, img_spec.get("url", "")) if isinstance(row, dict) else row)
            if url.startswith("http"):
                images.append(url)
    elif isinstance(img_spec, str) and img_spec:
        node = dig(root, img_spec)
        node = node if isinstance(node, list) else [node]
        images = [_text(x) for x in node if _text(x).startswith("http")]

    attributes: list[dict] = []
    attr_spec = field_map.get("attributes")
    if isinstance(attr_spec, dict):
        rows = dig(root, attr_spec.get("path", "")) or []
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _text(dig(row, attr_spec.get("name", "")))
            value = _text(dig(row, attr_spec.get("value", "")))
            unit = _text(dig(row, attr_spec.get("unit", ""))) if attr_spec.get("unit") else ""
            if name and value:
                attributes.append({"group": "", "name": name, "value": (value + " " + unit).strip()})

    documents: list[dict] = []
    doc_spec = field_map.get("documents")
    if isinstance(doc_spec, dict):
        rows = dig(root, doc_spec.get("path", "")) or []
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            url = _text(dig(row, doc_spec.get("url", "")) if isinstance(row, dict) else row)
            if url.startswith("http"):
                documents.append({
                    "url": url,
                    "type": _text(dig(row, doc_spec.get("type", ""))) if isinstance(row, dict) else "",
                    "label": _text(dig(row, doc_spec.get("label", ""))) if isinstance(row, dict) else "",
                })

    return {
        "gtin": gtin,
        "source_ref": one("external_id"),
        "title": one("title"),
        "brand": one("brand"),
        "mpn": one("mpn"),
        "category": {"id": one("category_id"), "name": one("category")},
        "description": one("description"),
        "short_description": one("short_description"),
        "bullets": [],
        "images": images,
        "attributes": attributes,
        "documents": documents,
        # I prezzi non si prendono mai dalle fonti: quello che c'è nei loro listini è
        # un prezzo d'acquisto o consigliato, non quello a cui vende il cliente.
        "regular_price": "",
        "sale_price": "",
        "warnings": [],
    }


# ------------------------------------------------------------------ chiamate
async def _fetch_http_json(provider: Provider, gtin: str, lang: str) -> dict:
    endpoint = (provider.endpoint or "").replace("{gtin}", gtin).replace("{lang}", lang)
    if not endpoint.startswith("http"):
        raise ValueError("Endpoint della fonte non valido.")

    params: dict[str, str] = {}
    try:
        params.update(json.loads(provider.extra_params or "{}"))
    except ValueError:
        pass
    params = {k: str(v).replace("{gtin}", gtin).replace("{lang}", lang) for k, v in params.items()}

    headers = {"Accept": "application/json", "User-Agent": "TDSyncCatalog/2.0"}
    auth = None
    username = decrypt(provider.username_enc)
    secret = decrypt(provider.secret_enc)

    mode = provider.auth_mode or "none"
    if mode == "header" and provider.auth_name:
        headers[provider.auth_name] = secret
    elif mode == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif mode == "query" and provider.auth_name:
        params[provider.auth_name] = secret
    elif mode == "basic":
        auth = (username, secret)

    async with httpx.AsyncClient(timeout=config.FONTE_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(endpoint, params=params, headers=headers, auth=auth)

    if resp.status_code == 404:
        raise FonteNonTrovata(f"{provider.name}: codice non presente.")
    if resp.status_code in (401, 403):
        raise FonteErrore(f"{provider.name}: credenziali rifiutate o livello di servizio insufficiente.")
    if resp.status_code >= 400:
        raise FonteErrore(f"{provider.name}: HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError:
        raise FonteErrore(f"{provider.name}: la risposta non è JSON.")


# La ricerca sul web coi motori a consumo si paga a chiamata, quindi un codice si
# cerca una volta sola: il risultato resta in cache, per lingua.
CACHE_WEB = "_WEB"
CACHE_WEB_VECCHIA = "_WEB2"   # schede italiane del motore precedente: valgono ancora


def chiave_cache(lang: str) -> str:
    return f"{CACHE_WEB}:{lingue.normalizza(lang)}"


def extra_ricerca(cfg: dict) -> dict:
    """Parametri extra per la ricerca: SearXNG, LibreTranslate, browser."""
    return {"cx": cfg.get("cx", ""), "url": cfg.get("url", ""),
            "lt_url": cfg.get("lt_url", ""), "lt_chiave": cfg.get("lt_chiave", ""),
            "render_url": cfg.get("render_url", ""), "motori": cfg.get("motori", ""),
            "giro_codici": cfg.get("giro_codici", 5), "giro_pausa": cfg.get("giro_pausa", 2.0),
            "riserva_chiave": cfg.get("riserva_chiave", ""), "cataloghi_forza": bool(cfg.get("cataloghi_forza"))}


# Con questi motori la ricerca non costa niente: la cache negativa non ha motivo
# di esistere e si ricerca sempre davvero. Serve solo coi motori a consumo.
MOTORI_GRATUITI = ("searxng", "duckduckgo")
GIORNI_SCHEDA_POVERA = 2


def _riga_cache(db: Session, gtin: str, chiave: str) -> EanCache | None:
    return db.scalar(select(EanCache).where(EanCache.gtin == gtin, EanCache.lang == chiave))


def _fresca(riga: EanCache, giorni: float) -> bool:
    if giorni <= 0:      # CACHE_DAYS=0: l'archivio non scade
        return True
    quando = riga.fetched_at
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - quando < timedelta(days=giorni)


async def _web_con_cache(db: Session, gtin: str, lang: str, mpn: str = "") -> dict:
    cfg = ricerca_config(db)
    gratuito = cfg["motore"] in MOTORI_GRATUITI
    chiave = chiave_cache(lang)

    riga = _riga_cache(db, gtin, chiave)
    if riga:
        if riga.found:
            dati = json.loads(riga.payload)
            # Una scheda povera (niente descrizione, una foto) si tiene poco: magari quel
            # giorno i motori bloccavano la ricerca col codice produttore che l'avrebbe completata.
            if _fresca(riga, GIORNI_SCHEDA_POVERA if dati.pop("_povera", False) else config.CACHE_GIORNI):
                return dati
        # Il buco vale poco: il web cambia. Coi motori gratuiti non vale niente.
        if not riga.found and not gratuito and _fresca(riga, 0.5):
            try:
                diag = json.loads(riga.payload or "{}").get("diagnosi")
            except ValueError:
                diag = None
            err = web_source.NonTrovato("già cercato di recente senza esito")
            err.diagnosi = diag
            raise err
    elif lingue.normalizza(lang) == "IT":
        vecchia = _riga_cache(db, gtin, CACHE_WEB_VECCHIA)
        if vecchia and vecchia.found and _fresca(vecchia, config.CACHE_GIORNI):
            return json.loads(vecchia.payload)

    try:
        prodotto = await web_source.cerca_automatica(gtin, cfg["motore"], cfg["chiave"], extra_ricerca(cfg),
                                                     mpn=mpn, lingua=lang)
    except web_source.NonTrovato as exc:
        # Si ricorda anche il buco, col suo perché: senza, ogni riprova coi
        # motori a pagamento ripagherebbe la stessa ricerca a vuoto.
        salva_cache_web(db, gtin, lang, json.dumps({"diagnosi": getattr(exc, "diagnosi", None)},
                                                   ensure_ascii=False), False)
        raise

    povera = not (prodotto.get("description") or "").strip() or len(prodotto.get("images") or []) < 2
    salva_cache_web(db, gtin, lang, json.dumps({**prodotto, "_povera": True} if povera else prodotto,
                                               ensure_ascii=False), True)
    return prodotto


def salva_cache_web(db: Session, gtin: str, lang: str, payload: str, trovato: bool, chiave: str = "") -> None:
    chiave = chiave or chiave_cache(lang)
    riga = _riga_cache(db, gtin, chiave)
    if riga:
        riga.payload = payload
        riga.found = trovato
        riga.fetched_at = datetime.now(timezone.utc)
    else:
        db.add(EanCache(gtin=gtin, lang=chiave, found=trovato, payload=payload))
    db.commit()


def svuota_cache_web(db: Session, gtins: list[str], anche_buone: bool = True) -> int:
    """Butta la cache web (tutte le lingue) di questi codici: la prossima ricerca riparte da zero.
    Con anche_buone=False restano le schede complete: l'archivio serve a tutti i clienti."""
    if not gtins:
        return 0
    righe = db.scalars(select(EanCache).where(EanCache.gtin.in_(gtins), EanCache.lang.like(CACHE_WEB + "%"))).all()
    tolte = 0
    for riga in righe:
        if not anche_buone and riga.found and '"_povera"' not in (riga.payload or ""):
            continue
        db.delete(riga)
        tolte += 1
    return tolte


def _ha_contenuto_base(product: dict) -> bool:
    """La scheda ha i tre pezzi che una fonte dovrebbe davvero procurarci?"""
    return bool(
        str(product.get("title") or "").strip()
        and str(product.get("description") or "").strip()
        and (product.get("images") or [])
    )


def _fondi_fonti(base: dict, extra: dict) -> dict:
    """Arricchisce la fonte prioritaria con i buchi coperti da quella successiva."""
    for campo in ("title", "brand", "mpn", "description", "short_description", "source_ref", "gtin"):
        if not base.get(campo) and extra.get(campo):
            base[campo] = extra[campo]

    cat_base = base.get("category") or {}
    cat_extra = extra.get("category") or {}
    if not cat_base.get("id") and cat_extra.get("id"):
        cat_base["id"] = cat_extra.get("id")
    if not cat_base.get("name") and cat_extra.get("name"):
        cat_base["name"] = cat_extra.get("name")
    base["category"] = cat_base

    immagini = []
    for u in list(base.get("images") or []) + list(extra.get("images") or []):
        if u and u not in immagini:
            immagini.append(u)
    base["images"] = immagini[:8]

    attrs = list(base.get("attributes") or [])
    viste = {(str(a.get("name") or "").strip().lower(), str(a.get("value") or "").strip().lower()) for a in attrs}
    for a in extra.get("attributes") or []:
        key = (str(a.get("name") or "").strip().lower(), str(a.get("value") or "").strip().lower())
        if key != ("", "") and key not in viste:
            viste.add(key)
            attrs.append(a)
    base["attributes"] = attrs[:80]

    docs = list(base.get("documents") or [])
    url_docs = {d.get("url") for d in docs if isinstance(d, dict)}
    for d in extra.get("documents") or []:
        if isinstance(d, dict) and d.get("url") and d.get("url") not in url_docs:
            url_docs.add(d.get("url"))
            docs.append(d)
    base["documents"] = docs[:20]

    warnings = list(base.get("warnings") or [])
    for w in extra.get("warnings") or []:
        if w and w not in warnings:
            warnings.append(w)
    base["warnings"] = warnings
    return base


# ------------------------------------------------------------------- Icecat
# Prima fonte per tutti i clienti, con l'utenza del portale. La cache sta accanto a quella
# web («_WEBI:IT»): «Riprova dati» la svuota insieme all'altra.
CACHE_ICECAT = "_WEBI"


def chiave_icecat(lang: str) -> str:
    return f"{CACHE_ICECAT}:{lingue.normalizza(lang)}"


async def _icecat_con_cache(db: Session, gtin: str, lang: str) -> dict:
    """La scheda Icecat (tradotta se serve). FonteNonTrovata se Icecat non ce l'ha."""
    from . import icecat

    chiave = chiave_icecat(lang)
    riga = _riga_cache(db, gtin, chiave)
    if riga:
        if riga.found and _fresca(riga, config.CACHE_GIORNI):
            return json.loads(riga.payload)
        if not riga.found and _fresca(riga, config.ICECAT_GIORNI_ASSENTE):
            try:
                motivo = json.loads(riga.payload or "{}").get("motivo") or ""
            except ValueError:
                motivo = ""
            raise FonteNonTrovata(motivo or "non c'è su Icecat")
    rc = ricerca_config(db)
    try:
        prodotto = await icecat.scheda(gtin, lang, icecat_config(db), {"url": rc.get("lt_url", ""),
                                                                         "chiave": rc.get("lt_chiave", "")})
    except icecat.IcecatAssente as exc:
        salva_cache_web(db, gtin, lang, json.dumps({"motivo": str(exc)}, ensure_ascii=False), False, chiave)
        raise FonteNonTrovata(str(exc)) from exc
    except icecat.IcecatErrore as exc:
        raise FonteErrore(str(exc)) from exc
    salva_cache_web(db, gtin, lang, json.dumps(prodotto, ensure_ascii=False), True, chiave)
    return prodotto


CACHE_ICECAT_CODICE = "_WEBJ"     # Icecat per marca + codice produttore, sullo stesso EAN


async def _icecat_per_codice(db: Session, gtin: str, marca: str, codice: str, lang: str) -> dict:
    """Icecat non ha l'EAN, ma magari ha il prodotto: lo si chiede per marca e codice produttore
    (quelli trovati dalle pagine che riportano l'EAN). «Samsung Electronics» si prova anche come
    «Samsung», il nome che usa Icecat."""
    from . import icecat

    chiave = f"{CACHE_ICECAT_CODICE}:{lingue.normalizza(lang)}"
    riga = _riga_cache(db, gtin, chiave)
    if riga:
        if riga.found and _fresca(riga, config.CACHE_GIORNI):
            return json.loads(riga.payload)
        if not riga.found and _fresca(riga, config.ICECAT_GIORNI_ASSENTE):
            raise FonteNonTrovata(icecat.MANCA_CODICE)
    rc = ricerca_config(db)
    trad = {"url": rc.get("lt_url", ""), "chiave": rc.get("lt_chiave", "")}
    marche = [marca.strip()]
    if len(marca.split()) > 1:
        marche.append(marca.split()[0])
    for nome in marche:
        try:
            prodotto = await icecat.scheda(gtin, lang, icecat_config(db), trad, marca=nome, codice=codice.strip())
        except icecat.IcecatAssente:
            continue
        except icecat.IcecatErrore as exc:
            raise FonteErrore(str(exc)) from exc
        prodotto["gtin"] = gtin
        salva_cache_web(db, gtin, lang, json.dumps(prodotto, ensure_ascii=False), True, chiave)
        return prodotto
    salva_cache_web(db, gtin, lang, json.dumps({"motivo": icecat.MANCA_CODICE}, ensure_ascii=False), False, chiave)
    raise FonteNonTrovata(icecat.MANCA_CODICE)


# ------------------------------------------------------- banche dati libere
# UPCitemdb e Open*Facts (app/banche_dati.py): gratuite, senza chiavi. La cache sta con
# le altre («_WEBD:IT»), quindi «Riprova» la svuota insieme.
CACHE_BANCHE = "_WEBD"
GIORNI_BANCHE_ASSENTE = 3


async def _banche_dati_con_cache(db: Session, gtin: str, lang: str) -> dict:
    """L'identità del prodotto dalle banche dati libere. FonteNonTrovata se non lo conoscono,
    FonteErrore se nessuna ha potuto rispondere (limiti giornalieri, rete)."""
    from . import banche_dati

    chiave = f"{CACHE_BANCHE}:{lingue.normalizza(lang)}"
    riga = _riga_cache(db, gtin, chiave)
    if riga:
        if riga.found and _fresca(riga, config.CACHE_GIORNI):
            return json.loads(riga.payload)
        if not riga.found and _fresca(riga, GIORNI_BANCHE_ASSENTE):
            raise FonteNonTrovata("non c'è nelle banche dati")
    try:
        prodotto = await banche_dati.scheda(gtin, lang)
    except banche_dati.NonDisponibile as exc:
        raise FonteErrore(str(exc)) from exc
    if not prodotto:
        salva_cache_web(db, gtin, lang, "{}", False, chiave)
        raise FonteNonTrovata("non c'è nelle banche dati")
    salva_cache_web(db, gtin, lang, json.dumps(prodotto, ensure_ascii=False), True, chiave)
    return prodotto


def _pagina_banche(esito: str, dettaglio: str, fonte: str = "") -> dict:
    return {"dominio": fonte or "banche dati libere", "url": "https://www.upcitemdb.com/", "esito": esito,
            "dettaglio": dettaglio[:160], "fonte": "banche_dati"}


def _manca_ean() -> str:
    from . import icecat

    return icecat.MANCA_EAN


def _codice_icecat(codice: str) -> bool:
    from . import icecat

    return icecat.codice_buono(codice)


def _pagina_icecat(esito: str, dettaglio: str) -> dict:
    return {"dominio": "icecat.biz", "url": "https://icecat.biz/", "esito": esito, "dettaglio": dettaglio[:160],
            "fonte": "icecat"}


def _diario_icecat(gtin: str, lang: str, voce: dict, spiegazione: str) -> dict:
    return {"versione": 1, "gtin": gtin, "lingua": lang, "motore": "icecat", "ricerche": [], "risultati": 0,
            "ancorati": 0, "candidati": [], "pagine": [voce], "note": [], "causa": "trovato",
            "spiegazione": spiegazione, "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")}


async def fetch(db: Session, client: Client, gtin: str, lang: str) -> tuple[dict, str]:
    """Prova le fonti in ordine e le usa anche per ARRICCHIRE la prima risposta.

    La priorità resta quella impostata, ma la catena continua finché mancano
    dati di contenuto (nome, descrizione, foto). Il diario della ricerca web
    viaggia nella scheda (``_diagnosi``) o nell'eccezione (``.diagnosi``)."""
    lang = lingue.normalizza(lang or config.DEFAULT_LANG)
    if codici.gtin_a_circolazione_limitata(gtin):
        raise NoSourceFound(
            f"{gtin} è un codice GS1 a circolazione limitata (prefisso 20-29): "
            "può essere un codice interno del grossista/punto vendita e non identifica "
            "globalmente una scheda prodotto. Usa l'EAN del produttore o il suo codice articolo.",
            {"causa": "codice_interno", "spiegazione": "Codice interno del grossista (prefisso 20-29): "
                                                       "usa l'EAN del produttore o il codice produttore."},
        )

    providers = chain(db, client)
    errors: list[str] = []
    hard_error = False
    migliore: dict | None = None
    fonti_usate: list[str] = []
    diagnosi: dict | None = None
    rimandata: RicercaRimandata | None = None

    # Icecat per primo, con l'utenza del portale, solo per gli EAN (Icecat cerca per GTIN).
    # Se la scheda è completa (nome, descrizione, foto) la ricerca sul web non parte; se
    # manca qualcosa, le fonti del cliente la completano; se non c'è, cercano loro.
    voce_icecat: dict | None = None
    if icecat_config(db)["attivo"] and codici.e_ean(gtin):
        try:
            prodotto_icecat = await _icecat_con_cache(db, gtin, lang)
            prodotto_icecat.pop("_diagnosi", None)
            migliore = prodotto_icecat
            fonti_usate.append("Icecat")
            voce_icecat = _pagina_icecat("confermata", "scheda Icecat" + (
                "" if _ha_contenuto_base(prodotto_icecat) else ", completata dal web"))
        except FonteNonTrovata as exc:
            voce_icecat = _pagina_icecat("icecat_assente", str(exc) or "non c'è su Icecat")
        except FonteErrore as exc:
            errors.append(f"Icecat: {exc}")
            voce_icecat = _pagina_icecat("http", str(exc))

    # Banche dati libere: danno nome, marca, codice produttore e foto. Se Icecat non ha
    # l'EAN o la scheda è incompleta, con marca e codice la ricerca sul web va a segno.
    voce_banche: dict | None = None
    if (migliore is None or not _ha_contenuto_base(migliore)) and codici.e_ean(gtin):
        try:
            da_banche = await _banche_dati_con_cache(db, gtin, lang)
        except FonteNonTrovata as exc:
            voce_banche = _pagina_banche("assente", str(exc))
        except FonteErrore as exc:
            errors.append(f"Banche dati: {exc}")
            voce_banche = _pagina_banche("http", str(exc))
        else:
            migliore = da_banche if migliore is None else _fondi_fonti(migliore, da_banche)
            fonti_usate.append("Banche dati libere")
            voce_banche = _pagina_banche("confermata", "identità del prodotto: " + (da_banche.get("title") or ""),
                                         da_banche.get("source_ref") or "")
    mpn_noto = (migliore or {}).get("mpn") or ""

    for provider in (providers if migliore is None or not _ha_contenuto_base(migliore) else []):
        if provider.kind == "manual":
            continue
        use_lang = lingue.normalizza(provider.lang or lang)
        label = provider.name or provider.kind
        try:
            if provider.kind == "web":
                modelli = [r.strip() for r in (provider.endpoint or "").splitlines() if r.strip()]
                product = None
                if modelli:
                    try:
                        product = await web_source.cerca(modelli, gtin, lingua=use_lang,
                                                         render_url=ricerca_config(db).get("render_url", ""))
                    except web_source.NonTrovato as exc:
                        log.info("Codice %s: indirizzi fissi a vuoto (%s), passo alla ricerca automatica", gtin, exc)
                        product = None
                if product is None:
                    product = await _web_con_cache(db, gtin, use_lang, mpn=mpn_noto)
            elif provider.kind == "http_json":
                raw = await _fetch_http_json(provider, gtin, use_lang)
                field_map = json.loads(provider.field_map or "{}")
                product = map_generic(raw, gtin, field_map, provider.root_path)
            else:
                continue

            if product.get("_diagnosi") and diagnosi is None:
                diagnosi = product.get("_diagnosi")
            if not product.get("title"):
                hard_error = True
                errors.append(f"{label}: risposta senza nome prodotto")
                continue

            product["regular_price"] = ""
            product["sale_price"] = ""
            if provider.id:
                provider.last_ok_at = datetime.now(timezone.utc)
                provider.last_error = ""
                db.commit()

            migliore = product if migliore is None else _fondi_fonti(migliore, product)
            if label not in fonti_usate:
                fonti_usate.append(label)
            if _ha_contenuto_base(migliore):
                break

        except web_search.MotoriBloccati as exc:
            errors.append(f"{label}: {exc}")
            rimandata = RicercaRimandata(str(exc), getattr(exc, "diagnosi", None),
                                         getattr(exc, "riprova_alle", None))
            diagnosi = diagnosi or getattr(exc, "diagnosi", None)
        except web_search.RicercaSpenta as exc:
            errors.append(f"{label}: {exc}")
            diagnosi = diagnosi or getattr(exc, "diagnosi", None)
        except web_search.RicercaError as exc:
            hard_error = True
            errors.append(f"{label}: {exc}")
            diagnosi = diagnosi or getattr(exc, "diagnosi", None)
        except (FonteNonTrovata, web_source.NonTrovato) as exc:
            errors.append(f"{label}: {exc}" if str(exc) else f"{label}: non trovato")
            diagnosi = diagnosi or getattr(exc, "diagnosi", None)
        except (FonteErrore, ValueError, httpx.HTTPError) as exc:
            hard_error = True
            errors.append(f"{label}: {exc}")
            if provider.id:
                provider.last_error = str(exc)[:400]
                db.commit()

    # Icecat non aveva l'EAN, ma la ricerca sul web ha trovato marca e codice produttore: si
    # richiede a Icecat per codice. Icecat ha spesso il prodotto senza quell'EAN collegato, e la
    # sua scheda (testi e foto con licenza) diventa la base; il web riempie i buchi.
    if (voce_icecat is not None and voce_icecat.get("dettaglio") == _manca_ean() and migliore is not None
            and (migliore.get("brand") or "").strip() and _codice_icecat(migliore.get("mpn") or "")):
        mpn = migliore["mpn"].strip()
        try:
            da_codice = await _icecat_per_codice(db, gtin, migliore["brand"], mpn, lang)
        except FonteNonTrovata:
            voce_icecat["dettaglio"] = f"{_manca_ean()}, né il codice produttore {mpn}"[:160]
        except FonteErrore as exc:
            errors.append(f"Icecat: {exc}")
        else:
            da_codice.pop("_diagnosi", None)
            migliore = _fondi_fonti(da_codice, migliore)
            fonti_usate.insert(0, "Icecat")
            voce_icecat = _pagina_icecat("confermata", f"scheda Icecat trovata col codice produttore {mpn}")

    # Nel diario della ricerca, le banche dati stanno subito dopo Icecat.
    if voce_banche is not None:
        if diagnosi:
            diagnosi = dict(diagnosi)
            diagnosi["pagine"] = [voce_banche] + [p for p in diagnosi.get("pagine") or [] if p.get("fonte") != "banche_dati"]
        elif voce_banche["esito"] == "confermata":
            diagnosi = _diario_icecat(gtin, lang, voce_banche, "Prodotto riconosciuto dalle banche dati libere.")
            diagnosi["motore"] = "banche_dati"

    # Nel diario della ricerca, Icecat è il primo «sito» aperto: trovato, assente o in errore.
    if voce_icecat is not None:
        if diagnosi:
            diagnosi = dict(diagnosi)
            diagnosi["pagine"] = [voce_icecat] + [p for p in diagnosi.get("pagine") or [] if p.get("fonte") != "icecat"]
            if voce_icecat["esito"] == "confermata":
                diagnosi["spiegazione"] = ("Scheda trovata su Icecat e completata dalla ricerca sul web. "
                                           + (diagnosi.get("spiegazione") or ""))[:400]
                diagnosi["causa"] = "trovato"
        elif voce_icecat["esito"] == "confermata":
            diagnosi = _diario_icecat(gtin, lang, voce_icecat, "Scheda trovata su Icecat.")
        if migliore is not None and diagnosi:
            migliore["_diagnosi"] = diagnosi

    if migliore is not None:
        if errors and not _ha_contenuto_base(migliore):
            avvisi = migliore.setdefault("warnings", [])
            msg = "Arricchimento incompleto: " + " · ".join(errors[-2:])
            if msg not in avvisi:
                avvisi.append(msg)
        migliore["regular_price"] = ""
        migliore["sale_price"] = ""
        if diagnosi and not migliore.get("_diagnosi"):
            migliore["_diagnosi"] = diagnosi
        # Qualunque fonte (cache vecchie e connettori JSON compresi): niente HTML
        # attivo nella scheda che si salva e si mostra.
        return scheda_sicura(migliore), " + ".join(fonti_usate)

    joined = " · ".join(errors) or "Nessuna fonte ha risposto."
    if rimandata is not None:
        rimandata.diagnosi = diagnosi or rimandata.diagnosi
        raise rimandata
    if hard_error:
        raise FonteErrore(joined, diagnosi)
    raise NoSourceFound(joined, diagnosi)
