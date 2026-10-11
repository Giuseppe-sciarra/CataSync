"""Lettura delle pagine prodotto e costruzione della scheda.

Non è uno scraper che indovina dove stanno le cose leggendo l'HTML: quello si rompe
al primo restyling. Legge prima di tutto i **dati strutturati** che i siti pubblicano
apposta per essere letti dalle macchine:

  1. JSON-LD schema.org/Product — il formato che Google Shopping pretende, quindi
     ce l'ha praticamente chiunque venda online
  2. microdata itemprop — la versione vecchia, ancora diffusa
  3. Open Graph — l'ultima spiaggia: dà titolo, descrizione e una foto

e, quando sono avari, quello che la pagina mostra (tabelle di specifiche, paragrafi).

Le pagine si scaricano con ``rete`` (robots.txt rispettato, una pagina alla volta
per sito, nessun aggiramento delle protezioni) e ogni esito finisce nel diario
della ricerca: è così che un «non trovato» sa dire perché.

Regola non negoziabile: **se la pagina non conferma il codice, la scheda si scarta.**
Meglio nessun dato che la descrizione di un altro prodotto scritta sul catalogo.
"""
import asyncio
import json
import logging
import re
import zlib
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import httpx

from . import diagnosi, lingue, rete
from .html_sicuro import scheda_sicura

log = logging.getLogger("catasync.web")


class WebError(Exception):
    pass


class NonTrovato(Exception):
    pass


# Compatibilità: chi importava questi nomi da qui li trova ancora.
decodifica = rete.decodifica


# --------------------------------------------------------- lettura della pagina
def _solo_cifre(valore) -> str:
    return "".join(c for c in str(valore or "") if c.isdigit())


def _testo(valore) -> str:
    """Da qualunque forma schema.org a una stringa pulita."""
    if valore is None:
        return ""
    if isinstance(valore, str):
        return unescape(re.sub(r"\s+", " ", valore)).strip()
    if isinstance(valore, (int, float)):
        return str(valore)
    if isinstance(valore, dict):
        for chiave in ("name", "@value", "value", "text", "url"):
            if chiave in valore:
                return _testo(valore[chiave])
    if isinstance(valore, list) and valore:
        return _testo(valore[0])
    return ""


def ripara_url(u: str) -> str:
    """Indirizzi rotti per errori comuni dei siti: «https://sito//sito/cdn/x.jpg»
    (un indirizzo senza schema incollato dopo il dominio) torna «https://sito/cdn/x.jpg»."""
    u = (u or "").strip()
    m = re.match(r"^(https?):\/\/[^/]+\/\/([a-z0-9-]+(?:\.[a-z0-9-]+)+\/.*)$", u, re.I)
    if m:
        return f"{m.group(1)}://{m.group(2)}"
    return u


def _immagini(valore, base: str) -> list[str]:
    grezze = valore if isinstance(valore, list) else [valore]
    fuori: list[str] = []
    for elemento in grezze:
        url = _testo(elemento.get("url") if isinstance(elemento, dict) else elemento)
        if not url:
            continue
        url = urljoin(base, url)
        if url.startswith("http") and url not in fuori:
            fuori.append(url)
    return fuori[:10]


def _prodotti_jsonld(html: str) -> list[dict]:
    """Tutti i nodi di tipo Product, anche annidati dentro @graph o liste."""
    trovati: list[dict] = []

    def scava(nodo):
        if isinstance(nodo, dict):
            tipo = nodo.get("@type", "")
            tipo = tipo if isinstance(tipo, str) else " ".join(str(t) for t in tipo)
            if "Product" in tipo:
                trovati.append(nodo)
            for valore in nodo.values():
                scava(valore)
        elif isinstance(nodo, list):
            for valore in nodo:
                scava(valore)

    for blocco in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I
    ):
        testo = blocco.strip()
        if not testo:
            continue
        try:
            scava(json.loads(testo))
        except ValueError:
            # Qualche CMS ci infila commenti o virgole di troppo: si tenta una ripulita
            try:
                scava(json.loads(re.sub(r",\s*([}\]])", r"\1", testo)))
            except ValueError:
                continue
    return trovati


def _microdata(html: str) -> dict:
    campi: dict[str, str] = {}
    for prop, valore in re.findall(
        r'itemprop=["\'](\w+)["\'][^>]*content=["\']([^"\']{2,400})["\']', html, re.I
    ):
        campi.setdefault(prop.lower(), unescape(valore).strip())
    return campi


def _opengraph(html: str) -> dict:
    campi: dict[str, str] = {}
    for prop, valore in re.findall(
        r'<meta[^>]+(?:property|name)=["\']og:(\w+)["\'][^>]+content=["\']([^"\']*)["\']', html, re.I
    ):
        campi.setdefault(prop.lower(), unescape(valore).strip())
    if "description" not in campi:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
        if m:
            campi["description"] = unescape(m.group(1)).strip()
    return campi


def lingua_scheda(html: str, testo: str, url: str) -> str:
    """Lingua in cui è scritta la pagina (IT, EN, FR, DE, ES; XX = altra)."""
    return lingue.della_pagina(html, testo, url, _dominio(url))


def in_italiano(html: str, testo: str, url: str) -> bool:
    """Compatibilità col vecchio nome: la pagina è in italiano?"""
    return lingua_scheda(html, testo, url) in ("IT", "")


def _norm(testo: str) -> str:
    return "".join(c for c in str(testo or "").lower() if c.isalnum())


def modello_combacia(pezzi: list[str], scheda: dict, html: str) -> bool:
    """Il codice articolo della pagina corrisponde al modello che cercavamo?

    Si confrontano i pezzi, non le stringhe intere: «FAST 14 FI» sulla pagina e
    «fast», «14» ricavati dai titoli sono lo stesso prodotto, ma non sarebbero mai
    risultati uguali con un confronto secco."""
    if not pezzi:
        return False
    candidati_pagina = [
        _norm(scheda.get("mpn")),
        _norm(scheda.get("sku")),
        _norm(scheda.get("productID")),
        _norm(scheda.get("model")),
        # Anche il nome: tante pagine non dichiarano il codice articolo ma lo
        # scrivono nel titolo, tipo «GT-S5610 | Samsung». Vale lo stesso controllo
        # severo, quindi «Fast Evo X» non passa per «Fast 14 FI».
        _norm(_testo(scheda.get("name"))),
    ]
    cercati = [_norm(p) for p in pezzi if len(_norm(p)) >= 2]
    if not cercati:
        return False

    numerici = [p for p in cercati if any(c.isdigit() for c in p)]
    testuali = [p for p in cercati if not any(c.isdigit() for c in p)]
    if not numerici:
        # Senza un pezzo con cifre non c'è un modello: non si conferma niente,
        # altrimenti basterebbe la marca e finiremmo per importare il prodotto sbagliato.
        return False

    # La marca si confronta col campo marca, non col codice articolo: dentro
    # «GT-S5610» la parola «samsung» non ci sarà mai.
    # Solo i campi dichiarati: dentro l'HTML grezzo «samsung» si trova anche
    # nell'indirizzo di un'immagine, e passerebbe la pagina di un altro marchio.
    contesto = _norm(_testo(scheda.get("brand")) + " " + _testo(scheda.get("name")))
    marca_ok = not testuali or any(p in contesto for p in testuali)

    for codice in candidati_pagina:
        if len(codice) < 4:
            continue
        if all(p in codice for p in numerici) and marca_ok:
            return True
    return False


# Frasi da venditore e prezzi: le regole stanno in lingue.py, in cinque lingue.
PREZZO = lingue.PREZZO
SPIE_NEGOZIO_TESTO = lingue.NEGOZIO_TUTTE
testo_da_negozio = lingue.testo_da_negozio
ripulisci_da_negozio = lingue.ripulisci_da_negozio


def codice_credibile(codice: str, pezzi: list[str]) -> bool:
    """Il codice articolo è del produttore o un identificativo interno del sito?

    «pcid-036537» è il numero di catalogo di puntocellulare, non un codice Samsung.
    Si riconosce perché non contiene niente del modello che stiamo cercando."""
    if not codice:
        return False
    if not pezzi:
        return True
    pulito = _norm(codice)
    return any(_norm(p) in pulito for p in pezzi if len(_norm(p)) >= 3)


