"""Magazzino: elenco dei prodotti del sito per categoria e ricerca, cestino del sito con
ripristino ed eliminazione definitiva. WooCommerce è finto (niente rete) ma si comporta come
quello vero: il cestino è uno stato del prodotto e lo slug prende «__trashed»."""
import json
import unittest

import _ambiente  # noqa: F401

from fastapi.testclient import TestClient

from app import wc_rest
from app.db import SessionLocal
from app.main import app
from app.models import Client, Job, JobItem, ProdottoCestinato


class WooMagazzino:
    def __init__(self):
        self.prodotti = {}
        for i in range(1, 26):
            cat = 31 if i <= 10 else 32
            self.prodotti[100 + i] = {
                "id": 100 + i, "name": f"Prodotto &amp; co {i:02d}", "slug": f"prodotto-{i}", "sku": f"SKU-{i:03d}",
                "global_unique_id": f"80000000{i:05d}", "status": "draft" if i == 3 else "publish",
                "categories": [{"id": cat, "name": "Caldaie" if cat == 31 else "Pompe"}],
                "price": "10", "regular_price": "10", "sale_price": "", "stock_status": "instock",
                "manage_stock": True, "stock_quantity": i, "images": [{"src": f"https://img.example/{i}.png"}],
                "permalink": f"https://negozio.example/p/{i}", "type": "simple", "date_modified": "2026-10-10T09:00:00",
            }
        self.chiamate: list[tuple] = []

    async def __call__(self, site, method, path, params=None, json_body=None, base=wc_rest.BASE):
        self.chiamate.append((method, path, dict(params or {}), json_body))
        params = params or {}
        if path == "/products" and method == "GET":
            stato = params.get("status", "any")
            lista = [p for p in self.prodotti.values()
                     if (p["status"] == "trash") == (stato == "trash")]
            if params.get("category"):
                lista = [p for p in lista if any(c["id"] == params["category"] for c in p["categories"])]
            if params.get("search"):
                t = params["search"].lower()
                lista = [p for p in lista if t in p["name"].lower() or t in p["sku"].lower() or t in p["global_unique_id"]]
            lista.sort(key=lambda p: p["name"])
            per, pag = int(params.get("per_page", 10)), int(params.get("page", 1))
            pagine = max(1, -(-len(lista) // per))
            if pag > pagine:
                raise wc_rest.SiteError("HTTP 400 dal sito. The page number requested is larger than the number of pages available.")
            return [dict(p) for p in lista[(pag - 1) * per: pag * per]], {"X-WP-Total": str(len(lista)), "X-WP-TotalPages": str(pagine)}
        if path == "/products/categories" and method == "GET":
            return [{"id": 15, "name": "Senza categoria", "slug": "senza-categoria", "parent": 0, "count": 0, "menu_order": 9},
                    {"id": 31, "name": "Caldaie", "slug": "caldaie", "parent": 0, "count": 10, "menu_order": 0},
                    {"id": 32, "name": "Pompe", "slug": "pompe", "parent": 0, "count": 15, "menu_order": 1}], {"X-WP-TotalPages": "1"}
        if path == "/products/batch" and method == "POST":
            fatti = []
            for pid in json_body.get("delete", []):
                fatti.append(dict(self.prodotti.pop(pid)))
            return {"delete": fatti}, {}
        if path.startswith("/products/"):
            pid = int(path.rsplit("/", 1)[1])
            p = self.prodotti[pid]
            if method == "GET":
                return dict(p), {}
            if method == "DELETE" and params.get("force") == "true":
                return dict(self.prodotti.pop(pid)), {}
            if method == "DELETE":
                p["status"], p["slug"] = "trash", p["slug"] + "__trashed"
                p["date_modified"] = "2026-10-15T10:30:00"
                return dict(p), {}
            if method == "PUT":
                p.update(json_body)
                return dict(p), {}
        return {}, {}


class Magazzino(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._originale = wc_rest._request
        cls.woo = WooMagazzino()
        wc_rest._request = cls.woo
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        cls.entra(cls, "admin@example.com")
        cls.cliente = cls.c.post("/api/admin/clients", json={"name": "Negozio Magazzino", "content_lang": "IT"}).json()["id"]
        r = cls.c.post("/api/admin/sites", json={"client_id": cls.cliente, "name": "Magazzino demo", "base_url": "https://magazzino.example",
                                                 "wc_key": "ck_x", "wc_secret": "cs_x", "giorni_cestino": 15})
        cls.sito = r.json()["id"]
        cls.c.post("/api/admin/users", json={"email": "magazzino@example.com", "password": "provaprova123",
                                             "role": "client", "client_id": cls.cliente})
        cls.entra(cls, "magazzino@example.com")

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        wc_rest._request = cls._originale

    def entra(self, email):
        self.c.post("/api/auth/logout")
        assert self.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"}).status_code == 200

    def url(self, coda=""):
        return f"/api/sites/{self.sito}/magazzino{coda}"

    def test_1_elenco_pagine_categoria_ricerca(self):
        r = self.c.get(self.url(), params={"per_pagina": 15})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["totale"], d["pagine"], len(d["prodotti"]), d["per_pagina"]), (25, 2, 15, 15))
        p = d["prodotti"][0]
        self.assertEqual(p["nome"], "Prodotto & co 01")          # «&amp;» in chiaro
        self.assertEqual((p["sku"], p["ean"], p["quantita"], p["immagine"]), ("SKU-001", "8000000000001", 1, "https://img.example/1.png"))
        chiamata = self.woo.chiamate[-2]
        self.assertEqual((chiamata[2]["status"], chiamata[2]["orderby"]), ("any", "title"))
        self.assertIn("price", chiamata[2]["_fields"])
        self.assertEqual(self.c.get(self.url(), params={"per_pagina": 7}).json()["per_pagina"], 20)   # solo i valori ammessi
        d = self.c.get(self.url(), params={"categoria": 31, "per_pagina": 20}).json()
        self.assertEqual(d["totale"], 10)
        d = self.c.get(self.url(), params={"q": "  sku-012 "}).json()
        self.assertEqual([p["nome"] for p in d["prodotti"]], ["Prodotto & co 12"])
        cerca = next(c for c in reversed(self.woo.chiamate) if c[2].get("search"))
        self.assertEqual((cerca[2]["search"], cerca[2]["search_fields[]"]), ("sku-012", ["name", "sku", "global_unique_id"]))
        # pagina che non c'è più: si torna alla prima
        d = self.c.get(self.url(), params={"pagina": 9}).json()
        self.assertEqual(d["pagina"], 1)
        cats = self.c.get(self.url("/categorie")).json()
        self.assertEqual([(c["nome"], c["riserva"]) for c in cats][0], ("Senza categoria", True))

    def test_2_cestino_ripristino_per_sempre(self):
        db = SessionLocal()
        job = Job(site_id=self.sito, label="Carico", status="done", total=2)
        db.add(job); db.commit()
        pubblicata = JobItem(job_id=job.id, gtin="8000000000004", status="pushed", wc_product_id=104, wc_action="created",
                             normalized=json.dumps({"gtin": "8000000000004", "title": "x"}))
        copia = JobItem(job_id=job.id, gtin="8000000000004", status="ready", normalized=json.dumps({"gtin": "8000000000004"}))
        db.add_all([pubblicata, copia]); db.commit()

        # draft nel cestino e ritorno come draft, col suo indirizzo
        r = self.c.delete(self.url("/103"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["nome"], r.json()["giorni_cestino"]), ("Prodotto & co 03", 15))
        self.assertEqual(self.woo.prodotti[103]["status"], "trash")
        self.assertEqual(self.c.delete(self.url("/103")).status_code, 409)
        d = self.c.get(self.url(), params={"cestino": True}).json()
        self.assertEqual((d["totale"], d["nel_cestino"]), (1, 1))
        self.assertEqual((d["prodotti"][0]["nel_cestino_dal"], d["prodotti"][0]["si_svuota_il"]), ("2026-10-15", "2026-10-30"))
        self.assertEqual(self.c.get(self.url()).json()["nel_cestino"], 1)
        r = self.c.post(self.url("/103/ripristina"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.woo.prodotti[103]["status"], self.woo.prodotti[103]["slug"]), ("draft", "prodotto-3"))
        self.assertEqual(self.c.post(self.url("/103/ripristina")).status_code, 409)
        self.assertEqual(db.query(ProdottoCestinato).count(), 0)

        # per sempre solo dal cestino; il portale toglie la riga collegata e la copia non pubblicata
        self.assertEqual(self.c.delete(self.url("/104/per-sempre")).status_code, 409)
        self.c.delete(self.url("/104"))
        r = self.c.delete(self.url("/104/per-sempre"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(104, self.woo.prodotti)
        db.refresh(pubblicata); db.refresh(copia)
        self.assertEqual((pubblicata.status, pubblicata.wc_product_id, copia.status), ("deleted", None, "deleted"))

        # svuota: tutto quello che è nel cestino, anche ciò che non ha cestinato il portale
        self.c.delete(self.url("/105"))
        self.woo.prodotti[106]["status"] = "trash"
        r = self.c.post(self.url("/cestino/svuota"))
        self.assertEqual((r.status_code, r.json()["eliminati"]), (200, 2))
        self.assertFalse({105, 106} & set(self.woo.prodotti))
        db.close()

    def test_2b_categoria_e_prezzi(self):
        db = SessionLocal()
        job = Job(site_id=self.sito, label="Carico prezzi", status="done", total=1)
        db.add(job); db.commit()
        riga = JobItem(job_id=job.id, gtin="8000000000020", status="pushed", wc_product_id=120, regular_price="10.00")
        db.add(riga); db.commit()
        r = self.c.patch(self.url("/120"), json={"categorie": [31], "prezzo": "1.290,50", "scontato": "999"})
        self.assertEqual(r.status_code, 200, r.text)
        put = self.woo.chiamate[-1]
        self.assertEqual((put[0], put[3]), ("PUT", {"categories": [{"id": 31}], "regular_price": "1290.50", "sale_price": "999.00"}))
        self.assertEqual(r.json()["prodotto"]["categorie"][0]["id"], 31)
        db.refresh(riga)
        self.assertEqual((riga.wc_category_id, riga.regular_price, riga.sale_price), (31, "1290.50", "999.00"))
        # scontato vuoto = niente sconto; scontato sopra il pieno: no
        self.assertEqual(self.c.patch(self.url("/120"), json={"scontato": ""}).status_code, 200)
        self.assertEqual(self.woo.chiamate[-1][3], {"sale_price": ""})
        r = self.c.patch(self.url("/120"), json={"prezzo": "10", "scontato": "12"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.patch(self.url("/120"), json={"prezzo": "dieci"}).status_code, 400)
        self.assertEqual(self.c.patch(self.url("/120"), json={"categorie": [999]}).status_code, 400)
        self.assertEqual(self.c.patch(self.url("/120"), json={"categorie": []}).status_code, 400)
        self.woo.prodotti[121]["type"] = "variable"
        self.assertEqual(self.c.patch(self.url("/121"), json={"prezzo": "5"}).status_code, 400)
        self.assertEqual(self.c.patch(self.url("/121"), json={"categorie": [32, 31]}).status_code, 200)
        self.assertEqual(self.c.patch(self.url("/120"), json={}).json()["cambiato"], False)
        db.close()

    def test_3_scrivere_vuole_account_attivo(self):
        db = SessionLocal()
        cliente = db.get(Client, self.cliente)
        cliente.is_active = False
        db.commit()
        try:
            self.assertEqual(self.c.delete(self.url("/110")).status_code, 402)
            self.assertEqual(self.c.post(self.url("/cestino/svuota")).status_code, 402)
            self.assertEqual(self.c.patch(self.url("/110"), json={"prezzo": "1"}).status_code, 402)
            self.assertEqual(self.c.get(self.url()).status_code, 200)   # guardare si può
        finally:
            cliente.is_active = True
            db.commit()
            db.close()

    def test_4_altri_clienti_no(self):
        self.entra("admin@example.com")
        altro = self.c.post("/api/admin/clients", json={"name": "Altro mag", "content_lang": "IT"}).json()["id"]
        self.c.post("/api/admin/users", json={"email": "altro-mag@example.com", "password": "provaprova123",
                                              "role": "client", "client_id": altro})
        self.entra("altro-mag@example.com")
        try:
            self.assertEqual(self.c.get(self.url()).status_code, 403)
            self.assertEqual(self.c.delete(self.url("/110")).status_code, 403)
        finally:
            self.entra("magazzino@example.com")


if __name__ == "__main__":
    unittest.main()
