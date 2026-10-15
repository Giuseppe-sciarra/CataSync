"""Categorie del sito da CataSync: creazione in fondo alle sorelle (anche sottocategorie),
nuovo nome, eliminazione con la pulizia nel portale, ordine a lotti, menu del sito tramite il
mu-plugin. WooCommerce e il plugin sono finti (niente rete), ma rispondono come quelli veri."""
import html
import json
import os
import unittest

import _ambiente

from fastapi.testclient import TestClient

from app import wc_rest
from app.db import SessionLocal
from app.main import app
from app.models import CategoryMap, Client, Job, JobItem


class WooFinto:
    """Categorie e mu-plugin del menu, con stato: si vede cosa arriva e cosa cambia."""

    def __init__(self):
        self.cats = {
            15: {"id": 15, "name": "Senza categoria", "slug": "senza-categoria", "parent": 0, "count": 0, "menu_order": 0},
            21: {"id": 21, "name": "Caldaie", "slug": "caldaie", "parent": 0, "count": 4, "menu_order": 0},
            22: {"id": 22, "name": "Pompe", "slug": "pompe", "parent": 0, "count": 2, "menu_order": 1},
            23: {"id": 23, "name": "Condensazione", "slug": "condensazione", "parent": 21, "count": 1, "menu_order": 0},
        }
        self.prossimo = 30
        self.lotti: list[dict] = []
        self.creati: list[dict] = []
        self.rinominate: list[tuple] = []
        self.plugin = True
        self.menu = {"attivo": False, "menu_id": 0, "voce_id": 0, "sottocategorie": True}

    def stato(self) -> dict:
        return {**self.menu, "plugin": "catasync-category-menu", "versione": "1.0.0", "problema": "",
                "menus": [{"id": 5, "nome": "Menu principale",
                           "voci": [{"id": 9, "titolo": "Prodotti", "livello": 0, "categoria": False}]}],
                "ultimo": None}

    async def __call__(self, site, method, path, params=None, json_body=None, base=wc_rest.BASE):
        if base == wc_rest.PLUGIN_MENU:
            if not self.plugin:
                raise wc_rest.PluginMancante("Sul sito manca il mu-plugin «CataSync – Menu delle categorie».")
            esito = {"aggiunte": 1, "spostate": 0, "tolte": 0, "riordinate": 2, "errore": "", "quando": "2026-10-15T10:00:00+00:00"}
            if path == "/menu" and method == "GET":
                return self.stato(), {}
            if path == "/menu" and method == "POST":
                self.menu.update(json_body or {})
                return {"esito": esito if self.menu["attivo"] else None, **self.stato()}, {}
            if path == "/menu/allinea" and method == "POST":
                return {"esito": esito, **self.stato()}, {}
        if path == "/products/categories" and method == "GET":
            return [dict(c) for c in self.cats.values()], {"X-WP-TotalPages": "1"}
        if path == "/products/categories" and method == "POST":
            nuova = {"id": self.prossimo, "slug": json_body["name"].lower(), "count": 0, "menu_order": 0, "parent": 0, **json_body}
            self.prossimo += 1
            self.cats[nuova["id"]] = nuova
            self.creati.append(json_body)
            return dict(nuova), {}
        if path == "/products/categories/batch" and method == "POST":
            self.lotti.append(json_body)
            for u in json_body.get("update", []):
                self.cats[u["id"]]["menu_order"] = u["menu_order"]
            return {"update": [dict(self.cats[u["id"]]) for u in json_body.get("update", [])]}, {}
        if path.startswith("/products/categories/"):
            cid = int(path.rsplit("/", 1)[1])
            if method == "GET":
                return dict(self.cats[cid]), {}
            if method == "PUT":   # come WordPress: «&» torna indietro come «&amp;»
                self.cats[cid].update({k: html.escape(v, quote=False) if k == "name" else v
                                       for k, v in json_body.items()})
                self.rinominate.append((cid, json_body))
                return dict(self.cats[cid]), {}
            if method == "DELETE":
                via = self.cats.pop(cid)
                for c in self.cats.values():   # come WordPress: le figlie salgono di un livello
                    if c["parent"] == cid:
                        c["parent"] = via["parent"]
                return via, {}
        if method == "GET" and path.startswith(("/products/attributes", "/products/brands")):
            return [], {}
        return {}, {}


