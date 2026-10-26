"""Le foto che WooCommerce scarica dal portale.

WooCommerce le immagini non le riceve: le scarica da sé, dall'indirizzo che gli si passa,
come un robot qualsiasi (niente Referer, «WordPress/x.y» come programma). Molti siti dei
produttori le negano a chi non arriva dalle loro pagine, e WooCommerce per una sola foto
negata rifiuta il prodotto intero («Error getting remote image …»). Il portale invece le
legge come un browser, dalla pagina in cui le ha trovate.

Quindi le foto passano di qui: il portale le scarica, controlla che siano davvero
immagini e le mette a un indirizzo suo, pubblico e non indovinabile, da cui WooCommerce
le prende senza storie. Lo stesso indirizzo serve alle foto caricate dal computer.

Due cartelle in UPLOAD_DIR:
- ``foto-temporanee``: quelle del Magazzino, che vanno sul sito subito; due giorni e via.
- ``foto-schede``: quelle delle schede del catalogo, che possono aspettare in revisione
  settimane. Restano finché una scheda le usa; quelle che nessuno usa più si buttano
  dopo una settimana.
"""
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from . import config, rete
from .html_sicuro import tipo_immagine

log = logging.getLogger("catasync.foto")

CARTELLA_MAGAZZINO = "foto-temporanee"
CARTELLA_SCHEDE = "foto-schede"
CARTELLE = (CARTELLA_SCHEDE, CARTELLA_MAGAZZINO)

MAX_BYTES = 8 * 1024 * 1024
DURATA_MAGAZZINO = 2 * 24 * 3600
# Una foto di scheda che nessuna riga usa più (scheda pubblicata o foto tolta) si tiene
# ancora una settimana: un passo indietro dell'utente non deve trovarla sparita.
DURATA_SCHEDE_ORFANE = 7 * 24 * 3600
PULIZIA_OGNI = 6 * 3600

# Formati che WooCommerce accetta di serie e che si riconoscono dai byte.
FORMATI = (".jpg", ".png", ".webp", ".gif")
NOME_VALIDO = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp|gif)")
PERCORSO_PUBBLICO = "/media/foto/"

_ultima_pulizia = 0.0


def tipo(blob: bytes) -> str:
    """Estensione vera dai byte, solo fra i formati che WooCommerce prende."""
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    t = tipo_immagine(blob) if blob else ""
    return t if t in FORMATI else ""


def attivo() -> bool:
    """Senza PUBLIC_URL il sito non saprebbe dove prendere le foto."""
    return bool(config.PUBLIC_URL)


def indirizzo(nome: str) -> str:
    return f"{config.PUBLIC_URL}{PERCORSO_PUBBLICO}{nome}"


def del_portale(url: str) -> bool:
    return bool(config.PUBLIC_URL) and str(url or "").startswith(config.PUBLIC_URL + PERCORSO_PUBBLICO)


def nome_da_indirizzo(url: str) -> str:
    """«…/media/foto/abc….jpg» → «abc….jpg», solo per le foto del portale."""
    if not del_portale(url):
        return ""
    nome = str(url)[len(config.PUBLIC_URL + PERCORSO_PUBBLICO):].split("?", 1)[0]
    return nome if NOME_VALIDO.fullmatch(nome) else ""


def percorso(nome: str) -> Path | None:
    """Il file di una foto del portale, in qualunque delle due cartelle stia."""
    if not NOME_VALIDO.fullmatch(nome or ""):
        return None
    for cartella in CARTELLE:
        p = config.UPLOAD_DIR / cartella / nome
        if p.is_file():
            return p
    return None


def salva(blob: bytes, cartella: str = CARTELLA_SCHEDE) -> str:
    """Scrive la foto e ne restituisce il nome. Il nome viene dal contenuto: la stessa
    foto caricata o scaricata due volte è un file solo, e senza la foto in mano il nome
    non si indovina."""
    est = tipo(blob)
    if not est:
        raise ValueError("Formati accettati: JPG, PNG, WebP, GIF.")
    nome = hashlib.sha256(blob).hexdigest()[:32] + est
    dove = config.UPLOAD_DIR / cartella
    dove.mkdir(parents=True, exist_ok=True)
    file = dove / nome
    if file.is_file():
        file.touch()          # torna giovane: qualcuno la sta usando di nuovo
    else:
        file.write_bytes(blob)
    return nome


