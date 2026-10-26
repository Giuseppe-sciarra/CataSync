"""Foto e pubblicazione: le foto prese dal web passano dal portale, una foto che il sito non
riesce a scaricare non blocca più il prodotto intero, il registro dice perché una scheda non
è arrivata sul sito e il cliente può caricare le foto dal computer nella scheda."""
import asyncio
import json
import struct
import unittest
import zlib

import _ambiente  # noqa: F401  (prima dell'app)

import httpx
from fastapi.testclient import TestClient

from app import config, foto, wc_rest, worker
from app.db import SessionLocal
from app.main import app
from app.models import Job, JobItem, Site
from app.routers.admin import causa_pubblicazione

PORTALE = "https://catalogo.example"


def _png(larga: int = 400, alta: int = 400, colore: bytes = b"\xff\x00\x00\xff") -> bytes:
    riga = b"\x00" + colore * larga

    def blocco(tipo, dati):
        return struct.pack(">I", len(dati)) + tipo + dati + struct.pack(">I", zlib.crc32(tipo + dati) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", larga, alta, 8, 6, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(riga * alta)) + blocco(b"IEND", b""))


class _Sito:
    id = 999
    base_url = "https://negozio.example"


def _esegui(coro):
    return asyncio.run(coro)


class RipiegoSulleFoto(unittest.TestCase):
    """wc_rest._scrivi_con_foto: si toglie la foto rifiutata e si riprova."""

    def setUp(self):
        self._orig = wc_rest._request
        self.chiamate = []

    def tearDown(self):
        wc_rest._request = self._orig

    def _woo(self, rifiuta: set[str], codice="woocommerce_product_image_upload_error", italiano=False):
        async def finto(site, method, path, params=None, json_body=None, base=None):
            self.chiamate.append(json.loads(json.dumps(json_body)))
            for img in (json_body or {}).get("images") or []:
                if img.get("src") in rifiuta:
                    testo = (f"Errore nell'ottenere l'immagine remota {img['src']}. Errore: Forbidden" if italiano
                             else f"Error getting remote image {img['src']}. Error: Forbidden")
                    raise wc_rest.SiteError(f"HTTP 400 dal sito. {testo}", codice)
            return {"id": 77, "images": [{"src": "https://negozio.example/wp-content/uploads/x.jpg"}]}, {}
        wc_rest._request = finto

    def test_toglie_solo_la_foto_rifiutata(self):
        self._woo({"https://grundfos.example/b.jpg"})
        avvisi = []
        corpo = {"name": "Pompa", "images": [{"src": "https://grundfos.example/a.jpg"},
                                             {"src": "https://grundfos.example/b.jpg"}]}
        out = _esegui(wc_rest._scrivi_con_foto(_Sito(), "POST", "/products", corpo, avvisi, True))
        self.assertEqual(out["id"], 77)
        self.assertEqual(len(self.chiamate), 2)
        self.assertEqual(self.chiamate[1]["images"], [{"src": "https://grundfos.example/a.jpg"}])
        self.assertEqual(avvisi, ["1 foto non pubblicata: il sito non è riuscito a scaricarla."])

    def test_messaggio_in_italiano_senza_codice(self):
        self._woo({"https://grundfos.example/a.jpg"}, codice="", italiano=True)
        avvisi = []
        corpo = {"name": "Pompa", "images": [{"src": "https://grundfos.example/a.jpg"},
                                             {"src": "https://grundfos.example/c.jpg"}]}
        _esegui(wc_rest._scrivi_con_foto(_Sito(), "POST", "/products", corpo, avvisi, True))
        self.assertEqual(self.chiamate[-1]["images"], [{"src": "https://grundfos.example/c.jpg"}])

    def test_nessuna_foto_buona_e_foto_obbligatorie(self):
        self._woo({"https://grundfos.example/a.jpg"})
        corpo = {"name": "Pompa", "images": [{"src": "https://grundfos.example/a.jpg"}]}
        with self.assertRaises(wc_rest.SiteError) as ctx:
            _esegui(wc_rest._scrivi_con_foto(_Sito(), "POST", "/products", corpo, [], True))
        self.assertIn("Carica le foto dal computer", str(ctx.exception))
        self.assertEqual(causa_pubblicazione(wc_rest.spiega(ctx.exception)), "pub_foto")

    def test_nessuna_foto_buona_senza_obbligo(self):
        self._woo({"https://grundfos.example/a.jpg"})
        avvisi = []
        corpo = {"name": "Pompa", "images": [{"src": "https://grundfos.example/a.jpg"}]}
        _esegui(wc_rest._scrivi_con_foto(_Sito(), "POST", "/products", corpo, avvisi, False))
        self.assertNotIn("images", self.chiamate[-1])
        self.assertTrue(avvisi)

    def test_altri_errori_passano_come_sono(self):
        async def finto(site, method, path, params=None, json_body=None, base=None):
            raise wc_rest.SiteError("HTTP 400 dal sito. Invalid or duplicated SKU.", "product_invalid_sku")
        wc_rest._request = finto
        with self.assertRaises(wc_rest.SiteError) as ctx:
            _esegui(wc_rest._scrivi_con_foto(_Sito(), "POST", "/products",
                                             {"images": [{"src": "https://x.example/a.jpg"}]}, [], True))
        self.assertTrue(wc_rest.spiega(ctx.exception).startswith("Lo SKU c'è già"))


class CauseNelRegistro(unittest.TestCase):
    def test_errori_salvati_prima_della_correzione(self):
        casi = {
            "HTTP 400 dal sito. Error getting remote image https://a.example/x.jpg. Error: Forbidden": "pub_foto",
            "HTTP 400 dal sito. Errore nell'ottenere l'immagine remota https://a.example/x.jpg.": "pub_foto",
            "HTTP 400 dal sito. Invalid image: Sorry, you are not allowed to upload this file type.": "pub_foto",
            "HTTP 400 dal sito. Invalid or duplicated SKU.": "pub_sku",
            "HTTP 400 dal sito. Invalid or duplicated GTIN, UPC, EAN or ISBN.": "pub_ean",
            "Chiavi REST rifiutate: controlla che abbiano permesso di lettura e scrittura.": "pub_chiavi",
            "Sito non raggiungibile: ReadTimeout": "pub_rete",
            "Qualcosa di mai visto": "pubblicazione",
            "": "pubblicazione",
        }
        for testo, causa in casi.items():
            self.assertEqual(causa_pubblicazione(testo), causa, testo)


class FotoDelPortale(unittest.TestCase):
    def setUp(self):
        self._pub, self._client = config.PUBLIC_URL, foto.rete.client_pubblico
        config.PUBLIC_URL = PORTALE

    def tearDown(self):
        config.PUBLIC_URL, foto.rete.client_pubblico = self._pub, self._client

    def test_riospita(self):
        immagine = _png()
        visti = []

        def risposta(req: httpx.Request):
            visti.append((str(req.url), req.headers.get("referer"), req.headers.get("user-agent")))
            if req.url.path == "/ok.png":
                return httpx.Response(200, content=immagine, headers={"content-type": "image/png"})
            if req.url.path == "/avif":
                return httpx.Response(200, content=b"\x00\x00\x00\x1cftypavif" + b"\x00" * 50)
            return httpx.Response(403)

        foto.rete.client_pubblico = lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(risposta), follow_redirects=True)
        urls = ["https://produttore.example/ok.png", "https://produttore.example/negata.jpg",
                "https://produttore.example/avif", "https://negozio.example/wp-content/uploads/gia.jpg"]
        nuove, perse = _esegui(foto.riospita(urls, referer="https://produttore.example/scheda",
                                             sito="https://negozio.example"))
        self.assertTrue(nuove[0].startswith(PORTALE + "/media/foto/") and nuove[0].endswith(".png"))
        self.assertEqual(nuove[1:], urls[1:])          # chi non si scarica resta com'era
        self.assertEqual(perse, urls[1:3])
        self.assertTrue(all(r == "https://produttore.example/scheda" for _, r, _ in visti))
        self.assertTrue(all("Chrome" in ua for _, _, ua in visti))
        self.assertNotIn("https://negozio.example/wp-content/uploads/gia.jpg", [u for u, _, _ in visti])
        self.assertIsNotNone(foto.percorso(foto.nome_da_indirizzo(nuove[0])))

    def test_pulizia(self):
        import os
        import time
        nome = foto.salva(_png(colore=b"\x00\xff\x00\xff"))
        vecchia = foto.percorso(nome)
        os.utime(vecchia, (time.time() - 30 * 86400,) * 2)
        foto.pulisci(lambda n: n == nome, forza=True)
        self.assertTrue(vecchia.is_file())           # una scheda la usa: resta
        foto.pulisci(lambda n: False, forza=True)
        self.assertFalse(vecchia.is_file())          # nessuno la usa più: via


