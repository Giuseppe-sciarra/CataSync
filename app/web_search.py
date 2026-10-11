"""Ricerca della pagina prodotto partendo dal codice (EAN o codice produttore).

Si chiede a un motore di ricerca chi pubblica quel codice, si ordinano i
risultati (prima le pagine che riportano davvero il codice, poi quelle nella
lingua delle schede, poi i produttori) e si passano a ``web_source`` che le
legge e le conferma.

Il punto debole di tutta la catena era QUI, non nella lettura delle pagine:

- ogni EAN partiva con fino a sedici richieste al motore (quattro formulazioni,
  ognuna ripetuta con e senza lingua, ognuna ritentata due volte), più altre
  sedici a DuckDuckGo. Su un carico da dieci codici Google, DuckDuckGo e Brave
  rispondevano CAPTCHA o «troppe richieste» dopo i primi due o tre: SearXNG li
  sospendeva e restava solo Bing, che sui numeri risponde a caso (pagine di
  supporto YouTube per l'EAN di una pompa). Da lì in poi: «non trovato».
  Ecco il «3 su 10».
- nessuno guardava ``unresponsive_engines``, il campo con cui SearXNG dice quali
  motori lo stanno bloccando: un blocco diventava un falso «non trovato».

Adesso: una o due richieste per codice, distanziate fra loro, ai motori che
sui codici funzionano davvero (si scelgono con ``engines=``, qualunque cosa ci
sia nel settings.yml), e se i motori sono bloccati il prodotto si RIMANDA
invece di finire fra i non trovati.
"""
import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from . import config, lingue

log = logging.getLogger("catasync.ricerca")

TIMEOUT = 25.0

# Motori di SearXNG che sui codici numerici rispondono con pagine pertinenti.
# Misurati su un lotto di EAN di termoidraulica: «google cse» e «yandex»
# riportano l'EAN esatto in quasi tutti i risultati; Bing sui numeri inventa,
# Qwant e Mojeek fanno corrispondenze «somiglianti». L'elenco si può cambiare
# da Impostazioni senza toccare il settings.yml.
MOTORI_SEARXNG_PREDEFINITI = "google cse, yandex, google, startpage, duckduckgo, brave"

# Motori la cui risposta conta per dire «il codice non esiste»: se rispondono
# solo gli altri (Bing, che sui numeri inventa) e questi sono bloccati, il
# codice non è stato davvero cercato.
MOTORI_BUONI = {"google", "google cse", "yandex", "startpage", "duckduckgo", "brave", "serpapi"}

# Siti che non sono mai la scheda di un prodotto in vendita.
ESCLUSI = {
    "google.com", "google.it", "bing.com", "facebook.com", "pinterest.com",
    "youtube.com", "instagram.com", "tiktok.com", "reddit.com", "x.com", "twitter.com",
    "linkedin.com", "wikipedia.org", "wikidata.org",
    # raccolte di manuali: danno il PDF delle istruzioni, non la scheda commerciale
    "manualslib.com", "manua.ls", "manualzz.com", "manualeduso.it", "manualeitaliano.it",
    "manualid.it", "manualsbrain.com", "manualeuso.it", "libble.eu", "manualzilla.com",
    # servizi che rispondono a qualunque sequenza di cifre e non c'entrano mai
    "aifa.gov.it", "federfarma.it", "torrinomedica.it", "codifa.it",
    "17track.net", "cercaspedizione.it", "trackingmore.com", "parcelsapp.com",
    "rintraccialamiaspedizione.it", "dovelamiaspedizione.it",
    "product-search.net", "clarityradar.com", "prontotelefono.it",
    "heylocate.mobi", "shopsicuro.it", "chiamatemi.it", "chimiha-chiamato.it",
    "tellows.it", "sitopericoloso.it", "truffa.net", "whocalled.eu",
    "numeroanonimo.it", "cercanumero.it", "paginebianche.it", "paginegialle.it",
}

# Esclusi qualunque sia l'estensione. Ci sono anche i cataloghi a licenza: i
# loro contenuti si usano solo con un contratto, quindi le loro pagine non si
# leggono e non si copiano.
RADICI_ESCLUSE = {
    "icecat", "openicecat", "manualslib", "manualzz", "manualzilla", "libble",
    "bedienungsanleitung", "bedienungsanleitu", "manualeduso", "manualeitaliano",
    "handleiding", "manuais", "gs1it", "gs1",
}

