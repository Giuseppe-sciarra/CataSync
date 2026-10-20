"""Lettura cortese delle pagine dei siti, con il motivo di ogni rifiuto.

«Cortese» vuol dire tre cose, e qui sono regole, non intenzioni:

1. **robots.txt secondo lo standard (RFC 9309)**: vale il gruppo scritto per noi
   o quello per «*», vince la regola più specifica, ``*`` e ``$`` funzionano.
   Prima si usava il lettore di Python, che ragiona per prima riga che combacia
   e ignora i caratteri jolly: certi siti risultavano vietati per sbaglio, altri
   permessi per sbaglio.
2. **Una pagina alla volta per sito**, con una pausa fra una richiesta e l'altra,
   e pochissime pagine per prodotto.
3. **Nessun aggiramento delle protezioni**: se un sito risponde con una verifica
   anti-bot (Cloudflare, Akamai, DataDome…), lo si registra e si passa oltre.
   Il browser headless serve alle pagine fatte solo di JavaScript, non a forzare
   chi ha deciso di non farsi leggere.

Ogni lettura restituisce una ``Pagina`` che dice com'è andata: codice HTTP,
server, blocco riconosciuto, robots.txt. È il materiale con cui la diagnosi
spiega perché un EAN non si trova.
"""
import asyncio
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

import httpx

from . import lingue

log = logging.getLogger("catasync.rete")

UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
# Nome con cui il portale si riconosce nei robots.txt.
TOKEN_ROBOTS = "tdsynccatalog"
TIMEOUT = httpx.Timeout(20.0, connect=8.0)
INTERVALLO_MINIMO = 1.5          # secondi fra due richieste allo stesso sito
MAX_BYTES = 4 * 1024 * 1024      # oltre non è una scheda prodotto
ROBOTS_TTL = 3600                # robots.txt riletto al massimo una volta all'ora
ROBOTS_TTL_IRRAGGIUNGIBILE = 600

try:  # HTTP/2 se c'è h2: è quello che parla un browser vero
    import h2  # noqa: F401
    HTTP2 = True
except ImportError:  # pragma: no cover
    HTTP2 = False


# ============================================================ solo internet
# Gli indirizzi da aprire arrivano da fuori (risultati dei motori, link e foto
# dichiarati dalle pagine): una pagina furba potrebbe indicare 192.168.1.1,
# il database del compose o il pannello di Proxmox. Pagine, robots.txt, foto e
# browser si aprono solo verso indirizzi pubblici, anche dopo un rimando.
class IndirizzoNonPubblico(httpx.RequestError):
    """L'indirizzo porta alla rete interna: non si apre."""


_esiti_host: dict[str, tuple[float, bool]] = {}


async def host_pubblico(host: str) -> bool:
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa")):
        return False
    try:
        ip = ipaddress.ip_address(host)
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return ip.is_global
    except ValueError:
        pass
    if "." not in host:
        return False      # nome corto: è un container o una macchina della LAN
    adesso = time.monotonic()
    noto = _esiti_host.get(host)
    if noto and adesso - noto[0] < 600:
        return noto[1]
    try:
        risolti = await asyncio.get_running_loop().getaddrinfo(host, None)
        indirizzi = {ipaddress.ip_address(r[4][0].split("%")[0]) for r in risolti}
        esito = bool(indirizzi) and all((getattr(i, "ipv4_mapped", None) or i).is_global for i in indirizzi)
    except (OSError, ValueError):
        esito = True      # DNS che non risponde: deciderà la connessione, non è un rischio
    _esiti_host[host] = (adesso, esito)
    return esito


async def _solo_pubblici(request: httpx.Request) -> None:
    if not await host_pubblico(request.url.host):
        raise IndirizzoNonPubblico(f"indirizzo interno: {request.url.host}", request=request)


GANCI = {"request": [_solo_pubblici]}


def client_pubblico(**opzioni) -> httpx.AsyncClient:
    """Un client httpx che apre solo indirizzi pubblici (rimandi compresi)."""
    return httpx.AsyncClient(event_hooks=GANCI, **opzioni)


