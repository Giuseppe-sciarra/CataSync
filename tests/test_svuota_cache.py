"""2.11: svuotamento della cache FastCGI dei siti dopo le scritture del portale.

Il sito è finto (trasporto httpx): si controlla che le scritture segnino il sito, che dopo la
quiete parta una chiamata sola al mu-plugin con la cartella del sito, che le letture e i siti
con lo svuotamento spento non facciano niente, e le impostazioni da Siti → Modifica."""
import asyncio
import json
import time
import unittest

import _ambiente  # noqa: F401  (prima dell'app)
import httpx
from fastapi.testclient import TestClient

from app import svuota_cache, wc_rest
from app.db import SessionLocal
from app.main import app
from app.models import Site


class SitoFinto:
    def __init__(self):
        self.chiamate = []
        self.cache = 200          # risposta del mu-plugin

    def __call__(self, request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content or b"{}") if request.content else {}
        self.chiamate.append((request.method, request.url.path, corpo))
        if request.url.path.startswith("/wp-json/wc-catasync-cache/v1/svuota"):
            if self.cache == 404:
                return httpx.Response(404, json={"code": "rest_no_route"})
            if self.cache == 400:
                return httpx.Response(400, json={"code": "catasync_cache", "message": "La cartella non esiste."})
            return httpx.Response(200, json={"percorso": corpo.get("percorso"), "host": "negozio.example",
                                             "file": 12, "letti": 12, "svuotati": 5, "non_cancellati": 0})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": 1})

    def svuotamenti(self):
        return [c for c in self.chiamate if "wc-catasync-cache" in c[1]]


class SvuotaCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        r = cls.c.post("/api/auth/login", json={"email": "admin@example.com", "password": "provaprova123"})
        assert r.status_code == 200, r.text
        cls.cid = cls.c.post("/api/admin/clients", json={"name": "Cache Srl", "content_lang": "IT"}).json()["id"]

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def setUp(self):
        self._client, self._quiete = httpx.AsyncClient, svuota_cache.QUIETE
        svuota_cache.QUIETE = 3600   # il giro in sottofondo dell'app non deve anticipare i test
        svuota_cache._segnati.clear()
        self.sito = SitoFinto()
        sito, originale = self.sito, httpx.AsyncClient

        class Client(originale):
            def __init__(inner, *a, **k):
                k["transport"] = httpx.MockTransport(sito)
                super().__init__(*a, **k)

        httpx.AsyncClient = Client

    def tearDown(self):
        httpx.AsyncClient = self._client
        svuota_cache.QUIETE = self._quiete
        svuota_cache._segnati.clear()

    def nuovo_sito(self, **campi) -> int:
        r = self.c.post("/api/admin/sites", json={"client_id": self.cid, "name": "Negozio cache",
                                                  "base_url": "https://negozio.example",
                                                  "wc_key": "ck_x", "wc_secret": "cs_x", **campi})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def carica(self, sid: int) -> Site:
        db = SessionLocal()
        try:
            return db.get(Site, sid)
        finally:
            db.close()

    def dopo_la_quiete(self) -> list[int]:
        return asyncio.run(svuota_cache.giro(time.monotonic() + svuota_cache.QUIETE + 1))

    def test_impostazioni_del_sito(self):
        sid = self.nuovo_sito()
        dati = next(s for s in self.c.get("/api/admin/sites").json() if s["id"] == sid)
        self.assertFalse(dati["cache_purge"])
        self.assertEqual(dati["cache_path"], "/www/server/fastcgi_cache")

        r = self.c.put(f"/api/admin/sites/{sid}", json={"client_id": self.cid, "name": "Negozio cache",
                                                         "base_url": "https://negozio.example",
                                                         "cache_purge": True, "cache_path": " /srv/cache/wp/ "})
        self.assertEqual(r.status_code, 200, r.text)
        dati = next(s for s in self.c.get("/api/admin/sites").json() if s["id"] == sid)
        self.assertTrue(dati["cache_purge"])
        self.assertEqual(dati["cache_path"], "/srv/cache/wp")

        r = self.c.put(f"/api/admin/sites/{sid}", json={"client_id": self.cid, "name": "Negozio cache",
                                                         "base_url": "https://negozio.example",
                                                         "cache_purge": True, "cache_path": "www/server"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("assoluto", r.json()["detail"])

        # le chiavi non si perdono salvando senza ripeterle
        self.assertTrue(self.carica(sid).wc_key_enc)

    def test_scritture_segnano_e_dopo_la_quiete_uno_svuotamento_solo(self):
        sid = self.nuovo_sito(cache_purge=True, cache_path="/www/server/fastcgi_cache")
        sito = self.carica(sid)

        asyncio.run(wc_rest._request(sito, "GET", "/products"))
        self.assertNotIn(sid, svuota_cache._segnati, "una lettura non cambia il sito")

        for i in range(3):
            asyncio.run(wc_rest._request(sito, "PUT", f"/products/{i}", json_body={"regular_price": "10"}))
        self.assertIn(sid, svuota_cache._segnati)

        self.assertEqual(asyncio.run(svuota_cache.giro()), [], "prima della quiete non si svuota")
        self.assertEqual(self.sito.svuotamenti(), [])

        self.assertEqual(self.dopo_la_quiete(), [sid])
        self.assertEqual(self.sito.svuotamenti(),
                         [("POST", "/wp-json/wc-catasync-cache/v1/svuota", {"percorso": "/www/server/fastcgi_cache"})])
        self.assertNotIn(sid, svuota_cache._segnati, "lo svuotamento non segna di nuovo il sito")
        self.assertEqual(self.dopo_la_quiete(), [])

    def test_sito_con_svuotamento_spento(self):
        sid = self.nuovo_sito()
        asyncio.run(wc_rest._request(self.carica(sid), "POST", "/products", json_body={"name": "x"}))
        self.assertNotIn(sid, svuota_cache._segnati)
        self.dopo_la_quiete()
        self.assertEqual(self.sito.svuotamenti(), [])

    def test_spento_dopo_la_scrittura(self):
        sid = self.nuovo_sito(cache_purge=True)
        asyncio.run(wc_rest._request(self.carica(sid), "DELETE", "/products/3"))
        db = SessionLocal()
        db.get(Site, sid).cache_purge = False
        db.commit()
        db.close()
        self.assertEqual(self.dopo_la_quiete(), [sid])
        self.assertEqual(self.sito.svuotamenti(), [], "spento nel frattempo: niente chiamata")

    def test_errore_del_sito_non_ferma_il_giro(self):
        sid = self.nuovo_sito(cache_purge=True)
        asyncio.run(wc_rest._request(self.carica(sid), "PUT", "/products/1", json_body={}))
        self.sito.cache = 404
        with self.assertLogs("catasync.cache", "WARNING") as log:
            self.assertEqual(self.dopo_la_quiete(), [sid])
        self.assertIn("mu-plugin «Connettore CataSync»", log.output[0])

    def test_prova_da_siti(self):
        sid = self.nuovo_sito(cache_purge=False, cache_path="/srv/cache")
        r = self.c.post(f"/api/admin/sites/{sid}/svuota-cache").json()
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["svuotati"], r["percorso"]), (5, "/srv/cache"))

        self.sito.cache = 404
        r = self.c.post(f"/api/admin/sites/{sid}/svuota-cache").json()
        self.assertEqual(r, {"ok": False, "error": "Sul sito manca il mu-plugin «Connettore CataSync»."})

        self.sito.cache = 400
        r = self.c.post(f"/api/admin/sites/{sid}/svuota-cache").json()
        self.assertEqual(r, {"ok": False, "error": "La cartella non esiste."})

        self.assertEqual(self.c.post("/api/admin/sites/99999/svuota-cache").status_code, 404)


if __name__ == "__main__":
    unittest.main()
