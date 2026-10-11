"""Controllo di completezza delle schede.

Una scheda va sul sito solo se ha tutto quello che serve. Cosa serve lo decide il sito:
un negozio di informatica senza prezzo non vende, uno showroom può pubblicare senza
immagini in attesa delle foto proprie. Quindi l'elenco è per sito, non fisso nel codice.
"""
import json

FIELDS = {
    "title": "Nome prodotto",
    "description": "Descrizione",
    "images": "Almeno un'immagine",
    "attributes": "Almeno una specifica",
    "brand": "Marca",
    "mpn": "Codice produttore",
    "category": "Categoria",
    "regular_price": "Prezzo",
}

DEFAULT_REQUIRED = ["title", "description", "images", "category", "regular_price"]


def required_for(site) -> list[str]:
    try:
        stored = json.loads(site.required_fields or "[]")
    except ValueError:
        stored = []
    if not isinstance(stored, list) or not stored:
        fields = list(DEFAULT_REQUIRED)
    else:
        fields = [f for f in stored if f in FIELDS]
    # La categoria del sito non è negoziabile: senza, il prodotto finirebbe in
    # «Senza categoria» e sparirebbe dalla navigazione del negozio.
    if "category" not in fields:
        fields.append("category")
    return fields


def _has(product: dict, field: str) -> bool:
    if field == "images":
        return bool(product.get("images"))
    if field == "attributes":
        return bool(product.get("attributes"))
    if field == "category":
        # Conta solo la categoria scelta sul sito (o risolta dalle corrispondenze):
        # il nome che arriva dalla fonte non basta a piazzare il prodotto.
        return bool(product.get("category_id"))
    return bool(str(product.get(field) or "").strip())


# A quale politica di scrittura corrisponde ogni campo obbligatorio.
POLICY_KEY = {
    "title": "title",
    "description": "description",
    "images": "images",
    "attributes": "attributes",
    "brand": "brand",
    "category": "categories",
    "regular_price": "prices",
}


def effective(site, policy: dict | None = None) -> list[str]:
    """Campi obbligatori che contano davvero per questo giro.

    Se una politica dice di non toccare un campo, pretenderlo non ha senso: il giro
    notturno non scrive i prezzi, quindi una scheda senza prezzo nel portale non deve
    bloccarsi — il prezzo sul sito c'è già ed è quello del cliente."""
    fields = required_for(site)
    if not policy:
        return fields
    return [f for f in fields if policy.get(POLICY_KEY.get(f, f), "fill") != "skip"]


def check(product: dict, site, policy: dict | None = None) -> list[str]:
    """Elenco dei campi obbligatori mancanti. Vuoto = si può pubblicare."""
    return [field for field in effective(site, policy) if not _has(product, field)]


def labels(missing: list[str]) -> str:
    return ", ".join(FIELDS.get(f, f) for f in missing)


def sale_price_issue(product: dict) -> str:
    """Il prezzo scontato deve essere sotto quello pieno, altrimenti WooCommerce lo ignora."""
    try:
        regular = float(str(product.get("regular_price") or "").replace(",", "."))
        sale = float(str(product.get("sale_price") or "").replace(",", "."))
    except ValueError:
        return ""
    if sale and regular and sale >= regular:
        return "Il prezzo scontato non è inferiore a quello pieno: WooCommerce lo ignorerebbe."
    return ""
