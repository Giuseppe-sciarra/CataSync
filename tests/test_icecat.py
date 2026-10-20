"""Icecat come prima fonte, con un'utenza sola per tutto il portale: completa → niente
ricerca sul web; parziale → la completa il web; assente → cerca il web. Icecat e la
ricerca sul web sono finti (httpx.MockTransport e una cerca_automatica di prova)."""
import asyncio
import json
import unittest

import _ambiente  # noqa: F401
import httpx
from fastapi.testclient import TestClient

from app import icecat, providers, web_source
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Client
from app.store import salva_icecat, salva_ricerca

COMPLETO = "8801643654689"      # Samsung: tutto in italiano
SENZA_TESTO = "4006381333931"   # in italiano niente descrizione, in inglese sì
SENZA_FOTO = "8059893356383"    # nome e descrizione, nessuna foto
ASSENTE = "5712608909306"       # non c'è
SOLO_FULL = "4006381333924"     # c'è, ma solo con Full Icecat
SENZA_EAN = "8711253001233"     # Icecat ha il prodotto, ma non con questo EAN: lo trova per codice


def _dati(titolo, descrizione="", lingua_desc="IT", foto=True, specifiche=True):
    generale = {"IcecatId": 98765, "Title": titolo, "Brand": "Samsung", "BrandPartCode": "AE040RXEDEG/EU",
                "Category": {"CategoryID": 1543, "Name": {"Value": "Pompe di calore"}}}
    if descrizione:
        generale["Description"] = {"LongDesc": descrizione, "Language": lingua_desc}
    dati = {"GeneralInfo": generale}
    if foto:
        dati["Image"] = {"HighPic": "https://images.icecat.biz/img/norm/high/98765-1.jpg"}
        dati["Gallery"] = [{"Pic": "https://images.icecat.biz/img/gallery/98765-2.jpg"},
                           {"Pic": "https://images.icecat.biz/img/gallery/98765-3.jpg"}]
    if specifiche:
        dati["FeaturesGroups"] = [{"FeatureGroup": {"Name": {"Value": "Prestazioni"}}, "Features": [
            {"Feature": {"Name": {"Value": "Potenza termica"}}, "PresentationValue": "4,4 kW"},
            {"Feature": {"Name": {"Value": "Refrigerante"}}, "PresentationValue": "R32"}]}]
    return {"msg": "OK", "data": dati}


DESC_IT = ("<p>Unità esterna della pompa di calore aria-acqua Samsung EHS Split, refrigerante R32, alimentazione "
           "monofase e 4,4 kW di potenza termica.</p>")
DESC_EN = "<p>Outdoor unit of the Samsung EHS Split air-to-water heat pump, R32 refrigerant, single phase.</p>"


class IcecatFinto:
    def __init__(self):
        self.chiamate: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        gtin, lingua = request.url.params.get("GTIN"), request.url.params.get("lang")
        self.chiamate.append((gtin, lingua, dict(request.headers), dict(request.url.params)))
        marca, codice = request.url.params.get("Brand"), request.url.params.get("ProductCode")
        if marca or codice:
            if (marca, codice) == ("Samsung", "AE040RXEDEG/EU"):
                return httpx.Response(200, json=_dati("Samsung AE040RXEDEG/EU pompa di calore", DESC_IT))
            return httpx.Response(400, json={"StatusCode": 400, "Message": "The product can not be found"})
        if gtin == SENZA_EAN:
            return httpx.Response(400, json={"StatusCode": 400, "Error": "Bad Request",
                                             "Message": "The GTIN can not be found"})
        if gtin == COMPLETO:
            return httpx.Response(200, json=_dati("Samsung AE040RXEDEG/EU pompa di calore", DESC_IT))
        if gtin == SENZA_TESTO:
            if lingua == "EN":
                return httpx.Response(200, json=_dati("Samsung AE040RXEDEG/EU heat pump", DESC_EN, "EN"))
            return httpx.Response(200, json=_dati("Samsung AE040RXEDEG/EU pompa di calore"))
        if gtin == SENZA_FOTO:
            return httpx.Response(200, json=_dati("Samsung AE040RXEDEG/EU pompa di calore", DESC_IT, foto=False))
        if gtin == SOLO_FULL:
            return httpx.Response(403, json={"StatusCode": 403, "Code": 9, "Error": "Forbidden", "Message":
                                             "Display of content for users with a Full Icecat subscription level"})
        return httpx.Response(404, json={"StatusCode": 404, "Code": 8, "Error": "Not Found",
                                         "Message": "The requested product is not present in the Icecat database"})