# --------------------------------------------------- coerenza fra più pagine
def _token_titolo(testo: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{2,}", (testo or "").lower())}


# Parole che descrivono la categoria o il marketing, non il modello: non contano
# come «distintive» nel confronto fra titoli, altrimenti «sistema di
# pressurizzazione» accomunerebbe tutte le pompe della stessa marca.
PAROLE_GENERICHE = {
    "sistema", "sistemi", "elettronico", "elettronica", "elettrico", "elettrica",
    "pressurizzazione", "pressione", "pompa", "pompe", "gruppo", "acqua", "gas",
    "digitale", "automatico", "automatica", "professionale", "portata", "prevalenza",
    "monofase", "trifase", "verticale", "orizzontale", "bianco", "nero", "inox",
    "litri", "watt", "volt", "schuko", "kit", "set", "serie", "linea", "nuovo",
    "nuova", "new", "originale", "ricambio", "ricambi", "the", "and", "per", "con",
    "senza", "scheda", "tecnica", "prodotto", "articolo", "modello",
}


def schede_coerenti(a: dict, b: dict) -> bool:
    """Due pagine parlano dello stesso prodotto? Senza IA: si incrociano i token.

    Coerente non vuol dire uguale: «CMBE TWIN 5-62 1x230 Schuko» e «Grupo de
    presión CMBE Twin 5-62» condividono i pezzi del modello. Se uno dei due
    titoli non riporta il codice («DAB Esybox Pop — sistema di pressurizzazione»)
    bastano due parole distintive in comune oltre alla marca, purché marca,
    codice produttore e specifiche non si contraddicano.
    """
    ta, tb = _token_titolo(a.get("title")), _token_titolo(b.get("title"))
    comuni = ta & tb

    marca_a, marca_b = _norm(a.get("brand")), _norm(b.get("brand"))
    if marca_a and marca_b and marca_a not in marca_b and marca_b not in marca_a:
        return False

    # Un token con cifre in comune è il modello: prova forte. Ma solo se è un
    # codice vero (lettere+cifre come «dab60220687», o cifre lunghe come
    # «99219423»): i «230» e «240» dei voltaggi accomunano anche varianti diverse.
    modello_condiviso = any(
        (any(c.isdigit() for c in t) and any(c.isalpha() for c in t))
        or (t.isdigit() and len(t) >= 5)
        for t in comuni
    )
    token_marca = _token_titolo((a.get("brand") or "") + " " + (b.get("brand") or ""))
    distintive = {
        t for t in comuni
        if len(t) >= 3 and not t.isdigit() and t not in token_marca and t not in PAROLE_GENERICHE
    }
    if not modello_condiviso and len(distintive) < 2:
        return False

    # Varianti in conflitto: «Esybox POP» contro «Esybox MINI» condividono la
    # serie ma sono prodotti diversi. Se ciascun titolo ha il suo qualificatore
    # corto che l'altro non ha, non sono la stessa cosa.
    if not modello_condiviso:
        def _qualificatori(token: set) -> set:
            return {t for t in token if t.isalpha() and 3 <= len(t) <= 5
                    and t not in token_marca and t not in PAROLE_GENERICHE}
        solo_a = _qualificatori(ta) - tb
        solo_b = _qualificatori(tb) - ta
        if solo_a and solo_b:
            return False

    mpn_a, mpn_b = _norm(a.get("mpn")), _norm(b.get("mpn"))
    if mpn_a and mpn_b and len(mpn_a) >= 4 and len(mpn_b) >= 4:
        if mpn_a not in mpn_b and mpn_b not in mpn_a:
            return False

    # Specifiche con lo stesso nome ma valori diversi: se si contraddicono su
    # più della metà delle voci in comune, non sono lo stesso prodotto.
    va = {_norm(v.get("name")): _norm(v.get("value")) for v in (a.get("attributes") or []) if v.get("name")}
    vb = {_norm(v.get("name")): _norm(v.get("value")) for v in (b.get("attributes") or []) if v.get("name")}
    in_comune = [k for k in va if k in vb and va[k] and vb[k]]
    if len(in_comune) >= 3:
        discordi = sum(1 for k in in_comune if va[k] != vb[k] and va[k] not in vb[k] and vb[k] not in va[k])
        if discordi * 2 > len(in_comune):
            return False
    return True


def _completezza(scheda: dict) -> int:
    """Quanto è piena una scheda: decide quale fa da base nella fusione."""
    return (
        len(scheda.get("description") or "")
        + 120 * len(scheda.get("images") or [])
        + 40 * len(scheda.get("attributes") or [])
        + (60 if scheda.get("mpn") else 0)
        + (0 if scheda.get("estera") else 200)  # meglio partire dall'italiano
    )


def _unione_immagini(base_imgs: list[str], altre: list[dict], massimo: int = 5) -> list[str]:
    """Somma le foto di più pagine coerenti, senza doppioni evidenti.

    Le foto sono neutre rispetto alla lingua, quindi si prendono da chiunque
    abbia confermato il prodotto. Il doppione si riconosce dall'URL identico o
    dallo stesso nome di file (la stessa foto del produttore rigirata da più
    negozi ha quasi sempre lo stesso nome); due scatti diversi passano entrambi.
    """
    viste_url: set[str] = set()
    visti_file: set[str] = set()
    fuori: list[str] = []

    def nome_file(u: str) -> str:
        try:
            percorso = urlparse(u).path
        except ValueError:
            return u.lower()
        nome = percorso.rsplit("/", 1)[-1].lower()
        return nome or u.lower()

    tutte = list(base_imgs or [])
    for altra in altre:
        tutte.extend(altra.get("images") or [])
    for u in tutte:
        if not u or u in viste_url or not _img_utile(u):
            continue
        f = nome_file(u)
        if f in visti_file:
            continue
        viste_url.add(u)
        visti_file.add(f)
        fuori.append(u)
        if len(fuori) >= massimo:
            break
    return fuori


def _qualita_descrizione(desc: str, titolo: str) -> int:
    """Punteggio di bontà di una descrizione: quante parole di prosa vera porta,
    oltre al titolo e ai codici. Una meta-description che ripete
    «99220847 Grundfos 99220847» vale poco anche se è lunga."""
    if not desc:
        return 0
    d = desc.strip()
    parole = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", d)
    if len(parole) < 8:
        return 0  # troppe poche parole vere: è un'etichetta, non una descrizione
    # frazione di token che sono codici/numeri: alta = elenco di codici, non prosa
    tutti = re.findall(r"\S+", d)
    codici = sum(1 for t in tutti if re.search(r"\d", t))
    if tutti and codici / len(tutti) > 0.5:
        return 0
    # parole "di contenuto" (≥3 lettere) che NON compaiono nel titolo: sono la
    # prosa vera. Se sono poche, la descrizione è solo il titolo ripetuto.
    t_parole = {p.lower() for p in re.findall(r"[a-zA-ZÀ-ÿ]{3,}", titolo)}
    fuori_titolo = [p for p in parole if p.lower() not in t_parole]
    if len(fuori_titolo) < 8:
        return 0
    return len(fuori_titolo)


def fondi_schede(schede: list[dict]) -> dict:
    """Unisce le pagine coerenti: la più completa fa da base, le altre riempiono.

    La descrizione mancante si prende dalla più lunga fra le coerenti, le immagini
    idem, le specifiche si sommano (a parità di nome vince la base). Niente viene
    inventato: è tutto materiale delle pagine che hanno confermato lo stesso EAN.
    """
    ordinate = sorted(schede, key=_completezza, reverse=True)

    def _peso_reale(x: dict) -> int:
        return (len(x.get("description") or "")
                + 200 * len(x.get("images") or [])
                + 60 * len(x.get("attributes") or []))

    # La base porta l'identità (titolo, EAN) e le foto: si preferisce una pagina
    # italiana se ne esiste una decente, così il nome resta in italiano. Ma il
    # CONTENUTO (descrizione, specifiche) si prende dal migliore in assoluto,
    # anche estero — che spesso è più ricco — e si traduce.
    italiane = [x for x in ordinate if not x.get("estera")]
    # Nessuna nella lingua delle schede: meglio una che si sa tradurre (le cinque
    # lingue gestite) di una in svedese o in russo, che resterebbe com'è.
    traducibili = [x for x in ordinate if x.get("lingua_pagina") in lingue.LINGUE]
    scelta = italiane[0] if italiane else (traducibili[0] if traducibili else ordinate[0])
    base = dict(scelta)
    altre = [x for x in ordinate if x is not scelta]
    origine_reale = base.get("origine_dominio", "")

    # DESCRIZIONE: vince la MIGLIORE (prosa vera), non la più lunga. Una
    # meta-description che ripete il titolo e i codici viene scartata anche se
    # è lunga. Se la vincente è estera, si marca per la traduzione.
    cand_desc = sorted(
        ordinate,
        key=lambda x: _qualita_descrizione(x.get("description") or "", x.get("title") or ""),
        reverse=True,
    )
    fonte_desc = None
    if cand_desc and _qualita_descrizione(cand_desc[0].get("description") or "", cand_desc[0].get("title") or "") > 0:
        vinc = cand_desc[0]
        base["description"] = vinc["description"]
        base["_desc_estera"] = bool(vinc.get("estera"))
        fonte_desc = vinc
    else:
        # Nessuna descrizione decente: meglio vuota che titolo ripetuto.
        base["description"] = ""
        base["_desc_estera"] = False

    # SPECIFICHE: vince il set con più specifiche TECNICHE VERE (con unità di
    # misura o valori numerici), non il più numeroso in assoluto — così le 3
    # righe ERP di un gestionale (Codice/Tipo/Prezzi) perdono contro le
    # specifiche vere di un produttore, anche se sono in pari numero.
    def _spec_tecniche(x: dict) -> int:
        n = 0
        for v in x.get("attributes") or []:
            val = (v.get("value") or "")
            nome = (v.get("name") or "").lower()
            if re.search(r"\d", val) or any(u in val.lower() for u in
                    ("kw", "watt", " v", "volt", "m3", "m³", "l/", "bar", " kg", " mm", "hz", "°c", "rpm", "ip")):
                if not spazzatura(f"{nome} {val}"):
                    n += 1
        return n
    cand_spec = sorted(ordinate, key=lambda x: (_spec_tecniche(x), len(x.get("attributes") or [])), reverse=True)
    fonte_spec = None
    if cand_spec and (cand_spec[0].get("attributes") or []):
        sorgente = cand_spec[0]
        fonte_spec = sorgente
        base_spec = list(sorgente.get("attributes") or [])
        base["_spec_estera"] = bool(sorgente.get("estera"))
        viste = {_norm(v.get("name")) for v in base_spec if v.get("name")}
        for altra in ordinate:
            if altra is sorgente or bool(altra.get("estera")) != bool(sorgente.get("estera")):
                continue
            for voce in altra.get("attributes") or []:
                k = _norm(voce.get("name"))
                if k and k not in viste:
                    viste.add(k)
                    base_spec.append(voce)
        base["attributes"] = specifiche_pulite(base_spec, "", base.get("title") or "")[:40]
    else:
        base["_spec_estera"] = bool(base.get("estera"))

    # L'origine segue il CONTENUTO: se descrizione e specifiche vengono
    # entrambe da un'altra pagina (spesso il produttore estero), «trovata su»
    # deve dire quella, non la base scelta solo per il nome.
    if fonte_desc and fonte_spec and fonte_desc is fonte_spec and fonte_desc is not base:
        if fonte_desc.get("origine_dominio"):
            origine_reale = fonte_desc["origine_dominio"]

    # Campi neutri: si prendono da chi ce li ha.
    for altra in altre:
        if not base.get("brand") and altra.get("brand"):
            base["brand"] = altra["brand"]
        if not base.get("mpn") and altra.get("mpn"):
            base["mpn"] = altra["mpn"]
        if not (base.get("category") or {}).get("name") and (altra.get("category") or {}).get("name"):
            base["category"] = altra["category"]

    # FOTO: tutte le pagine qui sono già state confermate come lo STESSO prodotto.
    # Prima si prendevano solo dalla scheda base: bastava che avesse una foto mediocre
    # per ignorare quattro immagini migliori trovate subito dopo. Ora si uniscono le
    # gallerie coerenti, dando priorità al produttore e togliendo i doppioni.
    fonti_foto = [c for c in ordinate if c.get("images")]
    fonti_foto.sort(
        key=lambda c: (
            _somiglia_produttore(c),
            bool(c.get("ean_dichiarato")),
            len(c.get("images") or []),
        ),
        reverse=True,
    )
    if fonti_foto:
        prima = fonti_foto[0]
        base["images"] = _unione_immagini(
            list(prima.get("images") or []),
            [c for c in fonti_foto[1:]],
            massimo=6,
        )
        if prima.get("origine_dominio"):
            origine_reale = prima["origine_dominio"]

    # Codice produttore mancante? Spesso sta in tabella come «Numero articolo»,
    # «Product number», «Artikelnummer», «Codice articolo»: lo si prende da lì.
    if not base.get("mpn"):
        for v in base.get("attributes") or []:
            nome = (v.get("name") or "").lower()
            val = (v.get("value") or "").strip()
            if (re.search(r"(numero|number|nummer|codice|code|ref)\S*\s*(di\s+|del\s+|de\s+)?(prodotto|articolo|product|artikel|art\.)", nome)
                    or re.match(r"^(mpn|part\s*number|p/n|artikelnummer|art\.?\s*nr\.?|référence)", nome)):
                if val and re.search(r"[A-Za-z0-9]{4,}", val) and _solo_cifre(val) != _solo_cifre(base.get("gtin") or ""):
                    base["mpn"] = val[:120]
                    break

    base["conferme"] = len(schede)
    base["origine_dominio"] = origine_reale or base.get("origine_dominio", "")
    return base


def _dominio(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _pulisci_titolo(titolo: str, url: str) -> str:
    """Toglie dal titolo la coda col nome del sito: « - Tecnica Industriale Srl - Shop».

    I segmenti finali separati da « - » o « | » che somigliano al dominio (o alle
    solite parole da negozio) sono l'insegna, non il prodotto: via."""
    dominio = _dominio(url)
    token_dominio = set(re.findall(r"[a-z0-9]{3,}", dominio.replace("-", " ")))
    pezzi = re.split(r"\s+[-|·»]\s+", titolo)
    while len(pezzi) > 1:
        coda = pezzi[-1].lower()
        token_coda = set(re.findall(r"[a-z0-9]{3,}", coda))
        da_negozio = any(p in coda for p in ("shop", "store", "srl", "spa", "s.r.l", "online", "vendita"))
        if token_coda and (token_coda & token_dominio or da_negozio) and not any(c.isdigit() for c in coda):
            pezzi.pop()
            continue
        break
    return " - ".join(p.strip() for p in pezzi).strip()


def _marca_credibile(marca: str, url: str) -> str:
    """La marca presa da og:site_name spesso è l'insegna del negozio: si butta.

    «Tecnica Industriale Srl - Shop» su shop.tecnicaindustriale.net non è la marca
    del prodotto. Se somiglia al dominio o sa di ragione sociale, meglio vuota."""
    if not marca:
        return ""
    minuscola = marca.lower()
    token_marca = set(re.findall(r"[a-z0-9]{3,}", minuscola))
    token_dominio = set(re.findall(r"[a-z0-9]{3,}", _dominio(url).replace("-", " ")))
    if token_marca & token_dominio:
        return ""
    if any(p in minuscola for p in ("shop", "store", "srl", "s.r.l", "spa", "online")):
        return ""
    return marca


# Nomi di file che tradiscono un segnaposto, non una foto del prodotto:
# «image-generique-piecesxpress.jpg» è il timbro "foto non disponibile" del negozio.
IMG_SEGNAPOSTO = (
    "generique", "generic", "placeholder", "no-image", "noimage", "no_image",
    "no-foto", "nofoto", "senza-immagine", "not-available", "notavailable",
    "missing", "default", "dummy", "blank", "coming-soon", "comingsoon",
    "notfound", "not-found", "nodisponibile", "non-disponibile", "awaiting",
)

# E questi sono arredamento del sito, non prodotto.
IMG_ARREDO = (
    "logo", "icon", "sprite", "banner", "payment", "pagament", "spedizion",
    "flag", "avatar", "spinner", "loading", "pixel", "badge", "trustpilot",
    "facebook", "instagram", "whatsapp", "cart", "carrello", "search",
    # promo e servizi del negozio travestiti da foto
    "pickup", "same-day", "sameday", "delivery", "consegna", "ritiro",
    "reference", "million", "useful", "service", "servizi", "promo",
    "offert", "garanzia", "warranty", "reso", "assistance", "support",
    "newsletter", "chi-siamo", "about",
    # icone e copertine di catalogo, non foto del prodotto
    "catalog", "categor", "picto", "menu-", "header", "footer", "slide-",
)


def _img_utile(u: str) -> bool:
    """La foto è del prodotto o è un segnaposto/arredo del sito?"""
    minuscolo = (u or "").lower()
    if not minuscolo or minuscolo.startswith("data:") or minuscolo.endswith(".svg"):
        return False
    if any(sp in minuscolo for sp in IMG_SEGNAPOSTO):
        return False
    nome = minuscolo.rsplit("/", 1)[-1]
    if any(ar in nome for ar in IMG_ARREDO):
        return False
    # I promo-card si chiamano col formato: «4-3-qualcosa.png», «16-9-altro.jpg».
    if re.match(r"\d+-\d+-[a-z]", nome):
        return False
    return True


def _variante_diversa(src: str, titolo: str) -> bool:
    """La foto è di una variante sorella? «…lisa-22-700x400-int-350-bianco.jpg»
    sulla pagina della «Lisa 22 1160X450 Int. 400»: stessa famiglia nel nome del
    file, misure diverse. Le miniature quadrate («316x316») non contano."""
    if not titolo:
        return False
    nome_file = unquote(urlparse(src).path.rsplit("/", 1)[-1]).lower()
    t_file = set(re.findall(r"[a-z0-9]+", nome_file.rsplit(".", 1)[0]))
    t_titolo = set(re.findall(r"[a-z0-9]+", titolo.lower()))
    parole_comuni = {t for t in t_file & t_titolo if t.isalpha() and len(t) >= 4}
    if len(parole_comuni) < 2:
        return False      # nome del file opaco o di un altro discorso: non si giudica

    def misure(token: set) -> set:
        fuori = set()
        for t in token:
            m = re.fullmatch(r"(\d+)x(\d+)", t)
            if m and m.group(1) == m.group(2):
                continue      # miniatura quadrata
            if any(c.isdigit() for c in t) and len(t) >= 2:
                fuori.add(t)
        return fuori
    return bool(misure(t_file) - t_titolo) and bool(misure(t_titolo) - t_file)


def estrai_immagini_html(html: str, url: str, pezzi: list[str] | None = None, titolo: str = "") -> list[str]:
    """Foto del prodotto pescate dai tag <img> della pagina.

    Serve quando i dati strutturati non le dichiarano (i gestionali B2B tipo
    sacchi.it). Per non raccogliere loghi e banner, l'immagine deve contenere
    un pezzo del modello nel percorso o nell'alt, oppure stare in un percorso
    da catalogo (/product, /prodott, /catalog)."""
    # Testata, menu e piè di pagina sono pieni di icone e copertine: via prima
    # di guardare. E il permesso «sta in un percorso da catalogo» vale solo per
    # le immagini VICINE al titolo del prodotto: la galleria sta lì, le icone no.
    corpo = re.sub(
        r"<(script|style|noscript|header|nav|footer)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S
    )
    pezzi_norm = [_norm(p) for p in (pezzi or []) if len(_norm(p)) >= 3]
    fuori: list[str] = []
    visti: set[str] = set()

    m_h1 = re.search(r"<h1\b", corpo, re.I)
    pos_titolo = m_h1.start() if m_h1 else 0

    for m_tag in re.finditer(r"<img\b[^>]*>", corpo, re.I):
        if len(fuori) >= 4:
            break
        tag = m_tag.group(0)
        vicino_al_titolo = abs(m_tag.start() - pos_titolo) < 20_000
        m = re.search(r'\b(?:src|data-src|data-original)=["\']([^"\']+)["\']', tag, re.I)
        if not m:
            continue
        src = urljoin(url, unescape(m.group(1)))
        if not _img_utile(src):
            continue
        alt = ""
        ma = re.search(r'\balt=["\']([^"\']*)["\']', tag, re.I)
        if ma:
            alt = ma.group(1)
        pagliaio = _norm(src + " " + alt)
        percorso_catalogo = any(p in src.lower() for p in ("/product", "/prodott", "/catalog", "/media/"))
        # Il solo nome della marca non basta: «Grundfos.png» è il logo, non la
        # pompa. Serve un pezzo con cifre (il codice) o almeno due parole diverse.
        pezzi_presenti = [p for p in pezzi_norm if p in pagliaio]
        del_modello = any(any(c.isdigit() for c in p) for p in pezzi_presenti) or len(pezzi_presenti) >= 2
        if not (del_modello or (percorso_catalogo and vicino_al_titolo)):
            continue
        chiave = src.split("?")[0]
        if chiave in visti:
            continue
        visti.add(chiave)
        if _variante_diversa(src, titolo):
            continue
        fuori.append(src)
    return fuori


# Parole che smascherano una riga commerciale in una finta "specifica".
# Si confrontano a INIZIO PAROLA, non come pezzi di testo qualunque: prima
# «reso» bocciava «presostato», «resi» bocciava «resistenza elettrica», «cart»
# bocciava «cartuccia» e «iva » bocciava «funzione attiva» — specifiche vere
# della termoidraulica buttate via.
_SPAZZATURA_PREFISSI = (
    "prezz", "price", "preis", "precio", "prix", "listino", "aggiungi", "carrell",
    "warenkorb", "panier", "carrito", "spedizion", "shipping", "versand", "livraison",
    "envío", "envio", "disponibil", "verfügbar", "availability", "recension", "review",
    "bewertung", "offert", "angebot", "quantit", "pagament", "payment", "zahlung",
    "paiement", "rückgabe", "cookie", "login", "account", "konto", "wishlist",
    "merkzettel", "newsletter", "mwst", "sconto", "rabatt", "discount", "remise",
    "omaggio", "punti fedelt", "collegare", "collegati", "accedi", "registrati",
    "anmelden", "vedi prezzo", "richiedi", "preventiv", "devis", "contatta", "chiama",
    "acquista", "kaufen", "acheter", "comprar", "commander",
)
_SPAZZATURA_PAROLE = ("cart", "basket", "avis", "reso", "resi", "returns", "compte",
                      "iva", "vat", "tva", "ordina", "compra")
_SPAZZATURA = re.compile(
    r"(?<![0-9a-zà-ÿ])(?:"
    + "|".join(re.escape(p) for p in _SPAZZATURA_PREFISSI)
    + r"|(?:" + "|".join(re.escape(p) for p in _SPAZZATURA_PAROLE) + r")(?![0-9a-zà-ÿ]))",
    re.I,
)
SPEC_SPAZZATURA = _SPAZZATURA_PREFISSI + _SPAZZATURA_PAROLE  # compatibilità


def spazzatura(testo: str) -> bool:
    """Il testo sa di negozio (prezzi, carrello, spedizioni…) invece che di scheda?"""
    return bool(_SPAZZATURA.search(testo or ""))


# Nomi di campo che nei gestionali dei grossisti contengono anagrafica, non
# specifiche: «Descrizione 3» con dentro l'EAN, «Nome» col titolo ripetuto.
_SPEC_ANAGRAFICA = re.compile(
    r"^(descrizione|description|desc)\s*\d*$"
    r"|^(nome|name|titolo|title|prodotto|articolo)$"
    r"|^(nome|name|titolo|title)\s+(del\s+|dell'|de\s+|of\s+the\s+|of\s+)?(prodotto|articolo|product|item)\b"
    r"|^(product\s*name|produktname|nombre\s+del\s+producto|nom\s+du\s+produit)\b"
    r"|^(ean|gtin|barcode|codice\s*ean|cod\.?\s*ean|codice\s*a\s*barre)\b"
    r"|^(telefono|tel|telefax|fax|cellulare|numero\s*verde|whatsapp)\b"
    r"|^(email|e-mail|mail|pec|indirizzo|sede|citt|cap|provincia|p\.?\s*iva|partita\s*iva)\b"
    r"|^(orari|apertura|contatt|assistenza\s*clienti)\b",
    re.I,
)

# Valori che tradiscono un contatto anche quando il nome del campo è innocuo
# («Referente: +39 080…»): numeri di telefono ed email non sono specifiche.
_VAL_CONTATTO = re.compile(
    r"(\+?\d[\d ().\-/]{7,}\d)|([\w.\-]+@[\w.\-]+\.[a-z]{2,})", re.I
)


def specifiche_pulite(voci: list[dict], gtin: str, titolo: str) -> list[dict]:
    """Butta le righe che non sono specifiche tecniche ma anagrafica di magazzino.

    Regole: via i campi con nomi da gestionale («Descrizione 3», «Nome»), via i
    valori che sono l'EAN travestito, via i valori che ripetono il titolo."""
    t_norm = _norm(titolo)
    fuori: list[dict] = []
    for v in voci:
        nome = (v.get("name") or "").strip()
        valore = (v.get("value") or "").strip()
        if not nome or not valore:
            continue
        if _SPEC_ANAGRAFICA.match(nome):
            continue
        # Nome-campo o valore che sanno di commerciale («Prezzi: Prezzo pro,
        # collegare»): non è un dato tecnico.
        minusc = f"{nome} {valore}".lower()
        if spazzatura(minusc):
            continue
        if _VAL_CONTATTO.search(valore) and not re.search(r"[a-z]{3,}", re.sub(r"@[\w.\-]+", "", valore), re.I):
            # Il valore è essenzialmente un numero/telefono o un'email, non un dato tecnico.
            continue
        cifre = re.sub(r"[^0-9]", "", valore)
        if cifre and cifre == gtin:
            continue
        v_norm = _norm(valore)
        if len(v_norm) > 15 and t_norm and (v_norm in t_norm or t_norm in v_norm):
            continue
        fuori.append(v)

    # Stesso nome su più righe («Approvazioni» ×3): un'unica riga coi valori
    # uniti, invece di tre righe uguali in tabella.
    unite: list[dict] = []
    posizione: dict[str, int] = {}
    for v in fuori:
        k = _norm(v.get("name"))
        if k in posizione:
            gia = unite[posizione[k]]
            if _norm(v["value"]) not in _norm(gia["value"]):
                gia["value"] = (gia["value"] + ", " + v["value"])[:120]
        else:
            posizione[k] = len(unite)
            unite.append(dict(v))
    return unite


def estrai_specifiche_html(html: str) -> list[dict]:
    """Caratteristiche lette dall'HTML visibile: tabelle, <dl> e liste «Nome: valore».

    Tanti negozi le specifiche le mostrano in una tabella e basta, senza
    dichiararle nei dati strutturati: prima quel materiale si perdeva."""
    corpo = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    voci: list[dict] = []
    visti: set[str] = set()

    def aggiungi(nome_html: str, valore_html: str) -> None:
        nome = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", nome_html))).strip(" :\u00a0\t")
        valore = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", valore_html))).strip()
        if not nome or not valore or len(nome) < 2 or len(nome) > 60 or len(valore) > 120:
            return
        minuscolo = f"{nome} {valore}".lower()
        if spazzatura(minuscolo) or PREZZO.search(valore):
            return
        chiave = _norm(nome)
        if not chiave or chiave in visti:
            return
        visti.add(chiave)
        voci.append({"name": nome[:60], "value": valore[:120]})

    # Righe di tabella con due celle: la forma più diffusa.
    for riga in re.findall(r"<tr[^>]*>(.*?)</tr>", corpo, re.I | re.S)[:150]:
        celle = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", riga, re.I | re.S)
        if len(celle) == 2:
            aggiungi(celle[0], celle[1])

    # Liste di definizione: <dt>Nome</dt><dd>Valore</dd>.
    for blocco in re.findall(r"<dl[^>]*>(.*?)</dl>", corpo, re.I | re.S)[:20]:
        dts = re.findall(r"<dt[^>]*>(.*?)</dt>", blocco, re.I | re.S)
        dds = re.findall(r"<dd[^>]*>(.*?)</dd>", blocco, re.I | re.S)
        for n, v in zip(dts, dds):
            aggiungi(n, v)

    # Voci di elenco nella forma «Nome: valore».
    for li in re.findall(r"<li[^>]*>(.*?)</li>", corpo, re.I | re.S)[:120]:
        testo = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", li))).strip()
        m = re.match(r"([^:]{2,50}):\s*(.{1,120})$", testo)
        if m:
            aggiungi(m.group(1), m.group(2))

    return voci[:40]


def estrai_descrizione_html(html: str) -> str:
    """Descrizione letta dai paragrafi della pagina, quando i dati strutturati tacciono.

    Si prendono i paragrafi sostanziosi (non slogan, non righe commerciali) e si
    passano dalla stessa pulizia anti-negozio del resto."""
    corpo = re.sub(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    paragrafi = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", corpo, re.I | re.S)[:80]:
        testo = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", p))).strip()
        if len(testo) < 120:
            continue
        minuscolo = testo.lower()
        if spazzatura(minuscolo):
            continue
        paragrafi.append(testo)
        if len(paragrafi) >= 3:
            break
    return ripulisci_da_negozio(" ".join(paragrafi))[:2000]


def _scheda_dal_grezzo(html: str, url: str, gtin: str, pezzi: list[str],
                       codice_presente: bool = False) -> dict | None:
    """Scheda minima da una pagina senza dati strutturati.

    Si prendono <h1> o <title> come nome e la meta description come testo. Se il
    codice NON è scritto nella pagina, il titolo deve contenere almeno un pezzo
    del modello cercato: senza quel riscontro potrebbe essere la pagina «chi
    siamo» con un elenco di numeri. Se il codice c'è, basta un titolo: prima si
    pretendeva il pezzo di modello anche lì, e i gestionali B2B senza dati
    strutturati venivano scartati pur riportando l'EAN esatto.
    """
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    titolo = re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""
    if not titolo.strip():
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        titolo = m.group(1) if m else ""
    titolo = re.sub(r"\s+", " ", titolo).strip()
    if not titolo:
        return None

    # Il titolo deve parlare del modello che cerchiamo (se un modello lo abbiamo
    # e il codice non è già scritto nella pagina).
    if pezzi and not codice_presente:
        norm_titolo = _norm(titolo)
        con_cifre = [p for p in pezzi if any(c.isdigit() for c in p)]
        if con_cifre and not any(_norm(p) in norm_titolo for p in con_cifre):
            return None

    descr = ""
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
    if m:
        descr = re.sub(r"\s+", " ", m.group(1)).strip()

    return {"name": titolo, "description": descr}


# ------------------------------------------------ pagine di elenco (categorie, ricerche)
# Una categoria o una ricerca interna del negozio può riportare l'EAN nella
# tessera del prodotto: il «codice in pagina» c'è, ma la scheda NON è quella.
# Prima passava: nome «Sistema pressurizzazione Archivi», niente foto né
# descrizione, e le schede vere degli altri siti venivano scartate perché «non
# coerenti» con quella. Ora l'elenco si riconosce e si segue il link della
# tessera che riporta il codice: è lì la scheda vera.
_CORPO_ELENCO = re.compile(
    r"(?<![\w-])(?:archive|tax-product_cat|tax-product_tag|tax-pa_[\w-]+|post-type-archive-product|"
    r"search-results|woocommerce-shop|template-collection|template-search|catalog-category-view|"
    r"catalogsearch-result-index|page-category|category-page|product-category|page-search)(?![\w-])",
    re.I)
_CORPO_PRODOTTO = re.compile(
    r"(?<![\w-])(?:single-product|template-product|catalog-product-view|product-template-default|"
    r"page-product|product-page|single-product-page|product-detail|productpage)(?![\w-])", re.I)
_ID_ELENCO = {"category", "search", "manufacturer", "supplier", "prices-drop", "new-products",
              "best-sales", "module-pm_advancedsearch4-searchresults"}
_PERCORSO_ELENCO = re.compile(
    r"/(?:categoria-prodotto|product-category|categoria|categorie|category|categories|"
    r"collections/[^/?#]+/?$|tag|tags|search|ricerca|cerca|suche|recherche|buscar|busqueda|"
    r"brand|brands|marca|marche|marchi|marque|marques|marken|produttore|manufacturer)(?:/|$)", re.I)
_QUERY_ELENCO = re.compile(r"(?:^|&)(?:s|q|search|query|keyword|keywords|text|controller=search)=", re.I)
_TITOLO_ELENCO = re.compile(r"(?:^|\s)(?:Archivi|Archives?|Archiv)(?:\s*[-–|·:]|\s*$)", re.I)


def _prodotti_principali(html: str) -> list[dict]:
    """I Product al primo livello del JSON-LD (non quelli annidati: «correlati»,
    varianti di un ProductGroup), più quelli dentro un ItemList."""
    trovati: list[dict] = []

    def tipo(nodo: dict) -> str:
        t = nodo.get("@type", "")
        return t if isinstance(t, str) else " ".join(str(x) for x in t)

    def guarda(nodo) -> None:
        if isinstance(nodo, list):
            for x in nodo:
                guarda(x)
            return
        if not isinstance(nodo, dict):
            return
        t = tipo(nodo)
        if "Product" in t and "ProductGroup" not in t:
            trovati.append(nodo)
        if "@graph" in nodo:
            guarda(nodo["@graph"])
        if "ItemList" in t or "CollectionPage" in t or "SearchResultsPage" in t:
            elementi = nodo.get("itemListElement")
            principale = nodo.get("mainEntity")
            if not elementi and isinstance(principale, dict):
                elementi = principale.get("itemListElement")
            for el in elementi if isinstance(elementi, list) else []:
                if isinstance(el, dict):
                    guarda(el.get("item") if isinstance(el.get("item"), dict) else el)

    for blocco in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I
    ):
        try:
            guarda(json.loads(blocco.strip()))
        except ValueError:
            try:
                guarda(json.loads(re.sub(r",\s*([}\]])", r"\1", blocco.strip())))
            except ValueError:
                continue
    return trovati


def _ean_visibili(html: str) -> set[str]:
    """Gli EAN-13 validi scritti nel testo visibile (script e stili esclusi)."""
    from . import codici as _cod

    testo = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html[:800_000], flags=re.S | re.I)
    testo = re.sub(r"<[^>]+>", " ", testo)
    return {e for e in set(re.findall(r"(?<!\d)(\d{13})(?!\d)", testo))
            if not e.startswith(("0000", "2")) and _cod.checksum_gtin(e)}


def pagina_elenco(html: str, url: str) -> str:
    """Perché la pagina è un elenco di prodotti e non una scheda ("" se è una scheda)."""
    m = re.search(r"<body\b[^>]*>", html[:800_000], re.I)
    tag = m.group(0) if m else ""
    classi = " ".join(re.findall(r'\bclass=["\']([^"\']*)["\']', tag))
    id_corpo = ""
    m = re.search(r'\bid=["\']([\w-]+)["\']', tag)
    if m:
        id_corpo = m.group(1).lower()
    # Il tema dice chiaramente «scheda prodotto»: si crede al tema.
    if _CORPO_PRODOTTO.search(classi) or id_corpo == "product":
        return ""
    motivo = "pagina di categoria o di ricerca, non la scheda del prodotto"
    if _CORPO_ELENCO.search(classi) or id_corpo in _ID_ELENCO:
        return motivo
    principali = _prodotti_principali(html)
    nomi = {_norm(_testo(p.get("name")))[:60] for p in principali if _testo(p.get("name"))}
    if len(nomi) >= 3:
        return "pagina con un elenco di prodotti, non la scheda"
    if len(nomi) == 1:
        return ""
    # Una scheda mostra uno, al massimo due EAN; un listino o una famiglia di
    # prodotti ne mostra decine (sulle pagine vere dei negozi: 0-1 contro 30-50).
    if len(_ean_visibili(html)) >= 5:
        return "pagina con un elenco di prodotti, non la scheda"
    parti = urlparse(url)
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:200_000], re.I | re.S)
    titolo = unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    if _PERCORSO_ELENCO.search(parti.path) or _QUERY_ELENCO.search(parti.query) or _TITOLO_ELENCO.search(titolo):
        return motivo
    return ""