# ----------------------------------------------------------------- pulizia
def pulisci(in_uso=None, forza: bool = False) -> int:
    """Butta le foto vecchie. Quelle del Magazzino dopo due giorni; quelle delle schede
    solo se nessuna riga le usa più (`in_uso(nome) -> bool`) e da almeno una settimana.
    Gira al massimo ogni sei ore, salvo `forza`."""
    global _ultima_pulizia
    adesso = time.time()
    if not forza and adesso - _ultima_pulizia < PULIZIA_OGNI:
        return 0
    _ultima_pulizia = adesso
    tolte = 0
    for cartella, durata in ((CARTELLA_MAGAZZINO, DURATA_MAGAZZINO), (CARTELLA_SCHEDE, DURATA_SCHEDE_ORFANE)):
        dove = config.UPLOAD_DIR / cartella
        if not dove.is_dir():
            continue
        for f in dove.glob("*"):
            try:
                if not f.is_file() or f.stat().st_mtime > adesso - durata:
                    continue
                if cartella == CARTELLA_SCHEDE and (in_uso is None or in_uso(f.name)):
                    continue
                f.unlink()
                tolte += 1
            except OSError:
                pass
    return tolte


def in_uso_nel_catalogo(db):
    """La funzione per pulisci(): una foto è in uso se una riga del catalogo la nomina."""
    from sqlalchemy import select

    from .models import JobItem

    def _usata(nome: str) -> bool:
        return db.scalar(select(JobItem.id).where(JobItem.normalized.contains(nome)).limit(1)) is not None
    return _usata


# ------------------------------------------------------- foto prese dal web
async def _scarica(client: httpx.AsyncClient, url: str, referer: str) -> bytes | None:
    parti = urlparse(url)
    intestazioni = {
        "User-Agent": rete.UA_BROWSER,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": referer or f"{parti.scheme}://{parti.netloc}/",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
    }
    try:
        async with client.stream("GET", url, headers=intestazioni) as r:
            if r.status_code >= 400:
                return None
            dati = bytearray()
            async for pezzo in r.aiter_bytes():
                dati.extend(pezzo)
                if len(dati) > MAX_BYTES:
                    return None
            return bytes(dati)
    except (httpx.HTTPError, rete.IndirizzoNonPubblico):
        return None


async def riospita(urls: list[str], referer: str = "", sito: str = "") -> tuple[list[str], list[str]]:
    """Porta sul portale le foto prese dal web. Restituisce (indirizzi da dare a WooCommerce,
    foto che il portale non è riuscito a prendere).

    Le foto già del portale restano come sono. Una foto che il portale non riesce a scaricare,
    o che non è in un formato che WooCommerce accetta (AVIF, SVG…), resta col suo indirizzo
    originale: ci prova WooCommerce, e se la rifiuta ci pensa il ripiego in wc_rest. Le foto
    che stanno già sul sito del cliente (`sito`, il suo indirizzo) non si toccano."""
    if not attivo() or not urls:
        return list(urls or []), []
    host_sito = (urlparse(sito).hostname or "").lower() if sito else ""

    async def _una(client, url):
        if del_portale(url) or not str(url).startswith(("http://", "https://")):
            return url, True
        if host_sito and (urlparse(url).hostname or "").lower() == host_sito:
            return url, True
        dati = await _scarica(client, url, referer)
        if not dati or not tipo(dati):
            return url, False
        try:
            return indirizzo(salva(dati, CARTELLA_SCHEDE)), True
        except (OSError, ValueError) as exc:
            log.warning("Foto %s non salvata: %s", url, exc)
            return url, False

    async with rete.client_pubblico(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True,
                                    http2=rete.HTTP2) as client:
        esiti = await asyncio.gather(*(_una(client, u) for u in urls))
    return [u for u, _ in esiti], [orig for orig, (_, ok) in zip(urls, esiti) if not ok]


def nel_messaggio(urls: list[str], messaggio: str) -> list[str]:
    """Le foto che WooCommerce nomina nel suo errore («Error getting remote image <url>»)."""
    testo = unquote(messaggio or "").replace("&#038;", "&").replace("&amp;", "&")
    return [u for u in urls if u and (u in testo or unquote(u) in testo)]
