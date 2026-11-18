"""Prezzi ritoccati dal cliente nel portale su prodotti già sul sito: arrivano a WooCommerce.

Prima non succedeva in tre casi: la riga era nata dal giro notturno (che ha i prezzi su «Mai»),
la scheda era stata corretta da «Modifica» o i prezzi incollati con «Prezzi in blocco» (la riga
restava «Sul sito» e non si ripubblicava). Le righe notturne, poi, nascevano senza prezzo: in
Prodotti il prezzo sembrava sparito."""
import asyncio
import json
import unittest

import _ambiente  # noqa: F401  (prima dell'app)

from fastapi.testclient import TestClient

from app import scheduler, wc_rest
from app.db import SessionLocal
from app.main import app
from app.models import Job, JobItem, Site
from app.store import set_setting

EAN = "8059893356383"
SCHEDA = json.dumps({"gtin": EAN, "title": "DAB Esybox Mini 3", "description": "Gruppo di pressurizzazione",
                     "images": ["https://negozio.example/wp-content/uploads/esybox.jpg"]})


class PrezziDelCliente(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._req = wc_rest._request
        cls.scritti = []

        async def woo(site, method, path, params=None, json_body=None, base=None):
            if method == "GET" and path == "/products" and EAN in (params or {}).values():
                return [{"id": 500, "name": "DAB Esybox Mini 3", "regular_price": "100.00", "sale_price": "",
                         "categories": [{"id": 15}], "images": [{"src": "https://negozio.example/x.jpg"}],
                         "description": "Gruppo", "short_description": "", "global_unique_id": EAN,
                         "sku": "", "attributes": [], "meta_data": []}], {}
            if method == "GET":
                return [], {"X-WP-Total": "0", "X-WP-TotalPages": "0"}
            if method == "PUT":
                cls.scritti.append(json_body)
                return {"id": 500, "images": []}, {}
            return {}, {}
        wc_rest._request = woo
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        cls.entra("admin@example.com")
        c = cls.c
        cid = c.post("/api/admin/clients", json={"name": "Prezzi Srl", "content_lang": "IT"}).json()["id"]
        cls.sid = c.post("/api/admin/sites", json={"client_id": cid, "name": "Negozio prezzi",
                                                   "base_url": "https://negozio.example",
                                                   "wc_key": "ck_x", "wc_secret": "cs_x"}).json()["id"]
        c.post("/api/admin/users", json={"email": "prezzi@example.com", "password": "provaprova123",
                                         "role": "client", "client_id": cid})

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        wc_rest._request = cls._req

    @classmethod
    def entra(cls, email):
        cls.c.post("/api/auth/logout")
        r = cls.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"})
        assert r.status_code == 200, r.text

    def riga(self, kind: str, **campi) -> int:
        db = SessionLocal()
        job = Job(site_id=self.sid, label=kind, kind=kind, status="done")
        db.add(job)
        db.flush()
        item = JobItem(job_id=job.id, gtin=EAN, normalized=SCHEDA, title="DAB Esybox Mini 3",
                       **{"status": "pushed", "wc_product_id": 500, **campi})
        db.add(item)
        db.commit()
        iid = item.id
        db.close()
        return iid

    def stato(self, iid):
        db = SessionLocal()
        r = db.get(JobItem, iid)
        db.close()
        return r

    def test_riga_del_notturno_eredita_e_pubblica_il_prezzo(self):
        self.riga("manual", regular_price="100.00", wc_category_id=15)
        db = SessionLocal()
        site = db.get(Site, self.sid)
        site.resync_min_age_days = 0          # il prodotto è appena stato pubblicato: lo si ripassa subito
        job, perche = asyncio.run(scheduler.build_job(db, site))
        self.assertIsNotNone(job, perche)
        notturna = db.query(JobItem).filter(JobItem.job_id == job.id).one()
        self.assertEqual((notturna.regular_price, notturna.wc_category_id), ("100.00", 15))  # non sparisce più
        notturna.status, notturna.normalized, notturna.wc_product_id = "pushed", SCHEDA, 500
        db.commit()
        nid = notturna.id
        db.close()

        self.entra("prezzi@example.com")
        riga = next(r for r in self.c.get(f"/api/sites/{self.sid}/catalogo").json()["righe"] if r["gtin"] == EAN)
        self.assertEqual((riga["item_id"], riga["regular_price"]), (nid, "100.00"))
        r = self.c.post(f"/api/sites/{self.sid}/catalogo/bulk", json={"item_ids": [nid], "regular_price": "120",
                                                                      "sale_price": "", "category_id": 15})
        self.assertEqual(r.json()["aggiornate"], 1)
        self.assertEqual(self.stato(nid).status, "ready")
        d = self.c.post(f"/api/items/{nid}/pubblica").json()
        self.assertEqual(d["stato"], "pushed", d)
        self.assertEqual(self.scritti[-1].get("regular_price"), "120.00")   # prima: assente (notturno = «Mai»)

    def test_modifica_e_prezzi_in_blocco_ripubblicano(self):
        iid = self.riga("manual", regular_price="100.00", wc_category_id=15)
        self.entra("prezzi@example.com")
        r = self.c.put(f"/api/items/{iid}", json={"title": "DAB Esybox Mini 3", "description": "Gruppo",
                                                  "images": ["https://negozio.example/x.jpg"],
                                                  "category_id": 15, "regular_price": "130"})
        self.assertEqual(r.json()["status"], "ready", r.text)               # prima restava «pushed»

        iid2 = self.riga("manual", regular_price="100.00", wc_category_id=15)
        job_id = self.stato(iid2).job_id
        r = self.c.post(f"/api/jobs/{job_id}/prices", json={"text": f"{EAN} 140"})
        self.assertEqual(r.json()["updated"], 1)
        self.assertEqual(self.stato(iid2).status, "ready")                  # prima restava «pushed»
        d = self.c.post(f"/api/items/{iid2}/pubblica").json()
        self.assertEqual((d["stato"], self.scritti[-1].get("regular_price")), ("pushed", "140.00"))

    def test_prezzi_su_mai_non_partono_e_lo_dice(self):
        db = SessionLocal()
        site = db.get(Site, self.sid)
        prima = site.write_policy
        site.write_policy = json.dumps({"prices": "skip"})
        db.commit()
        db.close()
        try:
            iid = self.riga("manual", regular_price="100.00", wc_category_id=15)
            self.entra("prezzi@example.com")
            self.c.post(f"/api/sites/{self.sid}/catalogo/bulk", json={"item_ids": [iid], "regular_price": "150"})
            d = self.c.post(f"/api/items/{iid}/pubblica").json()
            self.assertNotIn("regular_price", self.scritti[-1])
            self.assertIn("«Mai»", d["errore"])
        finally:
            db = SessionLocal()
            db.get(Site, self.sid).write_policy = prima
            db.commit()
            db.close()

    def test_righe_notturne_vecchie_riprendono_il_prezzo(self):
        self.riga("manual", regular_price="99.00", sale_price="89.00", wc_category_id=15)
        vuota = self.riga("resync")
        db = SessionLocal()
        set_setting(db, "notturno_prezzi_ripresi", "0")
        db.commit()
        self.assertGreaterEqual(scheduler.riprendi_dati_cliente(db), 1)
        self.assertEqual(scheduler.riprendi_dati_cliente(db), 0)             # una volta sola
        db.close()
        r = self.stato(vuota)
        self.assertEqual((r.regular_price, r.sale_price, r.wc_category_id), ("99.00", "89.00", 15))


if __name__ == "__main__":
    unittest.main()
