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
MOTORI_BUONI = {"google", "google cse", "yandex", "startpage", "duckduckgo", "brave", "serpapi", "brave api"}

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
    il codice non è stato davvero cercato. Si rimanda, non si dà per non trovato.

    ``riprova_alle`` (epoch) c'è quando nessun motore è stato nemmeno interrogato perché
    erano tutti in pausa: il codice si riprende a fine pausa, senza contare un rinvio."""

    def __init__(self, messaggio: str, ko: list | None = None, riprova_alle: float | None = None):
        super().__init__(messaggio)
        self.ko = ko or []
        self.riprova_alle = riprova_alle


class NessunProdotto(RicercaError):
    """Il motore ha risposto, ma con pagine che non c'entrano col prodotto."""


@dataclass
class Risposta:
    risultati: list[dict] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)          # motori che hanno dato risultati
    ko: list[tuple[str, str]] = field(default_factory=list)  # (motore, motivo)
    ms: int = 0
    # Motori scelti ma lasciati fuori da questa domanda perché in pausa: (motore, fino a epoch).
    pausa: list[tuple[str, float]] = field(default_factory=list)


# ============================================================== pause dei motori
# Un motore che ha appena risposto CAPTCHA o «troppe richieste» NON si richiama: ogni
# domanda in più allunga il blocco e tiene l'indirizzo del server nella sua lista nera.
# Prima ogni ricerca andava comunque anche ai motori già bloccati (DuckDuckGo, Brave,
# Startpage: rossi in ogni riga del diario) e il blocco non scadeva mai.
#
# Ora chi blocca resta fuori per un po' e intanto cercano gli altri: 15 minuti la prima
# volta, poi 30, 60, 120… fino a 6 ore. Finita la pausa rientra con una domanda sola: se
# risponde torna normale, se blocca ancora la pausa raddoppia. Niente CAPTCHA aggirati,
# niente indirizzi cambiati: si aspetta, come farebbe una persona.
PAUSA_PRIMA = 15 * 60
PAUSA_MASSIMA = 6 * 3600
MOTIVI_DI_BLOCCO = {"CAPTCHA", "accesso negato", "troppe richieste", "sospeso", "quota finita", "crediti finiti"}
# I motori più permalosi, anche quando rispondono, si interrogano al massimo una volta
# ogni tanti secondi: gli altri (Google CSE, Yandex) reggono la cadenza normale.
INTERVALLO_MINIMO = {"duckduckgo": 60, "startpage": 60, "qwant": 60, "brave": 45,
                     "google": 30, "bing": 30, "mojeek": 30}
# Oltre questa attesa non si aspetta un motore «a riposo»: si va avanti con gli altri.
ATTESA_MASSIMA_RIPOSO = 90

PAUSE: dict[str, dict] = {}        # motore -> {"livello", "fino", "motivo"}
_ULTIMO_USO: dict[str, float] = {}


