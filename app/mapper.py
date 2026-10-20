"""Dal JSON Icecat al formato canonico che il portale scrive poi su WooCommerce.

Icecat ha cambiato più volte la forma della risposta e alcuni container arrivano solo
con certi livelli di sottoscrizione. Qui si legge in modo difensivo: ogni campo prova
più percorsi noti e, se non trova niente, resta vuoto invece di far esplodere il job.
Il payload grezzo resta comunque salvato in cache, quindi un mapping si può correggere
a posteriori senza richiamare Icecat.
"""
from typing import Any

# Frasi che tipicamente introducono condizioni di garanzia dentro il marketing text.
# In Italia la garanzia legale è materia del venditore, non del produttore: meglio
# non pubblicarla alla cieca sul sito del cliente.
WARRANTY_HINTS = (
    "garanzia",
    "warranty",
    "guarantee",
    "garantie",
    "anni di garanzia",
)


def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def _txt(node: Any) -> str:
    """Icecat incapsula spesso i valori in {'Value': ...} o {'Name': {'Value': ...}}."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, dict):
        for key in ("Value", "value", "Name", "name", "Text", "text"):
            if key in node:
                return _txt(node[key])
    if isinstance(node, list) and node:
        return _txt(node[0])
    return ""


def _dig(data: Any, path: str) -> Any:
    """Percorso con punti, tollerante ai nodi mancanti: 'GeneralInfo.Category.Name'."""
    node = data
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            idx = int(part)
            node = node[idx] if idx < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def _root(payload: dict) -> dict:
    """La risposta a volte è {'data': {...}}, a volte già il contenuto."""
    for key in ("data", "Data"):
        node = payload.get(key)
        if isinstance(node, dict) and node:
            return node
    return payload


def _images(root: dict) -> list[str]:
    urls: list[str] = []

    main = _first(
        _dig(root, "Image.HighPic"),
        _dig(root, "Image.Pic"),
        _dig(root, "GeneralInfo.Image.HighPic"),
        _dig(root, "HighPic"),
    )
    if isinstance(main, str) and main:
        urls.append(main)

    gallery = _first(_dig(root, "Gallery"), _dig(root, "GeneralInfo.Gallery"), []) or []
    if isinstance(gallery, dict):
        gallery = list(gallery.values())
    for shot in gallery:
        if isinstance(shot, str):
            candidate = shot
        else:
            candidate = _first(
                shot.get("Pic"), shot.get("HighPic"), shot.get("Original"), shot.get("LowPic")
            )
        if isinstance(candidate, str) and candidate and candidate not in urls:
            urls.append(candidate)

    return [u for u in urls if u.startswith("http")]


def _attributes(root: dict) -> list[dict]:
    out: list[dict] = []
    groups = _first(
        _dig(root, "FeaturesGroups"),
        _dig(root, "GeneralInfo.FeaturesGroups"),
        _dig(root, "FeatureGroups"),
        [],
    ) or []
    if isinstance(groups, dict):
        groups = list(groups.values())

    for group in groups:
        if not isinstance(group, dict):
            continue
        gname = _txt(_first(group.get("FeatureGroup"), group.get("Name"), group.get("GroupName")))
        features = _first(group.get("Features"), group.get("Feature"), []) or []
        if isinstance(features, dict):
            features = list(features.values())
        for feat in features:
            if not isinstance(feat, dict):
                continue
            name = _txt(_first(feat.get("Feature"), feat.get("Name"), feat.get("LocalName")))
            value = _txt(
                _first(
                    feat.get("PresentationValue"),
                    feat.get("Value"),
                    feat.get("RawValue"),
                    feat.get("LocalValue"),
                )
            )
            if name and value:
                # Icecat marca "Searchable" le caratteristiche su cui fa filtrare:
                # diagonale, risoluzione, Wi-Fi. Sono quelle che guarda chi compra,
                # mentre l'ordine puro fa risalire cose come «Regolazioni formato
                # schermo». Il flag serve a scegliere quali tenere quando si taglia.
                chiave = str(feat.get("Searchable", "")).strip().lower() in ("1", "true", "y", "yes")
                out.append({"group": gname, "name": name, "value": value, "key": chiave})

    # Icecat ripete a volte la stessa feature in gruppi diversi.
    seen, unique = set(), []
    for attr in out:
        key = (attr["name"].lower(), attr["value"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(attr)
    return unique


def _bullets(root: dict) -> list[str]:
    node = _first(
        _dig(root, "GeneralInfo.BulletPoints.Values"),
        _dig(root, "GeneralInfo.BulletPoints"),
        _dig(root, "BulletPoints.Values"),
        _dig(root, "BulletPoints"),
        [],
    ) or []
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, str):
        node = [node]
    return [_txt(x) for x in node if _txt(x)]


def _documents(root: dict) -> list[dict]:
    docs: list[dict] = []
    media = _first(_dig(root, "Multimedia"), _dig(root, "GeneralInfo.Multimedia"), []) or []
    if isinstance(media, dict):
        media = list(media.values())
    for item in media:
        if not isinstance(item, dict):
            continue
        url = _first(item.get("URL"), item.get("Url"), item.get("Link"))
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        docs.append(
            {
                "url": url,
                "type": _txt(_first(item.get("Type"), item.get("ContentType"))),
                "label": _txt(_first(item.get("Description"), item.get("Caption"), item.get("Name"))),
            }
        )
    return docs


def _flag_warranty(text: str) -> bool:
    low = (text or "").lower()
    return any(hint in low for hint in WARRANTY_HINTS)


def normalize(payload: dict, gtin: str) -> dict:
    """Restituisce il dizionario canonico. Non solleva mai: se un campo manca, resta vuoto."""
    root = _root(payload or {})
    gi = root.get("GeneralInfo") if isinstance(root.get("GeneralInfo"), dict) else {}

    title = _txt(
        _first(
            gi.get("Title"),
            _dig(root, "GeneralInfo.TitleInfo.GeneratedLocalTitle.Value"),
            _dig(root, "TitleInfo.GeneratedLocalTitle.Value"),
            _dig(root, "TitleInfo.BrandLocalTitle.Value"),
            _dig(root, "GeneralInfo.TitleInfo.GeneratedIntTitle"),
            gi.get("ProductName"),
            root.get("Title"),
            root.get("ProductName"),
        )
    )
    brand = _txt(
        _first(
            gi.get("BrandName"),
            gi.get("Brand"),
            _dig(root, "EssentialInfo.Brand"),
            root.get("Brand"),
            root.get("BrandName"),
        )
    )
    mpn = _txt(
        _first(
            gi.get("BrandPartCode"),
            gi.get("ProductCode"),
            _dig(root, "EssentialInfo.ProductCode"),
            gi.get("PartNumber"),
            root.get("ProductCode"),
        )
    )
    icecat_id = _txt(
        _first(gi.get("IcecatId"), gi.get("ProductId"), root.get("IcecatId"), root.get("icecat_id"))
    )

    # L'API Live risponde in due forme diverse a seconda dei container chiesti:
    # con `generalinfo` i testi stanno annidati sotto GeneralInfo, senza stanno
    # in cima come MarketingText. Si leggono entrambe.
    description = _txt(
        _first(
            _dig(root, "GeneralInfo.Description.LongDesc"),
            _dig(root, "GeneralInfo.SummaryDescription.LongSummaryDescription"),
            _dig(root, "GeneralInfo.MarketingText"),
            root.get("MarketingText"),
            _dig(root, "MarketingText.Value"),
            _dig(root, "GeneralInfo.Description.MiddleDesc"),
            _dig(root, "SummaryDescription.LongSummaryDescription"),
            _dig(root, "Description.LongDesc"),
        )
    )
    # Descrizione breve non usata: sul sito si scrive solo quella lunga.
    short_description = ""

    category_name = _txt(
        _first(_dig(root, "GeneralInfo.Category.Name"), _dig(root, "Category.Name"), _dig(root, "GeneralInfo.Category"))
    )
    category_id = _txt(
        _first(
            _dig(root, "GeneralInfo.Category.CategoryID"),
            _dig(root, "GeneralInfo.Category.ID"),
            _dig(root, "Category.CategoryID"),
        )
    )

    bullets = _bullets(root)
    if not short_description and bullets:
        short_description = ""  # non usata: sul sito si scrive solo la descrizione lunga

    images = _images(root)
    attributes = _attributes(root)
    documents = _documents(root)

    warnings: list[str] = []
    if _flag_warranty(description) or _flag_warranty(short_description):
        warnings.append(
            "La descrizione del produttore contiene riferimenti alla garanzia: controllala "
            "prima di pubblicarla, la garanzia legale la definisce il venditore."
        )
    if not title:
        warnings.append("Titolo non trovato nella risposta Icecat: controlla il mapping.")
    if not images:
        warnings.append("Nessuna immagine disponibile: alcuni brand le riservano ai rivenditori autorizzati.")

    return {
        "gtin": gtin,
        "icecat_id": icecat_id,
        "title": title,
        "brand": brand,
        "mpn": mpn,
        "category": {"id": category_id, "name": category_name},
        "description": description,
        "short_description": short_description,
        "bullets": bullets,
        "images": images,
        "attributes": attributes,
        "documents": documents,
        # Icecat non fornisce i prezzi di vendita: li mette l'utente nel portale.
        "regular_price": "",
        "sale_price": "",
        "warnings": warnings,
    }


def diagnostics(payload: dict) -> dict:
    """Chiavi presenti nella risposta: utile per aggiustare il mapping su un EAN reale."""
    root = _root(payload or {})
    return {
        "root_keys": sorted(root.keys()) if isinstance(root, dict) else [],
        "general_info_keys": sorted(root.get("GeneralInfo", {}).keys())
        if isinstance(root.get("GeneralInfo"), dict)
        else [],
    }
