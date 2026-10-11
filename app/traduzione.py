"""Traduzione delle schede nella lingua scelta per il catalogo, senza IA esterne.

Si appoggia a LibreTranslate, che gira come container tuo accanto a SearXNG:
nessuna chiave obbligatoria, nessun costo, niente dati che escono. Se l'indirizzo
non è configurato la scheda resta nella lingua originale, con un avviso. La
lingua di arrivo è quella delle schede (italiano, inglese, francese, tedesco,
spagnolo): un cliente che lavora in inglese riceve le schede in inglese.

    docker run -d --name libretranslate -p 5000:5000 libretranslate/libretranslate

In Impostazioni si imposta l'indirizzo (es. http://libretranslate:5000).
"""
import logging
import re

import httpx

log = logging.getLogger("catasync.traduzione")

TIMEOUT = 60.0  # le descrizioni lunghe su hardware modesto richiedono tempo


async def _rileva_lingua(testo: str, base: str, chiave: str) -> str:
    """Chiede a LibreTranslate in che lingua è il testo. In dubbio: inglese.

    Si usa /detect con lingua esplicita invece di source=auto nella traduzione:
    su certe installazioni (LT_LOAD_ONLY, stringhe corte) l'auto risponde 500.
    """
    payload: dict = {"q": testo[:500]}
    if chiave:
        payload["api_key"] = chiave
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{base}/detect", json=payload)
        if resp.status_code < 400:
            dati = resp.json()
            if isinstance(dati, list) and dati and dati[0].get("language"):
                return str(dati[0]["language"])
    except (httpx.RequestError, ValueError):
        pass
    return "en"


async def traduci_testi(testi: list[str], url: str, chiave: str = "", verso: str = "it") -> list[str] | None:
    """Traduce una lista di testi in un colpo solo. None se qualcosa va storto.

    La lingua di partenza si chiede prima a /detect e si passa esplicita:
    source=auto su certe installazioni risponde 500. format=html conserva i
    <br> e i tag che stanno dentro le descrizioni.
    """
    base = (url or "").rstrip("/")
    if not base or not testi:
        return None
    verso = (verso or "it").lower()[:2]

    campione = max(testi, key=len) if testi else ""
    lingua = await _rileva_lingua(campione, base, chiave)
    if lingua == verso:
        return list(testi)  # già nella lingua giusta: niente da fare

    async def _traduci(source: str) -> list[str] | None:
        payload: dict = {"q": testi, "source": source, "target": verso, "format": "html"}
        if chiave:
            payload["api_key"] = chiave
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(f"{base}/translate", json=payload)
        except httpx.RequestError as exc:
            log.warning("LibreTranslate non raggiungibile: %s", exc.__class__.__name__)
            return None
        if resp.status_code >= 400:
            log.warning("LibreTranslate HTTP %s (source=%s): %s", resp.status_code, source, resp.text[:200])
            return None
        try:
            dati = resp.json()
        except ValueError:
            return None
        tradotti = dati.get("translatedText")
        if isinstance(tradotti, list) and len(tradotti) == len(testi):
            return [str(t) for t in tradotti]
        if isinstance(tradotti, str) and len(testi) == 1:
            return [tradotti]
        return None

    out = await _traduci(lingua)
    if out is None and lingua != "en":
        # La lingua rilevata non è caricata sul server: si ripiega sull'inglese.
        out = await _traduci("en")
    return out


# Token che NON devono mai passare dal traduttore: tutto ciò che contiene cifre
# (codici articolo, EAN, misure «1x230 V», «IP54», «5-62») e le sigle tutte
# maiuscole («AUTOADAPT», «FLOWADAPT», «RCM»). Argos li storpia: «97924465»
# tornava «979244», e i codici sono la parte che deve restare intatta.
_TOKEN_PROTETTO = re.compile(r"\S*\d\S*|\b[A-Z][A-Z0-9]{2,}\b")


def _ha_cifre(testo: str) -> bool:
    return bool(re.search(r"\d", testo or ""))


def _proteggi(testo: str) -> tuple[str, list[str]]:
    """Sostituisce i token protetti con segnaposto che il traduttore lascia stare."""
    protetti: list[str] = []

    def _sost(m: re.Match) -> str:
        protetti.append(m.group(0))
        return f" QZX{len(protetti) - 1}QZX "

    return _TOKEN_PROTETTO.sub(_sost, testo), protetti


