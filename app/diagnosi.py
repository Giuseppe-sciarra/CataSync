"""Diario di una ricerca e motivo del «non trovato», in parole povere.

Ogni ricerca tiene un diario: cosa si è chiesto ai motori e cosa hanno
risposto (compresi quelli che ci hanno bloccati), quali siti sono usciti e
cosa è successo aprendo ciascuno. Alla fine il diario sa dire PERCHÉ un codice
non si è trovato, distinguendo i casi che si risolvono in modi diversi:

- ``motori_bloccati``: Google & c. hanno risposto CAPTCHA o «troppe richieste».
  Il codice non è stato cercato davvero: si riprova più tardi, da soli.
- ``sconosciuto``: i motori hanno risposto, ma nessun sito riporta il codice.
  Probabilmente non è pubblicato online: si cerca col codice produttore.
- ``siti_bloccati``: il codice c'è, ma i siti che lo pubblicano rifiutano la
  lettura automatica (protezioni anti-bot, robots.txt, errori).
- ``non_confermato``: pagine aperte, ma nessuna dimostra che è QUESTO prodotto.
- ``solo_somiglianza``: una pagina sola che somiglia per nome, senza codice.

Il diario viaggia con la scheda (``_diagnosi``) o con l'eccezione
(``.diagnosi``) e il worker lo salva sulla riga: la tabella Prodotti lo mostra
alla voce «Perché?».
"""
import time
from datetime import datetime, timezone

ESITI_PAGINA = {
    "confermata": "confermata",
    "bloccata": "protezione anti-bot",
    "robots": "robots.txt",
    "http": "errore del sito",
    "rete": "non risponde",
    "senza_dati": "senza dati leggibili",
    "altro_prodotto": "altro prodotto",
    "non_conferma": "non conferma il codice",
    "incoerente": "non coerente con gli altri siti",
    "documento": "documento PDF",
    "elenco": "pagina di categoria o di ricerca",
}

CAUSE = {
    "motori_bloccati": "I motori di ricerca ci stanno bloccando: il codice non è stato cercato davvero. Riprovo da solo più tardi.",
    "motori_irraggiungibili": "Il motore di ricerca non risponde: controlla che SearXNG sia acceso.",
    "sconosciuto": "Nessun sito indicizzato riporta questo codice: probabilmente non è pubblicato online. Prova col codice produttore o compila a mano.",
    "siti_bloccati": "Il codice c'è, ma i siti che lo pubblicano rifiutano la lettura automatica.",
    "non_confermato": "Ho trovato pagine simili, ma nessuna dimostra che sia proprio questo prodotto.",
    "solo_somiglianza": "Una sola pagina somiglia per nome e nessuna riporta il codice: troppo poco per fidarsi.",
    "ricerca_spenta": "La ricerca automatica non è attiva.",
    "trovato": "Scheda trovata.",
}


class Diario:
    """Raccoglie gli eventi di una ricerca. Leggero: finisce in JSON sulla riga."""

    def __init__(self, gtin: str, lingua: str = "IT", motore: str = ""):
        self.dati: dict = {
            "versione": 1,
            "gtin": gtin,
            "lingua": lingua,
            "motore": motore,
            "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ricerche": [],
            "risultati": 0,
            "ancorati": 0,
            "candidati": [],
            "pagine": [],
            "note": [],
        }
        self._t0 = time.monotonic()

    # ------------------------------------------------------------ eventi
    def ricerca(self, voce: dict) -> None:
        self.dati["ricerche"].append(voce)

    def candidati(self, risultati: int, ancorati: int, ordinati: list[dict]) -> None:
        self.dati["risultati"] = risultati
        self.dati["ancorati"] = ancorati
        self.dati["candidati"] = [
            {"dominio": c.get("dominio", ""), "titolo": (c.get("titolo") or "")[:90],
             "punti": c.get("punti", 0), "codice": bool(c.get("ancorato")),
             "marketplace": bool(c.get("marketplace")), "lingua": c.get("lingua_url", "")}
            for c in ordinati[:16]
        ]

    def pagina(self, dominio: str, url: str, esito: str, dettaglio: str = "", **extra) -> None:
        voce = {"dominio": dominio, "url": (url or "")[:300], "esito": esito, "dettaglio": (dettaglio or "")[:160]}
        voce.update({k: v for k, v in extra.items() if v not in (None, "", False)})
        self.dati["pagine"].append(voce)

    def nota(self, testo: str) -> None:
        self.dati["note"].append(testo[:200])

    # ------------------------------------------------------------ esito
    def chiudi(self, causa: str, spiegazione: str = "") -> dict:
        self.dati["causa"] = causa
        self.dati["spiegazione"] = spiegazione or spiega(self.dati, causa)
        self.dati["secondi"] = round(time.monotonic() - self._t0, 1)
        return self.dati

    def classifica(self) -> str:
        return classifica(self.dati)


def _motori_ko(d: dict) -> list[str]:
    visti: dict[str, str] = {}
    for r in d.get("ricerche") or []:
        for voce in r.get("ko") or []:
            if isinstance(voce, (list, tuple)) and voce:
                visti[str(voce[0])] = str(voce[1]) if len(voce) > 1 else ""
    return [f"{m} ({w})" if w else m for m, w in visti.items()]


def classifica(d: dict) -> str:
    """La causa principale di un «non trovato», dal diario."""
    ricerche = d.get("ricerche") or []
    if ricerche and all(r.get("errore") for r in ricerche):
        if any(r.get("ko") for r in ricerche):
            return "motori_bloccati"
        return "motori_irraggiungibili"
    pagine = d.get("pagine") or []
    if not d.get("ancorati") and not pagine:
        return "motori_bloccati" if _motori_ko(d) and not any(r.get("ancorati") for r in ricerche) and \
            not any(r.get("ok") for r in ricerche) else "sconosciuto"
    if pagine:
        esiti = [p.get("esito") for p in pagine]
        if "confermata" in esiti:
            return "solo_somiglianza"
        rifiutate = {"bloccata", "robots", "http", "rete"}
        if all(e in rifiutate for e in esiti):
            return "siti_bloccati"
        return "non_confermato"
    return "sconosciuto"


def spiega(d: dict, causa: str) -> str:
    """La frase per il cliente, con i nomi veri (motori, siti) dove servono."""
    base = CAUSE.get(causa, "")
    if causa == "motori_bloccati":
        ko = _motori_ko(d)
        if ko:
            return base + " Motori bloccati: " + ", ".join(ko[:5]) + "."
    if causa == "siti_bloccati":
        dettagli = []
        for p in d.get("pagine") or []:
            if p.get("esito") in ("bloccata", "robots", "http", "rete"):
                dettagli.append(f"{p.get('dominio')}: {p.get('dettaglio') or ESITI_PAGINA.get(p.get('esito'), '')}")
        if dettagli:
            return base + " " + "; ".join(dettagli[:4]) + "."
    if causa == "sconosciuto" and d.get("risultati"):
        return base + f" (i motori hanno dato {d['risultati']} risultati, nessuno col codice)"
    return base


def riassunto_pagine(d: dict) -> dict:
    """Conteggi per esito: servono alla tabella riassuntiva del pannello."""
    conti: dict[str, int] = {}
    for p in d.get("pagine") or []:
        conti[p.get("esito", "?")] = conti.get(p.get("esito", "?"), 0) + 1
    return conti


# =========================================================== prove a lotti
# Prove lanciate da Impostazioni su un elenco di codici: girano in
# sottofondo nello stesso processo e si leggono a pezzi mentre vanno.
LOTTI: dict[str, dict] = {}
