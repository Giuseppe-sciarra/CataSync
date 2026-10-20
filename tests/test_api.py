"""Giro completo delle API con un WooCommerce e una ricerca finti (niente rete):
amministratore, cliente, lingua delle schede, pulizia dell'HTML che arriva da
fuori, pubblicazione, favicon caricabile, isolamento fra ruoli."""
import asyncio
import json
import os
import struct
import unittest
import zlib

import _ambiente

from fastapi.testclient import TestClient

from app import wc_rest, web_source, worker
from app.db import SessionLocal
from app.main import app
from app.models import Job, JobItem, Site

RADICE = _ambiente.RADICE
scritti: list[dict] = []


async def _woo_finto(site, method, path, params=None, json_body=None, base=None):
    if method == "GET" and path.startswith("/products/categories"):
        return [{"id": 15, "name": "Pompe", "parent": 0, "count": 3, "slug": "pompe"}], {"X-WP-TotalPages": "1"}
    if method == "GET" and path.startswith(("/products/attributes", "/products/brands")):
        return [], {}
    if method == "GET" and path == "/system_status":
        return {"environment": {"version": "9.9", "wp_version": "6.9"}}, {}
    if method == "GET" and path == "/products":
        return [], {"X-WP-Total": "0", "X-WP-TotalPages": "0"}
    if method in ("POST", "PUT") and path.startswith("/products"):
        scritti.append({"metodo": method, "path": path, "corpo": json_body})
        return {"id": 501, **(json_body or {})}, {}
    return {}, {}


async def _ricerca_finta(gtin, motore, chiave, extra, mpn="", lingua="IT"):
    # Una scheda «ostile», come può arrivare da una pagina qualsiasi.
    return {
        "gtin": gtin, "title": "<img src=x onerror=alert(1)>Pompa DAB Esybox Pop", "brand": "DAB", "mpn": "60220687",
        "description": '<p onclick="x()">Pressurizzazione <b>costante</b></p><script>alert(1)</script>'
                       '<iframe src="https://evil.example"></iframe>',
        "images": ["https://example.com/a.jpg", "javascript:alert(1)"],
        "attributes": [{"name": "<i>Portata</i>", "value": "3,9 m³/h"}],
        "category": {"id": "", "name": "Pompe"}, "documents": [], "warnings": [], "lingua": lingua,
        "_diagnosi": {"versione": 1, "gtin": gtin, "lingua": lingua, "causa": "trovato", "ricerche": [], "pagine": []},
    }


