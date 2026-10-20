"""Riconoscimento dei codici che il cliente incolla: EAN o codice produttore.

Nello stesso riquadro possono finire EAN («8059893356383») e codici produttore
(«97924465» di Grundfos, «FAST 14 FI» di Ariston, «60220687»). Si distinguono
da soli: un numero di 8/12/13/14 cifre con la cifra di controllo giusta è un
EAN; tutto il resto è un codice produttore. Il codice produttore si cerca sul
web così com'è — sono quasi sempre univoci — e la marca la si ricava dai
risultati.
"""
import re

LUNGHEZZE_GTIN = (8, 12, 13, 14)


def gtin_a_circolazione_limitata(cifre: str) -> bool:
    """Codici EAN-13 con prefisso 20-29: numerazioni GS1 a circolazione limitata.

    Sono usate spesso come codici interni di punto vendita/grossista, prodotti a peso
    variabile o numerazioni locali. Il checksum puo' essere perfettamente valido, ma
    non identificano in modo globale una scheda prodotto sul web.
    Trattarli come EAN pubblici produce falsi positivi molto convincenti.
    """
    return (
        isinstance(cifre, str)
        and len(cifre) == 13
        and cifre.isdigit()
        and 20 <= int(cifre[:2]) <= 29
    )


def checksum_gtin(cifre: str) -> bool:
    """Cifra di controllo GTIN (EAN-8/UPC-A/EAN-13/GTIN-14): pesi 3-1 da destra."""
    if not cifre.isdigit() or len(cifre) not in LUNGHEZZE_GTIN:
        return False
    corpo, controllo = cifre[:-1], int(cifre[-1])
    somma = 0
    for i, ch in enumerate(reversed(corpo)):
        somma += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - somma % 10) % 10 == controllo


def e_ean(codice: str) -> bool:
    """È un GTIN pubblico utilizzabile per una ricerca prodotto?

    I prefissi EAN-13 20-29 hanno checksum valido ma sono a circolazione limitata:
    non vanno cercati né trattati come identificatori globali.
    """
    cifre = "".join(ch for ch in (codice or "") if ch.isdigit())
    return (
        cifre == (codice or "").strip()
        and checksum_gtin(cifre)
        and not gtin_a_circolazione_limitata(cifre)
    )


def classifica(grezzo: str) -> tuple[str, str] | None:
    """('ean', cifre) oppure ('mpn', codice) oppure None se non è un codice."""
    testo = re.sub(r"\s+", " ", (grezzo or "")).strip(" \t'\"")
    if not testo:
        return None
    cifre = "".join(ch for ch in testo if ch.isdigit())
    # Solo cifre (con eventuali spazi/apostrofi da Excel) della lunghezza giusta
    # e col checksum buono: EAN.
    if re.fullmatch(r"[\d\s'\.]+", testo) and checksum_gtin(cifre):
        if gtin_a_circolazione_limitata(cifre):
            return ("interno", cifre)
        return ("ean", cifre)
    # Un EAN in mezzo a qualche parola («8801643654689 SAMSUNG», «EAN: 8801643654689»): si
    # cerca l'EAN, le parole intorno farebbero solo confusione nella ricerca.
    gtin_nella_riga = [t for t in re.findall(r"(?<![\w])\d{12,14}(?![\w])", testo) if checksum_gtin(t)]
    if len(gtin_nella_riga) == 1 and len(testo.split()) <= 4:
        cifre = gtin_nella_riga[0]
        return ("interno", cifre) if gtin_a_circolazione_limitata(cifre) else ("ean", cifre)
    # Codice produttore: da 4 a 32 caratteri e al massimo quattro pezzi («FAST 14 FI»), con
    # almeno una cifra, oppure una parola sola di quattro lettere o più («ESYBOX»). Frasi,
    # comandi e descrizioni incollate per sbaglio («docker inspect searxng --format»,
    # «GAMMA POMPE DI CALORE (EHS) – Unità esterna…») non sono codici: cercarle consuma
    # ricerche e fa scattare i blocchi dei motori.
    if len(testo) < 4 or len(testo) > 32 or len(testo.split()) > 4:
        return None
    if re.search(r"--|https?:|www\.|[$|<>{}=@]", testo):
        return None
    if not (re.search(r"\d", testo) or (" " not in testo and len(re.findall(r"[A-Za-z]", testo)) >= 4)):
        return None
    return ("mpn", testo)


def parse_codici(raw: str) -> tuple[list[str], list[str]]:
    """Righe incollate → (codici buoni, scarti). Un codice per riga; le righe
    di soli numeri possono contenere più EAN separati da virgola o spazio."""
    buoni: list[str] = []
    scarti: list[str] = []
    visti: set[str] = set()
    for riga in (raw or "").replace("\r", "").split("\n"):
        riga = riga.strip()
        if not riga:
            continue
        # riga di soli numeri e separatori: più EAN sulla stessa riga
        if re.fullmatch(r"[\d\s,;'\.]+", riga) and re.search(r"[,;\s]", riga.strip()):
            pezzi = [p for p in re.split(r"[,;\s]+", riga) if p]
        else:
            pezzi = [p for p in re.split(r"[;]+", riga) if p.strip()] or [riga]
        for pezzo in pezzi:
            esito = classifica(pezzo)
            if not esito:
                if pezzo.strip():
                    scarti.append(pezzo.strip())
                continue
            tipo, codice = esito
            if tipo == "interno":
                scarti.append(f"{codice} (codice a circolazione limitata 20-29)")
                continue
            chiave = codice.lower()
            if chiave in visti:
                continue
            visti.add(chiave)
            buoni.append(codice)
    return buoni, scarti