# Marketplace, grandi catene, comparatori e archivi di codici: inserzioni di
# terzi. Vanno in fondo e valgono SOLO se la pagina dichiara proprio quell'EAN.
MARKETPLACE = {
    "ebay.it", "ebay.com", "ebay.de", "ebay.fr", "ebay.es", "ebay.co.uk",
    "amazon.it", "amazon.com", "amazon.de", "amazon.co.uk", "amazon.es", "amazon.fr",
    "aliexpress.com", "temu.com", "wish.com", "subito.it", "kleinanzeigen.de", "leboncoin.fr",
    "mediaworld.it", "unieuro.it", "euronics.it", "trony.it", "expert.it", "comet.it",
    "eprice.it", "monclick.it", "yeppon.it", "bpm-power.com", "drimmer.it",
    "manomano.it", "manomano.fr", "manomano.de", "manomano.es", "manomano.co.uk",
    "leroymerlin.it", "leroymerlin.fr", "obi-italia.it", "obi.de", "bricoman.it", "brico.io",
    "trovaprezzi.it", "idealo.it", "idealo.de", "idealo.fr", "kelkoo.it", "shopalike.it",
    "pagomeno.it", "prezzoforte.it", "ciao.it", "shoppydoo.it", "geizhals.de", "geizhals.at",
    "geizhals.eu", "billiger.de", "ledenicheur.fr", "pricerunner.com",
    "puntocellulare.it", "telefonino.net", "cellularmagazine.it", "hdblog.it",
    "smartworld.it", "tuttoandroid.net", "androidworld.it", "gsmchoice.com",
    "phonearena.com", "devicespecifications.com", "kimovil.com", "productz.com",
    "carrefour.it", "esselunga.it", "coop.it", "conad.it", "bennet.com",
    "barcodelookup.com", "ean-search.org", "gtin.info", "upcitemdb.com",
    "buycott.com", "eandata.com", "go-upc.com", "qualescegliere.it",
}
RADICI_MARKETPLACE = {"amazon", "ebay", "aliexpress", "barcodelookup", "upcitemdb", "ean-search",
                      "eandata", "idealo", "manomano", "geizhals"}

# Parole che nel nome di dominio tradiscono un negozio, non un produttore.
SPIE_NEGOZIO = ("shop", "store", "ecommerce", "acquista", "vendita", "outlet", "prezzi", "offerte",
                "boutique", "tienda", "markt", "kaufen")


class RicercaSpenta(Exception):
    """Nessun motore configurato: non è un errore, è una funzione non attivata."""


class RicercaError(Exception):
    pass


class MotoriBloccati(RicercaError):
    """I motori hanno risposto con CAPTCHA, accesso negato o «troppe richieste»:
    il codice non è stato davvero cercato. Si rimanda, non si dà per non trovato."""

    def __init__(self, messaggio: str, ko: list | None = None):
        super().__init__(messaggio)
        self.ko = ko or []


class NessunProdotto(RicercaError):
    """Il motore ha risposto, ma con pagine che non c'entrano col prodotto."""


@dataclass
class Risposta:
    risultati: list[dict] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)          # motori che hanno dato risultati
    ko: list[tuple[str, str]] = field(default_factory=list)  # (motore, motivo)
    ms: int = 0


# ============================================================ salute dei motori
# Ultimo stato visto per ogni motore: lo mostra la pagina Impostazioni.
SALUTE: dict[str, dict] = {}


def _registra(risposta: Risposta, via: str) -> None:
    adesso = time.time()
    for nome in risposta.ok:
        voce = SALUTE.setdefault(nome, {"motore": nome, "ok": 0, "ko": 0})
        voce["ok"] += 1
        voce["ultimo_ok"] = adesso
        voce["via"] = via
    for nome, motivo in risposta.ko:
        voce = SALUTE.setdefault(nome, {"motore": nome, "ok": 0, "ko": 0})
        voce["ko"] += 1
        voce["ultimo_ko"] = adesso
        voce["motivo"] = motivo
        voce["via"] = via


def stato_motori() -> list[dict]:
    """Per il pannello: chi risponde, chi è bloccato e perché."""
    fuori = []
    for voce in SALUTE.values():
        ok, ko = voce.get("ultimo_ok", 0), voce.get("ultimo_ko", 0)
        fuori.append({
            **voce,
            "stato": "ok" if ok >= ko else "bloccato",
            "ultimo_ok": ok or None,
            "ultimo_ko": ko or None,
        })
    fuori.sort(key=lambda v: (v["stato"] != "bloccato", v["motore"]))
    return fuori


def _motivo_leggibile(motivo: str) -> str:
    m = (motivo or "").lower()
    if "captcha" in m:
        return "CAPTCHA"
    if "access denied" in m or "403" in m:
        return "accesso negato"
    if "too many" in m or "429" in m:
        return "troppe richieste"
    if "timeout" in m:
        return "timeout"
    if "suspended" in m:
        return "sospeso"
    return motivo[:40] or "errore"


# ===================================================================== cadenza
# Le ricerche si distanziano fra loro, anche fra prodotti diversi: è il modo più
# semplice per non farsi bloccare. Una pausa di qualche secondo su un carico da
# cento codici costa pochi minuti; un blocco di Google costa la giornata.
_ultima_ricerca = 0.0
_cadenza = asyncio.Lock()


async def _aspetta_turno(pausa: float) -> None:
    global _ultima_ricerca
    async with _cadenza:
        attesa = _ultima_ricerca + pausa + random.uniform(0, pausa * 0.4) - time.monotonic()
        if attesa > 0:
            await asyncio.sleep(attesa)
        _ultima_ricerca = time.monotonic()


