"""Pause dei motori di ricerca: chi risponde CAPTCHA o «troppe richieste» resta fuori per un
po' invece di essere richiamato a ogni domanda (che è ciò che tiene vivo il blocco).
SearXNG è finto (httpx.MockTransport): niente rete."""
import asyncio
import json
import time
import unittest
from datetime import timezone

import _ambiente  # noqa: F401
import httpx

from app import config, providers, web_search, worker

EAN = "8801643654689"


class SearxngFinto:
    """Risponde come SearXNG: Google CSE trova la pagina, i motori in `bloccano` danno CAPTCHA."""

    def __init__(self, bloccano=("brave", "duckduckgo", "startpage")):
        self.bloccano = set(bloccano)
        self.domande: list[list[str]] = []
        self.ddg_diretto = 0
        self.brave_api: list[tuple] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.search.brave.com":
            self.brave_api.append((request.url.params.get("q"), request.headers.get("X-Subscription-Token")))
            return httpx.Response(200, json={"web": {"results": [
                {"url": "https://www.sonepar.it/catalog/it-it/products/samsung-smgae040rxedeg-eu-esterne",
                 "title": "SAMSUNG - SMGAE040RXEDEG/EU ESTERNE EHS SPLIT R32", "description": f"EAN {EAN}"}]}})
        if request.url.host == "html.duckduckgo.com":
            self.ddg_diretto += 1
            return httpx.Response(202, text="anomaly")
        motori = [m for m in request.url.params.get("engines", "").split(",") if m]
        self.domande.append(motori)
        risultati, ko = [], []
        for m in motori:
            if m in self.bloccano:
                ko.append([m, "Suspended: CAPTCHA" if m != "brave" else "Suspended: too many requests"])
            elif m == "google cse":
                risultati.append({"url": "https://rexel.it/prodotto/esterne-ehs-split-r32-monofase-4--1365849",
                                  "title": f"ESTERNE EHS SPLIT R32 (MONOFASE) 4 Samsung AE040RXEDEG/EU {EAN}",
                                  "content": f"Cod. EAN {EAN}", "engines": ["google cse"]})
        return httpx.Response(200, json={"results": risultati, "unresponsive_engines": ko})


class Pause(unittest.TestCase):
    def setUp(self):
        web_search.PAUSE.clear()
        web_search._ULTIMO_USO.clear()
        web_search.SALUTE.clear()
        self._pausa, config.RICERCA_PAUSA = config.RICERCA_PAUSA, 0
        self.finto = SearxngFinto()
        originale = httpx.AsyncClient
        self._client = originale

        class Client(originale):
            def __init__(inner, *a, **k):
                k["transport"] = httpx.MockTransport(self.finto)
                super().__init__(*a, **k)

        web_search.httpx.AsyncClient = Client

    def tearDown(self):
        web_search.httpx.AsyncClient = self._client
        config.RICERCA_PAUSA = self._pausa
        web_search.PAUSE.clear()
        web_search._ULTIMO_USO.clear()

    def cerca(self):
        return asyncio.run(web_search.candidati(EAN, "searxng", "", {"url": "http://searxng:8080"}, lingua="IT"))

    def test_chi_blocca_non_si_richiama(self):
        trovati = self.cerca()
        self.assertIn("rexel.it", trovati[0]["url"])
        self.assertIn("brave", self.finto.domande[0])
        for m in ("brave", "duckduckgo", "startpage"):
            self.assertTrue(web_search.fine_pausa(m) > time.time() + 10 * 60, m)    # almeno ~15 minuti
        self.assertFalse(web_search.fine_pausa("google cse"))
        self.assertEqual(self.finto.ddg_diretto, 0)       # il codice si è trovato: niente ripiego

        web_search._ULTIMO_USO.clear()
        self.finto.domande.clear()
        self.cerca()
        for motori in self.finto.domande:
            self.assertFalse({"brave", "duckduckgo", "startpage"} & set(motori), motori)
            self.assertIn("google cse", motori)
        stato = {m["motore"]: m for m in web_search.stato_motori()}
        self.assertEqual(stato["brave"]["stato"], "pausa")
        self.assertEqual(stato["google cse"]["stato"], "ok")

    def test_la_pausa_raddoppia_e_si_azzera(self):
        web_search.metti_in_pausa("brave", "CAPTCHA", adesso=1000)
        primo = web_search.PAUSE["brave"]["fino"] - 1000
        web_search.metti_in_pausa("brave", "CAPTCHA", adesso=1001)          # già in pausa: non cambia
        self.assertEqual(web_search.PAUSE["brave"]["livello"], 1)
        fine = web_search.PAUSE["brave"]["fino"]
        web_search.metti_in_pausa("brave", "CAPTCHA", adesso=fine + 1)      # blocca di nuovo alla ripresa
        secondo = web_search.PAUSE["brave"]["fino"] - (fine + 1)
        self.assertGreater(secondo, primo * 1.5)
        self.assertLessEqual(web_search.PAUSA_PRIMA * 0.9, primo)
        for _ in range(12):                                                  # mai oltre le 6 ore
            adesso = web_search.PAUSE["brave"]["fino"] + 1
            web_search.metti_in_pausa("brave", "CAPTCHA", adesso=adesso)
            self.assertLessEqual(web_search.PAUSE["brave"]["fino"] - adesso, web_search.PAUSA_MASSIMA * 1.15)
        web_search._registra(web_search.Risposta(ok=["brave"]), "searxng")   # risponde: si riparte da zero
        self.assertNotIn("brave", web_search.PAUSE)

    def test_tutti_in_pausa_si_rimanda_senza_domande(self):
        for m in [x.strip() for x in web_search.MOTORI_SEARXNG_PREDEFINITI.split(",")]:
            web_search.metti_in_pausa(m, "CAPTCHA")
        # Motori tutti in pausa: restano i cataloghi diretti, col blocco scritto sopra (se
        # nemmeno loro trovano niente, cerca_automatica rimanda a fine pausa).
        trovati = self.cerca()
        self.assertEqual(self.finto.domande, [])
        self.assertEqual(self.finto.ddg_diretto, 0)
        self.assertTrue(trovati and all(c.get("da_catalogo") for c in trovati))
        riprova = trovati[0]["motori_bloccati"]["riprova_alle"]
        self.assertTrue(riprova and riprova > time.time())

    def test_motori_permalosi_a_riposo(self):
        web_search._usati(["duckduckgo"])
        usare, in_pausa, riposo = web_search.scegli_motori(["google cse", "duckduckgo"])
        self.assertEqual(usare, ["google cse"])
        self.assertEqual(in_pausa, [])
        self.assertGreater(riposo, 0)


