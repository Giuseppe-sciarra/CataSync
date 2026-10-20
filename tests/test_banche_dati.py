"""Banche dati libere (UPCitemdb, Open*Facts): identità del prodotto quando Icecat non ha
l'EAN, codice produttore passato alla ricerca sul web, limiti giornalieri senza buchi in cache."""
import asyncio
import unittest

import _ambiente  # noqa: F401
import httpx

from app import banche_dati, providers, web_source
from app.db import Base, SessionLocal, engine
from app.models import Client, EanCache
from app.store import salva_ricerca

TROVATO = "0885909950805"
SOLO_FOOD = "3017620422003"
NESSUNO = "5712608909306"
LIMITE = "4006381333931"


def _trasporto(request: httpx.Request) -> httpx.Response:
    host, gtin = request.url.host, request.url.params.get("upc") or request.url.path.rsplit("/", 1)[-1][:13]
    if host == "api.upcitemdb.com":
        if gtin == LIMITE:
            return httpx.Response(429, json={"code": "EXCEED_LIMIT"})
        if gtin == TROVATO:
            return httpx.Response(200, json={"items": [{"title": "Apple iPhone 15 128GB Black", "brand": "Apple",
                                                       "model": "MTP03ZD/A", "description": "Phone.",
                                                       "images": ["https://i.ebayimg.com/a.jpg", "ftp://no"]}]})
        return httpx.Response(404, json={"items": []})
    if host == "world.openfoodfacts.org" and gtin == SOLO_FOOD:
        return httpx.Response(200, json={"product": {"product_name": "Nutella", "product_name_it": "Nutella crema",
                                                     "brands": "Nutella, Ferrero",
                                                     "image_url": "https://images.openfoodfacts.org/x.jpg"}})
    if gtin == LIMITE:
        raise httpx.ConnectError("giù")
    return httpx.Response(404, json={"status": 0})


class Banche(unittest.TestCase):
    def setUp(self):
        self._client = httpx.AsyncClient

        class Client_(self._client):
            def __init__(inner, *a, **k):
                k["transport"] = httpx.MockTransport(_trasporto)
                super().__init__(*a, **k)

        banche_dati.httpx.AsyncClient = Client_
        banche_dati.PAUSA = 0
        self._scheda, banche_dati.scheda = banche_dati.scheda, banche_dati.scheda_vera   # quella vera

    def tearDown(self):
        banche_dati.httpx.AsyncClient = self._client
        banche_dati.scheda = self._scheda

    def test_upcitemdb(self):
        s = asyncio.run(banche_dati.scheda(TROVATO, "it"))
        self.assertEqual((s["title"], s["brand"], s["mpn"], s["description"]), ("Apple iPhone 15 128GB Black", "Apple", "MTP03ZD/A", ""))
        self.assertEqual(s["images"], ["https://i.ebayimg.com/a.jpg"])      # solo http(s)
        self.assertEqual(asyncio.run(banche_dati.scheda(TROVATO, "en"))["description"], "Phone.")

    def test_open_facts_in_lingua(self):
        s = asyncio.run(banche_dati.scheda(SOLO_FOOD, "IT"))
        self.assertEqual((s["title"], s["brand"], s["source_ref"]), ("Nutella crema", "Nutella", "openfoodfacts"))

    def test_nessuno_e_limite(self):
        self.assertIsNone(asyncio.run(banche_dati.scheda(NESSUNO, "IT")))
        with self.assertRaises(banche_dati.NonDisponibile):
            asyncio.run(banche_dati.scheda(LIMITE, "IT"))


class Catena(unittest.TestCase):
    """Icecat spento, banche dati che riconoscono il prodotto: la ricerca sul web riceve il codice."""

    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.db.query(EanCache).delete()
        salva_ricerca(self.db, "searxng", None, "", "http://searxng:8080", lt_url="")
        self.cliente = self.db.query(Client).filter_by(name="Banche srl").first() or Client(name="Banche srl")
        self.db.add(self.cliente)
        self.db.commit()
        self.visti = []

        async def finta(gtin, lang):
            if gtin == TROVATO:
                return {"title": "Apple iPhone 15", "brand": "Apple", "mpn": "MTP03ZD/A", "description": "",
                        "images": ["https://i.ebayimg.com/a.jpg"], "source_ref": "upcitemdb", "gtin": gtin}
            if gtin == LIMITE:
                raise banche_dati.NonDisponibile("UPCitemdb: limite giornaliero raggiunto")
            return None

        async def web_finta(gtin, *_a, mpn="", **_k):
            self.visti.append((gtin, mpn))
            return {"title": "Apple iPhone 15 128GB", "description": "<p>Un telefono con tante parole dentro.</p>",
                    "images": ["https://apple.com/1.jpg", "https://apple.com/2.jpg"], "attributes": [],
                    "_diagnosi": {"causa": "trovato", "spiegazione": "ok", "pagine": [{"dominio": "apple.com", "esito": "confermata"}]}}

        self._b, banche_dati.scheda = banche_dati.scheda, finta
        self._w, web_source.cerca_automatica = web_source.cerca_automatica, web_finta

    def tearDown(self):
        banche_dati.scheda = self._b
        web_source.cerca_automatica = self._w
        self.db.query(EanCache).delete()
        self.db.commit()
        self.db.close()

    def test_identita_poi_web_col_codice(self):
        scheda, fonte = asyncio.run(providers.fetch(self.db, self.cliente, TROVATO, "IT"))
        self.assertEqual(fonte, "Banche dati libere + Ricerca sul web")
        self.assertEqual(self.visti, [(TROVATO, "MTP03ZD/A")])
        self.assertEqual((scheda["brand"], scheda["mpn"]), ("Apple", "MTP03ZD/A"))
        self.assertEqual(scheda["images"][0], "https://i.ebayimg.com/a.jpg")        # la foto certa resta prima
        self.assertEqual([p["dominio"] for p in scheda["_diagnosi"]["pagine"]], ["upcitemdb", "apple.com"])
        self.assertTrue(self.db.query(EanCache).filter(EanCache.lang == "_WEBD:IT", EanCache.found.is_(True)).one())

    def test_limite_non_lascia_buchi(self):
        asyncio.run(providers.fetch(self.db, self.cliente, LIMITE, "IT"))
        self.assertEqual(self.visti, [(LIMITE, "")])
        self.assertFalse(self.db.query(EanCache).filter(EanCache.lang == "_WEBD:IT").all())


if __name__ == "__main__":
    unittest.main()
