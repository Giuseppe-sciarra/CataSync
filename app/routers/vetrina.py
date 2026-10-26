"""Pagina pubblica del portale: presentazione, «Richiedi informazioni», informativa privacy.

La radice «/» è la pagina pubblica; il pannello sta su /app. Dati legali, prezzi,
destinatario delle richieste e testo della privacy li imposta l'amministratore
(/api/admin/vetrina); il server di posta sta nel .env (SMTP_*)."""
import html
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config, posta, vetrina
from ..db import get_db
from ..html_sicuro import pulisci_html_ricco
from ..models import User
from ..security import current_user, require_admin
from ..store import audit, portal_brand
from ..web import contesto_favicon, templates

log = logging.getLogger("catasync.vetrina")

router = APIRouter(include_in_schema=False)
admin_router = APIRouter(prefix="/api/admin/vetrina", tags=["admin"], dependencies=[Depends(require_admin)])

PIANI = {"mensile": "Mensile", "annuale": "Annuale"}
LIMITE_NOME = 80
LIMITE_MESSAGGIO = 5000

# Richieste per indirizzo IP: al massimo 5 in 10 minuti, e 60 in un'ora per tutto il portale.
# Basta a fermare chi martella il modulo senza disturbare chi scrive davvero.
_PER_IP = (5, 600)
_IN_TUTTO = (60, 3600)
_visti: dict[str, deque] = defaultdict(deque)
_tutti: deque = deque()


def _troppe(ip: str) -> bool:
    adesso = time.monotonic()
    for coda, (quante, finestra) in ((_visti[ip], _PER_IP), (_tutti, _IN_TUTTO)):
        while coda and adesso - coda[0] > finestra:
            coda.popleft()
        if len(coda) >= quante:
            return True
    _visti[ip].append(adesso)
    _tutti.append(adesso)
    return False


def _destinatario(dati: dict) -> str:
    return (dati.get("destinatario") or "").strip() or config.CONTACT_TO or posta.mittente()


def _testo_privacy(testo: str) -> str:
    """Testo personalizzato dell'informativa: HTML ripulito, oppure testo semplice a paragrafi."""
    testo = (testo or "").strip()
    if not testo:
        return ""
    if "<" in testo:
        return pulisci_html_ricco(testo)
    paragrafi = [p.strip() for p in re.split(r"\n\s*\n", testo) if p.strip()]
    return "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragrafi)


def _legali(dati: dict) -> list[tuple[str, str]]:
    """Le righe del footer, solo quelle compilate. Etichetta e valore restano separati:
    l'etichetta si traduce, il valore no."""
    righe = []
    piva, cf = (dati.get("partita_iva") or "").strip(), (dati.get("codice_fiscale") or "").strip()
    for etichetta, valore in (
        ("Sede legale", dati.get("sede_legale")),
        ("Sede operativa", dati.get("sede_operativa")),
        ("P.IVA e C.F." if piva and piva == cf else "P.IVA", piva),
        ("C.F.", "" if cf == piva else cf),
        ("REA", dati.get("rea")),
        ("Capitale sociale", dati.get("capitale_sociale")),
    ):
        if (valore or "").strip():
            righe.append((etichetta, valore.strip()))
    return righe


def _contesto(request: Request, db: Session, user: User | None, **altro) -> dict:
    marca = portal_brand(db)
    dati = vetrina.leggi(db)
    return {
        "app_name": marca["name"],
        "portal_logo": marca["logo"],
        **contesto_favicon(marca),
        "v": dati,
        "prezzi": vetrina.prezzi(dati),
        "legali": _legali(dati),
        "utente": user,
        "anno": datetime.now().year,
        # Il codice a barre della pagina: quello del circolatore dei dati dimostrativi.
        "ean": vetrina.EAN_DI_SERIE,
        "barre_eroe": vetrina.barre(),
        # Le schermate stanno in italiano e in inglese: la pagina parte dall'italiano e
        # vetrina.js passa all'inglese se la lingua dell'interfaccia è un'altra.
        "lingua_img": "it",
        **altro,
    }


