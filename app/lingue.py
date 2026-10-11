"""Lingue delle schede: riconoscerle, preferirle, ripulirle.

Il portale si rivende anche fuori dall'Italia: chi lavora in inglese o in
francese deve ricevere schede in inglese o in francese. Qui stanno i dati che
servono a tutta la catena per ragionare per lingua di destinazione invece che
«italiano sì / italiano no»:

- quali domini e quali percorsi tradiscono una lingua (``.fr``, ``/de-de/``…)
- quali parole la riconoscono nel testo (quando l'attributo ``lang`` manca o mente)
- quali frasi da negozio togliere dalle descrizioni, in ogni lingua
"""
import re

# Lingue gestite per le schede, nell'ordine in cui compaiono nei menu.
LINGUE = ("IT", "EN", "FR", "DE", "ES")
PREDEFINITA = "IT"

NOMI = {
    "IT": "Italiano",
    "EN": "English",
    "FR": "Français",
    "DE": "Deutsch",
    "ES": "Español",
}


def normalizza(lingua: str | None, ripiego: str = PREDEFINITA) -> str:
    """«it», «it-IT», «IT» → «IT». Una lingua sconosciuta torna al ripiego."""
    codice = (lingua or "").strip().upper().replace("_", "-").split("-")[0]
    return codice if codice in LINGUE else ripiego


# Domini nazionali: un .fr parla quasi sempre francese. I .com, .eu, .net non
# dicono niente e restano neutri. Gli svizzeri (.ch) sono plurilingui: neutri.
TLD = {
    "IT": (".it", ".sm"),
    "EN": (".co.uk", ".uk", ".ie", ".us", ".com.au", ".au", ".co.nz", ".nz", ".ca"),
    "FR": (".fr", ".be", ".lu"),
    "DE": (".de", ".at"),
    "ES": (".es", ".com.mx", ".mx", ".com.ar", ".ar", ".cl", ".co", ".pe"),
}

# Domini di paesi con lingue fuori dalle cinque gestite: valgono come «altra lingua».
TLD_ALTRE = (
    ".nl", ".pt", ".pl", ".cz", ".sk", ".hu", ".ro", ".bg", ".gr", ".hr", ".si",
    ".dk", ".se", ".no", ".fi", ".ru", ".ua", ".tr", ".cn", ".jp", ".kr", ".in",
    ".br", ".com.br", ".il", ".ae", ".lt", ".lv", ".ee", ".rs", ".ba", ".fo",
    ".is", ".tw", ".hk", ".sg", ".my", ".th", ".vn", ".id", ".za",
)

# Sezioni di lingua nei siti internazionali: bosch-home.com/it/…, /fr-fr/…
PERCORSI = {
    "IT": ("/it/", "/it-it/", "/ita/", "/italia/", "/it_it/", "lang=it", "hl=it", "/i/it", "/it-ch/"),
    "EN": ("/en/", "/en-gb/", "/en-us/", "/en_gb/", "/en_us/", "/en-int/", "/en-ie/", "/uk/", "/gb/",
           "lang=en", "hl=en", "/en-au/"),
    "FR": ("/fr/", "/fr-fr/", "/fr_fr/", "/fr-be/", "/fr-ch/", "lang=fr", "hl=fr", "/fra/"),
    "DE": ("/de/", "/de-de/", "/de_de/", "/de-at/", "/de-ch/", "/deu/", "/ger/", "lang=de", "hl=de"),
    "ES": ("/es/", "/es-es/", "/es_es/", "/esp/", "lang=es", "hl=es", "/es-mx/"),
}

# Parole che in un testo di quella lingua ci sono quasi per forza.
SPIE = {
    # Solo parole ESCLUSIVE di ciascuna lingua: «con», «del», «una», «su», «al»
    # sono italiane quanto spagnole, e una pagina italiana con «Su Elettroclimaweb
    # trovi… con corriere… al tuo servizio» risultava spagnola.
    "IT": (" il ", " lo ", " gli ", " degli ", " dei ", " della ", " delle ", " dello ",
           " nel ", " nella ", " sul ", " sulla ", " alla ", " allo ", " agli ", " dal ", " dalla ",
           " che ", " per ", " uno ", " sono ", " più ", " anche ", " questo ", " questa ",
           " oppure ", " senza ", " tutti ", " ogni ", " è ", " già ", " può ", " di ", " dell'",
           " nell'", " all'", " sull'", " essere ", " grazie ", " tuo ", " tua ", " nostro "),
    "EN": (" the ", " and ", " with ", " for ", " this ", " from ", " is ", " are ", " of ",
           " to ", " can ", " be ", " which ", " your ", " that ", " it ", " by ", " or ", " an "),
    "FR": (" les ", " des ", " du ", " avec ", " pour ", " dans ", " est ", " sur ", " et ",
           " au ", " aux ", " cette ", " qui ", " pas ", " vous ", " ou ", " votre ", " être ",
           " très ", " leur ", " d'un ", " d'une ", " l'eau "),
    "DE": (" der ", " die ", " das ", " und ", " mit ", " für ", " ist ", " ein ", " eine ",
           " nicht ", " auch ", " von ", " zu ", " den ", " dem ", " im ", " oder ", " sie ", " bei "),
    "ES": (" el ", " los ", " las ", " para ", " por ", " es ", " está ", " como ", " más ",
           " sin ", " y ", " muy ", " también ", " este ", " esta ", " puede ", " agua ", " sus "),
}