_LINK_INUTILE = re.compile(
    r"add-to-cart|add_to_cart|addtocart|carrello|/cart\b|wishlist|compare|confronta|login|account|"
    r"checkout|/my-|mailto:|tel:|javascript:", re.I)
_TESSERA = re.compile(
    r'<(?:li|div|article|tr|section)\b[^>]*\bclass=["\'][^"\']*(?<![\w-])'
    r'(?:product(?![\w-])|products-item|product-item|product-miniature|product-card|product-tile|'
    r'prodotto(?![\w-])|item-product|card-product|grid-item)(?![\w])[^"\']*["\']', re.I)


def link_dal_elenco(html: str, url: str, gtin: str) -> str:
    """Nella pagina di elenco, il link alla scheda del prodotto che riporta il codice.

    Si guarda la «tessera» che contiene il codice (l'ultimo contenitore con
    classe da prodotto prima del codice) e si prende l'ultimo link dello stesso
    sito che porta lì prima del codice: di solito il titolo o la foto. La
    pagina seguita deve poi confermare il codice come tutte le altre."""
    base = rete.dominio(url)
    proprio = url.split("#")[0].rstrip("/")

    def utile(href: str) -> str:
        assoluto = urljoin(url, unescape(href).strip())
        if not assoluto.startswith(("http://", "https://")) or _LINK_INUTILE.search(assoluto):
            return ""
        if rete.dominio(assoluto) != base or assoluto.split("#")[0].rstrip("/") == proprio:
            return ""
        parti = urlparse(assoluto)
        if parti.path in ("", "/") or re.search(r"\.(?:jpe?g|png|webp|gif|svg|pdf|zip)$", parti.path, re.I):
            return ""
        if _PERCORSO_ELENCO.search(parti.path) or _QUERY_ELENCO.search(parti.query):
            return ""
        return assoluto.split("#")[0]

    for occorrenza in list(re.finditer(re.escape(gtin), html))[:6]:
        pos = occorrenza.start()
        finestra = html[max(0, pos - 8000):pos]
        tessere = list(_TESSERA.finditer(finestra))
        prima = finestra[tessere[-1].start():] if tessere else finestra[-2500:]
        link = [utile(h) for h in re.findall(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', prima, re.I)]
        link = [x for x in link if x]
        if link:
            return link[-1]
        dopo = html[pos:pos + 1500]
        link = [utile(h) for h in re.findall(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', dopo, re.I)]
        link = [x for x in link if x]
        if link:
            return link[0]
    return ""


def estrai(
    html: str,
    url: str,
    gtin: str,
    mpn: str = "",
    pezzi_modello: list[str] | None = None,
    motivo: list[str] | None = None,
    solo_ean: bool = False,
    consenti_estero: bool = False,
    richiedi_ean: bool = False,
    marche: list[str] | None = None,
    lingua: str = "IT",
) -> dict | None:
    """Ricava la scheda dai dati strutturati. None se la pagina non è quel prodotto.

    La verifica dell'EAN è il cuore: una ricerca interna può restituire l'elenco dei
    risultati invece della scheda, e senza controllo si importerebbe il primo che
    capita. Se la pagina dichiara un EAN diverso, si scarta senza discutere.
    """
    candidati = _prodotti_jsonld(html)
    micro = _microdata(html)
    og = _opengraph(html)

    # Serve a dire perché una pagina è stata rifiutata: «non ha dati strutturati»
    # e «parla di un altro prodotto» sono problemi diversi e si risolvono diversamente.
    if motivo is not None:
        motivo.clear()

    # Categoria o ricerca interna: anche se riporta il codice non è la scheda.
    # Chi chiama può seguire il link della tessera (``link_dal_elenco``).
    elenco = pagina_elenco(html, url)
    if elenco:
        if motivo is not None:
            motivo.append(elenco)
        return None

    from . import codici as _cod
    cerca_ean = _cod.checksum_gtin(gtin)
    codice_norm = re.sub(r"[^a-z0-9]", "", gtin.lower())
    if cerca_ean:
        codice_in_pagina = gtin in re.sub(r"[^0-9]", "", html[:400_000])
        codice_in_url = gtin in re.sub(r"[^0-9]", "", url)
    else:
        # codice produttore: confronto alfanumerico nel testo visibile e nell'URL
        testo_pagina = re.sub(r"[^a-z0-9]", "", re.sub(r"<[^>]+>", " ", html[:400_000]).lower())
        codice_in_pagina = len(codice_norm) >= 4 and codice_norm in testo_pagina
        codice_in_url = len(codice_norm) >= 4 and codice_norm in re.sub(r"[^a-z0-9]", "", url.lower())

    if not candidati and not micro and not og.get("title"):
        # Niente JSON-LD, niente microdata, niente Open Graph. Prima ci si fermava
        # qui; ma se la pagina contiene proprio quell'EAN vale la pena leggere il
        # grezzo: <title> o <h1> come nome, la meta description come testo.
        # Tanti gestionali B2B (i grossisti idraulici, per dire) non pubblicano
        # dati strutturati eppure hanno titoli perfetti.
        grezza = _scheda_dal_grezzo(html, url, gtin, pezzi_modello or [],
                                    codice_presente=codice_in_pagina or codice_in_url)
        if grezza is None:
            if motivo is not None:
                motivo.append("la pagina non pubblica dati strutturati")
            return None
        # L'identità (EAN in pagina o nell'URL, codice produttore, modello) la
        # verifica comunque il controllo più sotto: qui basta un titolo sensato.
        candidati = [grezza]

    # Si sta cercando un EAN o un codice produttore? Cambia come si conferma
    # l'identità: per l'EAN valgono i gtin dichiarati e le cifre in pagina; per
    # il codice produttore valgono mpn/sku dichiarati e il codice nel testo.
    # Il checksum decide: «97924465» ha 8 cifre ma è un codice Grundfos, non un EAN-8.
    scheda: dict | None = None
    for prodotto in candidati:
        codici = set()
        for chiave in ("gtin13", "gtin14", "gtin12", "gtin8", "gtin", "ean", "productID", "sku", "mpn"):
            codici.add(_solo_cifre(_testo(prodotto.get(chiave))))
        codici.discard("")
        alfanumerici = {re.sub(r"[^a-z0-9]", "", _testo(prodotto.get(k)).lower())
                        for k in ("sku", "mpn", "productID")}

        if cerca_ean and (gtin in codici or gtin.lstrip("0") in {c.lstrip("0") for c in codici}):
            scheda = prodotto
            break
        if not cerca_ean and codice_norm and codice_norm in alfanumerici:
            scheda = prodotto
            break
        if not scheda and not codici:
            scheda = prodotto  # unico Product della pagina, senza codici: si valuta dopo

    if scheda is None and candidati:
        scheda = candidati[0]

    # --- verifica dell'identità -------------------------------------------
    # Contano solo i valori che SONO un GTIN (lunghezza e cifra di controllo
    # giuste). Prima entrava anche il productID numerico che tanti negozi usano
    # come id interno («12447494»): la pagina «dichiarava un altro codice» e la
    # scheda giusta veniva buttata.
    def _gtin_valido(valore: str) -> bool:
        return len(valore) in (8, 12, 13, 14) and _cod.checksum_gtin(valore)

    dichiarati = set()
    if scheda:
        for chiave in ("gtin13", "gtin14", "gtin12", "gtin8", "gtin", "ean", "productID"):
            valore = _solo_cifre(_testo(scheda.get(chiave)))
            if valore and (_gtin_valido(valore) or (chiave != "productID" and len(valore) >= 12)):
                dichiarati.add(valore)
    for chiave in ("gtin13", "gtin", "productID"):
        valore = _solo_cifre(micro.get(chiave, ""))
        if valore and _gtin_valido(valore):
            dichiarati.add(valore)

    # Cercando per codice produttore, un GTIN dichiarato non contraddice niente:
    # anzi, è l'EAN che recuperiamo per il prodotto. Il controllo «dichiara un
    # altro codice» vale solo quando si cerca per EAN.
    livello_conferma = "dichiarato"
    if dichiarati and cerca_ean:
        # GTIN-14 con zeri davanti («05710626495436») è lo stesso codice.
        if gtin not in dichiarati and gtin.lstrip("0") not in {d.lstrip("0") for d in dichiarati}:
            if motivo is not None:
                motivo.clear()
                motivo.append(f"dichiara un altro codice ({sorted(dichiarati)[0]})")
            return None
    else:
        # Nessun EAN dichiarato nei dati strutturati: si accetta lo stesso se il
        # codice compare nella pagina o nell'indirizzo (tanti negozi lo mettono
        # nell'URL e basta), se combacia il codice produttore, o se il modello
        # ricavato dai titoli torna. Vale per qualunque sito: la scheda serve al
        # cliente, non importa da dove arriva — l'importante è che sia QUESTO
        # prodotto, e i controlli qui sotto servono esattamente a quello.
        mpn_pagina = _testo((scheda or {}).get("mpn"))
        mpn_ok = bool(mpn) and bool(mpn_pagina) and mpn.lower() == str(mpn_pagina).lower()
        modello_ok = modello_combacia(pezzi_modello or [], scheda or {}, html)
        if richiedi_ean:
            # Marketplace: fra mille annunci simili il modello non garantisce
            # niente, deve esserci scritto proprio quel codice.
            mpn_ok = False
            modello_ok = False
        # Come si è confermata la pagina? Serve a valle: «codice» (EAN/MPN visto
        # da qualche parte) è una prova forte, «modello» (solo somiglianza del
        # nome) è debole e da sola non basta per pubblicare.
        if codice_in_pagina or codice_in_url:
            livello_conferma = "codice"
        elif mpn_ok:
            livello_conferma = "mpn"
        elif modello_ok:
            livello_conferma = "modello"
        else:
            livello_conferma = ""
        if not (codice_in_pagina or codice_in_url or mpn_ok or modello_ok):
            if motivo is not None:
                motivo.clear()
                nome_visto = _testo((scheda or {}).get("name")) or og.get("title", "")
                motivo.append(
                    f"parla di un altro prodotto ({nome_visto[:38]})" if nome_visto
                    else "non c'è niente che confermi questo prodotto"
                )
            return None

    # Una pagina può avere solo microdata, senza JSON-LD né Open Graph: si prosegue
    # comunque, purché da qualche parte esca un nome prodotto.
    scheda = scheda or {}
    marca = _testo(scheda.get("brand")) or _testo(micro.get("brand")) \
        or _marca_credibile(og.get("site_name", ""), url)
    titolo = _pulisci_titolo(
        _testo(scheda.get("name")) or micro.get("name", "") or og.get("title", ""), url
    )
    # Nome povero nei dati strutturati: certi negozi ci mettono solo la marca
    # («Cordivari») mentre il titolo vero sta nell'<h1>. Se il titolo della
    # pagina contiene quel nome ed è decisamente più ricco, vince lui.
    if titolo and len(titolo.split()) <= 2:
        m_h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.I | re.S)
        h1 = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m_h1.group(1)))).strip() if m_h1 else ""
        for alternativa in (h1, og.get("title", "")):
            alternativa = _pulisci_titolo(alternativa, url)
            if (alternativa and len(alternativa.split()) >= len(titolo.split()) + 2
                    and _norm(titolo) in _norm(alternativa)):
                # Quel nome corto era quasi sempre la marca: se manca, eccola.
                if not marca and re.fullmatch(r"[^\W\d_][\w&.' -]{1,40}", titolo):
                    marca = titolo
                titolo = alternativa
                break
    # Un negozio che si chiama come il suo dominio spesso si dichiara «brand» nei
    # dati strutturati (Anphibius su anphibius.com): non è la marca del prodotto.
    # Si tiene solo se compare nel titolo o fra le marche viste nei risultati
    # di ricerca — il produttore vero passa sempre (grundfos su grundfos.com
    # sta anche nei titoli degli altri siti).
    if marca:
        token_marca = set(re.findall(r"[a-z0-9]{3,}", marca.lower()))
        token_dominio = set(re.findall(r"[a-z0-9]{3,}", _dominio(url).replace("-", " ")))
        nel_titolo = bool(token_marca & set(re.findall(r"[a-z0-9]{3,}", titolo.lower())))
        fra_le_marche = any(m.lower() in marca.lower() for m in (marche or []))
        if token_marca & token_dominio and not nel_titolo and not fra_le_marche:
            marca = ""
    descrizione = _testo(scheda.get("description")) or micro.get("description", "") or og.get("description", "")

    immagini = _immagini(scheda.get("image"), url) if scheda.get("image") else []
    # La foto di Open Graph si aggiunge sempre in coda: certi negozi sbagliano
    # quella dei dati strutturati (Shopify: «https://sito//sito/cdn/…», 404) e
    # mettono giusta quella per i social.
    if og.get("image"):
        immagini.append(urljoin(url, og["image"]))
    visti: set[str] = set()
    riparate = []
    for u in immagini:
        u = ripara_url(u)
        if u and u not in visti:
            visti.add(u)
            riparate.append(u)
    # Via i segnaposto «foto non disponibile» che i negozi dichiarano come se
    # fossero il prodotto; se non resta niente, si pescano le foto vere dai
    # tag <img> della pagina.
    immagini = [u for u in riparate if _img_utile(u)]
    foto_html = estrai_immagini_html(html, url, pezzi_modello, titolo)
    if not immagini:
        immagini = foto_html

    caratteristiche = []
    proprieta = scheda.get("additionalProperty") or []
    if isinstance(proprieta, dict):
        proprieta = [proprieta]
    for voce in proprieta if isinstance(proprieta, list) else []:
        if not isinstance(voce, dict):
            continue
        nome = _testo(voce.get("name"))
        valore = _testo(voce.get("value"))
        if nome and valore:
            caratteristiche.append({"group": "", "name": nome, "value": valore, "key": True})

    if not titolo:
        if motivo is not None:
            motivo.clear()
            motivo.append("nessun nome prodotto nei dati della pagina")
        return None

    # Testo commerciale di un venditore: si tolgono solo le frasi con prezzi e
    # richiami all'acquisto, il resto della descrizione si tiene.
    if descrizione:
        descrizione = ripulisci_da_negozio(descrizione)

    # Descrizione che è solo il titolo ripetuto: non aggiunge niente e sul sito
    # del cliente farebbe una scheda che sembra vuota anche se piena.
    if descrizione and _norm(descrizione) and _norm(titolo):
        if _norm(titolo) in _norm(descrizione) and len(descrizione) < len(titolo) * 1.6:
            descrizione = ""

    codice_articolo = _testo(scheda.get("mpn")) or _testo(scheda.get("sku"))
    if not codice_credibile(codice_articolo, pezzi_modello or []):
        codice_articolo = ""

    # I dati strutturati sono avari? Si legge quello che la pagina MOSTRA:
    # tabelle di specifiche e paragrafi di descrizione. È materiale dello stesso
    # sito sulla stessa pagina, cambia solo il modo di leggerlo.
    if len(caratteristiche) < 5:
        for voce in estrai_specifiche_html(html):
            if _norm(voce["name"]) not in {_norm(v.get("name")) for v in caratteristiche}:
                caratteristiche.append(voce)
    if len(descrizione or "") < 80:
        dal_visibile = estrai_descrizione_html(html)
        if len(dal_visibile) > len(descrizione or ""):
            descrizione = dal_visibile

    caratteristiche = specifiche_pulite(caratteristiche, gtin, titolo)

    # La lingua si giudica sul testo FINALE: la descrizione spesso arriva dai
    # paragrafi della pagina, e giudicando solo il titolo una pagina inglese
    # senza lang dichiarato passava per «lingua giusta» e restava non tradotta.
    lingua = lingue.normalizza(lingua)
    nomi_spec = " ".join(f"{v.get('name', '')} {v.get('value', '')}" for v in caratteristiche[:12])
    lingua_pagina = lingua_scheda(html, f"{titolo} {descrizione} {nomi_spec}", url)
    estera = bool(lingua_pagina) and lingua_pagina != lingua
    if estera and not consenti_estero:
        if motivo is not None:
            motivo.clear()
            motivo.append("pagina in un'altra lingua")
        return None

    # Cercando per codice produttore: se la pagina dichiara un EAN valido lo si
    # recupera (finirà sul prodotto WooCommerce), e il codice cercato diventa
    # il codice produttore della scheda.
    gtin_scheda = gtin
    if not cerca_ean:
        from . import codici as _codici
        validi = sorted(d for d in dichiarati if _codici.checksum_gtin(d))
        gtin_scheda = validi[0] if validi else ""
        if not codice_articolo:
            codice_articolo = gtin

    return scheda_sicura({
        "gtin": gtin_scheda,
        "source_ref": "",
        # La pagina dichiarava proprio questo EAN nei dati strutturati: è la
        # conferma più forte, e nel confronto fra più siti questa scheda comanda.
        "ean_dichiarato": bool(dichiarati),
        "conferma": livello_conferma,
        "title": titolo[:255],
        "brand": marca[:120],
        "mpn": codice_articolo,
        "category": {"id": "", "name": _testo(scheda.get("category"))},
        "description": descrizione,
        "short_description": "",
        "bullets": [],
        "images": immagini,
        "attributes": caratteristiche[:40],
        "documents": [],
        # Mai dalle fonti: il prezzo lo mette il cliente.
        "regular_price": "",
        "sale_price": "",
        "warnings": [],
        "origine_url": url,
        "estera": estera,
        "lingua_pagina": lingua_pagina,
        # Riserva: le foto della pagina, se quelle dichiarate non reggono la verifica.
        "_foto_html": [u for u in foto_html if u not in immagini][:6],
    })