def metti_in_pausa(nome: str, motivo: str, adesso: float | None = None) -> None:
    adesso = time.time() if adesso is None else adesso
    voce = PAUSE.get(nome) or {"livello": 0, "fino": 0.0}
    if voce["fino"] > adesso:
        # Risposta di una domanda partita prima della pausa: la pausa c'è già, non si raddoppia.
        return
    livello = voce["livello"] + 1
    durata = min(PAUSA_PRIMA * 2 ** (livello - 1), PAUSA_MASSIMA) * random.uniform(0.9, 1.15)
    PAUSE[nome] = {"livello": livello, "fino": adesso + durata, "motivo": motivo}
    log.info("Motore %s in pausa per %d minuti (%s)", nome, durata // 60, motivo)


def fine_pausa(nome: str, adesso: float | None = None) -> float:
    """L'ora (epoch) in cui il motore torna disponibile, o 0 se non è in pausa."""
    adesso = time.time() if adesso is None else adesso
    fino = (PAUSE.get(nome) or {}).get("fino", 0.0)
    return fino if fino > adesso else 0.0


def scegli_motori(scelti: list[str], adesso: float | None = None) -> tuple[list[str], list[tuple[str, float]], float]:
    """Dai motori scelti in Impostazioni, quelli da interrogare adesso.

    Restituisce (da usare, in pausa con la loro fine, secondi al primo «a riposo» che si
    libera: 0 se nessuno)."""
    adesso = time.time() if adesso is None else adesso
    usare: list[str] = []
    in_pausa: list[tuple[str, float]] = []
    riposo_minimo = 0.0
    for nome in scelti:
        fino = fine_pausa(nome, adesso)
        if fino:
            in_pausa.append((nome, fino))
            continue
        resta = INTERVALLO_MINIMO.get(nome, 0) - (adesso - _ULTIMO_USO.get(nome, 0.0))
        if resta > 0:
            riposo_minimo = resta if not riposo_minimo else min(riposo_minimo, resta)
            continue
        usare.append(nome)
    return usare, in_pausa, riposo_minimo


def _usati(nomi: list[str]) -> None:
    adesso = time.time()
    for nome in nomi:
        _ULTIMO_USO[nome] = adesso


# ============================================================= giri di ricerca
# I codici si cercano a gruppi: dopo CODICI_PER_GIRO codici cercati davvero (quelli già in
# memoria non contano, non toccano i motori) ci si ferma PAUSA_FRA_GIRI minuti prima del
# gruppo successivo. Un carico da cinquanta codici ci mette di più, ma ai motori arriva un
# ritmo da persona e non una raffica. Entrambi si cambiano da Impostazioni.
CODICI_PER_GIRO = 5
PAUSA_FRA_GIRI = 2.0      # minuti
GIRO = {"codici": 0, "riposo_fino": 0.0}


def _numero(valore, predefinito: float) -> float:
    try:
        return float(valore)
    except (TypeError, ValueError):
        return predefinito


def conta_codice(extra: dict | None = None) -> None:
    """Un codice in più nel giro; arrivati al limite parte la pausa."""
    extra = extra or {}
    per_giro = max(1, int(_numero(extra.get("giro_codici"), CODICI_PER_GIRO)))
    pausa = max(0.0, _numero(extra.get("giro_pausa"), PAUSA_FRA_GIRI))
    GIRO["codici"] += 1
    if GIRO["codici"] >= per_giro:
        GIRO["codici"] = 0
        if pausa:
            GIRO["riposo_fino"] = time.time() + pausa * 60
            log.info("Fatti %s codici: pausa di %s minuti prima dei prossimi", per_giro, f"{pausa:g}")


def fine_riposo_giro(adesso: float | None = None) -> float:
    """Quando finisce la pausa fra un giro e l'altro (epoch), o 0 se si può cercare."""
    adesso = time.time() if adesso is None else adesso
    return GIRO["riposo_fino"] if GIRO["riposo_fino"] > adesso else 0.0


async def aspetta_giro() -> None:
    """Per i lavori in sottofondo (prova a lotti): aspetta la fine della pausa fra i giri."""
    while True:
        fine = fine_riposo_giro()
        if not fine:
            return
        await asyncio.sleep(min(30.0, fine - time.time() + 0.5))


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
        PAUSE.pop(nome, None)          # ha risposto: si riparte da zero
    for nome, motivo in risposta.ko:
        voce = SALUTE.setdefault(nome, {"motore": nome, "ok": 0, "ko": 0})
        voce["ko"] += 1
        voce["ultimo_ko"] = adesso
        voce["motivo"] = motivo
        voce["via"] = via
        if motivo in MOTIVI_DI_BLOCCO:
            metti_in_pausa(nome, motivo, adesso)


def stato_motori() -> list[dict]:
    """Per il pannello: chi risponde, chi è bloccato e perché, chi è in pausa e fino a quando."""
    fuori = []
    for voce in SALUTE.values():
        ok, ko = voce.get("ultimo_ok", 0), voce.get("ultimo_ko", 0)
        fino = fine_pausa(voce["motore"])
        fuori.append({
            **voce,
            "stato": "pausa" if fino else ("ok" if ok >= ko else "bloccato"),
            "pausa_fino": fino or None,
            "ultimo_ok": ok or None,
            "ultimo_ko": ko or None,
        })
    fuori.sort(key=lambda v: (v["stato"] == "ok", v["motore"]))
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
    scelti = [m for m in _motori_searxng(extra).split(",") if m]
    usare, in_pausa, riposo = scegli_motori(scelti)
    if not usare and riposo and riposo <= ATTESA_MASSIMA_RIPOSO:
        # Restano solo motori «a riposo» (interrogati da poco): si aspetta il primo libero.
        await asyncio.sleep(riposo + random.uniform(0.5, 2.0))
        usare, in_pausa, riposo = scegli_motori(scelti)
    if scelti and not usare:
        riprende = min((fino for _, fino in in_pausa), default=time.time() + (riposo or 60))
        raise MotoriBloccati("tutti i motori scelti sono in pausa dopo un blocco: si riprende da soli",
                             [(m, "in pausa") for m, _ in in_pausa], riprova_alle=riprende)
    if usare:
        params["engines"] = ",".join(usare)
        _usati(usare)
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
    return Risposta(risultati=risultati, ok=ok, ko=ko, ms=ms, pausa=in_pausa)


async def _duckduckgo(query: str, chiave: str, extra: dict, lingua: str) -> Risposta:
    """DuckDuckGo senza chiave, dalla sua pagina in versione essenziale.

    Ripiego, non soluzione: senza un accordo dietro, dopo qualche richiesta
    risponde 202 con una verifica anti-bot. Prima quel 202 passava per «zero
    risultati» e diventava un «non trovato»: ora si riconosce."""
    from html import unescape
    from urllib.parse import parse_qs, unquote

    kl = {"IT": "it-it", "EN": "uk-en", "FR": "fr-fr", "DE": "de-de", "ES": "es-es"}.get(lingue.normalizza(lingua), "")
    fino = fine_pausa("duckduckgo")
    if fino:
        raise MotoriBloccati("DuckDuckGo è in pausa dopo un blocco", [("duckduckgo", "in pausa")], riprova_alle=fino)
    _usati(["duckduckgo"])
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
        "note": "Google l'ha chiusa ai nuovi clienti e la spegne il 1° gennaio 2027: solo per chi ce l'ha già. "
                "Serve anche l'ID del motore (cx).",
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


# =========================================================== cataloghi diretti
# Comparatori, marketplace e vetrine con la ricerca interna per EAN nell'indirizzo: nessun
# motore in mezzo, quindi funzionano anche quando Google & c. ci bloccano. Si aprono SOLO se i
# motori non hanno dato pagine col codice, uno alla volta, e chi risponde CAPTCHA o 403 va in
# pausa come un motore (metti_in_pausa: 15 min, poi 30, 60…) e compare in Impostazioni fra i
# motori. La pagina di ricerca è un elenco: web_source segue il primo link fatto come una scheda
# («scheda») e la scheda vale solo se dichiara l'EAN (sono marketplace). Con «esterni» i link
# portano ad altri siti (Google Shopping): diventano candidati come quelli di un motore.
#
# «vietato»: il robots.txt del sito vieta la ricerca ai programmi. Questi si usano SOLO con
# l'interruttore «Leggi i cataloghi anche se robots.txt lo vieta» acceso in Impostazioni
# (spento di serie): la scelta e la responsabilità sono di chi gestisce il portale.
# Amazon resta fuori apposta: non dichiara l'EAN in pagina (la scheda non si confermerebbe
# mai) e il suo anti-bot brucia l'indirizzo del server per tutti i clienti.
def _c(dominio, url, scheda="", vietato=False, esterni=False, diretto=False):
    return {"dominio": dominio, "url": url, "scheda": scheda, "vietato": vietato, "esterni": esterni,
            "diretto": diretto}


# Com'è fatto il link di una scheda quando non si conosce il sito: percorsi tipici delle schede
# prodotto. Non importa sbagliare: la pagina seguita deve comunque dichiarare l'EAN.
_GENERICO = (r"/(?:p|pd|dp|d|a|art|item|itm|produkt|produkte|product|products|prodotto|prodotti|produit|"
             r"produits|producto|productos|artikel|offer|offerta|offres?)[/-]|-p-\d|/\d{5,}[/-]|\.html?$")


def _ebay(tld):
    return _c(f"ebay.{tld}", f"https://www.ebay.{tld}/sch/i.html?_nkw={{gtin}}", r"/itm/", vietato=True)



def _shopping(hl, gl):
    return _c("google.com", f"https://www.google.com/search?tbm=shop&q={{gtin}}&hl={hl}&gl={gl}", esterni=True)


def _bing(mkt):
    return _c("bing.com", f"https://www.bing.com/shop?q={{gtin}}&setmkt={mkt}", esterni=True)


def _idealo(dominio, percorso, scheda):
    return _c(dominio, f"https://www.{dominio}{percorso}", scheda)


def _manomano(dominio, percorso):
    return _c(dominio, f"https://www.{dominio}/{percorso}/{{gtin}}", r"/p/", vietato=True)


def _negozio(dominio, percorso, scheda=_GENERICO, vietato=False):
    """Un negozio o comparatore con la ricerca interna nell'indirizzo («?q={gtin}»)."""
    return _c(dominio, f"https://www.{dominio}{percorso}", scheda, vietato=vietato)


# Archivi di codici a barre: l'indirizzo È la scheda (titolo, marca, spesso descrizione e foto, EAN
# dichiarato). Senza motore, senza elenco da seguire. Valgono per tutte le lingue.
ARCHIVI_EAN = [
    _c("go-upc.com", "https://go-upc.com/search?q={gtin}", diretto=True),
    _c("barcodelookup.com", "https://www.barcodelookup.com/{gtin}", diretto=True),
    _c("buycott.com", "https://www.buycott.com/upc/{gtin}", diretto=True),
    _c("ean-search.org", "https://www.ean-search.org/?q={gtin}", diretto=True),
    _c("eandata.com", "https://eandata.com/lookup/{gtin}/", diretto=True),
    _c("upcindex.com", "https://www.upcindex.com/{gtin}", diretto=True),
    _c("barcodespider.com", "https://www.barcodespider.com/{gtin}", diretto=True),
    _c("gtin.info", "https://gtin.info/{gtin}", diretto=True),
    _c("upcitemdb.com", "https://www.upcitemdb.com/upc/{gtin}", diretto=True),
    _c("codecheck.info", "https://www.codecheck.info/product.search?q={gtin}", _GENERICO),
    _c("barcode-list.com", "https://barcode-list.com/barcode/EN/Search.htm?barcode={gtin}", _GENERICO),
]

CATALOGHI = {
    "IT": [
        _idealo("idealo.it", "/cerca?q={gtin}", r"/confronta-prezzi/\d+"),
        _shopping("it", "it"), _bing("it-IT"),
        _ebay("it"),
        _negozio("trovaprezzi.it", "/categoria.aspx?libera={gtin}", r"/prezz", vietato=True),
        _negozio("kelkoo.it", "/ss-{gtin}.html", r"/(?:cp|p)-", vietato=True),
        _negozio("eprice.it", "/search?q={gtin}", r"/d-\d+", vietato=True),
        _manomano("manomano.it", "ricerca"),
        _negozio("shopalike.it", "/cerca?q={gtin}"),
        _negozio("pagomeno.it", "/cerca?q={gtin}"),
        _negozio("prezzoforte.it", "/cerca?q={gtin}"),
        _negozio("shoppydoo.it", "/prezzi?q={gtin}"),
        _negozio("mediaworld.it", "/it/search.html?query={gtin}", r"/product/"),
        _negozio("unieuro.it", "/online/search?q={gtin}", r"/online/.+/p\d+|/p\d+"),
        _negozio("euronics.it", "/search?q={gtin}"),
        _negozio("expert.it", "/search?text={gtin}"),
        _negozio("comet.it", "/search?q={gtin}"),
        _negozio("monclick.it", "/search?q={gtin}"),
        _negozio("yeppon.it", "/catalogsearch/result/?q={gtin}"),
        _negozio("bpm-power.com", "/catalogsearch/result/?q={gtin}"),
        _negozio("leroymerlin.it", "/search?q={gtin}", r"/prodotti/|/p/"),
        _negozio("bricoman.it", "/search?q={gtin}"),
        _negozio("obi-italia.it", "/search/{gtin}/", r"/p/"),
        _negozio("brico.io", "/catalogsearch/result/?q={gtin}"),
        _negozio("ferramenta.online", "/catalogsearch/result/?q={gtin}"),   # termoidraulica
        _negozio("desivero.com", "/catalogsearch/result/?q={gtin}"),
        _negozio("climamarket.it", "/catalogsearch/result/?q={gtin}"),
        _negozio("rexel.it", "/search?q={gtin}"),
        _negozio("sonepar.it", "/search?q={gtin}"),
        _negozio("emmebistore.com", "/catalogsearch/result/?q={gtin}"),
    ],
    "DE": [
        _c("geizhals.de", "https://geizhals.de/?fs={gtin}", r"/a\d+\.html"),
        _idealo("idealo.de", "/preisvergleich/MainSearchProductCategory.html?q={gtin}", r"/preisvergleich/OffersOfProduct/\d+"),
        _shopping("de", "de"), _bing("de-DE"),
        _negozio("billiger.de", "/search?searchstring={gtin}", r"/produkt/|/p\d+"),
        _negozio("guenstiger.de", "/Suche/{gtin}.html"),
        _negozio("preis.de", "/suche/?q={gtin}"),
        _ebay("de"),
        _manomano("manomano.de", "suche"),
        _negozio("otto.de", "/suche/{gtin}/", r"/p/"),
        _negozio("mediamarkt.de", "/de/search.html?query={gtin}", r"/product/"),
        _negozio("saturn.de", "/de/search.html?query={gtin}", r"/product/"),
        _negozio("conrad.de", "/de/search.html?search={gtin}", r"/p/"),
        _negozio("reichelt.de", "/index.html?ACTION=446&q={gtin}"),
        _negozio("alternate.de", "/listing.xhtml?q={gtin}", r"/html/product/"),
        _negozio("notebooksbilliger.de", "/produkte/{gtin}"),
        _negozio("galaxus.de", "/de/search?q={gtin}", r"/product/"),
        _negozio("kaufland.de", "/s/?search_value={gtin}", r"/product/"),
        _negozio("hornbach.de", "/s/{gtin}", r"/p/"),
        _negozio("obi.de", "/search/{gtin}/", r"/p/"),
        _negozio("bauhaus.info", "/suche/produkte?text={gtin}", r"/p/"),
        _negozio("voelkner.de", "/search/?q={gtin}"),
        _negozio("elv.com", "/search?q={gtin}"),
    ],
    "FR": [
        _idealo("idealo.fr", "/prechcat.html?q={gtin}", r"/prix/\d+"),
        _shopping("fr", "fr"), _bing("fr-FR"),
        _negozio("ledenicheur.fr", "/search?search={gtin}", r"/product\.php"),
        _negozio("kelkoo.fr", "/ss-{gtin}.html", r"/(?:cp|p)-", vietato=True),
        _ebay("fr"),
        _manomano("manomano.fr", "recherche"),
        _negozio("cdiscount.com", "/search/10/{gtin}.html", r"/f-|/dp/"),
        _negozio("fnac.com", "/SearchResult/ResultList.aspx?Search={gtin}", r"/a\d+"),
        _negozio("darty.com", "/nav/recherche?text={gtin}", r"/nav/achat/"),
        _negozio("boulanger.com", "/resultats?tr={gtin}", r"/ref/"),
        _negozio("rueducommerce.fr", "/recherche/{gtin}", r"/p-"),
        _negozio("ldlc.com", "/recherche/{gtin}/", r"/fiche/"),
        _negozio("leroymerlin.fr", "/recherche?q={gtin}", r"/produits/|/p/"),
        _negozio("castorama.fr", "/search?term={gtin}", r"/p/"),
        _negozio("materiel.net", "/recherche/{gtin}/", r"/produit/"),
    ],
    "ES": [
        _idealo("idealo.es", "/resultados.html?q={gtin}", r"/precios/\d+"),
        _shopping("es", "es"), _bing("es-ES"),
        _negozio("kelkoo.es", "/ss-{gtin}.html", r"/(?:cp|p)-", vietato=True),
        _ebay("es"),
        _manomano("manomano.es", "busqueda"),
        _negozio("pccomponentes.com", "/buscar/?query={gtin}"),
        _negozio("mediamarkt.es", "/es/search.html?query={gtin}", r"/product/"),
        _negozio("carrefour.es", "/?q={gtin}", r"/p/|/R-"),
        _negozio("leroymerlin.es", "/buscador?q={gtin}", r"/productos/|/p/"),
        _negozio("fnac.es", "/SearchResult/ResultList.aspx?Search={gtin}", r"/a\d+"),
        _negozio("elcorteingles.es", "/search/?s={gtin}", r"/[A-Z0-9]{10,}/"),
        _negozio("worten.es", "/search?query={gtin}", r"/productos/"),
    ],
    "EN": [
        _idealo("idealo.co.uk", "/compare/MainSearchProductCategory.html?q={gtin}", r"/compare/\d+"),
        _shopping("en", "uk"), _shopping("en", "us"), _bing("en-GB"),
        _negozio("pricerunner.com", "/search?q={gtin}", r"/pl/\d+", vietato=True),
        _negozio("pricespy.co.uk", "/search?search={gtin}", r"/product\.php"),
        _negozio("kelkoo.co.uk", "/ss-{gtin}.html", r"/(?:cp|p)-", vietato=True),
        _ebay("co.uk"), _ebay("com"),
        _negozio("currys.co.uk", "/search?q={gtin}", r"/products/"),
        _negozio("argos.co.uk", "/search/{gtin}/", r"/product/"),
        _negozio("screwfix.com", "/search?search={gtin}", r"/p/"),
        _negozio("toolstation.com", "/search?q={gtin}", r"/p\d+"),
        _negozio("newegg.com", "/p/pl?d={gtin}", r"/p/"),
        _negozio("bhphotovideo.com", "/c/search?q={gtin}", r"/c/product/"),
        _negozio("walmart.com", "/search?q={gtin}", r"/ip/", vietato=True),
        _negozio("bestbuy.com", "/site/searchpage.jsp?st={gtin}", r"/site/.+/\d+\.p"),
        _negozio("homedepot.com", "/s/{gtin}", r"/p/"),
        _negozio("lowes.com", "/search?searchTerm={gtin}", r"/pd/"),
        _negozio("target.com", "/s?searchTerm={gtin}", r"/p/"),
    ],
}
# Per tutte le lingue, in coda: la copia internazionale di geizhals ed eBay.com.
CATALOGHI_COMUNI = [_c("geizhals.eu", "https://geizhals.eu/?fs={gtin}", r"/a\d+\.html"), _ebay("com")]
# shortcut: elenco fisso nel codice; quando serve cambiarlo senza rilascio, portarlo in Impostazioni.


def _cataloghi_per(lingua: str) -> list[dict]:
    """Prima gli archivi EAN (una pagina = la scheda, costano poco), poi i cataloghi del paese
    (comparatori, vetrine, marketplace, negozi), in coda quelli internazionali."""
    propri = CATALOGHI.get(lingue.normalizza(lingua), [])
    domini = {c["dominio"] for c in propri}
    return ARCHIVI_EAN + propri + [c for c in CATALOGHI_COMUNI if c["dominio"] not in domini and c not in ARCHIVI_EAN]


def elenco_cataloghi() -> list[dict]:
    """Per Impostazioni: dominio e se il suo robots.txt vieta la ricerca, senza doppioni."""
    visti: dict[str, bool] = {}
    for voci in [ARCHIVI_EAN] + list(CATALOGHI.values()) + [CATALOGHI_COMUNI]:
        for c in voci:
            visti.setdefault(c["dominio"], c["vietato"])
    return [{"dominio": d, "vietato": v} for d, v in sorted(visti.items())]


def candidati_cataloghi(gtin: str, lingua: str = "IT", forza: bool = False) -> list[dict]:
    """Le pagine di ricerca dei cataloghi, nella forma dei candidati di ordina(); fuori chi è in
    pausa e, senza ``forza``, chi vieta la ricerca nel robots.txt."""
    from . import codici as _cod

    if not _cod.checksum_gtin(gtin):        # i cataloghi cercano per EAN, non per codice produttore
        return []
    fuori = []
    for c in _cataloghi_per(lingua):
        if fine_pausa(c["dominio"]) or (c["vietato"] and not forza):
            continue
        fuori.append({"url": c["url"].format(gtin=gtin), "dominio": c["dominio"],
                      "titolo": f"ricerca {gtin} su {c['dominio']}", "punti": -1000, "ancorato": False,
                      "lingua_url": "", "estero": False, "marketplace": not c["esterni"],
                      "da_catalogo": c["scheda"] or ("esterni" if c["esterni"] else "diretto"),
                      "catalogo_esterni": c["esterni"],
                      "forza_robots": forza and c["vietato"], "modello": [], "marche": [], "motori": []})
    return fuori


def segna_catalogo(dominio: str, ok: bool, motivo: str = "") -> None:
    """Esito di una pagina di catalogo, nella stessa tabella dei motori (salute e pause)."""
    if ok:
        _registra(Risposta(ok=[dominio]), "catalogo")
    else:
        _registra(Risposta(ko=[(dominio, motivo or "accesso negato")]), "catalogo")


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
    interrogati = 0      # domande arrivate davvero ai motori (non fermate da una pausa)
    # Giri e pause servono solo ai motori letti senza accordo (SearXNG, DuckDuckGo): un'API a
    # pagamento non blocca, e farla aspettare rallenterebbe e basta.
    a_giri = motore in ("searxng", "duckduckgo")

    async def chiedi(domanda: str, cerca_con=funzione, via=motore, lingua_domanda: str | None = None) -> list[dict]:
        nonlocal interrogati
        await _aspetta_turno(pausa)
        lingua_domanda = lingua if lingua_domanda is None else lingua_domanda
        voce = {"q": domanda, "via": via, "lingua": lingua_domanda or "*"}
        try:
            risposta = await cerca_con(domanda, chiave, extra, lingua_domanda)
        except MotoriBloccati as exc:
            voce.update(errore=str(exc), ko=[list(k) for k in exc.ko])
            if exc.riprova_alle:
                voce["riprova_alle"] = exc.riprova_alle
            else:
                interrogati += 1
                if interrogati == 1 and a_giri:
                    conta_codice(extra)
            ko_visti.extend(exc.ko)
            if not exc.riprova_alle:       # «in pausa» non è un nuovo blocco: non si conta
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
        interrogati += 1
        if interrogati == 1 and a_giri:
            conta_codice(extra)        # il codice è arrivato ai motori: conta nel giro
        _registra(risposta, via)
        ko_visti.extend(risposta.ko)
        ok_visti.update(risposta.ok)
        ancorati = sum(1 for r in risposta.risultati if _ancorato(gtin, r))
        voce.update(risultati=len(risposta.risultati), ancorati=ancorati, ok=risposta.ok,
                    ko=[list(k) for k in risposta.ko], ms=risposta.ms)
        if risposta.pausa:
            voce["pausa"] = [[m, fino] for m, fino in risposta.pausa]
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
    # DuckDuckGo diretto, che è un'altra strada verso l'indice. Solo se DuckDuckGo non è
    # in pausa e non è stato interrogato da poco: altrimenti si allunga il suo blocco.
    ddg_libero = (not fine_pausa("duckduckgo")
                  and time.time() - _ULTIMO_USO.get("duckduckgo", 0.0) >= INTERVALLO_MINIMO["duckduckgo"])
    if (motore == "searxng" and ddg_libero and not any(_ancorato(gtin, r) for r in grezzi)
            and extra.get("ripiego_ddg", True)):
        try:
            aggiungi(await chiedi(formulazioni(gtin)[0][0], _duckduckgo, "duckduckgo"))
        except MotoriBloccati as exc:
            bloccato = bloccato or exc

    # Riserva a pagamento: SearXNG bloccato o senza due risultati col codice, e c'è una chiave
    # Brave Search API. La stessa domanda va all'API, che non blocca mai.
    chiave_riserva = (extra.get("riserva_chiave") or "").strip()
    if (motore == "searxng" and chiave_riserva and not fine_pausa("brave api")
            and sum(1 for r in grezzi if _ancorato(gtin, r)) < 2):
        async def _riserva(domanda: str, _chiave: str, ex: dict, lingua_domanda: str) -> Risposta:
            try:
                risposta = await _brave(domanda, chiave_riserva, ex, lingua_domanda or lingua)
            except MotoriBloccati as exc:
                # Credito finito o troppe richieste: non è il «brave» di SearXNG, non va messo in pausa quello.
                raise MotoriBloccati(str(exc), [("brave api", w) for _, w in exc.ko]) from exc
            for r in risposta.risultati:
                r["motori"] = ["brave api"]
            risposta.ok = ["brave api"] if risposta.ok else []
            return risposta
        try:
            aggiungi(await chiedi(formulazioni(gtin, lingua, "brave")[0][0], _riserva, "brave api", lingua))
        except MotoriBloccati as exc:
            bloccato = bloccato or exc

    ancorati = [r for r in grezzi if _ancorato(gtin, r)]

    # Niente col codice: i cataloghi (idealo, eBay, geizhals…) si cercano da soli, senza
    # motore in mezzo. Vanno in coda ai candidati; se i motori sono bloccati sono l'unica
    # strada, e il blocco resta scritto sui candidati (se non cavano niente si rimanda).
    cataloghi = candidati_cataloghi(gtin, lingua, bool(extra.get("cataloghi_forza"))) \
        if not ancorati and not extra.get("solo_codice") else []

    # Niente col codice, e i motori buoni sui codici hanno risposto con un
    # blocco: il codice non è stato cercato davvero (i risultati di Bing sui
    # numeri non contano: sono a caso). Meglio rimandare che dire «non esiste».
    if not ancorati:
        buoni_ok = ok_visti & MOTORI_BUONI
        if (bloccato is not None or ko_visti) and not buoni_ok:
            nomi = sorted({f"{m} ({w})" for m, w in ko_visti})
            # Nessuna domanda è arrivata ai motori (erano tutti in pausa): si riprende a fine
            # pausa, e il codice non si conta come rinviato.
            riprova = bloccato.riprova_alle if bloccato is not None and not interrogati else None
            if cataloghi:
                if diario is not None:
                    diario.nota("Motori bloccati: provo i cataloghi (" + ", ".join(c["dominio"] for c in cataloghi) + ")")
                for c in cataloghi:
                    c["motori_bloccati"] = {"nomi": nomi, "ko": [list(k) for k in ko_visti], "riprova_alle": riprova}
                if diario is not None:
                    diario.candidati(len(grezzi), 0, cataloghi)
                return cataloghi
            raise MotoriBloccati(
                "motori di ricerca bloccati: " + (", ".join(nomi[:5]) or str(bloccato)), ko_visti,
                riprova_alle=riprova)
        if not grezzi and errori and not cataloghi:
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
    if (len([c for c in ordinati if c["ancorato"]]) < 2 and _corpus_valido(gtin, grezzi)
            and not extra.get("solo_codice")):
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
    if cataloghi:
        if diario is not None:
            diario.nota("Nessuna pagina col codice dai motori: in coda i cataloghi (" + ", ".join(c["dominio"] for c in cataloghi) + ")")
        for c in cataloghi:
            c["modello"], c["marche"] = pezzi, marche
        ordinati = ordinati + cataloghi

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
    return contingentati[:14] + [c for c in contingentati[14:] if c.get("da_catalogo")]