class Base_(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        from app.models import EanCache
        self.db.query(EanCache).delete()
        self.db.commit()
        salva_icecat(self.db, True, "tastiere-digitali", "api-123", "content-456")
        salva_ricerca(self.db, "searxng", None, "", "http://searxng:8080", lt_url="")
        self.cliente = self.db.query(Client).filter_by(name="Icecat srl").first() or Client(name="Icecat srl")
        self.db.add(self.cliente)
        self.db.commit()

        self.finto = IcecatFinto()
        originale = httpx.AsyncClient
        self._client = originale
        finto = self.finto

        class Client_(originale):
            def __init__(inner, *a, **k):
                k["transport"] = httpx.MockTransport(finto)
                super().__init__(*a, **k)

        icecat.httpx.AsyncClient = Client_
        self.web = []

        async def web_finta(gtin, *_a, **_k):
            self.web.append(gtin)
            if gtin == SENZA_EAN:
                return {"title": "Esterne EHS split R32 (monofase) 4.", "brand": "Samsung Electronics",
                        "mpn": "AE040RXEDEG/EU", "description": "", "origine_dominio": "rexel.it",
                        "images": ["https://files.rexel.it/products/1.jpg"], "attributes": [],
                        "_diagnosi": {"causa": "trovato", "spiegazione": "Scheda trovata su rexel.it.",
                                      "pagine": [{"dominio": "rexel.it", "esito": "confermata"}]}}
            if gtin == SENZA_FOTO:
                return {"title": "Samsung EHS split", "description": "dal web", "origine_dominio": "rexel.it",
                        "images": ["https://files.rexel.it/products/1.jpg", "https://files.rexel.it/products/2.jpg"],
                        "attributes": [], "_diagnosi": {"causa": "trovato", "spiegazione": "Scheda trovata su rexel.it.",
                                                        "pagine": [{"dominio": "rexel.it", "esito": "confermata"}]}}
            err = web_source.NonTrovato("nessun sito")
            err.diagnosi = {"causa": "sconosciuto", "spiegazione": "Nessun sito riporta il codice.", "pagine": []}
            raise err

        self._cerca, web_source.cerca_automatica = web_source.cerca_automatica, web_finta

    def tearDown(self):
        icecat.httpx.AsyncClient = self._client
        web_source.cerca_automatica = self._cerca
        salva_icecat(self.db, False, "")
        from app.models import EanCache
        self.db.query(EanCache).delete()
        self.db.commit()
        self.db.close()

    def cerca(self, gtin):
        return asyncio.run(providers.fetch(self.db, self.cliente, gtin, "IT"))


class PrimaIcecat(Base_):
    def test_completa_niente_web(self):
        scheda, fonte = self.cerca(COMPLETO)
        self.assertEqual(fonte, "Icecat")
        self.assertEqual(self.web, [])
        self.assertEqual(scheda["title"], "Samsung AE040RXEDEG/EU pompa di calore")
        self.assertEqual(scheda["brand"], "Samsung")
        self.assertEqual(scheda["mpn"], "AE040RXEDEG/EU")
        self.assertEqual(len(scheda["images"]), 3)
        self.assertEqual(scheda["source_ref"], "98765")
        self.assertIn({"group": "Prestazioni", "name": "Potenza termica", "value": "4,4 kW", "key": False},
                      scheda["attributes"])
        self.assertEqual(scheda["_diagnosi"]["pagine"][0]["dominio"], "icecat.biz")
        # Un'utenza sola, coi token nelle intestazioni.
        _, lingua, intestazioni, parametri = self.finto.chiamate[0]
        self.assertEqual((lingua, parametri["shopname"]), ("IT", "tastiere-digitali"))
        self.assertEqual((intestazioni["api-token"], intestazioni["content-token"]), ("api-123", "content-456"))
        # Seconda volta: dalla cache, Icecat non si richiama.
        self.cerca(COMPLETO)
        self.assertEqual(len(self.finto.chiamate), 1)

    def test_testo_in_inglese(self):
        scheda, fonte = self.cerca(SENZA_TESTO)
        self.assertEqual(fonte, "Icecat")
        self.assertIn("Outdoor unit", scheda["description"])       # LibreTranslate non impostato: resta inglese
        self.assertEqual(scheda["title"], "Samsung AE040RXEDEG/EU pompa di calore")
        self.assertTrue(any("inglese" in w for w in scheda["warnings"]))
        self.assertEqual([c[1] for c in self.finto.chiamate], ["IT", "EN"])

    def test_parziale_la_completa_il_web(self):
        scheda, fonte = self.cerca(SENZA_FOTO)
        self.assertEqual(fonte, "Icecat + Ricerca sul web")
        self.assertEqual(self.web, [SENZA_FOTO])
        self.assertIn("pompa di calore aria-acqua", scheda["description"])     # il testo resta quello di Icecat
        self.assertEqual(len(scheda["images"]), 2)                              # le foto le porta il web
        domini = [p["dominio"] for p in scheda["_diagnosi"]["pagine"]]
        self.assertEqual(domini, ["icecat.biz", "banche dati libere", "rexel.it"])

    def test_assente_cerca_il_web(self):
        with self.assertRaises(providers.NoSourceFound) as ctx:
            self.cerca(ASSENTE)
        self.assertEqual(self.web, [ASSENTE])
        prima = ctx.exception.diagnosi["pagine"][0]
        self.assertEqual((prima["dominio"], prima["esito"]), ("icecat.biz", "icecat_assente"))
        with self.assertRaises(providers.NoSourceFound):
            self.cerca(ASSENTE)
        self.assertEqual(len(self.finto.chiamate), 1)          # il «non c'è» resta in memoria qualche giorno

    def test_ean_assente_ma_codice_si(self):
        scheda, fonte = self.cerca(SENZA_EAN)
        self.assertEqual(fonte, "Icecat + Ricerca sul web")
        self.assertEqual(scheda["gtin"], SENZA_EAN)
        self.assertEqual(scheda["title"], "Samsung AE040RXEDEG/EU pompa di calore")      # la base è Icecat
        self.assertIn("pompa di calore aria-acqua", scheda["description"])
        self.assertIn("https://files.rexel.it/products/1.jpg", scheda["images"])          # il web aggiunge
        prima = scheda["_diagnosi"]["pagine"][0]
        self.assertEqual((prima["dominio"], prima["esito"]), ("icecat.biz", "confermata"))
        self.assertIn("codice produttore AE040RXEDEG/EU", prima["dettaglio"])
        # «Samsung Electronics» non c'è su Icecat, «Samsung» sì.
        marche = [c[3].get("Brand") for c in self.finto.chiamate if c[3].get("Brand")]
        self.assertEqual(marche[:2], ["Samsung Electronics", "Samsung"])

    def test_solo_full(self):
        with self.assertRaises(providers.NoSourceFound) as ctx:
            self.cerca(SOLO_FULL)
        self.assertIn("solo con Full Icecat", ctx.exception.diagnosi["pagine"][0]["dettaglio"])

    def test_spento_o_codice_produttore(self):
        salva_icecat(self.db, False, "tastiere-digitali")
        with self.assertRaises(providers.NoSourceFound):
            self.cerca(ASSENTE)
        salva_icecat(self.db, True, "tastiere-digitali")
        with self.assertRaises(providers.NoSourceFound):
            self.cerca("AE040RXEDEG/EU")                       # Icecat cerca per EAN
        self.assertEqual(self.finto.chiamate, [])


class Impostazioni(Base_):
    def test_utenza_e_prova(self):
        with TestClient(app) as c:
            c.headers.update({"Origin": "http://testserver"})
            r = c.post("/api/auth/login", json={"email": "admin@example.com", "password": "provaprova123"})
            self.assertEqual(r.status_code, 200, r.text)
            r = c.put("/api/admin/icecat", json={"attivo": True, "utente": "td-icecat", "api_token": "segretissimo"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("segretissimo", r.text)
            self.assertTrue(r.json()["pronto"])
            self.assertTrue(r.json()["api_token_masked"].endswith("imo"))
            self.assertEqual(c.put("/api/admin/icecat", json={"attivo": True, "utente": ""}).status_code, 400)
            r = c.post("/api/admin/icecat/prova", params={"gtin": COMPLETO, "lang": "IT"})
            self.assertTrue(r.json()["ok"], r.text)
            self.assertEqual((r.json()["foto"], r.json()["specifiche"]), (3, 2))
            r = c.post("/api/admin/icecat/prova", params={"gtin": SENZA_EAN})
            self.assertEqual((r.json()["ok"], r.json()["assente"]), (False, True))
            self.assertEqual(r.json()["messaggio"], icecat.MANCA_EAN)
            r = c.post("/api/admin/icecat/prova", params={"marca": "Samsung", "codice": "AE040RXEDEG/EU"})
            self.assertTrue(r.json()["ok"] and r.json()["per_codice"], r.text)
            r = c.post("/api/admin/icecat/prova", params={"gtin": SOLO_FULL})
            self.assertEqual((r.json()["ok"], r.json()["assente"]), (False, True))
            self.assertEqual(c.post("/api/admin/icecat/prova", params={"gtin": "ABC123"}).status_code, 400)
            r = c.put("/api/admin/icecat", json={"attivo": True, "utente": "td-icecat", "togli_token": True})
            self.assertEqual(r.json()["api_token_masked"], "")


if __name__ == "__main__":
    unittest.main()