class CaricaEPubblica(unittest.TestCase):
    """Il giro dal portale: foto caricata dal computer, scheda salvata, pubblicazione."""

    @classmethod
    def setUpClass(cls):
        cls._pub = config.PUBLIC_URL
        config.PUBLIC_URL = "http://testserver"
        cls._req = wc_rest._request
        cls.scritti = []

        async def woo(site, method, path, params=None, json_body=None, base=None):
            if method == "GET" and path == "/products":
                return [], {"X-WP-Total": "0", "X-WP-TotalPages": "0"}
            if method == "GET":
                return [], {}
            if method == "POST" and path == "/products":
                cls.scritti.append(json_body)
                return {"id": 900, "permalink": "https://negozio.example/p/900",
                        "images": [{"src": "https://negozio.example/wp-content/uploads/foto.png"}]}, {}
            return {}, {}
        wc_rest._request = woo
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        wc_rest._request = cls._req
        config.PUBLIC_URL = cls._pub

    def entra(self, email):
        self.c.post("/api/auth/logout")
        r = self.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_giro(self):
        c = self.c
        self.entra("admin@example.com")
        cid = c.post("/api/admin/clients", json={"name": "Foto Srl", "content_lang": "IT"}).json()["id"]
        sid = c.post("/api/admin/sites", json={"client_id": cid, "name": "Negozio foto", "base_url": "https://negozio.example",
                                               "wc_key": "ck_x", "wc_secret": "cs_x"}).json()["id"]
        r = c.post("/api/admin/users", json={"email": "foto@example.com", "password": "provaprova123",
                                             "role": "client", "client_id": cid})
        self.assertEqual(r.status_code, 201, r.text)
        db = SessionLocal()
        job = Job(site_id=sid, label="prova", kind="manual", status="done")
        db.add(job)
        db.commit()
        item = JobItem(job_id=job.id, gtin="99221340", status="incomplete", wc_category_id=15, regular_price="100.00",
                       normalized=json.dumps({"gtin": "", "mpn": "99221340", "title": "Grundfos UPM3",
                                              "description": "Circolatore", "images": []}))
        db.add(item)
        db.commit()
        iid = item.id
        db.close()

        self.entra("foto@example.com")
        self.assertTrue(c.get(f"/api/items/{iid}/preview").json()["carica_foto"])
        r = c.post(f"/api/items/{iid}/foto", files={"file": ("foto.png", _png(), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        src = r.json()["src"]
        self.assertTrue(src.startswith("http://testserver/media/foto/"))
        self.assertEqual(c.get(src[len("http://testserver"):]).content, _png())
        self.assertEqual(c.post(f"/api/items/{iid}/foto", files={"file": ("x.txt", b"ciao", "text/plain")}).status_code, 415)

        r = c.put(f"/api/items/{iid}", json={"title": "Grundfos UPM3", "description": "Circolatore", "mpn": "99221340",
                                             "images": [src], "category_id": 15, "regular_price": "100"})
        self.assertEqual(r.json()["status"], "ready", r.text)
        r = c.post(f"/api/items/{iid}/pubblica")
        self.assertEqual(r.json()["stato"], "pushed", r.text)
        self.assertEqual(self.scritti[-1]["images"][0]["src"], src)   # WooCommerce la prende dal portale
        db = SessionLocal()
        salvata = json.loads(db.get(JobItem, iid).normalized)
        db.close()
        self.assertEqual(salvata["images"], ["https://negozio.example/wp-content/uploads/foto.png"])

        # Un altro cliente non carica foto sulle schede altrui.
        self.entra("admin@example.com")
        cid2 = c.post("/api/admin/clients", json={"name": "Altro", "content_lang": "IT"}).json()["id"]
        c.post("/api/admin/users", json={"email": "altro@example.com", "password": "provaprova123",
                                         "role": "client", "client_id": cid2})
        self.entra("altro@example.com")
        r = c.post(f"/api/items/{iid}/foto", files={"file": ("foto.png", _png(), "image/png")})
        self.assertIn(r.status_code, (403, 404))


if __name__ == "__main__":
    unittest.main()