class Categorie(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._originale = wc_rest._request
        cls.woo = WooFinto()
        wc_rest._request = cls.woo
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        cls.entra(cls, "admin@example.com")
        r = cls.c.post("/api/admin/clients", json={"name": "Negozio Categorie", "content_lang": "IT"})
        cls.cliente = r.json()["id"]
        r = cls.c.post("/api/admin/sites", json={"client_id": cls.cliente, "name": "Categorie demo",
                                                 "base_url": "https://categorie.example", "wc_key": "ck_x", "wc_secret": "cs_x"})
        cls.sito = r.json()["id"]
        cls.c.post("/api/admin/users", json={"email": "categorie@example.com", "password": "provaprova123",
                                             "role": "client", "client_id": cls.cliente})
        cls.entra(cls, "categorie@example.com")

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        wc_rest._request = cls._originale

    def entra(self, email):
        self.c.post("/api/auth/logout")
        r = self.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"})
        assert r.status_code == 200, r.text

    def url(self, coda=""):
        return f"/api/sites/{self.sito}/categories{coda}"

    def test_1_crea_in_fondo_alle_sorelle(self):
        r = self.c.post(self.url(), json={"name": "  Radiatori   da  bagno "})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.woo.creati[-1], {"name": "Radiatori da bagno", "menu_order": 2})
        r = self.c.post(self.url(), json={"name": "Murali", "parent": 21})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.woo.creati[-1], {"name": "Murali", "parent": 21, "menu_order": 1})
        r = self.c.post(self.url(), json={"name": "caldaie"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "C'è già una categoria «caldaie» a questo livello.")
        self.assertEqual(self.c.post(self.url(), json={"name": "murali", "parent": 21}).status_code, 409)
        self.assertEqual(self.c.post(self.url(), json={"name": "Nuova", "parent": 999}).status_code, 400)
        self.assertEqual(self.c.post(self.url(), json={"name": "Nuova", "parent": 15}).status_code, 400)
        self.assertEqual(self.c.post(self.url(), json={"name": ""}).status_code, 422)
        # Nell'elenco del portale: nome da solo, percorso, ordine, e senza «Senza categoria».
        cats = self.c.get(f"/api/sites/{self.sito}/taxonomy").json()["categories"]
        self.assertNotIn(15, [c["id"] for c in cats])
        murali = next(c for c in cats if c["nome"] == "Murali")
        self.assertEqual((murali["name"], murali["menu_order"]), ("Caldaie › Murali", 1))

    def test_2_ordine_a_lotti(self):
        ids = [c["id"] for c in self.c.get(f"/api/sites/{self.sito}/taxonomy").json()["categories"]]
        radiatori = next(i for i in ids if self.woo.cats[i]["name"] == "Radiatori da bagno")
        murali = next(i for i in ids if self.woo.cats[i]["name"] == "Murali")
        prima = len(self.woo.lotti)
        r = self.c.put(self.url("/order"), json={"ids": [22, 21, 23, murali, radiatori]})
        self.assertEqual(r.status_code, 200, r.text)
        cambi = {u["id"]: u["menu_order"] for u in self.woo.lotti[prima]["update"]}
        # Pompe prima, Caldaie seconda; «Senza categoria», che non era nell'elenco, va in fondo.
        self.assertEqual(cambi, {22: 0, 21: 1, 15: 3})
        self.assertEqual(r.json()["cambiate"], 3)
        r = self.c.put(self.url("/order"), json={"ids": [22, 21, 23, murali, radiatori]})
        self.assertEqual(r.json()["cambiate"], 0)
        self.assertEqual(len(self.woo.lotti), prima + 1)   # niente da cambiare: nessuna chiamata

    def test_2b_rinomina(self):
        db = SessionLocal()
        db.add(CategoryMap(site_id=self.sito, source_category="pompe di calore", wc_term_id=22, wc_label="Pompe"))
        db.commit()
        r = self.c.patch(self.url("/22"), json={"name": "  Pompe  &  circolatori "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True, "id": 22, "name": "Pompe & circolatori", "cambiata": True})
        self.assertEqual(self.woo.rinominate[-1], (22, {"name": "Pompe & circolatori"}))   # lo slug non si tocca
        self.assertEqual(self.woo.cats[22]["slug"], "pompe")
        mappa = db.query(CategoryMap).filter(CategoryMap.wc_term_id == 22).one()
        db.refresh(mappa)
        self.assertEqual(mappa.wc_label, "Pompe & circolatori")
        db.close()
        # Nell'elenco il nome è in chiaro, non «&amp;»; lo stesso nome non rifà la chiamata.
        cats = self.c.get(f"/api/sites/{self.sito}/taxonomy").json()["categories"]
        self.assertEqual(next(c for c in cats if c["id"] == 22)["nome"], "Pompe & circolatori")
        quante = len(self.woo.rinominate)
        r = self.c.patch(self.url("/22"), json={"name": "Pompe & circolatori"})
        self.assertEqual((r.status_code, r.json()["cambiata"], len(self.woo.rinominate)), (200, False, quante))
        # Solo maiuscole diverse: si può. Stesso nome di una sorella: no. Di un'altra a un altro livello: sì.
        self.assertEqual(self.c.patch(self.url("/22"), json={"name": "pompe & circolatori"}).status_code, 200)
        r = self.c.patch(self.url("/22"), json={"name": "CALDAIE"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "C'è già una categoria «CALDAIE» a questo livello.")
        self.assertEqual(self.c.patch(self.url("/23"), json={"name": "Pompe & circolatori"}).status_code, 200)
        self.assertEqual(self.c.patch(self.url("/15"), json={"name": "Varie"}).status_code, 400)
        self.assertEqual(self.c.patch(self.url("/999"), json={"name": "Varie"}).status_code, 404)
        self.assertEqual(self.c.patch(self.url("/22"), json={"name": ""}).status_code, 422)

    def test_3_elimina_e_pulisce_il_portale(self):
        r = self.c.post("/api/jobs", json={"site_id": self.sito, "eans": "8059893356383"})
        self.assertEqual(r.status_code, 201, r.text)
        db = SessionLocal()
        riga = db.query(JobItem).filter(JobItem.job_id == r.json()["id"]).one()
        riga.normalized = json.dumps({"gtin": riga.gtin, "title": "Caldaia", "description": "<p>x</p>",
                                      "images": ["https://example.com/c.jpg"]})
        riga.regular_price, riga.wc_category_id, riga.status = "990.00", 21, "ready"
        db.add(CategoryMap(site_id=self.sito, source_category="caldaie a gas", wc_term_id=21, wc_label="Caldaie"))
        db.commit()

        self.assertEqual(self.c.delete(self.url("/15")).status_code, 400)   # la categoria di riserva no
        r = self.c.delete(self.url("/21"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["name"], r.json()["prodotti"], r.json()["corrispondenze"], r.json()["righe"]),
                         ("Caldaie", 4, 1, 1))
        db.refresh(riga)
        self.assertIsNone(riga.wc_category_id)
        self.assertEqual(riga.status, "incomplete")
        self.assertIn("category", json.loads(riga.issues))
        self.assertEqual(db.query(CategoryMap).filter(CategoryMap.wc_term_id == 21).count(), 0)
        self.assertEqual(self.woo.cats[23]["parent"], 0)   # la sottocategoria è salita
        db.close()

    def test_4_menu_del_sito(self):
        r = self.c.get(f"/api/sites/{self.sito}/menu")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["plugin"])
        self.assertEqual(r.json()["menus"][0]["voci"][0]["titolo"], "Prodotti")
        r = self.c.put(f"/api/sites/{self.sito}/menu", json={"attivo": True, "menu_id": 5, "voce_id": 9})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.woo.menu, {"attivo": True, "menu_id": 5, "voce_id": 9, "sottocategorie": True})
        self.assertEqual(r.json()["esito"]["aggiunte"], 1)
        self.assertEqual(self.c.post(f"/api/sites/{self.sito}/menu/allinea").status_code, 200)
        self.woo.plugin = False
        try:
            self.assertEqual(self.c.get(f"/api/sites/{self.sito}/menu").json(), {"plugin": False})
            self.assertEqual(self.c.put(f"/api/sites/{self.sito}/menu", json={"attivo": False}).status_code, 409)
        finally:
            self.woo.plugin = True
        r = self.c.get("/api/wordpress/catasync-category-menu.php")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers.get("content-disposition", ""))
        self.assertIn("Plugin Name:", r.text)
        with open(os.path.join(_ambiente.RADICE, "wordpress", "catasync-category-menu.php"), encoding="utf-8") as f:
            self.assertEqual(r.text, f.read())

    def test_5_account_sospeso_non_scrive(self):
        db = SessionLocal()
        cliente = db.get(Client, self.cliente)
        cliente.is_active = False
        db.commit()
        try:
            self.assertEqual(self.c.post(self.url(), json={"name": "Sospesa"}).status_code, 402)
            self.assertEqual(self.c.put(self.url("/order"), json={"ids": []}).status_code, 402)
            self.assertEqual(self.c.patch(self.url("/22"), json={"name": "Sospesa"}).status_code, 402)
            self.assertEqual(self.c.put(f"/api/sites/{self.sito}/menu", json={"attivo": False}).status_code, 402)
            self.assertEqual(self.c.get(f"/api/sites/{self.sito}/menu").status_code, 200)   # leggere si può
        finally:
            cliente.is_active = True
            db.commit()
            db.close()

    def test_6_altri_clienti_non_vedono(self):
        self.entra("admin@example.com")
        r = self.c.post("/api/admin/clients", json={"name": "Altro negozio", "content_lang": "IT"})
        altro = r.json()["id"]
        self.c.post("/api/admin/users", json={"email": "altro-cat@example.com", "password": "provaprova123",
                                              "role": "client", "client_id": altro})
        self.entra("altro-cat@example.com")
        try:
            self.assertEqual(self.c.post(self.url(), json={"name": "Intrusa"}).status_code, 403)
            self.assertEqual(self.c.delete(self.url("/22")).status_code, 403)
            self.assertEqual(self.c.patch(self.url("/22"), json={"name": "Intrusa"}).status_code, 403)
            self.assertEqual(self.c.get(f"/api/sites/{self.sito}/menu").status_code, 403)
        finally:
            self.entra("categorie@example.com")

    def test_7_invito_alla_guida_ricordato(self):
        """Chiuso una volta, l'invito alla Guida resta chiuso: lo sa il server, non il browser."""
        self.assertEqual(self.c.get("/api/auth/me").json()["guide_chiuse"], [])
        r = self.c.put("/api/auth/me/guide", json={"guida": "benvenuto", "aperta": False})
        self.assertEqual((r.status_code, r.json()["guide_chiuse"]), (200, ["benvenuto"]))
        self.assertEqual(self.c.put("/api/auth/me/guide", json={"guida": "benvenuto", "aperta": False}).json()["guide_chiuse"],
                         ["benvenuto"])   # due volte: niente doppioni
        self.entra("categorie@example.com")   # nuova sessione, come da un altro browser
        self.assertEqual(self.c.get("/api/auth/me").json()["guide_chiuse"], ["benvenuto"])
        self.assertEqual(self.c.put("/api/auth/me/guide", json={"guida": "altro", "aperta": False}).status_code, 422)
        self.assertEqual(self.c.put("/api/auth/me/guide", json={"guida": "benvenuto", "aperta": True}).json()["guide_chiuse"], [])
        self.c.post("/api/auth/logout")
        self.assertEqual(self.c.put("/api/auth/me/guide", json={"guida": "benvenuto", "aperta": False}).status_code, 401)
        self.entra("categorie@example.com")


if __name__ == "__main__":
    unittest.main()