def intestazioni(lingua: str = "IT", referer: str = "") -> dict:
    """Le intestazioni di un Chrome normale, con la lingua delle schede."""
    h = {
        "User-Agent": UA_BROWSER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": lingue.ACCEPT_LANGUAGE.get(lingue.normalizza(lingua), lingue.ACCEPT_LANGUAGE["IT"]),
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site" if referer else "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="129", "Not=A?Brand";v="8", "Google Chrome";v="129"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if referer:
        h["Referer"] = referer
    return h


def dominio(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ================================================================ robots.txt
class Robots:
    """Regole di un robots.txt lette secondo l'RFC 9309."""

    def __init__(self, gruppi: list[tuple[list[str], list[tuple[bool, str]]]]):
        self.gruppi = gruppi

    @classmethod
    def da_testo(cls, testo: str) -> "Robots":
        gruppi: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agenti: list[str] = []
        regole: list[tuple[bool, str]] = []
        dentro_regole = False
        for grezza in (testo or "")[:512_000].splitlines():
            riga = grezza.split("#", 1)[0].strip()
            if ":" not in riga:
                continue
            chiave, valore = riga.split(":", 1)
            chiave, valore = chiave.strip().lower(), valore.strip()
            if chiave == "user-agent":
                if dentro_regole:
                    gruppi.append((agenti, regole))
                    agenti, regole, dentro_regole = [], [], False
                agenti.append(valore.lower())
            elif chiave in ("allow", "disallow"):
                if not agenti:
                    continue  # regola fuori da un gruppo: non vale per nessuno
                dentro_regole = True
                if valore:  # «Disallow:» vuoto non vieta niente
                    regole.append((chiave == "allow", valore))
        if agenti:
            gruppi.append((agenti, regole))
        return cls(gruppi)

    def _regole_per(self, token: str) -> list[tuple[bool, str]] | None:
        nostre = [r for agenti, regole in self.gruppi if token in agenti for r in regole]
        if any(token in agenti for agenti, _ in self.gruppi):
            return nostre
        generali = [r for agenti, regole in self.gruppi if "*" in agenti for r in regole]
        if any("*" in agenti for agenti, _ in self.gruppi):
            return generali
        return None

    @staticmethod
    def _combacia(schema: str, percorso: str) -> bool:
        ancorata = schema.endswith("$")
        if ancorata:
            schema = schema[:-1]
        regex = "^" + ".*".join(re.escape(pezzo) for pezzo in schema.split("*"))
        if ancorata:
            regex += "$"
        return re.match(regex, percorso) is not None

    def consente(self, url: str, token: str = TOKEN_ROBOTS) -> bool:
        parti = urlparse(url)
        percorso = (parti.path or "/") + (("?" + parti.query) if parti.query else "")
        if percorso == "/robots.txt":
            return True
        regole = self._regole_per(token.lower())
        if not regole:
            return True
        percorso_dec = unquote(percorso)
        migliore: tuple[int, bool] | None = None   # (lunghezza, consente)
        for consente, schema in regole:
            if not (self._combacia(schema, percorso) or self._combacia(unquote(schema), percorso_dec)):
                continue
            lunghezza = len(schema)
            # Vince la regola più lunga; a parità vince Allow.
            if migliore is None or lunghezza > migliore[0] or (lunghezza == migliore[0] and consente):
                migliore = (lunghezza, consente)
        return True if migliore is None else migliore[1]


# origine → (quando, Robots | None, stato) con stato: ok | assente | irraggiungibile
_robots: dict[str, tuple[float, Robots | None, str]] = {}
_ultimo_accesso: dict[str, float] = {}
_turno_lock = asyncio.Lock()


async def robots_per(url: str, client: httpx.AsyncClient | None = None) -> tuple[Robots | None, str]:
    """robots.txt del sito, con le regole dell'RFC 9309 sui codici di risposta.

    - 2xx: si leggono le regole
    - 4xx (compresi 401, 403, 429): file «non disponibile», si può leggere tutto
    - 5xx o rete giù: file «irraggiungibile», per ora non si legge niente
    """
    parti = urlparse(url)
    origine = f"{parti.scheme}://{parti.netloc}"
    adesso = time.monotonic()
    voce = _robots.get(origine)
    if voce:
        ttl = ROBOTS_TTL_IRRAGGIUNGIBILE if voce[2] == "irraggiungibile" else ROBOTS_TTL
        if adesso - voce[0] < ttl:
            return voce[1], voce[2]

    proprio = client is None
    client = client or client_pubblico(timeout=httpx.Timeout(10.0, connect=6.0), follow_redirects=True,
                                       http2=HTTP2, max_redirects=5)
    try:
        resp = await client.get(f"{origine}/robots.txt", headers=intestazioni())
        if resp.status_code >= 500:
            stato, regole = "irraggiungibile", None
        elif resp.status_code >= 400:
            stato, regole = "assente", None
        else:
            testo = resp.text
            if "<html" in testo[:600].lower():
                stato, regole = "assente", None   # il sito risponde con una pagina: nessuna regola
            else:
                stato, regole = "ok", Robots.da_testo(testo)
    except IndirizzoNonPubblico:
        stato, regole = "interno", None
    except httpx.HTTPError:
        stato, regole = "irraggiungibile", None
    finally:
        if proprio:
            await client.aclose()
    _robots[origine] = (adesso, regole, stato)
    return regole, stato


async def attendi_turno(url: str) -> None:
    """Una richiesta per sito ogni secondo e mezzo: non si martella nessuno."""
    host = urlparse(url).netloc
    async with _turno_lock:
        scorso = _ultimo_accesso.get(host, 0.0)
        attesa = INTERVALLO_MINIMO - (time.monotonic() - scorso)
        _ultimo_accesso[host] = time.monotonic() + max(0.0, attesa)
    if attesa > 0:
        await asyncio.sleep(attesa)


# ============================================================ blocchi anti-bot
NOMI_BLOCCO = {
    "cloudflare": "protezione Cloudflare (verifica anti-bot)",
    "akamai": "protezione Akamai (accesso negato ai programmi)",
    "datadome": "protezione DataDome (verifica anti-bot)",
    "imperva": "protezione Imperva/Incapsula",
    "sucuri": "firewall Sucuri",
    "perimeterx": "protezione PerimeterX/HUMAN",
    "captcha": "il sito chiede un captcha",
    "limite": "troppe richieste: il sito ci ha rallentati",
    "waf": "il sito rifiuta i programmi automatici",
}


def riconosci_blocco(stato: int, headers, html: str) -> str:
    """Il sito ha risposto con una barriera anti-bot invece che con la pagina?"""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    server = (h.get("server") or "").lower()
    testa = (html or "")[:8000].lower()
    titolo = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", testa, re.S)
    if m:
        titolo = re.sub(r"\s+", " ", m.group(1)).strip()

    if (h.get("cf-mitigated") or "").lower() == "challenge":
        return "cloudflare"
    if any(t in titolo for t in ("just a moment", "attention required", "un momento", "ci siamo quasi",
                                 "un instant", "einen moment", "un momento…")) and (
            "cloudflare" in server or "cf-chl" in testa or "challenge-platform" in testa or "cf_chl" in testa):
        return "cloudflare"
    if "/cdn-cgi/challenge-platform/" in testa and stato >= 400:
        return "cloudflare"
    if "datadome" in server or "x-datadome" in h or "captcha-delivery.com" in testa:
        return "datadome"
    if "_incapsula_resource" in testa or "incapsula incident" in testa:
        return "imperva"
    if "sucuri website firewall" in testa or ("x-sucuri-id" in h and stato >= 400):
        return "sucuri"
    if "px-captcha" in testa or "perimeterx" in testa and stato >= 400:
        return "perimeterx"
    if "akamaighost" in server and stato in (403, 429):
        return "akamai"
    if stato == 403 and "access denied" in titolo and "reference #" in testa:
        return "akamai"
    if "/errors/validatecaptcha" in testa or (
            "captcha" in testa and stato in (202, 403, 429, 503) and len(html or "") < 40_000):
        return "captcha"
    if stato == 429:
        return "limite"
    return ""


# ==================================================================== pagina
@dataclass
class Pagina:
    url: str
    url_finale: str = ""
    stato: int = 0
    html: str = ""
    server: str = ""
    blocco: str = ""
    errore: str = ""
    robots: str = ""          # "" | vietato | irraggiungibile
    via_browser: bool = False
    ms: int = 0
    note: list[str] = field(default_factory=list)

    @property
    def leggibile(self) -> bool:
        return bool(self.html) and not self.blocco and not self.robots and (self.via_browser or self.stato < 400)

    def motivo(self) -> str:
        """In parole povere, perché la pagina non si è potuta leggere."""
        if self.robots == "vietato":
            return "il sito vieta la lettura automatica (robots.txt)"
        if self.robots == "irraggiungibile":
            return "robots.txt non raggiungibile: per regola non si legge"
        if self.blocco:
            return NOMI_BLOCCO.get(self.blocco, self.blocco) + (" anche col browser" if self.via_browser else "")
        if self.errore:
            return {"ConnectTimeout": "il sito non risponde (timeout)", "ReadTimeout": "il sito è troppo lento (timeout)",
                    "ConnectError": "sito irraggiungibile", "RemoteProtocolError": "il sito ha chiuso la connessione",
                    "TooManyRedirects": "troppi rimandi",
                    "IndirizzoNonPubblico": "indirizzo della rete interna: non si apre"}.get(
                self.errore, f"errore di rete ({self.errore})")
        if self.stato == 404 or self.stato == 410:
            return "pagina inesistente (HTTP %d)" % self.stato
        if self.stato >= 400:
            return f"il sito risponde HTTP {self.stato}"
        if not self.html:
            return "pagina vuota"
        return ""

    def diario(self) -> dict:
        voce = {"url": self.url[:300], "dominio": dominio(self.url), "http": self.stato}
        for chiave in ("server", "blocco", "errore", "robots"):
            valore = getattr(self, chiave)
            if valore:
                voce[chiave] = valore[:60]
        if self.via_browser:
            voce["browser"] = True
        voce["ms"] = self.ms
        return voce


def decodifica(risposta: httpx.Response, grezzo: bytes | None = None) -> str:
    """Testo della pagina con la codifica giusta.

    Parecchi siti servono ancora ISO-8859-1 senza dirlo nelle intestazioni:
    letti come UTF-8 diventano «S5610 � un telefono». Il charset dichiarato nel
    meta della pagina vince su quello indovinato."""
    grezzo = risposta.content if grezzo is None else grezzo
    # UTF-8 valido è UTF-8, qualunque cosa dichiari la pagina: certi siti
    # scrivono «iso-8859-2» nel meta e servono UTF-8, e «vorgedämmt» diventava
    # «vorgedĂ¤mmt». Una sequenza a più byte valida per caso in un'altra
    # codifica, in pratica, non esiste.
    try:
        return grezzo.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start >= len(grezzo) - 4:          # pagina troncata a metà carattere
            return grezzo[:exc.start].decode("utf-8", "replace")
    testa = grezzo[:4096].decode("ascii", "ignore").lower()
    m = re.search(r'charset["\']?\s*=\s*["\']?\s*([a-z0-9_-]+)', testa)
    dichiarata = m.group(1) if m else ""
    m2 = re.search(r"charset=([a-z0-9_-]+)", (risposta.headers.get("content-type") or "").lower())
    da_intestazione = m2.group(1) if m2 else ""
    for codifica in (dichiarata, da_intestazione, "cp1252", "latin-1"):
        if not codifica:
            continue
        try:
            testo = grezzo.decode(codifica)
        except (LookupError, UnicodeDecodeError):
            continue
        if testo.count("�") > max(3, len(testo) // 2000):
            continue
        return testo
    return grezzo.decode("utf-8", "replace")


# ==================================================================== browser
# Modalità che il browser accetta: «ricca» (con user-agent, lingua e senza
# scaricare immagini e font, molto più veloce) o «base» per le versioni di
# browserless che rifiutano i campi in più con un 400.
_modo_browser: dict[str, str] = {}


async def renderizza(url: str, render_url: str, lingua: str = "IT") -> Pagina:
    """Apre la pagina con un Chrome vero e riporta l'HTML dopo il JavaScript."""
    base = (render_url or "").rstrip("/")
    pagina = Pagina(url=url, url_finale=url, via_browser=True)
    if not base:
        pagina.errore = "browser non configurato"
        return pagina
    if not await host_pubblico(urlparse(url).hostname or ""):
        pagina.errore = "IndirizzoNonPubblico"
        return pagina
    t0 = time.monotonic()
    ricca = {
        "url": url,
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": 30000},
        "userAgent": UA_BROWSER,
        "setExtraHTTPHeaders": {"Accept-Language": lingue.ACCEPT_LANGUAGE.get(lingue.normalizza(lingua))},
        "rejectResourceTypes": ["image", "media", "font"],
    }
    semplice = {"url": url, "gotoOptions": {"waitUntil": "networkidle2", "timeout": 30000}}
    tentativi = [semplice] if _modo_browser.get(base) == "base" else [ricca, semplice]
    resp = None
    for corpo in tentativi:
        try:
            async with httpx.AsyncClient(timeout=55.0) as client:
                resp = await client.post(f"{base}/content", json=corpo)
        except httpx.HTTPError as exc:
            pagina.errore = f"browser non raggiungibile ({exc.__class__.__name__})"
            pagina.ms = int((time.monotonic() - t0) * 1000)
            return pagina
        if resp.status_code == 400 and corpo is ricca:
            _modo_browser[base] = "base"
            continue
        break
    pagina.ms = int((time.monotonic() - t0) * 1000)
    if resp is None or resp.status_code >= 400:
        pagina.stato = resp.status_code if resp is not None else 0
        pagina.errore = f"il browser ha risposto HTTP {pagina.stato}"
        return pagina
    pagina.stato = 200
    pagina.html = resp.text
    pagina.blocco = riconosci_blocco(200, {}, pagina.html)
    return pagina


# ==================================================================== sessione
class Sessione:
    """Le letture di una ricerca: stesso client (connessioni e cookie riusati),
    stessa lingua, un tetto alle aperture col browser che sono lente."""

    def __init__(self, lingua: str = "IT", render_url: str = "", max_browser: int = 3):
        self.lingua = lingue.normalizza(lingua)
        self.render_url = (render_url or "").rstrip("/")
        self.max_browser = max_browser
        self.usi_browser = 0
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Sessione":
        self.client = client_pubblico(timeout=TIMEOUT, follow_redirects=True, http2=HTTP2, max_redirects=6)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client:
            await self.client.aclose()

    @property
    def browser_disponibile(self) -> bool:
        return bool(self.render_url) and self.usi_browser < self.max_browser

    async def browser(self, url: str) -> Pagina:
        self.usi_browser += 1
        await attendi_turno(url)
        return await renderizza(url, self.render_url, self.lingua)

    async def apri(self, url: str, rendi_se_serve: bool = True, ignora_robots: bool = False) -> Pagina:
        """Legge una pagina come la leggerebbe un browser, se il sito lo consente.

        ``ignora_robots`` vale solo per le ricerche interne dei cataloghi, se l'amministratore
        l'ha acceso in Impostazioni: il controllo sugli indirizzi interni resta."""
        assert self.client is not None
        pagina = Pagina(url=url)
        regole, stato_robots = await robots_per(url, self.client)
        if stato_robots == "interno":
            pagina.errore = "IndirizzoNonPubblico"
            return pagina
        if stato_robots == "irraggiungibile" and not ignora_robots:
            pagina.robots = "irraggiungibile"
            return pagina
        if regole is not None and not regole.consente(url) and not ignora_robots:
            pagina.robots = "vietato"
            return pagina

        await attendi_turno(url)
        t0 = time.monotonic()
        try:
            async with self.client.stream("GET", url, headers=intestazioni(self.lingua)) as resp:
                grezzo = b""
                async for pezzo in resp.aiter_bytes():
                    grezzo += pezzo
                    if len(grezzo) > MAX_BYTES:
                        break
                pagina.stato = resp.status_code
                pagina.url_finale = str(resp.url)
                pagina.server = resp.headers.get("server", "")
                tipo = (resp.headers.get("content-type") or "").lower()
                if "pdf" in tipo or url.lower().split("?")[0].endswith(".pdf"):
                    pagina.errore = "documento PDF, non una pagina"
                else:
                    pagina.html = decodifica(resp, grezzo)
                pagina.blocco = riconosci_blocco(resp.status_code, resp.headers, pagina.html)
        except httpx.HTTPError as exc:
            pagina.errore = exc.__class__.__name__
        pagina.ms = int((time.monotonic() - t0) * 1000)

        if not rendi_se_serve or pagina.errore == "documento PDF, non una pagina":
            return pagina

        # Il browser serve ai gusci JavaScript senza contenuto e ai guasti del
        # server (5xx, timeout), MAI a forzare chi ci ha respinti: un 403, un
        # 429 o una verifica anti-bot vogliono dire «non leggere», e si rispetta.
        respinta = pagina.stato in (401, 403, 429) or bool(pagina.blocco)
        if respinta or pagina.errore == "IndirizzoNonPubblico":
            return pagina
        guasta = pagina.stato in (500, 502, 503, 504, 520, 521, 522, 523, 524)
        magra = pagina.stato < 400 and pagina.html and (
            len(pagina.html) < 3000 or "<img" not in pagina.html.lower())
        if (guasta or magra or pagina.errore in ("ReadTimeout", "RemoteProtocolError")) and self.browser_disponibile:
            resa = await self.browser(url)
            resa.note.append("riaperta col browser")
            if resa.html and not resa.blocco and (guasta or pagina.errore or len(resa.html) > len(pagina.html or "")):
                resa.server = pagina.server
                return resa
            if resa.blocco and (guasta or pagina.errore):
                pagina.blocco = resa.blocco
                pagina.via_browser = True
        return pagina