class Riserva(Pause):
    """Chiave Brave Search API di riserva: quando SearXNG è bloccato la domanda va all'API."""

    def setUp(self):
        super().setUp()
        self.finto = SearxngFinto(bloccano=[m.strip() for m in web_search.MOTORI_SEARXNG_PREDEFINITI.split(",")])
        web_search.GIRO.update(codici=0, riposo_fino=0.0)

    def cerca(self, chiave="brave-xyz"):
        extra = {"url": "http://searxng:8080", "riserva_chiave": chiave}
        return asyncio.run(web_search.candidati(EAN, "searxng", "", extra, lingua="IT"))

    def test_searxng_bloccato_risponde_brave(self):
        trovati = self.cerca()
        self.assertIn("sonepar.it", trovati[0]["url"])
        self.assertEqual(self.finto.brave_api, [(EAN, "brave-xyz")])
        # Il «brave» di SearXNG resta in pausa: ha risposto l'API, non lui.
        self.assertTrue(web_search.fine_pausa("brave"))
        self.assertFalse(web_search.fine_pausa("brave api"))

    def test_senza_chiave_si_rimanda(self):
        trovati = self.cerca(chiave="")
        self.assertTrue(trovati and all(c.get("da_catalogo") and c.get("motori_bloccati") for c in trovati))
        self.assertEqual(self.finto.brave_api, [])

    def test_con_l_api_niente_giri(self):
        extra = {"giro_codici": 1, "giro_pausa": 5}
        asyncio.run(web_search.candidati(EAN, "brave", "chiave", extra, lingua="IT"))
        self.assertFalse(web_search.fine_riposo_giro())

    # Test della classe madre che qui non c'entrano.
    test_chi_blocca_non_si_richiama = test_la_pausa_raddoppia_e_si_azzera = None
    test_tutti_in_pausa_si_rimanda_senza_domande = test_motori_permalosi_a_riposo = None


class Rinvio(unittest.TestCase):
    """Codice fermato da una pausa: riparte a fine pausa e non consuma un rinvio."""

    def test_riparte_a_fine_pausa(self):
        from app.db import Base, SessionLocal, engine
        from app.models import Client, Job, JobItem, Site

        Base.metadata.create_all(bind=engine)     # da solo, senza l'avvio dell'app
        db = SessionLocal()
        cliente = Client(name="Pause srl")
        sito = Site(client=cliente, name="Negozio", base_url="https://negozio.example")
        job = Job(site=sito, label="prova")
        riga = JobItem(job=job, gtin=EAN, rinvii=1)
        db.add_all([cliente, sito, job, riga])
        db.commit()
        alle = time.time() + 1800

        async def bloccata(*_a, **_k):
            raise providers.RicercaRimandata("motori in pausa", {"causa": "motori_bloccati"}, alle)

        originale, providers.fetch = providers.fetch, bloccata
        try:
            asyncio.run(worker._scarica(db, riga, job, sito))
        finally:
            providers.fetch = originale
        db.refresh(riga)
        self.assertEqual(riga.status, "pending")
        self.assertEqual(riga.rinvii, 1)
        ripresa = riga.retry_after if riga.retry_after.tzinfo else riga.retry_after.replace(tzinfo=timezone.utc)
        self.assertGreaterEqual(ripresa.timestamp(), alle)
        self.assertLess(ripresa.timestamp(), alle + 120)
        self.assertIn("riparte da solo alle", riga.error)
        self.assertEqual(json.loads(riga.diagnosi)["causa"], "motori_bloccati")
        db.close()


if __name__ == "__main__":
    unittest.main()