def _ripristina(testo: str, protetti: list[str]) -> str | None:
    """Rimette i token al posto dei segnaposto. None se il traduttore ne ha persi."""
    out = testo
    for i, orig in enumerate(protetti):
        segnaposto = re.compile(rf"\s*QZX\s*{i}\s*QZX\s*", re.I)
        if not segnaposto.search(out):
            return None
        out = segnaposto.sub(f" {orig} ", out, count=1)
    if re.search(r"QZX\s*\d+\s*QZX", out, re.I):
        return None  # segnaposto avanzati: qualcosa non torna
    return re.sub(r"\s{2,}", " ", out).strip()


def _cifre_intatte(originale: str, tradotto: str) -> bool:
    """Ogni sequenza di cifre dell'originale deve stare anche nel tradotto."""
    for seq in re.findall(r"\d+", originale or ""):
        if seq not in (tradotto or ""):
            return False
    return True


async def traduci_sicuro(testi: list[str], url: str, chiave: str = "", verso: str = "it") -> list[str]:
    """Traduce una lista di testi senza rovinare numeri e codici.

    Strategia, per ogni testo: 1) se non ha cifre né sigle si traduce e basta;
    2) altrimenti si traduce con i token protetti da segnaposto e si ripristina;
    3) se il traduttore ha perso i segnaposto, si riprova in chiaro e si accetta
    solo se tutte le cifre dell'originale sono sopravvissute; 4) altrimenti si
    tiene l'originale. Un numero giusto vale più di una frase tradotta.
    Restituisce SEMPRE una lista lunga quanto l'input (mai None)."""
    if not testi:
        return []
    risultato = list(testi)

    # passata 1: tutti insieme con protezione
    protetti_per_testo = [_proteggi(t) if t else ("", []) for t in testi]
    out = await traduci_testi([p for p, _ in protetti_per_testo], url, chiave, verso=verso)
    da_riprovare: list[int] = []
    if out:
        for i, (tr, (_, prot)) in enumerate(zip(out, protetti_per_testo)):
            if not testi[i]:
                continue
            rip = _ripristina(tr, prot) if prot else tr
            if rip is not None and _cifre_intatte(testi[i], rip):
                risultato[i] = rip
            else:
                da_riprovare.append(i)
    else:
        da_riprovare = [i for i, t in enumerate(testi) if t]

    # passata 2: i falliti in chiaro, accettati solo con le cifre intatte
    if da_riprovare:
        out2 = await traduci_testi([testi[i] for i in da_riprovare], url, chiave, verso=verso)
        if out2:
            for k, i in enumerate(da_riprovare):
                if out2[k] and _cifre_intatte(testi[i], out2[k]):
                    risultato[i] = out2[k]
                # altrimenti resta l'originale
    return risultato


async def traduci_scheda(scheda: dict, url: str, chiave: str = "", verso: str = "it") -> bool:
    """Traduce nella lingua delle schede titolo, descrizione e specifiche.

    Tutto in una richiesta sola: i testi viaggiano come lista e tornano nello
    stesso ordine. True se la traduzione è riuscita e la scheda è stata aggiornata.
    """
    titolo = scheda.get("title") or ""
    descrizione = scheda.get("description") or ""
    caratteristiche = scheda.get("attributes") or []

    # I valori con cifre (codici, misure) non si traducono proprio: «97924465»
    # resta «97924465», «6 m3/h» resta «6 m3/h». Si traducono i nomi delle
    # specifiche e i valori solo-testo («Water» → «Acqua»).
    testi = [titolo, descrizione]
    idx_nome: list[int] = []
    idx_val: list[int] = []
    for voce in caratteristiche:
        idx_nome.append(len(testi))
        testi.append(voce.get("name") or "")
        val = voce.get("value") or ""
        if _ha_cifre(val):
            idx_val.append(-1)            # non si traduce: resta com'è
        else:
            idx_val.append(len(testi))
            testi.append(val)

    out = await traduci_sicuro(testi, url, chiave, verso=verso)
    if not out or len(out) != len(testi):
        return False

    scheda["title"] = (out[0] or titolo)[:255]
    scheda["description"] = out[1] or descrizione
    for k, voce in enumerate(caratteristiche):
        nome = out[idx_nome[k]]
        if nome:
            voce["name"] = nome
        if idx_val[k] >= 0 and out[idx_val[k]]:
            voce["value"] = out[idx_val[k]]
    return True