@router.get("/", response_class=HTMLResponse)
def pagina_pubblica(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if not vetrina.leggi(db)["attiva"]:
        return RedirectResponse("/app" if user else "/login", status_code=302)
    return templates.TemplateResponse(request, "vetrina/pagina.html", _contesto(request, db, user, classe_body="v-pagina"))


def _modulo(request: Request, db: Session, user: User | None, *, valori: dict, errori: list[str],
            inviata: bool = False, codice: int = 200):
    return templates.TemplateResponse(
        request, "vetrina/informazioni.html",
        _contesto(request, db, user, valori=valori, errori=errori, inviata=inviata, piani=PIANI,
                  classe_body="v-pagina-modulo"),
        status_code=codice,
    )


@router.get("/richiedi-informazioni", response_class=HTMLResponse)
def richiedi_informazioni(
    request: Request, email: str = "", piano: str = "",
    db: Session = Depends(get_db), user: User | None = Depends(current_user),
):
    if not vetrina.leggi(db)["attiva"]:
        return RedirectResponse("/app" if user else "/login", status_code=302)
    valori = {"nome": "", "cognome": "", "email": email[:190], "messaggio": "",
              "piano": piano if piano in PIANI else ""}
    return _modulo(request, db, user, valori=valori, errori=[])


@router.post("/richiedi-informazioni", response_class=HTMLResponse)
async def invia_richiesta(
    request: Request,
    nome: str = Form(""), cognome: str = Form(""), email: str = Form(""), messaggio: str = Form(""),
    piano: str = Form(""), sito_web: str = Form(""),
    db: Session = Depends(get_db), user: User | None = Depends(current_user),
):
    dati = vetrina.leggi(db)
    if not dati["attiva"]:
        return RedirectResponse("/login", status_code=303)
    valori = {
        "nome": " ".join(nome.split())[:LIMITE_NOME],
        "cognome": " ".join(cognome.split())[:LIMITE_NOME],
        "email": email.strip()[:190],
        "messaggio": messaggio.strip(),
        "piano": piano if piano in PIANI else "",
    }
    # Il campo «sito web» è nascosto alle persone: se è pieno l'ha compilato un programma.
    # Si risponde come se fosse andato tutto bene, così non impara niente.
    if sito_web.strip():
        log.info("Richiesta scartata: campo trappola compilato")
        return _modulo(request, db, user, valori=valori, errori=[], inviata=True)

    errori = []
    if not valori["nome"]:
        errori.append("Scrivi il tuo nome.")
    if not valori["cognome"]:
        errori.append("Scrivi il tuo cognome.")
    try:
        valori["email"] = validate_email(valori["email"], check_deliverability=False).normalized
    except EmailNotValidError:
        errori.append("Serve un indirizzo email valido.")
    if not valori["messaggio"]:
        errori.append("Scrivi il messaggio.")
    elif len(valori["messaggio"]) > LIMITE_MESSAGGIO:
        errori.append("Il messaggio è troppo lungo: al massimo 5000 caratteri.")
    if errori:
        return _modulo(request, db, user, valori=valori, errori=errori, codice=400)

    ip = request.client.host if request.client else "?"
    if _troppe(ip):
        return _modulo(request, db, user, valori=valori, codice=429,
                       errori=["Hai già mandato diverse richieste: riprova fra qualche minuto."])

    marca = portal_brand(db)
    righe = [
        f"Nuova richiesta di informazioni dalla pagina pubblica di {marca['name']}.",
        "",
        f"Nome: {valori['nome']}",
        f"Cognome: {valori['cognome']}",
        f"Email: {valori['email']}",
    ]
    if valori["piano"]:
        righe.append(f"Piano che interessa: {PIANI[valori['piano']].lower()}")
    righe += ["", "Messaggio:", valori["messaggio"], "",
              "Per rispondere basta rispondere a questa email: va direttamente a chi ha scritto."]
    try:
        await posta.invia(
            _destinatario(dati),
            f"Richiesta di informazioni: {valori['nome']} {valori['cognome']}",
            "\n".join(righe),
            rispondi_a=valori["email"],
            nome_mittente=marca["name"],
        )
    except posta.PostaErrore as exc:
        log.error("Richiesta di informazioni non spedita: %s", exc)
        return _modulo(request, db, user, valori=valori, codice=503,
                       errori=["Non siamo riusciti a spedire la richiesta. Riprova fra poco."])
    return _modulo(request, db, user, valori=valori, errori=[], inviata=True)


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    dati = vetrina.leggi(db)
    return templates.TemplateResponse(
        request, "vetrina/privacy.html",
        _contesto(request, db, user, testo_personalizzato=_testo_privacy(dati.get("privacy_testo")),
                  aggiornata=vetrina.privacy_aggiornata(dati), classe_body="v-pagina-privacy"),
    )


# ------------------------------------------------------------- amministrazione
class VetrinaIn(BaseModel):
    attiva: bool = True
    ragione_sociale: str = Field(default="", max_length=190)
    sede_legale: str = Field(default="", max_length=255)
    sede_operativa: str = Field(default="", max_length=255)
    partita_iva: str = Field(default="", max_length=40)
    codice_fiscale: str = Field(default="", max_length=40)
    rea: str = Field(default="", max_length=60)
    capitale_sociale: str = Field(default="", max_length=80)
    pec: str = Field(default="", max_length=190)
    email: str = Field(default="", max_length=190)
    telefono: str = Field(default="", max_length=60)
    prezzo_mese: str = Field(default="", max_length=20)
    prezzo_anno_mese: str = Field(default="", max_length=20)
    nota_prezzi: str = Field(default="", max_length=120)
    destinatario: str = Field(default="", max_length=190)
    privacy_testo: str = Field(default="", max_length=60_000)


def _per_admin(dati: dict) -> dict:
    return {**dati, "posta": posta.stato(), "destinatario_effettivo": _destinatario(dati),
            "privacy_aggiornata": vetrina.privacy_aggiornata(dati).isoformat()}


@admin_router.get("")
def leggi_vetrina(db: Session = Depends(get_db)):
    return _per_admin(vetrina.leggi(db))


@admin_router.put("")
def salva_vetrina(data: VetrinaIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    nuovi = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.model_dump().items()}
    for campo, nome in (("pec", "La PEC"), ("email", "L'email"), ("destinatario", "L'email che riceve le richieste")):
        if nuovi[campo]:
            try:
                nuovi[campo] = validate_email(nuovi[campo], check_deliverability=False).normalized
            except EmailNotValidError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{nome} non è un indirizzo valido.")
    for campo in ("prezzo_mese", "prezzo_anno_mese"):
        if nuovi[campo] and vetrina.prezzo(nuovi[campo]) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Prezzo non valido: scrivi un numero, per esempio 30 o 24,90.")
        nuovi[campo] = format(vetrina.prezzo(nuovi[campo]).normalize(), "f") if nuovi[campo] else ""
    nuovi["privacy_testo"] = pulisci_html_ricco(nuovi["privacy_testo"]) if "<" in nuovi["privacy_testo"] else nuovi["privacy_testo"]
    dati = vetrina.salva(db, nuovi)
    audit(db, admin.email, "vetrina_update", "attiva" if dati["attiva"] else "spenta")
    return _per_admin(dati)


class ProvaIn(BaseModel):
    a: str = Field(default="", max_length=190)


@admin_router.post("/prova")
async def prova_posta(data: ProvaIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Un'email di prova: per sapere subito se il .env è giusto, non alla prima richiesta persa."""
    dati = vetrina.leggi(db)
    destinatario = data.a.strip() or _destinatario(dati)
    try:
        destinatario = validate_email(destinatario, check_deliverability=False).normalized
    except EmailNotValidError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manca l'indirizzo che riceve le richieste.")
    marca = portal_brand(db)
    try:
        await posta.invia(destinatario, f"Prova di invio da {marca['name']}",
                          "Se leggi questa email, la posta del portale funziona: "
                          "le richieste di informazioni della pagina pubblica arriveranno qui.",
                          nome_mittente=marca["name"])
    except posta.PostaErrore as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit(db, admin.email, "vetrina_prova_posta", destinatario)
    return {"ok": True, "a": destinatario}