def _png(larga: int, alta: int) -> bytes:
    riga = b"\x00" + b"\xff\x00\x00\xff" * larga

    def blocco(tipo, dati):
        return struct.pack(">I", len(dati)) + tipo + dati + struct.pack(">I", zlib.crc32(tipo + dati) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", larga, alta, 8, 6, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(riga * alta)) + blocco(b"IEND", b""))


class GiroCompleto(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._originali = (wc_rest._request, web_source.cerca_automatica)
        wc_rest._request = _woo_finto
        web_source.cerca_automatica = _ricerca_finta
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        wc_rest._request, web_source.cerca_automatica = cls._originali

    def entra(self, email):
        self.c.post("/api/auth/logout")
        r = self.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_giro(self):
        c = self.c
        # --- pagine e statici
        self.assertEqual(c.get("/login").status_code, 200)
        r = c.get("/favicon.ico")
        self.assertTrue(r.headers["content-type"].startswith("image"))
        favicon_di_serie = r.content
        r = c.get("/static/i18n/catalog.js")
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")

        # --- amministratore: cliente, sito, utente, ricerca
        self.entra("admin@example.com")
        r = c.post("/api/admin/clients", json={"name": "Termoidraulica Demo", "content_lang": "IT"})
        self.assertEqual(r.status_code, 201, r.text)
        cid = r.json()["id"]
        r = c.post("/api/admin/sites", json={"client_id": cid, "name": "Negozio demo", "base_url": "https://negozio.example",
                                             "wc_key": "ck_prova", "wc_secret": "cs_prova"})
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        self.assertEqual(r.json().get("content_lang", ""), "")
        r = c.post("/api/admin/users", json={"email": "cliente@example.com", "password": "provaprova123",
                                             "role": "client", "client_id": cid})
        self.assertEqual(r.status_code, 201, r.text)
        r = c.put("/api/admin/ricerca", json={"motore": "searxng", "url": "http://searxng:8080"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/admin/ricerca/salute").status_code, 200)

        # --- favicon caricabile
        r = c.post("/api/admin/brand/favicon", files={"file": ("icona.png", _png(256, 256), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["favicon"].startswith("/media/portale/favicon?v="))
        self.assertTrue(c.get("/favicon.ico").content.startswith(b"\x89PNG"))
        pagina = c.get("/login").text
        self.assertIn('rel="apple-touch-icon" href="/media/portale/favicon', pagina)
        self.assertIn("quadrata", c.post("/api/admin/brand/favicon",
                                         files={"file": ("lunga.png", _png(300, 100), "image/png")}).json()["avviso"])
        ico = open(os.path.join(RADICE, "static", "favicon.ico"), "rb").read()
        r = c.post("/api/admin/brand/favicon", files={"file": ("x.ico", ico, "application/octet-stream")})
        self.assertEqual(r.json()["favicon_type"], "image/x-icon")
        r = c.post("/api/admin/brand/favicon", files={"file": ("x.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")})
        self.assertEqual(r.status_code, 400)
        svg = open(os.path.join(RADICE, "static", "favicon.svg"), "rb").read()
        self.assertEqual(c.post("/api/admin/brand/favicon", files={"file": ("x.svg", svg, "image/svg+xml")}).status_code, 200)
        self.assertIn("sandbox", c.get("/media/portale/favicon").headers.get("content-security-policy", ""))
        self.assertEqual(c.post("/api/admin/brand/favicon", files={"file": ("x.txt", b"ciao", "text/plain")}).status_code, 415)
        self.assertEqual(c.delete("/api/admin/brand/favicon").json()["favicon"], "")
        self.assertEqual(c.get("/favicon.ico").content, favicon_di_serie)

        # --- cliente: la lingua delle schede segue quella scelta
        self.entra("cliente@example.com")
        r = c.post("/api/jobs", json={"site_id": sid, "eans": "8059893356383\n97924465", "lang": "EN"})
        self.assertEqual(r.status_code, 201, r.text)
        job_id = r.json()["id"]
        self.assertEqual(r.json()["lang"], "EN")
        r = c.get("/app")
        self.assertIn("catalog.js", r.text)
        self.assertIn("Codice sorgente", r.text)

        # --- ricerca finta con scheda ostile: si salva pulita
        db = SessionLocal()
        job, site = db.get(Job, job_id), db.get(Site, sid)
        riga = db.query(JobItem).filter(JobItem.job_id == job_id, JobItem.gtin == "8059893356383").one()
        asyncio.run(worker._scarica(db, riga, job, site))
        db.refresh(riga)
        scheda = json.loads(riga.normalized)
        for vietato in ("<script", "onclick", "<iframe"):
            self.assertNotIn(vietato, scheda["description"])
        self.assertEqual(scheda["title"], "Pompa DAB Esybox Pop")
        self.assertEqual(scheda["images"], ["https://example.com/a.jpg"])
        self.assertNotIn("<script", c.get(f"/api/items/{riga.id}/preview").text)
        self.assertEqual(c.get(f"/api/items/{riga.id}/diagnosi").json()["diagnosi"]["causa"], "trovato")

        # --- correzione a mano (ostile) e pubblicazione
        corpo = {"title": "Pompa <b>DAB</b>", "brand": "DAB", "mpn": "60220687",
                 "description": '<p>ok</p><img src=x onerror="fetch(1)"><script>x</script>', "short_description": "",
                 "category_name": "Pompe", "images": ["https://example.com/a.jpg"],
                 "attributes": [{"name": "Portata", "value": "3,9 m³/h"}],
                 "regular_price": "199,90", "sale_price": "", "category_id": 15}
        self.assertEqual(c.put(f"/api/items/{riga.id}", json=corpo).status_code, 200)
        db.refresh(riga)
        self.assertEqual(json.loads(riga.normalized)["description"], "<p>ok</p>")
        self.assertEqual(c.post(f"/api/items/{riga.id}/pubblica").status_code, 200)
        db.refresh(riga)
        self.assertEqual(riga.status, "pushed", riga.error)
        inviato = [s for s in scritti if s["metodo"] == "POST"][-1]["corpo"]
        self.assertNotIn("<script", json.dumps(inviato))
        self.assertEqual(inviato["name"], "Pompa DAB")
        self.assertEqual(inviato["regular_price"], "199.90")
        db.close()

        # --- scheda cliente: il cliente la legge, i dati li scrive solo l'amministratore
        r = c.get("/api/profile")
        self.assertEqual(r.status_code, 200, r.text)
        profilo = r.json()
        self.assertEqual(profilo["name"], "Termoidraulica Demo")
        self.assertFalse(profilo["modificabile"])
        self.assertEqual([s["nome"] for s in profilo["siti"]], ["Negozio demo"])
        self.assertEqual(profilo["pubblicati_totale"], 1)
        self.assertIn("status_label", profilo["subscription"])
        self.assertEqual(c.put("/api/profile", json={"legal_name": "Furbo srl"}).status_code, 403)
        self.assertEqual(c.post("/api/profile/logo", files={"file": ("l.png", _png(64, 64), "image/png")}).status_code, 403)
        self.assertEqual(c.delete("/api/profile/logo").status_code, 403)

        self.entra("admin@example.com")
        r = c.put(f"/api/admin/clients/{cid}", json={
            "name": "Termoidraulica Demo", "content_lang": "IT", "legal_name": "Termoidraulica Demo srl",
            "vat_number": "01234567890", "tax_code": "01234567890", "sdi_code": "ABC1234",
            "zip_code": "00100", "city": "Roma", "province": "RM", "country": "Italia",
            "website": "www.esempio.it", "subscription_status": "active", "plan_name": "Base", "monthly_quota": 50})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post(f"/api/profile/logo?client_id={cid}", files={"file": ("l.png", _png(64, 64), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)

        self.entra("cliente@example.com")
        profilo = c.get("/api/profile").json()
        self.assertEqual(profilo["legal_name"], "Termoidraulica Demo srl")
        self.assertEqual((profilo["sdi_code"], profilo["zip_code"], profilo["website"]), ("ABC1234", "00100", "www.esempio.it"))
        self.assertEqual(profilo["subscription"]["plan"], "Base")
        self.assertEqual(profilo["subscription"]["quota"], 50)
        self.assertEqual(profilo["logo"], f"/media/logo/{cid}")
        self.assertEqual(c.get(profilo["logo"]).status_code, 200)

        # --- isolamento
        self.assertEqual(c.get("/api/admin/clients").status_code, 403)
        self.assertEqual(c.post("/api/admin/brand/favicon", files={"file": ("x.ico", ico, "image/x-icon")}).status_code, 403)
        r = c.post("/api/jobs", json={"site_id": sid, "eans": "8059893356383"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