# Accept-Language da mandare ai siti: la pagina arriva già nella lingua giusta
# quando il sito sa servirla.
ACCEPT_LANGUAGE = {
    "IT": "it-IT,it;q=0.9,en;q=0.6",
    "EN": "en-GB,en;q=0.9,en-US;q=0.8",
    "FR": "fr-FR,fr;q=0.9,en;q=0.6",
    "DE": "de-DE,de;q=0.9,en;q=0.6",
    "ES": "es-ES,es;q=0.9,en;q=0.6",
}

# Frasi da venditore, non da scheda prodotto: si tolgono dalle descrizioni.
NEGOZIO = {
    "IT": ("prezzo più basso", "prezzo piu basso", "in offerta", "offerta online",
           "acquista online", "spedizione gratuita", "spedizione gratis", "aggiungi al carrello",
           "disponibilità immediata", "risparmia ", " risparmi ", "sconto del", "miglior prezzo",
           "confronta i prezzi", "prezzi a partire da", "compra online", "consegna in 24",
           "stai cercando", "spedizioni veloci", "spedizione veloce", "corriere espresso",
           "al tuo servizio", "acquistalo", "acquistala", "compralo", "su ordinazione"),
    "EN": ("add to cart", "add to basket", "free shipping", "free delivery", "buy now",
           "best price", "lowest price", "order now", "special offer", "in stock now",
           "next day delivery", "price match", "compare prices", "looking for", "fast delivery",
           "fast shipping"),
    "FR": ("ajouter au panier", "livraison gratuite", "livraison offerte", "meilleur prix",
           "acheter maintenant", "commandez", "prix le plus bas", "en promotion", "comparer les prix",
           "vous cherchez", "livraison rapide"),
    "DE": ("in den warenkorb", "versandkostenfrei", "kostenloser versand", "jetzt kaufen",
           "bester preis", "günstigster preis", "sofort lieferbar", "preisvergleich", "jetzt bestellen",
           "sie suchen", "schneller versand"),
    "ES": ("añadir al carrito", "envío gratis", "envío gratuito", "comprar ahora", "mejor precio",
           "precio más bajo", "en oferta", "comparar precios", "estás buscando", "envío rápido"),
}

# Tutte insieme: una pagina tedesca tradotta resta comunque una pagina di negozio.
NEGOZIO_TUTTE = tuple(f for frasi in NEGOZIO.values() for f in frasi)

PREZZO = re.compile(
    r"\d+[.,]\d{2}\s*(?:€|euro|eur\b|£|chf)|(?:€|£|\$|chf)\s*\d+[.,]\d{2}", re.I
)


def da_url(url: str, dominio: str) -> str:
    """Lingua che l'indirizzo dichiara (percorso o dominio). '' se non dice niente."""
    minuscolo = (url or "").lower()
    for lingua, segnali in PERCORSI.items():
        if any(s in minuscolo for s in segnali):
            return lingua
    for lingua, tld in TLD.items():
        if dominio.endswith(tld):
            return lingua
    if dominio.endswith(TLD_ALTRE):
        return "XX"   # altra lingua, fuori dalle cinque
    return ""


def dal_testo(testo: str) -> tuple[str, int]:
    """Lingua più probabile del testo e lo stacco dalla seconda (0 se incerta)."""
    campione = " " + re.sub(r"\s+", " ", (testo or "")).lower() + " "
    if len(campione) < 40:
        return "", 0
    punti = {lingua: sum(1 for s in spie if s in campione) for lingua, spie in SPIE.items()}
    migliore = max(punti, key=lambda k: punti[k])
    secondo = max((v for k, v in punti.items() if k != migliore), default=0)
    if punti[migliore] < 3 or punti[migliore] <= secondo:
        return "", 0
    return migliore, punti[migliore] - secondo


def attributo_html(html: str) -> str:
    m = re.search(r'<html[^>]+lang=["\']([a-zA-Z]{2})', (html or "")[:4000], re.I)
    if not m:
        return ""
    codice = m.group(1).upper()
    return codice if codice in LINGUE else "XX"


def della_pagina(html: str, testo: str, url: str, dominio: str) -> str:
    """Lingua di una pagina: prima com'è scritta, poi cosa dichiara.

    Tanti siti mettono lang="en" e servono testi tradotti, o non dichiarano
    niente: il testo vince quando è netto, altrimenti si guardano attributo
    e indirizzo."""
    dal_corpo, stacco = dal_testo(testo)
    dichiarata = attributo_html(html)
    # Il testo smentisce la pagina solo se è netto: tre spie più della seconda lingua.
    if dal_corpo and (not dichiarata or stacco >= 3):
        return dal_corpo
    if dichiarata:
        return dichiarata
    return da_url(url, dominio) or ""


def ripulisci_da_negozio(descrizione: str) -> str:
    """Toglie le frasi da venditore e tiene il resto della descrizione."""
    if not descrizione:
        return descrizione
    frasi = re.split(r"(?<=[.!?…])\s+", descrizione)
    buone = []
    for frase in frasi:
        minuscolo = frase.lower()
        if any(spia in minuscolo for spia in NEGOZIO_TUTTE):
            continue
        if PREZZO.search(frase):
            continue
        buone.append(frase)
    ripulita = " ".join(buone).strip()
    # Se non è rimasto niente di sensato, meglio vuota che tre parole monche.
    return ripulita if len(ripulita) >= 40 else ""


def testo_da_negozio(descrizione: str) -> bool:
    minuscolo = (descrizione or "").lower()
    return any(spia in minuscolo for spia in NEGOZIO_TUTTE) or bool(PREZZO.search(descrizione or ""))
