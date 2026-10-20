"""Icecat come prima fonte: una sola utenza (Open o Full Icecat) per tutto il portale.

Si chiede la scheda all'API Live in JSON col codice EAN:
    GET https://live.icecat.biz/api?lang=IT&shopname=<utente>&GTIN=<ean>
con i token nelle intestazioni (``api-token`` per Full Icecat, ``content-token`` per i
file). Senza token si vede solo Open Icecat, i marchi che pagano Icecat per essere
pubblicati. Se in italiano mancano nome o descrizione si prende l'inglese e lo si traduce
con LibreTranslate, come per la ricerca sul web. Quello che manca ancora (foto, testi)
lo completa la ricerca sul web, che parte dopo.
"""
import asyncio
import logging
import re
import time

import httpx

from . import config, lingue, mapper
from .html_sicuro import scheda_sicura

log = logging.getLogger("catasync.icecat")

_turno = asyncio.Lock()
_ultima = 0.0


class IcecatErrore(Exception):
    """Utenza sbagliata, token non validi, servizio che non risponde: da sistemare in Impostazioni."""


class IcecatAssente(Exception):
    """Il prodotto su Icecat non c'è, o non con questa utenza: si passa alla ricerca sul web."""


# Codici d'errore dell'API Live (manuale «Icecat JSON Product Requests»).
ASSENTE = {
    8: "non c'è su Icecat",
    9: "su Icecat c'è, ma solo con Full Icecat",
    12: "su Icecat c'è, ma fuori dal settore del tuo abbonamento",
    14: "il marchio ne limita la pubblicazione su Icecat",
    15: "su Icecat non è ancora pubblicato",
    16: "codice non valido per Icecat",
    17: "su Icecat mancano i dati del prodotto",
}
MANCA_EAN = "Icecat non ha questo EAN nel suo catalogo"
MANCA_CODICE = "Icecat non ha questo codice produttore"
ERRORE = {
    1: "manca il nome utente Icecat",
    3: "nome utente Icecat sconosciuto",
    5: "indirizzo del server non autorizzato nel profilo Icecat",
    18: "token Icecat non trovato",
    19: "token Icecat scritto male",
}


async def _aspetta_turno() -> None:
    global _ultima
    async with _turno:
        attesa = config.ICECAT_THROTTLE - (time.monotonic() - _ultima)
        if attesa > 0:
            await asyncio.sleep(attesa)
        _ultima = time.monotonic()


def _codice_errore(dati: dict) -> int | None:
    for chiave in ("Code", "code", "StatusCode", "statusCode"):
        try:
            return int(dati.get(chiave))
        except (TypeError, ValueError):
            continue
    return None