# =================================================================== i motori
def _motori_searxng(extra: dict) -> str:
    scelti = (extra.get("motori") or MOTORI_SEARXNG_PREDEFINITI).strip()
    return ",".join(p.strip() for p in scelti.split(",") if p.strip())


async def _searxng(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    """SearXNG self-hosted: aggrega Google, Yandex, DuckDuckGo e gli altri.

    Nessuna chiave, nessun costo, gira su un container tuo. Serve l'uscita JSON
    attiva nel settings.yml (``search: formats: [html, json]``). I motori da
    usare si chiedono con ``engines=``: vale anche per quelli spenti di serie.
    ``lingua`` vuota = nessun filtro: è la seconda domanda, quella che trova gli
    EAN che vivono solo su siti esteri (vedi ``formulazioni``).
    """
    base = (extra.get("url") or "").rstrip("/")
    if not base:
        raise RicercaSpenta("Manca l'indirizzo di SearXNG.")
    params = {"q": query, "format": "json", "safesearch": 0}
    if lingua:
        params["language"] = LOCALE_SEARXNG.get(lingue.normalizza(lingua), "all")
    motori = _motori_searxng(extra)
    if motori:
        params["engines"] = motori
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(f"{base}/search", params=params,
                                headers={"Accept": "application/json", "User-Agent": "TDSyncCatalog/2.0"})
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code == 403:
        raise RicercaError("SearXNG: uscita JSON non abilitata (settings.yml → search: formats: [html, json])")
    if resp.status_code == 429:
        raise MotoriBloccati("SearXNG: il suo limitatore ci ha fermati (troppe richieste)",
                             [("searxng", "troppe richieste")])
    if resp.status_code >= 400:
        raise RicercaError(f"SearXNG: HTTP {resp.status_code}")
    try:
        dati = resp.json()
    except ValueError:
        raise RicercaError("SearXNG ha risposto in HTML: manca `json` fra i formati abilitati")
    risultati = [
        {"url": r.get("url", ""), "titolo": r.get("title", ""), "testo": r.get("content", ""),
         "motori": r.get("engines") or []}
        for r in dati.get("results", [])
    ]
    ok = sorted({m for r in risultati for m in r["motori"]})
    ko = [(str(x[0]), _motivo_leggibile(str(x[1]) if len(x) > 1 else "")) for x in dati.get("unresponsive_engines") or []
          if isinstance(x, (list, tuple)) and x]
    return Risposta(risultati=risultati, ok=ok, ko=ko, ms=ms)


async def _duckduckgo(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    """DuckDuckGo senza chiave, dalla sua pagina in versione essenziale.

    Ripiego, non soluzione: senza un accordo dietro, dopo qualche richiesta
    risponde 202 con una verifica anti-bot. Prima quel 202 passava per «zero
    risultati» e diventava un «non trovato»: ora si riconosce."""
    from html import unescape
    from urllib.parse import parse_qs, unquote

    kl = {"IT": "it-it", "EN": "uk-en", "FR": "fr-fr", "DE": "de-de", "ES": "es-es"}.get(lingue.normalizza(lingua), "")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": kl} if kl else {"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                     "Accept-Language": lingue.ACCEPT_LANGUAGE.get(lingue.normalizza(lingua), "it-IT")},
        )
    ms = int((time.monotonic() - t0) * 1000)
    corpo = resp.text
    if resp.status_code == 202 or "anomaly" in corpo[:5000].lower() or "captcha" in corpo[:5000].lower():
        raise MotoriBloccati("DuckDuckGo chiede la verifica anti-bot", [("duckduckgo", "CAPTCHA")])
    if resp.status_code == 429:
        raise MotoriBloccati("DuckDuckGo: troppe richieste", [("duckduckgo", "troppe richieste")])
    if resp.status_code >= 400:
        raise RicercaError(f"DuckDuckGo: HTTP {resp.status_code}")

    fuori: list[dict] = []
    for href, titolo in re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', corpo, re.S):
        if "duckduckgo.com/l/" in href:
            vero = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query).get("uddg", [""])[0]
            href = unquote(vero) if vero else href
        if not href.startswith("http"):
            continue
        fuori.append({"url": href, "titolo": unescape(re.sub(r"<[^>]+>", "", titolo)).strip(),
                      "testo": "", "motori": ["duckduckgo"]})
    return Risposta(risultati=fuori, ok=["duckduckgo"] if fuori else [], ko=[], ms=ms)


async def _brave(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    paese = {"IT": "it", "EN": "gb", "FR": "fr", "DE": "de", "ES": "es"}.get(lingue.normalizza(lingua), "it")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "country": paese, "count": 20},
            headers={"Accept": "application/json", "X-Subscription-Token": chiave},
        )
    if resp.status_code in (401, 403):
        raise RicercaError("Brave: chiave rifiutata")
    if resp.status_code == 429:
        raise MotoriBloccati("Brave: troppe richieste o credito esaurito", [("brave", "troppe richieste")])
    if resp.status_code >= 400:
        raise RicercaError(f"Brave: HTTP {resp.status_code}")
    risultati = [{"url": r.get("url", ""), "titolo": r.get("title", ""), "testo": r.get("description", ""),
                  "motori": ["brave"]} for r in (resp.json().get("web") or {}).get("results", [])]
    return Risposta(risultati=risultati, ok=["brave"] if risultati else [], ms=int((time.monotonic() - t0) * 1000))


