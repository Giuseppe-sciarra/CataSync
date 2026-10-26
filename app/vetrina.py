"""Pagina pubblica: dati legali del footer, prezzi, destinatario delle richieste, privacy.

Tutto in un'unica impostazione JSON, modificabile dall'amministrazione."""
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from .store import get_setting, set_setting

CHIAVE = "vetrina"
# Data del testo dell'informativa di serie: cambia solo quando cambia il testo.
PRIVACY_DI_SERIE = date(2026, 10, 16)

CAMPI_LEGALI = ("ragione_sociale", "sede_legale", "sede_operativa", "partita_iva", "codice_fiscale",
                "rea", "capitale_sociale", "pec", "email", "telefono")

DI_SERIE = {
    "attiva": True,
    **{campo: "" for campo in CAMPI_LEGALI},
    "prezzo_mese": "30",
    "prezzo_anno_mese": "25",
    "nota_prezzi": "IVA esclusa",
    "destinatario": "",
    "privacy_testo": "",
    "aggiornato": "",
}


def leggi(db: Session) -> dict:
    try:
        salvati = json.loads(get_setting(db, CHIAVE) or "{}")
    except ValueError:
        salvati = {}
    dati = dict(DI_SERIE)
    dati.update({k: v for k, v in salvati.items() if k in DI_SERIE})
    return dati


def salva(db: Session, nuovi: dict) -> dict:
    dati = leggi(db)
    dati.update({k: v for k, v in nuovi.items() if k in DI_SERIE and k != "aggiornato"})
    dati["aggiornato"] = datetime.now(timezone.utc).date().isoformat()
    set_setting(db, CHIAVE, json.dumps(dati, ensure_ascii=False))
    return dati


def prezzo(valore) -> Decimal | None:
    """«30», «24,90», «24.90 €» → Decimal; vuoto o non numerico → None."""
    testo = re.sub(r"[^\d,.]", "", str(valore or "")).replace(",", ".")
    if testo.count(".") > 1:
        return None
    try:
        numero = Decimal(testo)
    except InvalidOperation:
        return None
    return numero.quantize(Decimal("0.01")) if numero >= 0 else None


def euro(numero: Decimal | None) -> str:
    """Alla italiana e senza decimali inutili: 30, 24,90, 1.200."""
    if numero is None:
        return ""
    intero = int(numero)
    gruppi = f"{intero:,}".replace(",", ".")
    centesimi = int((numero - intero) * 100)
    return gruppi if centesimi == 0 else f"{gruppi},{centesimi:02d}"


def prezzi(dati: dict) -> dict:
    """I numeri del riquadro prezzi, già scritti: mese, annuale al mese, totale e risparmio."""
    mese = prezzo(dati.get("prezzo_mese"))
    anno_mese = prezzo(dati.get("prezzo_anno_mese"))
    totale = anno_mese * 12 if anno_mese is not None else None
    risparmio = (mese - anno_mese) * 12 if mese is not None and anno_mese is not None and mese > anno_mese else None
    # «2 mesi gratis» solo quando il conto torna preciso: risparmio = N canoni mensili interi.
    mesi_gratis = int(risparmio / mese) if risparmio and mese and risparmio % mese == 0 else 0
    return {
        "mese": euro(mese),
        "anno_mese": euro(anno_mese),
        "anno_totale": euro(totale),
        "risparmio": euro(risparmio),
        "mesi_gratis": mesi_gratis,
        # Il confronto a barre dell'annuale: un anno col mensile contro un anno con l'annuale.
        "mese_totale": euro(mese * 12) if risparmio else "",
        "quota_anno": round(totale / (mese * 12) * 100) if risparmio else 100,
        "nota": (dati.get("nota_prezzi") or "").strip(),
        "mostra": mese is not None or anno_mese is not None,
    }


def ha_dati_legali(dati: dict) -> bool:
    return any((dati.get(campo) or "").strip() for campo in CAMPI_LEGALI)


def privacy_aggiornata(dati: dict) -> date:
    """La data in fondo all'informativa: quella del testo di serie o dell'ultimo salvataggio."""
    try:
        salvata = date.fromisoformat(dati.get("aggiornato") or "")
    except ValueError:
        salvata = None
    return max(d for d in (PRIVACY_DI_SERIE, salvata) if d)


# ------------------------------------------------------------ codice a barre
# EAN-13 disegnato a moduli: 95 moduli, ognuno pieno (1) o vuoto (0). La pagina pubblica
# lo disegna lato server (si vede anche senza JavaScript) e static/vetrina/vetrina.js lo
# ridisegna dal vivo con le stesse regole.
_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
_G = ("0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111")
_R = tuple("".join("1" if b == "0" else "0" for b in c) for c in _L)
_PARITA = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")
GUARDIE = frozenset((0, 1, 2, 45, 46, 47, 48, 49, 92, 93, 94))
EAN_DI_SERIE = "8034110000034"


def moduli_ean13(cifre: str) -> str:
    """Le 95 cifre binarie di un EAN-13 (le prime 13 cifre valide; le mancanti valgono 0)."""
    cifre = (re.sub(r"\D", "", cifre or "") + "0" * 13)[:13]
    prima, sinistra, destra = int(cifre[0]), cifre[1:7], cifre[7:13]
    parti = ["101"]
    for i, c in enumerate(sinistra):
        parti.append((_L if _PARITA[prima][i] == "L" else _G)[int(c)])
    parti.append("01010")
    parti.extend(_R[int(c)] for c in destra)
    parti.append("101")
    return "".join(parti)


def barre(cifre: str = EAN_DI_SERIE) -> list[dict]:
    """Un rettangolo per modulo: acceso o spento, lungo (guardia) o normale."""
    return [{"i": i, "acceso": b == "1", "guardia": i in GUARDIE} for i, b in enumerate(moduli_ean13(cifre))]
