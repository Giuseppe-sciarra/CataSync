"""Testi e immagini che arrivano da fuori, resi innocui prima del portale e del sito.

Le schede si leggono da pagine di terzi: una descrizione in JSON-LD può
contenere ``<script>``, ``<img onerror=…>`` o un ``<iframe>``. Finita così nel
portale sarebbe codice eseguito nella console dell'amministratore; finita su
WooCommerce, con chiavi di un amministratore (che può scrivere HTML libero),
sarebbe codice eseguito dai visitatori del negozio del cliente.

Regola: nelle descrizioni restano solo i tag di formattazione (paragrafi,
elenchi, grassetti, tabelle), senza attributi; script, stili, iframe, form,
immagini e link spariscono (dei link resta il testo). Nomi, marche e
specifiche sono testo puro.
"""
import re
from html import escape, unescape
from html.parser import HTMLParser

PERMESSI = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h2", "h3", "h4", "h5",
            "table", "thead", "tbody", "tfoot", "tr", "th", "td", "sup", "sub", "blockquote"}
# Tag da buttare INTERI, contenuto compreso.
DA_BUTTARE = {"script", "style", "iframe", "object", "svg", "math", "form", "select", "textarea",
              "noscript", "template", "head", "title", "frameset", "applet", "audio", "video",
              "canvas", "button", "map"}
# Tag senza chiusura: si ignorano e basta (img, input, embed, link, meta…).
VUOTI = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
         "source", "track", "wbr", "frame", "keygen"}
# Contenitori di blocco non permessi: il testo resta, separato da uno spazio.
BLOCCHI = {"div", "section", "article", "header", "footer", "aside", "main", "dl", "dt", "dd",
           "figure", "figcaption", "center", "address", "nav", "details", "summary"}


class _Pulitore(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fuori: list[str] = []
        self.salta = 0
        self.aperti: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DA_BUTTARE:
            self.salta += 1
            return
        if self.salta:
            return
        if tag == "br":
            self.fuori.append("<br>")
        elif tag in PERMESSI:
            self.fuori.append(f"<{tag}>")
            self.aperti.append(tag)
        elif tag in BLOCCHI or tag in VUOTI:
            self.fuori.append(" ")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.salta or tag in DA_BUTTARE:
            return
        if tag == "br":
            self.fuori.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DA_BUTTARE:
            if self.salta:
                self.salta -= 1
            return
        if self.salta:
            return
        if tag in PERMESSI and tag in self.aperti:
            while self.aperti:
                aperto = self.aperti.pop()
                self.fuori.append(f"</{aperto}>")
                if aperto == tag:
                    break
        elif tag in BLOCCHI:
            self.fuori.append(" ")

    def handle_data(self, data):
        if not self.salta:
            self.fuori.append(escape(data, quote=False))

    def risultato(self) -> str:
        while self.aperti:
            self.fuori.append(f"</{self.aperti.pop()}>")
        return re.sub(r"[ \t]{2,}", " ", "".join(self.fuori)).strip()


def pulisci_html(testo: str) -> str:
    """Descrizione sicura. Il testo semplice resta com'è (nessun tag possibile)."""
    if not testo:
        return ""
    testo = str(testo)
    if "<" not in testo:
        return testo
    p = _Pulitore()
    try:
        p.feed(testo)
        p.close()
    except Exception:  # pragma: no cover - HTMLParser non solleva quasi mai
        return escape(re.sub(r"<[^>]*>", " ", testo), quote=False)
    return p.risultato()


def testo_puro(testo) -> str:
    """Nomi, marche, specifiche: niente tag, niente entità che diventano tag."""
    if testo is None:
        return ""
    s = unescape(str(testo))
    s = re.sub(r"<[^>]*>", " ", s)
    s = s.replace("<", "‹").replace(">", "›")
    return re.sub(r"\s+", " ", s).strip()


def scheda_sicura(scheda: dict) -> dict:
    """La stessa scheda con descrizioni pulite e nomi/specifiche in testo puro."""
    if not isinstance(scheda, dict):
        return scheda
    for campo in ("description", "short_description"):
        if scheda.get(campo):
            scheda[campo] = pulisci_html(scheda[campo])
    for campo in ("title", "brand", "mpn"):
        if scheda.get(campo):
            scheda[campo] = testo_puro(scheda[campo])
    categoria = scheda.get("category")
    if isinstance(categoria, dict) and categoria.get("name"):
        categoria["name"] = testo_puro(categoria["name"])
    voci = []
    for voce in scheda.get("attributes") or []:
        if isinstance(voce, dict):
            voce = dict(voce)
            for k in ("name", "value", "group"):
                if voce.get(k):
                    voce[k] = testo_puro(voce[k])
            voci.append(voce)
    if "attributes" in scheda:
        scheda["attributes"] = voci
    if scheda.get("bullets"):
        scheda["bullets"] = [testo_puro(b) for b in scheda["bullets"] if b]
    if scheda.get("images"):
        scheda["images"] = [u for u in scheda["images"] if isinstance(u, str) and u.startswith(("http://", "https://"))]
    return scheda


# ------------------------------------------------------------ immagini caricate
# Un SVG è codice, non un'immagine: dentro un <img> non esegue niente, ma aperto
# direttamente dal suo indirizzo sì. Loghi e favicon SVG si accettano solo puliti
# e si servono comunque con una CSP che spegne gli script.
_SVG_ATTIVO = re.compile(
    rb"<script|javascript:|<foreignobject|<iframe|<embed|<object|<handler|"
    rb"[\s/\"']on[a-z]+\s*=|xlink:href\s*=\s*[\"']?\s*data:text/html|"
    rb"<set\b[^>]*attributename\s*=\s*[\"']?on",
    re.I)

INTESTAZIONI_SVG = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def svg_attivo(blob: bytes) -> bool:
    """True se l'SVG contiene script, gestori di eventi o contenuti incorporati."""
    return bool(_SVG_ATTIVO.search(blob[:400_000]))


def tipo_immagine(blob: bytes) -> str:
    """Il formato lo dicono i byte, non il nome né il tipo dichiarato dal browser
    (un .ico arriva come image/x-icon, image/vnd.microsoft.icon o application/octet-stream)."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:4] == b"\x00\x00\x01\x00":
        return ".ico"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    testa = blob[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if testa.startswith((b"<svg", b"<?xml", b"<!--", b"<!doctype svg")) and b"<svg" in blob[:200_000].lower():
        return ".svg"
    return ""