async def _google_cse(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    cx = extra.get("cx", "")
    if not cx:
        raise RicercaError("Google: manca l'identificativo del motore (cx)")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": chiave, "cx": cx, "q": query, "num": 10,
                    "hl": lingue.normalizza(lingua).lower()},
        )
    if resp.status_code == 429:
        raise MotoriBloccati("Google: quota giornaliera finita", [("google", "quota finita")])
    if resp.status_code in (401, 403):
        raise RicercaError("Google: chiave rifiutata o quota giornaliera finita")
    if resp.status_code >= 400:
        raise RicercaError(f"Google: HTTP {resp.status_code}")
    risultati = [{"url": r.get("link", ""), "titolo": r.get("title", ""), "testo": r.get("snippet", ""),
                  "motori": ["google"]} for r in resp.json().get("items", [])]
    return Risposta(risultati=risultati, ok=["google"] if risultati else [], ms=int((time.monotonic() - t0) * 1000))


async def _serpapi(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={"api_key": chiave, "q": query, "engine": "google", "num": 20,
                    "hl": lingue.normalizza(lingua).lower()},
        )
    if resp.status_code in (401, 403):
        raise RicercaError("SerpAPI: chiave rifiutata")
    if resp.status_code == 429:
        raise MotoriBloccati("SerpAPI: crediti finiti", [("serpapi", "crediti finiti")])
    if resp.status_code >= 400:
        raise RicercaError(f"SerpAPI: HTTP {resp.status_code}")
    risultati = [{"url": r.get("link", ""), "titolo": r.get("title", ""), "testo": r.get("snippet", ""),
                  "motori": ["google"]} for r in resp.json().get("organic_results", [])]
    return Risposta(risultati=risultati, ok=["google"] if risultati else [], ms=int((time.monotonic() - t0) * 1000))


MOTORI = {
    "searxng": {
        "nome": "SearXNG (tuo, consigliato)",
        "funzione": _searxng,
        "campi": ["url"],
        "note": "Container tuo, nessuna chiave e nessun costo. Interroga Google, Yandex, "
                "DuckDuckGo e gli altri; i motori da usare si scelgono qui sotto.",
    },
    "duckduckgo": {
        "nome": "DuckDuckGo (senza chiave)",
        "funzione": _duckduckgo,
        "campi": [],
        "note": "Zero configurazione, ma senza accordo dietro: dopo qualche ricerca chiede "
                "la verifica anti-bot. Bene per provare, non per lavorare.",
    },
    "brave": {
        "nome": "Brave Search (API ufficiale)",
        "funzione": _brave,
        "campi": ["chiave"],
        "note": "api.search.brave.com: API vera, nessun blocco. A consumo, pochi euro ogni mille ricerche.",
    },
    "google_cse": {
        "nome": "Google Programmable Search (API ufficiale)",
        "funzione": _google_cse,
        "campi": ["chiave", "cx"],
        "note": "100 ricerche al giorno gratis, poi 5 $ ogni mille. Serve anche l'ID del motore (cx).",
    },
    "serpapi": {
        "nome": "SerpAPI",
        "funzione": _serpapi,
        "campi": ["chiave"],
        "note": "Risultati Google tramite servizio a pagamento. Piano gratuito limitato.",
    },
}