async def chiedi(gtin: str, lingua: str, cfg: dict, marca: str = "", codice: str = "") -> dict:
    """La risposta grezza di Icecat in una lingua, per EAN oppure per marca + codice produttore."""
    if not cfg.get("utente"):
        raise IcecatErrore("manca il nome utente Icecat")
    params = {"lang": lingue.normalizza(lingua), "shopname": cfg["utente"]}
    if marca and codice:
        params.update(Brand=marca, ProductCode=codice)
    else:
        params["GTIN"] = gtin
    intestazioni = {"Accept": "application/json", "User-Agent": "TD-Catalog-Sync"}
    if cfg.get("api_token"):
        intestazioni["api-token"] = cfg["api_token"]
    if cfg.get("content_token"):
        intestazioni["content-token"] = cfg["content_token"]
    await _aspetta_turno()
    try:
        async with httpx.AsyncClient(timeout=config.ICECAT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(config.ICECAT_ENDPOINT, params=params, headers=intestazioni)
    except httpx.HTTPError as exc:
        raise IcecatErrore(f"Icecat non risponde ({exc.__class__.__name__})") from exc
    try:
        dati = resp.json()
    except ValueError:
        dati = {}
    dati = dati if isinstance(dati, dict) else {}
    if resp.status_code < 400 and isinstance(dati.get("data"), dict) and dati["data"]:
        return dati

    numero = _codice_errore(dati)
    messaggio = str(dati.get("Message") or dati.get("message") or dati.get("Error") or "")[:200]
    basso = messaggio.lower()
    if resp.status_code == 429:
        raise IcecatErrore("Icecat sta limitando le richieste: riprova fra poco")
    if numero in ERRORE:
        raise IcecatErrore(ERRORE[numero])
    if "full icecat" in basso:
        raise IcecatAssente(ASSENTE[9])
    if "vertical" in basso:
        raise IcecatAssente(ASSENTE[12])
    if re.search(r"\btoken\b|username|user name|unknown user|shopname|app.?key", basso):
        raise IcecatErrore("Icecat ha rifiutato l'utenza o i token" + (f" ({messaggio})" if messaggio else ""))
    # «The GTIN can not be found» (HTTP 400), «not present in the Icecat database» (404): il
    # prodotto con quel codice nel catalogo di Icecat non c'è. Non è un guasto.
    manca = MANCA_CODICE if (marca and codice) else MANCA_EAN
    if any(f in basso for f in ("not present", "not found", "not be found", "cannot be found")):
        raise IcecatAssente(manca)
    if numero in ASSENTE:
        raise IcecatAssente(manca if numero == 8 else ASSENTE[numero])
    if resp.status_code == 404 or (resp.status_code == 400 and ("gtin" in basso or "product" in basso)):
        raise IcecatAssente(manca)
    if resp.status_code in (401, 403):
        raise IcecatErrore("Icecat ha rifiutato l'utenza o i token" + (f" ({messaggio})" if messaggio else ""))
    raise IcecatErrore(f"Icecat ha risposto HTTP {resp.status_code}" + (f": {messaggio}" if messaggio else ""))


def _lingua_descrizione(dati: dict) -> str:
    radice = mapper._root(dati)
    valore = mapper._dig(radice, "GeneralInfo.Description.Language") or ""
    return str(valore).strip().upper()[:2]


def codice_buono(codice: str) -> bool:
    """Un codice produttore che vale la pena chiedere a Icecat: lettere e cifre, 5-30 caratteri
    («AE040RXEDEG/EU» sì; «333100», spesso il codice interno del negozio, no)."""
    import re

    intero = re.sub(r"[^a-z0-9]", "", (codice or "").lower())
    return 5 <= len(intero) <= 30 and bool(re.search(r"\d", intero)) and bool(re.search(r"[a-z]", intero))


async def scheda(gtin: str, lingua: str, cfg: dict, trad: dict | None = None,
                 marca: str = "", codice: str = "") -> dict:
    """La scheda nel formato del portale, nella lingua delle schede quando si può.

    Per EAN; con ``marca`` e ``codice`` per codice produttore (Icecat a volte ha il prodotto
    ma non quell'EAN collegato)."""
    from . import web_source

    lingua = lingue.normalizza(lingua)
    dati = await chiedi(gtin, lingua, cfg, marca, codice)
    finale = mapper.normalize(dati, gtin)
    lingua_desc = _lingua_descrizione(dati)
    if finale.get("description") and lingua_desc and lingua_desc != lingua:
        finale["_desc_estera"] = True

    # In italiano mancano nome, descrizione o specifiche? Si chiede l'inglese e lo si traduce.
    if lingua != "EN" and not (finale.get("title") and finale.get("description") and finale.get("attributes")):
        try:
            inglese = mapper.normalize(await chiedi(gtin, "EN", cfg, marca, codice), gtin)
        except IcecatAssente:
            inglese = {}
        if not finale.get("title") and inglese.get("title"):
            finale["title"] = inglese["title"]
            finale["estera"] = True
        if not finale.get("description") and inglese.get("description"):
            finale["description"] = inglese["description"]
            finale["_desc_estera"] = True
        if not finale.get("attributes") and inglese.get("attributes"):
            finale["attributes"] = inglese["attributes"]
            finale["_spec_estera"] = True
        for u in inglese.get("images") or []:
            if u not in finale["images"]:
                finale["images"].append(u)

    if not finale.get("title"):
        raise IcecatAssente(ASSENTE[17])

    # La foto mancante non è più un avviso: se Icecat non ce l'ha, la cerca il web dopo.
    finale["warnings"] = [w for w in finale.get("warnings") or [] if "immagine" not in w.lower()]
    trad = trad or {}
    if finale.get("estera") or finale.get("_desc_estera") or finale.get("_spec_estera"):
        if trad.get("url") and await web_source._traduci_verso(finale, trad, lingua):
            finale["warnings"].append("Parte della scheda Icecat era in inglese ed è stata tradotta in automatico: rileggila.")
        elif not trad.get("url"):
            finale["warnings"].append("Parte della scheda Icecat è in inglese. Imposta LibreTranslate in Impostazioni "
                                      "per tradurla in automatico.")
        else:
            finale["warnings"].append("Parte della scheda Icecat è in inglese e la traduzione automatica non è riuscita: "
                                      "controllala.")
    for chiave in ("estera", "_desc_estera", "_spec_estera"):
        finale.pop(chiave, None)

    finale["source_ref"] = finale.pop("icecat_id", "") or ""
    finale["origine_dominio"] = "icecat.biz"
    finale["origine_url"] = ""
    finale["conferma"] = "forte"
    finale["lingua"] = lingua
    finale["attributes"] = (finale.get("attributes") or [])[:40]
    finale["images"] = (finale.get("images") or [])[:8]
    log.info("Codice %s trovato su Icecat (%s foto, %s specifiche)", gtin, len(finale["images"]),
             len(finale["attributes"]))
    return scheda_sicura(finale)


def riassunto(s: dict) -> dict:
    """Per la prova in Impostazioni: cosa ha dato Icecat, in breve."""
    return {
        "title": s.get("title", ""),
        "brand": s.get("brand", ""),
        "mpn": s.get("mpn", ""),
        "categoria": (s.get("category") or {}).get("name", ""),
        "descrizione": len(s.get("description") or ""),
        "foto": len(s.get("images") or []),
        "specifiche": len(s.get("attributes") or []),
        "avvisi": s.get("warnings") or [],
        "immagine": (s.get("images") or [""])[0],
    }
