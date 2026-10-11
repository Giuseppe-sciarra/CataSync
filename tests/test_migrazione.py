"""Un database della v1 (con le colonne e i dati del vecchio connettore a
catalogo) si aggiorna da solo al primo avvio, e una seconda passata non fa danni.
Gira in un processo a parte: il motore del database si lega al primo import."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import _ambiente

SCRIPT = r'''
import json, sqlite3, sys
percorso = sys.argv[1]
con = sqlite3.connect(percorso)
con.executescript("""
CREATE TABLE clients (id INTEGER PRIMARY KEY, name VARCHAR(160) NOT NULL, is_active BOOLEAN DEFAULT 1,
  icecat_lang VARCHAR(8) DEFAULT 'IT', icecat_username_enc TEXT DEFAULT '', icecat_api_token_enc TEXT DEFAULT '',
  icecat_content_token_enc TEXT DEFAULT '');
CREATE TABLE sites (id INTEGER PRIMARY KEY, client_id INTEGER, name VARCHAR(160), base_url VARCHAR(500));
CREATE TABLE providers (id INTEGER PRIMARY KEY, client_id INTEGER, name VARCHAR(120), kind VARCHAR(20), priority INTEGER);
CREATE TABLE category_map (id INTEGER PRIMARY KEY, site_id INTEGER, icecat_category VARCHAR(190),
  icecat_label VARCHAR(190), wc_term_id INTEGER, wc_label VARCHAR(190));
CREATE TABLE job_items (id INTEGER PRIMARY KEY, job_id INTEGER, gtin VARCHAR(20), status VARCHAR(20),
  icecat_id VARCHAR(40), source VARCHAR(60), error TEXT, normalized TEXT);
CREATE TABLE settings (key VARCHAR(80) PRIMARY KEY, value TEXT);
CREATE TABLE ean_cache (id INTEGER PRIMARY KEY, gtin VARCHAR(20), lang VARCHAR(16), found BOOLEAN, payload TEXT);
INSERT INTO clients (id, name, icecat_lang, icecat_username_enc) VALUES (1, 'Negozio', 'EN', 'cifrato');
INSERT INTO sites (id, client_id, name, base_url) VALUES (1, 1, 'Sito', 'https://example.com');
INSERT INTO providers (client_id, name, kind, priority) VALUES (1, 'Icecat', 'icecat', 10), (1, 'Web', 'web', 20);
INSERT INTO category_map (site_id, icecat_category, icecat_label, wc_term_id, wc_label) VALUES (1, 'pompe', 'Pompe', 12, 'Pompe');
INSERT INTO job_items (job_id, gtin, status, icecat_id, source, error, normalized) VALUES
  (1, '8059893356383', 'not_found', '12345', 'Icecat + Ricerca sul web',
   'Icecat: HTTP 400. · Ricerca sul web: già cercato di recente senza esito',
   '{"gtin": "8059893356383", "icecat_id": "12345", "warnings": ["Scheda Icecat in EN", "Altro avviso"]}');
INSERT INTO settings VALUES ('partner_mode', '1'), ('partner_username', 'x');
INSERT INTO ean_cache (gtin, lang, found, payload) VALUES ('8059893356383', 'IT', 0, '{}'), ('8059893356383', '_WEB2', 1, '{}');
""")
con.commit(); con.close()

from sqlalchemy import inspect, text
from app.db import Base, engine, ensure_columns, migra_dati, migra_schema
from app import models  # noqa: F401
for _ in range(2):          # la seconda passata deve essere innocua
    Base.metadata.create_all(bind=engine); migra_schema(); ensure_columns(); migra_dati()
ins = inspect(engine)
with engine.connect() as c:
    riga = c.execute(text("select source, error, normalized, source_ref from job_items")).first()
    print(json.dumps({
        "clients": sorted(x["name"] for x in ins.get_columns("clients")),
        "job_items": sorted(x["name"] for x in ins.get_columns("job_items")),
        "category_map": sorted(x["name"] for x in ins.get_columns("category_map")),
        "providers": [r[0] for r in c.execute(text("select kind from providers"))],
        "settings": sorted(r[0] for r in c.execute(text("select key from settings"))),
        "cache": [r[0] for r in c.execute(text("select lang from ean_cache"))],
        "lingua_cliente": c.execute(text("select content_lang from clients")).scalar(),
        "lingua_sito": c.execute(text("select content_lang from sites")).scalar(),
        "riga": list(riga),
    }))
'''


class Migrazione(unittest.TestCase):
    def test_database_v1(self):
        cartella = tempfile.mkdtemp(prefix="catasync-migr-")
        percorso = os.path.join(cartella, "v1.db")
        ambiente = dict(os.environ, DATABASE_URL=f"sqlite:///{percorso}", PYTHONPATH=_ambiente.RADICE)
        uscita = subprocess.run([sys.executable, "-c", SCRIPT, percorso], env=ambiente, cwd=_ambiente.RADICE,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(uscita.returncode, 0, uscita.stderr[-2000:])
        dati = json.loads(uscita.stdout.strip().splitlines()[-1])

        self.assertFalse([c for c in dati["clients"] if "icecat" in c])
        self.assertIn("content_lang", dati["clients"])
        self.assertIn("source_ref", dati["job_items"])
        self.assertIn("source_category", dati["category_map"])
        self.assertEqual(dati["providers"], ["web"])
        self.assertNotIn("partner_mode", dati["settings"])
        self.assertEqual(dati["cache"], ["_WEB2"])
        self.assertEqual(dati["lingua_cliente"], "EN")
        self.assertEqual(dati["lingua_sito"], "EN")          # i negozi esistenti restano nella loro lingua
        source, errore, normalizzata, ref = dati["riga"]
        self.assertEqual(source, "Ricerca sul web")
        self.assertNotIn("Icecat", errore)
        self.assertNotIn("icecat", normalizzata.lower())
        self.assertEqual(ref, "12345")


if __name__ == "__main__":
    unittest.main()