# =============================================================== ordinamento
def _dominio(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _escluso(dominio: str) -> bool:
    if any(dominio == d or dominio.endswith("." + d) for d in ESCLUSI):
        return True
    pezzi = dominio.split(".")
    return bool(pezzi) and pezzi[0] in RADICI_ESCLUSE


def e_marketplace(dominio: str) -> bool:
    return (any(dominio == m or dominio.endswith("." + m) for m in MARKETPLACE)
            or dominio.split(".")[0] in RADICI_MARKETPLACE)


def _accanto_a_parola(titolo: str, numero: str, parole: list[str]) -> bool:
    """Il numero sta a fianco di una parola del nome prodotto?

    «Fast 14 FI»: il 14 segue «fast», quindi fa parte del modello. «(2.4") 91 g»:
    il 91 sta fra una parentesi e un'unità di misura, è il peso."""
    if not parole:
        return False
    pezzi = re.findall(r"[A-Za-zÀ-ÿ0-9.,\"]+", titolo.lower())
    obiettivo = numero.lower()
    for i, pezzo in enumerate(pezzi):
        if pezzo != obiettivo:
            continue
        intorno = []
        if i > 0:
            intorno.append(pezzi[i - 1])
        if i + 1 < len(pezzi):
            intorno.append(pezzi[i + 1])
        if any(p in parole for p in intorno):
            return True
    return False


# Parole troppo comuni per dire «di che prodotto si parla», in ogni lingua.
COMUNI = set(RADICI_ESCLUSE) | {
    "per", "con", "the", "and", "del", "della", "dei", "delle", "dal", "che", "una", "uno",
    "for", "with", "pour", "avec", "des", "les", "und", "mit", "für", "der", "die", "das",
    "para", "los", "las", "con", "por",
    "prezzo", "prezzi", "offerta", "offerte", "acquista", "online", "spedizione",
    "price", "prices", "buy", "shop", "store", "sale", "deal", "deals", "cheap", "best",
    "prix", "acheter", "preis", "kaufen", "precio", "comprar",
    "scheda", "schede", "tecnica", "tecniche", "prodotto", "prodotti", "codice", "articolo",
    "product", "products", "item", "datasheet", "produit", "produkt", "producto",
    "vendita", "italia", "italiano", "italiana", "english", "france", "deutschland", "españa",
    "manuale", "manuali", "istruzioni", "pagine", "pagina", "guida", "libretto", "pdf",
    "manual", "manuel", "handbuch", "manuel", "instructions",
    "recensioni", "opinioni", "confronta", "migliori", "cerca", "trova", "specifiche",
    "reviews", "review", "compare", "search", "find", "specifications", "specs",
    "scaldabagno", "scaldabagni", "scaldacqua", "caldaia", "caldaie", "condizionatore",
    "condizionatori", "climatizzatore", "lavatrice", "lavastoviglie", "frigorifero",
    "forno", "piano", "cappa", "asciugatrice", "telefono", "cellulare", "smartphone",
    "televisore", "monitor", "stampante", "notebook", "portatile", "tablet",
    "miscelatore", "rubinetto", "piatto", "doccia", "radiatore", "termosifone",
    "gas", "metano", "elettrico", "elettrica", "litri", "camera", "aperta", "stagna",
    "pump", "pumps", "pompe", "pumpe", "bomba", "boiler", "heater", "chaudière", "heizung",
}


def _parole_marca(risultati: list[dict]) -> list[str]:
    """Indovina la marca dalle parole che tornano nei titoli dei risultati."""
    documenti: dict[str, int] = {}
    peso: dict[str, int] = {}
    for r in risultati[:12]:
        parole = re.findall(r"[A-Za-zÀ-ÿ]{3,}", r.get("titolo", "").lower())
        viste = set()
        for posizione, parola in enumerate(parole):
            if parola in viste:
                continue
            viste.add(parola)
            documenti[parola] = documenti.get(parola, 0) + 1
            peso[parola] = peso.get(parola, 0) + (2 if posizione < 3 else 1)
    candidate = [(peso[p], p) for p in peso if documenti.get(p, 0) >= 2 and p not in COMUNI]
    candidate.sort(reverse=True)
    return [p for _, p in candidate[:6]]


def _in_tema(titolo: str, dominanti: list[str]) -> bool:
    if not dominanti:
        return True
    minuscolo = titolo.lower()
    return any(d in minuscolo for d in dominanti)


def _ancorato(gtin: str, r: dict) -> bool:
    """Il risultato riporta il codice da qualche parte (indirizzo, titolo, anteprima)?

    Per un EAN si guardano le sequenze di cifre (anche con spazi e trattini);
    per un codice produttore («FAST 14 FI», «97924465») la forma normalizzata."""
    from . import codici as _cod

    blob = f"{r.get('url', '')} {r.get('titolo', '')} {r.get('testo', '')}"
    if _cod.checksum_gtin(gtin):
        for seq in re.findall(r"\d[\d .-]{6,20}\d", blob):
            if gtin in re.sub(r"[^0-9]", "", seq):
                return True
        # GTIN-14 con lo zero davanti, EAN-13 scritto senza
        corto = gtin.lstrip("0")
        if corto != gtin and len(corto) >= 12:
            return corto in re.sub(r"[^0-9]", " ", blob).split()
        return False
    chiave = re.sub(r"[^a-z0-9]", "", gtin.lower())
    return len(chiave) >= 4 and chiave in re.sub(r"[^a-z0-9]", "", blob.lower())


def ordina(risultati: list[dict], gtin: str = "", lingua: str = "IT") -> list[dict]:
    """Dal più promettente al meno.

    Regola principale: una pagina che riporta il codice batte sempre una che
    non lo riporta, in qualunque lingua sia — le pagine estere si traducono, le
    pagine sbagliate no. A parità: la lingua delle schede, poi i produttori,
    poi i negozi; i marketplace in fondo, buoni solo se dichiarano l'EAN."""
    lingua = lingue.normalizza(lingua)
    marche = _parole_marca(risultati)
    fuori = []
    for posizione, r in enumerate(risultati):
        url = r.get("url", "")
        dominio = _dominio(url)
        if not dominio or _escluso(dominio):
            continue
        ancorato = bool(gtin) and _ancorato(gtin, r)
        if not ancorato and not _in_tema(r.get("titolo", ""), marche):
            continue
        lingua_url = lingue.da_url(url, dominio)
        marketplace = e_marketplace(dominio)

        punti = 100 - min(posizione, 60)
        if ancorato:
            punti += 500
        if lingua_url == lingua:
            punti += 80
        elif lingua_url == "":
            punti += 20
        else:
            punti -= 120
        radice = dominio.split(".")[0]
        if any(m in radice for m in marche):
            punti += 150            # il dominio ha il nome della marca: è il produttore
        if any(s in dominio for s in SPIE_NEGOZIO):
            punti -= 40
        if re.search(r"/(p|prodotto|prodotti|product|products|produit|produkt|producto|artikel|item|art)[/-]",
                     url.lower()):
            punti += 30
        if url.lower().split("?")[0].endswith(".pdf"):
            punti -= 300            # un PDF non è una scheda leggibile
        if marketplace:
            punti -= 200
        # Più motori lo hanno trovato, più è solido.
        punti += 10 * max(0, len(r.get("motori") or []) - 1)

        fuori.append({**r, "dominio": dominio, "punti": punti, "ancorato": ancorato,
                      "lingua_url": lingua_url, "estero": bool(lingua_url) and lingua_url != lingua,
                      "marketplace": marketplace})
    fuori.sort(key=lambda x: -x["punti"])
    return fuori


# ================================================================= nome e modello
def nome_prodotto(risultati: list[dict]) -> str:
    """Ricava marca e modello dai titoli, per poter cercare una seconda volta.

    Cercare un EAN spesso restituisce solo negozi: i loro titoli però dicono di
    che prodotto si tratta. Serve un pezzo che somigli a un codice modello (con
    dentro delle cifre): è quello che distingue un prodotto vero da una pagina
    di ricerca generica."""
    dominanti = _parole_marca(risultati)
    if not dominanti:
        return ""
    modelli: dict[str, int] = {}
    for r in risultati[:12]:
        titolo = r.get("titolo", "")
        visti = set()
        for pezzo in re.findall(r"\b[A-Za-z]{0,6}\d+[A-Za-z0-9]{0,8}\b", titolo):
            chiave = pezzo.lower()
            if chiave in visti:
                continue
            ha_lettere = any(c.isalpha() for c in chiave)
            if ha_lettere:
                if len(chiave) < 3:
                    continue
            elif len(chiave) < 4 and not _accanto_a_parola(titolo, pezzo, dominanti):
                continue
            visti.add(chiave)
            modelli[chiave] = modelli.get(chiave, 0) + 1
    codici = [c for c, n in sorted(modelli.items(), key=lambda x: -x[1]) if n >= 2][:3]
    codici.sort(key=lambda c: (-int(any(x.isalpha() for x in c)), -len(c)))
    if not codici:
        return ""
    vicine: list[str] = []
    for parola in dominanti:
        for r in risultati[:12]:
            titolo = r.get("titolo", "").lower()
            if parola in titolo and any(codice in titolo for codice in codici):
                vicine.append(parola)
                break
    if not vicine:
        return ""
    return " ".join(vicine[:3] + codici[:2])


def pezzi_modello(risultati: list[dict]) -> list[str]:
    """I pezzi che identificano il modello, per riconoscerlo su una pagina."""
    nome = nome_prodotto(risultati)
    pezzi = [p for p in nome.split() if len(p) >= 2][:5] if nome else []
    if any(any(c.isdigit() for c in p) for p in pezzi):
        return pezzi
    ignora = COMUNI | {"sistema", "gruppo", "pompa", "grupo", "presion", "presión", "pressione"}
    conta: dict[str, int] = {}
    for r in risultati[:12]:
        for t in set(re.findall(r"[a-z0-9][a-z0-9-]{1,}", (r.get("titolo") or "").lower())):
            if t in ignora or len(t) < 2:
                continue
            conta[t] = conta.get(t, 0) + 1
    ricorrenti = sorted((t for t, n in conta.items() if n >= 2), key=lambda t: -conta[t])
    con_cifre = [t for t in ricorrenti if any(c.isdigit() for c in t)][:2]
    if not con_cifre:
        return pezzi
    if not pezzi:
        pezzi = [t for t in ricorrenti if not any(c.isdigit() for c in t)][:3]
    return (pezzi + con_cifre)[:5]


def _corpus_valido(gtin: str, grezzi: list[dict]) -> bool:
    """I risultati parlano davvero di questo codice, o sono riempitivo del motore?"""
    if any(_ancorato(gtin, r) for r in grezzi):
        return True
    return any(any(c.isdigit() for c in p) for p in pezzi_modello(grezzi))


# ===================================================================== ricerca
# Lingua e paese da chiedere a SearXNG per la prima domanda: i negozi del paese
# delle schede vengono fuori per primi (testo già nella lingua giusta, niente
# traduzione) e DuckDuckGo, che senza regione risponde «HTTP error», funziona.
LOCALE_SEARXNG = {"IT": "it-IT", "EN": "en-GB", "FR": "fr-FR", "DE": "de-DE", "ES": "es-ES"}


_TRACCIAMENTO = re.compile(r"^(?:utm_\w+|srsltid|gclid|gbraid|wbraid|fbclid|msclkid|mc_cid|mc_eid|_ga|yclid)$", re.I)


def senza_tracciamento(url: str) -> str:
    """Lo stesso indirizzo senza i parametri di tracciamento («?srsltid=…» di
    Google Shopping, utm_*): due risultati che differiscono solo per quelli sono
    la stessa pagina, e non si chiede due volte a un sito che ci ha respinti."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parti = urlsplit(url)
    except ValueError:
        return url
    if not parti.query:
        return url
    tenuti = [(k, v) for k, v in parse_qsl(parti.query, keep_blank_values=True) if not _TRACCIAMENTO.match(k)]
    return urlunsplit((parti.scheme, parti.netloc, parti.path, urlencode(tenuti), parti.fragment))


def formulazioni(gtin: str, lingua: str = "IT", motore: str = "") -> list[tuple[str, str]]:
    """Le domande da fare al motore, dalla più utile: (testo, lingua da chiedere).

    Con SearXNG la prima domanda va nella lingua delle schede, la seconda
    ovunque: col filtro lingua Google & c. nascondono gli EAN che vivono solo su
    siti esteri, senza filtro i negozi italiani finiscono sotto quelli russi.
    Si va avanti solo se serve (ci si ferma a due risultati col codice)."""
    from . import codici as _cod

    lingua = lingue.normalizza(lingua)
    if _cod.checksum_gtin(gtin):
        domande = [gtin, f'"{gtin}"', f"ean {gtin}"]
    else:
        domande = [f'"{gtin}"', gtin]
    if motore == "searxng":
        return [(domande[0], lingua), (domande[0], ""), (domande[1], "")]
    return [(d, lingua) for d in domande]


async def candidati(gtin: str, motore: str, chiave: str, extra: dict | None = None,
                    lingua: str = "IT", diario=None) -> list[dict]:
    """Indirizzi da provare, dal più promettente al meno.

    Al massimo tre domande sul codice (ci si ferma appena due risultati lo
    riportano) e una sul nome del prodotto, distanziate fra loro. Se tutti i
    motori rispondono con un blocco, si solleva ``MotoriBloccati``: il prodotto
    va rimandato, non dato per inesistente."""
    if not motore or motore not in MOTORI:
        raise RicercaSpenta("Ricerca automatica non attivata.")
    if not chiave and motore not in ("searxng", "duckduckgo"):
        raise RicercaSpenta("Manca la chiave del motore di ricerca.")
    extra = extra or {}
    lingua = lingue.normalizza(lingua)
    funzione = MOTORI[motore]["funzione"]
    pausa = config.RICERCA_PAUSA if motore in ("searxng", "duckduckgo") else 0.3

    grezzi: list[dict] = []
    errori: list[str] = []
    ko_visti: list[tuple[str, str]] = []
    ok_visti: set[str] = set()

    async def chiedi(domanda: str, cerca_con=funzione, via=motore, lingua_domanda: str | None = None) -> list[dict]:
        await _aspetta_turno(pausa)
        lingua_domanda = lingua if lingua_domanda is None else lingua_domanda
        voce = {"q": domanda, "via": via, "lingua": lingua_domanda or "*"}
        try:
            risposta = await cerca_con(domanda, chiave, extra, lingua_domanda)
        except MotoriBloccati as exc:
            voce.update(errore=str(exc), ko=[list(k) for k in exc.ko])
            ko_visti.extend(exc.ko)
            _registra(Risposta(ko=list(exc.ko)), via)
            if diario is not None:
                diario.ricerca(voce)
            raise
        except (httpx.HTTPError, RicercaError) as exc:
            testo = str(exc) or exc.__class__.__name__
            if isinstance(exc, httpx.HTTPError):
                testo = f"{via} non raggiungibile ({exc.__class__.__name__})"
            errori.append(testo)
            voce["errore"] = testo
            if diario is not None:
                diario.ricerca(voce)
            return []
        _registra(risposta, via)
        ko_visti.extend(risposta.ko)
        ok_visti.update(risposta.ok)
        ancorati = sum(1 for r in risposta.risultati if _ancorato(gtin, r))
        voce.update(risultati=len(risposta.risultati), ancorati=ancorati, ok=risposta.ok,
                    ko=[list(k) for k in risposta.ko], ms=risposta.ms)
        if diario is not None:
            diario.ricerca(voce)
        log.info("Codice %s, ricerca «%s» (%s): %s risultati, %s col codice%s", gtin, domanda, via,
                 len(risposta.risultati), ancorati,
                 (" · bloccati: " + ", ".join(f"{m} ({w})" for m, w in risposta.ko)) if risposta.ko else "")
        return risposta.risultati

    def aggiungi(nuovi: list[dict]) -> None:
        visti = {r["url"] for r in grezzi}
        for r in nuovi:
            if r.get("url"):
                r["url"] = senza_tracciamento(r["url"])
            if r.get("url") and r["url"] not in visti:
                grezzi.append(r)
                visti.add(r["url"])

    bloccato: MotoriBloccati | None = None
    for domanda, lingua_domanda in formulazioni(gtin, lingua, motore):
        try:
            aggiungi(await chiedi(domanda, lingua_domanda=lingua_domanda))
        except MotoriBloccati as exc:
            bloccato = exc
            break
        if sum(1 for r in grezzi if _ancorato(gtin, r)) >= 2:
            break

    # Il motore principale non ha portato niente col codice: un giro su
    # DuckDuckGo diretto, che è un'altra strada verso l'indice.
    if motore == "searxng" and not any(_ancorato(gtin, r) for r in grezzi) and extra.get("ripiego_ddg", True):
        try:
            aggiungi(await chiedi(formulazioni(gtin)[0][0], _duckduckgo, "duckduckgo"))
        except MotoriBloccati as exc:
            bloccato = bloccato or exc

    ancorati = [r for r in grezzi if _ancorato(gtin, r)]

    # Niente col codice, e i motori buoni sui codici hanno risposto con un
    # blocco: il codice non è stato cercato davvero (i risultati di Bing sui
    # numeri non contano: sono a caso). Meglio rimandare che dire «non esiste».
    if not ancorati:
        buoni_ok = ok_visti & MOTORI_BUONI
        if (bloccato is not None or ko_visti) and not buoni_ok:
            nomi = sorted({f"{m} ({w})" for m, w in ko_visti})
            raise MotoriBloccati(
                "motori di ricerca bloccati: " + (", ".join(nomi[:5]) or str(bloccato)), ko_visti)
        if not grezzi and errori:
            raise RicercaError(errori[-1])

    # Il tema lo decidono i risultati ANCORATI: le pagine a caso che un motore
    # aggiunge (Bing ci mette il supporto Microsoft) non devono definire «di
    # cosa si parla» né rubare i posti.
    ordinati = ordina(grezzi, gtin, lingua)
    if ancorati:
        seme: set[str] = set()
        for r in ancorati:
            for t in re.findall(r"[a-z0-9][a-z0-9-]{1,}", (r.get("titolo") or "").lower()):
                if (len(t) >= 4 or any(c.isdigit() for c in t)) and t not in COMUNI:
                    seme.add(t)
        a_tema = [c for c in ordinati if c["ancorato"] or
                  set(re.findall(r"[a-z0-9][a-z0-9-]{1,}", (c.get("titolo") or "").lower())) & seme]
        if a_tema:
            ordinati = a_tema

    # Seconda domanda, sul NOME: porta i produttori, che l'EAN spesso non lo
    # scrivono. Solo se il codice da solo non basta.
    if len([c for c in ordinati if c["ancorato"]]) < 2 and _corpus_valido(gtin, grezzi):
        nome = nome_prodotto(ancorati or grezzi) or " ".join(pezzi_modello(ancorati or grezzi))
        if nome.strip():
            try:
                secondi = await chiedi(nome.strip())
            except MotoriBloccati:
                secondi = []
            if secondi:
                gia = {c["url"] for c in ordinati}
                for c in ordina(grezzi + [r for r in secondi if r.get("url")], gtin, lingua):
                    if c["url"] not in gia:
                        c["punti"] -= 15
                        c["da_nome"] = True
                        ordinati.append(c)
                        gia.add(c["url"])
                ordinati.sort(key=lambda x: -x["punti"])

    seme_corpus = ancorati or grezzi
    pezzi = pezzi_modello(seme_corpus) or pezzi_modello(grezzi)
    marche = _parole_marca(seme_corpus)[:3] or _parole_marca(grezzi)[:3]

    # Fuori ciò che non c'entra: né il codice, né un pezzo del modello nel titolo.
    if pezzi or ancorati:
        def _pertinente(c: dict) -> bool:
            if c["ancorato"]:
                return True
            titolo = re.sub(r"[^a-z0-9]", "", (c.get("titolo") or "").lower())
            return any(re.sub(r"[^a-z0-9]", "", p.lower()) in titolo
                       for p in pezzi if len(re.sub(r"[^a-z0-9]", "", p)) >= 2)
        pertinenti = [c for c in ordinati if _pertinente(c)]
        if pertinenti:
            ordinati = pertinenti

    for c in ordinati:
        c["modello"] = pezzi
        c["marche"] = marche

    if diario is not None:
        diario.candidati(len(grezzi), len(ancorati), ordinati)

    if not ordinati:
        if grezzi:
            titoli = [r.get("titolo", "")[:34] for r in grezzi[:3] if r.get("titolo")]
            raise NessunProdotto(
                "il motore non conosce questo codice: ha risposto con pagine che parlano "
                "di prodotti diversi fra loro" + (f" ({'; '.join(titoli)})" if titoli else ""))
        raise NessunProdotto("nessun sito indicizzato riporta questo codice")

    # Un sito solo non deve occupare la classifica: al massimo 2 posti a testa.
    per_dominio: dict[str, int] = {}
    contingentati: list[dict] = []
    for c in ordinati:
        per_dominio[c["dominio"]] = per_dominio.get(c["dominio"], 0) + 1
        if per_dominio[c["dominio"]] <= 2:
            contingentati.append(c)
    return contingentati[:14]