# --------------------------------------------------------------------- fetch
def _dimensioni_immagine(dati: bytes) -> tuple[int, int] | None:
    """Larghezza e altezza dai byte di PNG, JPEG, GIF, WebP e AVIF.

    WebP/AVIF sono ormai normalissimi nei cataloghi prodotto: considerarli
    "formati esotici" significava buttare via proprio le foto dei siti moderni.
    """
    if dati[:8] == b"\x89PNG\r\n\x1a\n" and len(dati) >= 24:
        return int.from_bytes(dati[16:20], "big"), int.from_bytes(dati[20:24], "big")
    if dati[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(dati):
            if dati[i] != 0xFF:
                i += 1
                continue
            marcatore = dati[i + 1]
            if 0xC0 <= marcatore <= 0xCF and marcatore not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(dati[i + 7:i + 9], "big"), int.from_bytes(dati[i + 5:i + 7], "big")
            if i + 4 > len(dati):
                break
            lung = int.from_bytes(dati[i + 2:i + 4], "big")
            if lung < 2:
                break
            i += 2 + lung
    if dati[:6] in (b"GIF87a", b"GIF89a") and len(dati) >= 10:
        return int.from_bytes(dati[6:8], "little"), int.from_bytes(dati[8:10], "little")

    # WebP: RIFF....WEBP + VP8X / VP8L / VP8.
    if len(dati) >= 30 and dati[:4] == b"RIFF" and dati[8:12] == b"WEBP":
        chunk = dati[12:16]
        if chunk == b"VP8X" and len(dati) >= 30:
            w = 1 + int.from_bytes(dati[24:27], "little")
            h = 1 + int.from_bytes(dati[27:30], "little")
            return w, h
        if chunk == b"VP8L" and len(dati) >= 25 and dati[20] == 0x2F:
            b0, b1, b2, b3 = dati[21], dati[22], dati[23], dati[24]
            w = 1 + (b0 | ((b1 & 0x3F) << 8))
            h = 1 + (((b1 & 0xC0) >> 6) | (b2 << 2) | ((b3 & 0x0F) << 10))
            return w, h
        if chunk == b"VP8 ":
            firma = dati.find(b"\x9d\x01\x2a", 20, min(len(dati), 80))
            if firma >= 0 and firma + 7 <= len(dati):
                w = int.from_bytes(dati[firma + 3:firma + 5], "little") & 0x3FFF
                h = int.from_bytes(dati[firma + 5:firma + 7], "little") & 0x3FFF
                if w and h:
                    return w, h

    # AVIF/HEIF: il box ispe porta le dimensioni dell'immagine primaria.
    if len(dati) >= 32 and (b"avif" in dati[:64] or b"heic" in dati[:64] or b"heif" in dati[:64]):
        pos = dati.find(b"ispe")
        if pos >= 4 and pos + 16 <= len(dati):
            w = int.from_bytes(dati[pos + 8:pos + 12], "big")
            h = int.from_bytes(dati[pos + 12:pos + 16], "big")
            if 0 < w < 50000 and 0 < h < 50000:
                return w, h
    return None


def _pochi_colori_png(dati: bytes, soglia: int = 64) -> bool | None:
    """True se un PNG usa pochissimi colori (grafica/pittogramma), non una foto.

    Decodifica il PNG a mano — niente librerie esterne — e conta i colori
    distinti su un campione di righe. Le icone lineari ne hanno una manciata
    (gli scaffali di sacchi: 10); una foto compressa ne ha centinaia. None se
    il formato non è PNG o la decodifica non riesce (lì decide il peso/pixel)."""
    if dati[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        larghezza = int.from_bytes(dati[16:20], "big")
        altezza = int.from_bytes(dati[20:24], "big")
        prof = dati[24]      # bit depth
        tipo = dati[25]      # color type
        if larghezza == 0 or altezza == 0:
            return None

        # PNG con palette (color type 3): il numero di colori è la dimensione
        # della PLTE. ≤64 tinte = grafica/pittogramma, non una foto (gli
        # scaffali di sacchi sono un PNG a palette da 16 colori). Zero decodifica.
        if tipo == 3:
            j = 8
            while j + 8 <= len(dati):
                lung = int.from_bytes(dati[j:j + 4], "big")
                if dati[j + 4:j + 8] == b"PLTE":
                    return (lung // 3) <= soglia
                if dati[j + 4:j + 8] == b"IDAT":
                    break
                j += 12 + lung
            return None

        if prof != 8 or tipo not in (2, 6):
            return None      # 16-bit o formati strani: decide il peso/pixel
        canali = 4 if tipo == 6 else 3

        # concateno gli IDAT e decomprimo
        i = 8
        idat = bytearray()
        while i + 8 <= len(dati):
            lung = int.from_bytes(dati[i:i + 4], "big")
            nome = dati[i + 4:i + 8]
            if nome == b"IDAT":
                idat += dati[i + 8:i + 8 + lung]
            elif nome == b"IEND":
                break
            i += 12 + lung
        if not idat:
            return None
        grezzo = zlib.decompress(bytes(idat))

        larghezza_byte = larghezza * canali
        colori: set = set()
        righe_lette = 0
        pos = 0
        precedente = bytearray(larghezza_byte)
        passo = max(1, altezza // 40)   # ~40 righe campione
        y = 0
        while pos + 1 + larghezza_byte <= len(grezzo) and righe_lette < 40:
            filtro = grezzo[pos]
            riga = bytearray(grezzo[pos + 1:pos + 1 + larghezza_byte])
            # defiltro minimale: gestisco Sub(1), Up(2), nessuno(0). Per il
            # conteggio colori basta un defiltro grossolano.
            if filtro == 2:
                for k in range(larghezza_byte):
                    riga[k] = (riga[k] + precedente[k]) & 0xFF
            elif filtro == 1:
                for k in range(canali, larghezza_byte):
                    riga[k] = (riga[k] + riga[k - canali]) & 0xFF
            if y % passo == 0:
                for k in range(0, larghezza_byte, canali * 7):  # 1 pixel ogni 7
                    colori.add(bytes(riga[k:k + canali]))
                    if len(colori) > soglia:
                        return False
                righe_lette += 1
            precedente = riga
            pos += 1 + larghezza_byte
            y += 1
        return len(colori) <= soglia
    except (zlib.error, ValueError, IndexError):
        return None


async def verifica_foto(urls: list[str], massimo: int = 6, referer: str = "") -> list[str]:
    """Tiene immagini plausibili del prodotto senza massacrare i formati moderni.

    La selezione iniziale ha già escluso loghi, placeholder e arredo del sito. Qui
    si controlla che l'URL risponda e che l'immagine non sia minuscola o un
    pittogramma. Su errori di rete transitori la foto di una pagina già
    confermata non si butta: la riproverà WooCommerce.
    """
    if not urls:
        return []

    async def _controlla(client: httpx.AsyncClient, u: str) -> str | None:
        parti = urlparse(u)
        intestazioni = {
            "User-Agent": rete.UA_BROWSER,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": referer or f"{parti.scheme}://{parti.netloc}/",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
        }
        try:
            r = await client.get(u, headers=intestazioni)
        except rete.IndirizzoNonPubblico:
            return None   # foto «dichiarata» su un indirizzo della rete interna: fuori
        except httpx.HTTPError:
            return u
        if r.status_code >= 400:
            return None

        ctype = (r.headers.get("content-type") or "").lower()
        dati = r.content
        if ctype and not ctype.startswith("image/") and not _dimensioni_immagine(dati):
            return None
        peso = len(dati)
        dim = _dimensioni_immagine(dati)
        if not dim:
            return u if ctype.startswith("image/") and peso >= 12_000 else None
        if min(dim) < 180:
            return None
        densita = peso / max(1, dim[0] * dim[1])
        pochi = _pochi_colori_png(dati)
        if pochi is True and densita < 0.010:
            return None
        if densita < 0.003:
            return None
        return u

    async with rete.client_pubblico(timeout=14.0, follow_redirects=True, http2=rete.HTTP2) as client:
        esiti = await asyncio.gather(*(_controlla(client, u) for u in urls[:12]))
    return [u for u in esiti if u][:massimo]


def _somiglia_produttore(scheda: dict) -> bool:
    """Il dominio d'origine contiene il nome della marca? (grundfos.com per Grundfos)"""
    dom = _norm(scheda.get("origine_dominio") or "")
    marca = _norm(scheda.get("brand") or "")
    return bool(marca) and len(marca) >= 3 and marca in dom


# ================================================================ traduzione
async def _traduci_verso(finale: dict, trad: dict, lingua: str) -> bool:
    """Porta nella lingua delle schede i campi arrivati in un'altra lingua.

    Scheda tutta estera: nome, descrizione e specifiche. Altrimenti solo i pezzi
    presi da pagine estere (descrizione e/o specifiche). I codici e le misure non
    passano mai dal traduttore. Ogni fallimento si ignora: una traduzione mancata
    non blocca mai la scheda."""
    from . import traduzione

    url, chiave = trad.get("url", ""), trad.get("chiave", "")
    verso = lingue.normalizza(lingua).lower()
    if not url:
        return False

    if finale.get("estera"):
        return await traduzione.traduci_scheda(finale, url, chiave, verso=verso)

    fatto = False
    if finale.get("_desc_estera") and (finale.get("description") or ""):
        out = await traduzione.traduci_sicuro([finale["description"]], url, chiave, verso=verso)
        if out and out[0] and out[0] != finale["description"]:
            finale["description"] = out[0]
            fatto = True

    if finale.get("_spec_estera") and (finale.get("attributes") or []):
        voci = finale["attributes"]
        testi: list[str] = []
        idx_nome, idx_val = [], []
        for v in voci:
            idx_nome.append(len(testi))
            testi.append(v.get("name") or "")
            val = v.get("value") or ""
            if traduzione._ha_cifre(val):
                idx_val.append(-1)
            else:
                idx_val.append(len(testi))
                testi.append(val)
        out = await traduzione.traduci_sicuro(testi, url, chiave, verso=verso)
        if out and len(out) == len(testi):
            tradotte = []
            for k, v in enumerate(voci):
                nome = out[idx_nome[k]] or v.get("name") or ""
                valore = (out[idx_val[k]] if idx_val[k] >= 0 else v.get("value")) or ""
                if nome and valore:
                    tradotte.append({"name": nome[:60], "value": valore[:120]})
            if tradotte:
                finale["attributes"] = tradotte
                fatto = True
    return fatto


# ============================================================ apertura pagine
_LIVELLI = {"dichiarato": "dichiara il codice", "codice": "codice in pagina",
            "mpn": "codice produttore", "modello": "solo somiglianza del nome"}


def _esito_rifiuto(motivo: str) -> str:
    m = (motivo or "").lower()
    if "elenco" in m or "categoria o di ricerca" in m:
        return "elenco"
    if "altro codice" in m or "altro prodotto" in m:
        return "altro_prodotto"
    if "dati strutturati" in m or "nessun nome" in m:
        return "senza_dati"
    return "non_conferma"


async def cerca_automatica(gtin: str, motore: str, chiave: str, extra: dict, mpn: str = "",
                           lingua: str = "IT") -> dict:
    """Trova la pagina da sola: chiede al motore di ricerca, poi legge i candidati.

    Il diario della ricerca viaggia con la scheda (``_diagnosi``) o con
    l'eccezione (``.diagnosi``): è quello che la tabella mostra alla voce «Perché?»."""
    from . import web_search

    lingua = lingue.normalizza(lingua)
    diario = diagnosi.Diario(gtin, lingua, motore)
    try:
        trovati = await web_search.candidati(gtin, motore, chiave, extra, lingua=lingua, diario=diario)
    except web_search.MotoriBloccati as exc:
        exc.diagnosi = diario.chiudi("motori_bloccati")
        raise
    except web_search.NessunProdotto as exc:
        err = NonTrovato(str(exc))
        err.diagnosi = diario.chiudi(diario.classifica())
        err.esiti = []
        raise err from exc
    except web_search.RicercaSpenta as exc:
        exc.diagnosi = diario.chiudi("ricerca_spenta")
        raise
    except web_search.RicercaError as exc:
        exc.diagnosi = diario.chiudi("motori_irraggiungibili")
        raise

    trad = {"url": extra.get("lt_url", ""), "chiave": extra.get("lt_chiave", "")}
    scheda = await apri_candidati(gtin, trovati, mpn, trad, render_url=extra.get("render_url", ""),
                                  lingua=lingua, diario=diario)
    esiti = scheda.pop("_esiti", None)
    if esiti:
        log.info("Codice %s, esiti pagine: %s", gtin, " · ".join(esiti[:8]))
    return scheda


async def apri_candidati(
    gtin: str, trovati: list[dict], mpn: str = "", trad: dict | None = None, render_url: str = "",
    lingua: str = "IT", diario: "diagnosi.Diario | None" = None,
) -> dict:
    """Apre i siti in ordine, incrocia le pagine che confermano e fonde la scheda.

    Non ci si ferma alla prima pagina buona: se ne raccolgono fino a tre, si
    controlla che parlino dello stesso prodotto (coerenza sui token del modello,
    marca e specifiche — niente IA) e si fondono: la più completa fa da base, le
    altre riempiono i buchi. Quello che arriva in un'altra lingua si traduce
    nella lingua delle schede, se LibreTranslate è configurato.
    """
    lingua = lingue.normalizza(lingua)
    diario = diario or diagnosi.Diario(gtin, lingua)
    if not trovati:
        err = NonTrovato("la ricerca non ha prodotto siti utilizzabili")
        err.esiti = []
        err.diagnosi = diario.chiudi("sconosciuto")
        raise err
    pezzi = trovati[0].get("modello") or []
    marche = trovati[0].get("marche") or []
    trad = trad or {}

    motivi: list[str] = []
    confermate: list[dict] = []
    voci_confermate: list[dict] = []   # la riga del diario di ciascuna, per correggerla dopo
    riserva_foto: list[str] = []
    aperture = 0      # pagine davvero lette
    tentativi = 0     # richieste fatte, comprese quelle respinte
    MAX_APERTURE = 8
    MAX_TENTATIVI = 16
    BASTANO = 3
    coda = list(trovati)
    provati: set[str] = set()

    def _ricca(sch: dict) -> bool:
        return bool(sch.get("description") and sch.get("images"))

    async with rete.Sessione(lingua, render_url, max_browser=3) as sessione:
        while coda:
            candidato = coda.pop(0)
            if tentativi >= MAX_TENTATIVI or aperture >= MAX_APERTURE:
                break
            if len(confermate) >= BASTANO and any(_ricca(c) for c in confermate):
                break
            if len(confermate) >= BASTANO + 2:
                break
            # Con una scheda completa nella lingua giusta in mano, le pagine in
            # altre lingue e i marketplace non aggiungono niente.
            if confermate and (candidato.get("estero") or candidato.get("marketplace")):
                migliore = max(confermate, key=_completezza)
                if _ricca(migliore) and not migliore.get("estera"):
                    break

            url = candidato["url"]
            if url in provati:
                continue
            provati.add(url)
            dominio = candidato.get("dominio") or rete.dominio(url)
            if url.lower().split("?")[0].endswith(".pdf"):
                diario.pagina(dominio, url, "documento", "documento PDF, non una scheda")
                continue

            tentativi += 1
            pagina = await sessione.apri(url)
            if not pagina.leggibile:
                motivo = pagina.motivo() or "pagina non leggibile"
                if pagina.robots:
                    esito = "robots"
                elif pagina.blocco:
                    esito = "bloccata"
                elif pagina.errore and not pagina.stato:
                    esito = "rete"
                elif pagina.errore == "documento PDF, non una pagina":
                    esito = "documento"
                else:
                    esito = "http"
                diario.pagina(dominio, url, esito, motivo, http=pagina.stato or None,
                              blocco=pagina.blocco, browser=pagina.via_browser)
                motivi.append(f"{dominio}: {motivo}")
                continue
            aperture += 1
            url_letta = pagina.url_finale or url
            provati.add(url_letta)

            perche: list[str] = []
            scheda = estrai(pagina.html, url_letta, gtin, mpn, pezzi, perche, consenti_estero=True,
                            richiedi_ean=bool(candidato.get("marketplace")), marche=marche, lingua=lingua)
            usato_browser = pagina.via_browser
            html_letto = pagina.html
            if (not scheda and perche and "dati strutturati" in perche[0]
                    and not pagina.via_browser and sessione.browser_disponibile):
                # Pagina servita come guscio JavaScript: renderizzata potrebbe avere tutto.
                resa = await sessione.browser(url_letta)
                if resa.leggibile:
                    perche = []
                    usato_browser = True
                    html_letto = resa.html
                    scheda = estrai(resa.html, url_letta, gtin, mpn, pezzi, perche, consenti_estero=True,
                                    richiedi_ean=bool(candidato.get("marketplace")), marche=marche,
                                    lingua=lingua)
            if not scheda:
                motivo = perche[0] if perche else "non conferma il prodotto"
                esito = _esito_rifiuto(motivo)
                if candidato.get("marketplace") and esito not in ("altro_prodotto", "elenco"):
                    motivo = "marketplace senza l'EAN dichiarato"
                # Categoria o ricerca interna che riporta il codice: la scheda vera è
                # dietro il link della tessera. Si prova subito, prima degli altri siti.
                segui = ""
                if esito == "elenco" and not candidato.get("da_elenco"):
                    segui = link_dal_elenco(html_letto, url_letta, gtin)
                    if segui in provati:
                        segui = ""
                    if segui:
                        coda.insert(0, {**candidato, "url": segui, "dominio": dominio, "da_elenco": True})
                diario.pagina(dominio, url_letta, esito, motivo, browser=usato_browser, segue=segui)
                motivi.append(f"{dominio}: {motivo}")
                continue

            scheda["origine_dominio"] = dominio
            livello = _LIVELLI.get(scheda.get("conferma", ""), "ok")
            diario.pagina(dominio, url_letta, "confermata", livello, conferma=scheda.get("conferma"),
                          lingua=scheda.get("lingua_pagina"), browser=usato_browser)
            voci_confermate.append(diario.dati["pagine"][-1])
            motivi.append(f"{dominio}: confermata ({livello})")
            confermate.append(scheda)

    # Coerenza fra i siti, a giochi fatti: comanda la pagina più forte (EAN
    # dichiarato nei dati strutturati, poi codice in pagina, poi completezza),
    # non la prima arrivata. Prima una pagina debole letta per prima faceva da
    # metro e le schede vere venivano buttate come «non coerenti».
    if len(confermate) > 1:
        forza = {"dichiarato": 4, "codice": 3, "mpn": 2, "modello": 1}

        def _peso(i: int) -> tuple:
            s = confermate[i]
            return (forza.get(s.get("conferma", ""), 0), not s.get("estera"), _completezza(s))

        tenute: list[int] = []
        for i in sorted(range(len(confermate)), key=_peso, reverse=True):
            if all(schede_coerenti(confermate[i], confermate[j]) for j in tenute):
                tenute.append(i)
                continue
            voce = voci_confermate[i]
            voce.update(esito="incoerente", dettaglio="non coerente con gli altri siti")
            voce.pop("conferma", None)
            motivi.append(f"{confermate[i].get('origine_dominio')}: scheda non coerente con gli altri siti")
        confermate = [confermate[i] for i in sorted(tenute)]
    for scheda in confermate:
        for u in (scheda.get("images") or []) + (scheda.get("_foto_html") or []):
            if u not in riserva_foto:
                riserva_foto.append(u)

    if not confermate:
        err = NonTrovato(" · ".join(motivi[:4]) or "nessuna pagina ha confermato il codice")
        err.esiti = list(motivi)
        err.diagnosi = diario.chiudi(diario.classifica())
        raise err

    # Regola anti-cantonata: se NESSUNA pagina ha mostrato il codice (EAN o
    # codice produttore) e c'è UNA sola pagina che somiglia per nome, non si
    # pubblica una scheda «a cazzo». Servono almeno due pagine coerenti fra loro.
    forti = [c for c in confermate if c.get("conferma") in ("dichiarato", "codice", "mpn")]
    if not forti and len(confermate) < 2:
        err = NonTrovato(
            "una sola pagina somiglia per nome ma nessuna riporta il codice: troppo poco per fidarsi · "
            + " · ".join(motivi[:3]))
        err.esiti = list(motivi)
        err.diagnosi = diario.chiudi("solo_somiglianza")
        raise err

    finale = fondi_schede(confermate) if len(confermate) > 1 else dict(confermate[0])
    if not forti:
        finale.setdefault("warnings", []).append(
            "Trovata solo per somiglianza del nome, nessun sito riporta il codice: "
            "controlla che sia proprio questo prodotto prima di pubblicare.")
    finale["conferma"] = "forte" if forti else "debole"
    if len(confermate) > 1:
        log.info("Codice %s confermato su %s siti: %s", gtin, len(confermate),
                 ", ".join(c["origine_dominio"] for c in confermate))

    if len(confermate) == 1:
        finale["_desc_estera"] = bool(finale.get("estera"))
        finale["_spec_estera"] = bool(finale.get("estera"))

    da_tradurre = finale.get("estera") or finale.get("_desc_estera") or finale.get("_spec_estera")
    if da_tradurre:
        if trad.get("url") and await _traduci_verso(finale, trad, lingua):
            finale.setdefault("warnings", []).append(
                "Parte della scheda è stata tradotta in automatico da un altro sito: rileggila.")
            log.info("Codice %s: campi in altra lingua tradotti in %s", gtin, lingua)
        elif not trad.get("url"):
            finale.setdefault("warnings", []).append(
                "Alcune informazioni vengono da un sito in un'altra lingua. Imposta "
                "LibreTranslate in Impostazioni per tradurle in automatico.")
        else:
            finale.setdefault("warnings", []).append(
                "Alcune informazioni sono in un'altra lingua e la traduzione automatica non è riuscita: "
                "controllale.")

    for k in ("estera", "_desc_estera", "_spec_estera", "lingua_pagina", "_foto_html"):
        finale.pop(k, None)

    # Ultima dogana per le foto: si scaricano e si misurano.
    referer = finale.get("origine_url") or ""
    finale["images"] = await verifica_foto(finale.get("images") or [], referer=referer)
    if not finale["images"] and riserva_foto:
        finale["images"] = await verifica_foto(riserva_foto)

    finale["lingua"] = lingua
    scheda_sicura(finale)   # dopo fusione e traduzione: niente HTML attivo
    finale["_esiti"] = list(motivi)
    finale["_diagnosi"] = diario.chiudi(
        "trovato", f"Scheda trovata su {finale.get('origine_dominio') or 'un sito'}"
        + (f", confermata da {len(confermate)} siti" if len(confermate) > 1 else "") + ".")
    log.info("Codice %s risolto su %s", gtin, finale.get("origine_dominio"))
    return finale


async def cerca(modelli: list[str], gtin: str, mpn: str = "", marca: str = "",
                lingua: str = "IT", render_url: str = "") -> dict:
    """Indirizzi fissi impostati per un cliente: la prima pagina che conferma vince."""
    motivi: list[str] = []
    async with rete.Sessione(lingua, render_url, max_browser=2) as sessione:
        for modello in modelli[:6]:
            url = (modello.replace("{gtin}", gtin).replace("{ean}", gtin)
                   .replace("{mpn}", mpn or "").replace("{brand}", marca or "")).strip()
            if not url.startswith("http"):
                continue
            pagina = await sessione.apri(url)
            dominio = rete.dominio(url)
            if not pagina.leggibile:
                motivi.append(f"{dominio}: {pagina.motivo() or 'pagina non leggibile'}")
                continue
            scheda = estrai(pagina.html, pagina.url_finale or url, gtin, mpn, lingua=lingua)
            if scheda:
                scheda["origine_dominio"] = dominio
                scheda.pop("estera", None)
                scheda.pop("lingua_pagina", None)
                scheda.pop("_foto_html", None)
                log.info("Scheda trovata su %s per %s", dominio, gtin)
                return scheda
            motivi.append(f"{dominio}: la pagina non conferma questo codice")
    raise NonTrovato(" · ".join(motivi) or "nessun indirizzo utilizzabile")
