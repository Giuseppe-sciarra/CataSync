"""2.7.2: registro anonimo delle ricerche, ricerca a giri, scheda di chironpro (prodotti
collegati, insegna, foto PrestaShop) e controllo d'andata e ritorno della traduzione.
Niente rete: LibreTranslate e le ricerche sono finti."""
import asyncio
import json
import re
import time
import unittest
from datetime import datetime, timedelta, timezone

import _ambiente  # noqa: F401
import httpx
from fastapi.testclient import TestClient

from app import providers, traduzione, web_search, web_source, worker
from app.main import app

EAN = "8801643654689"
CLIENTE = "Idraulica Riservata srl"
NEGOZIO = "https://negozio-riservato.example"
SITO = "Negozio riservato"
CARICO = "listino-segreto-ottobre"


def _diario(causa, ko=(), pagine=()):
    return json.dumps({
        "versione": 1, "gtin": EAN, "lingua": "IT", "causa": causa, "spiegazione": "x",
        "ricerche": [{"q": EAN, "via": "searxng", "ko": [list(k) for k in ko], "ok": ["google cse"]}],
        "pagine": [dict(p) for p in pagine], "risultati": 3, "ancorati": 1,
    })


class Registro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        from app.db import SessionLocal
        from app.models import Client, Job, JobItem, Site

        db = SessionLocal()
        cliente = Client(name=CLIENTE)
        sito = Site(client=cliente, name=SITO, base_url=NEGOZIO)
        job = Job(site=sito, label=CARICO, lang="FR")
        resync = Job(site=sito, label="automatico", kind="resync")
        domani = datetime.now(timezone.utc) + timedelta(hours=1)
        cls.righe = {
            "non_trovato": JobItem(job=job, gtin=EAN, status="not_found", diagnosi=_diario(
                "siti_bloccati", ko=[("brave", "Suspended: CAPTCHA"), ("startpage", "in pausa")],
                pagine=[{"dominio": "rexel.it", "url": "https://rexel.it/p", "esito": "bloccata",
                         "dettaglio": "protezione anti-bot"}])),
            "trovato": JobItem(job=job, gtin="8059893356383", status="ready", title=f"Pompa di {CLIENTE}",
                               diagnosi=_diario("trovato", pagine=[{"dominio": "sonepar.it", "url": "https://sonepar.it/p",
                                                                    "esito": "confermata"}])),
            "solo_prezzo": JobItem(job=job, gtin="5712608909306", status="incomplete",
                                   issues=json.dumps(["regular_price", "category"]), diagnosi=_diario("trovato")),
            "incompleto": JobItem(job=job, gtin="4048164001234", status="incomplete",
                                  issues=json.dumps(["images", "regular_price"]), diagnosi=_diario("trovato")),
            "rimandato": JobItem(job=job, gtin="3760188221049", status="pending", retry_after=domani,
                                 diagnosi=_diario("motori_bloccati", ko=[("duckduckgo", "Suspended: CAPTCHA")])),
            "errore": JobItem(job=job, gtin="8012345678907", status="error", diagnosi=_diario("trovato"),
                              error=f"WooCommerce {NEGOZIO}/wp-json: 401"),
            "resync": JobItem(job=resync, gtin="97924465", status="not_found", diagnosi=_diario("sconosciuto")),
        }
        db.add_all([cliente, sito, job, resync, *cls.righe.values()])
        db.commit()
        cls.ids = {k: v.id for k, v in cls.righe.items()}
        db.close()
        r = cls.c.post("/api/auth/login", json={"email": "admin@example.com", "password": "provaprova123"})
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def registro(self, esito="tutti"):
        r = self.c.get("/api/admin/ricerca/registro", params={"esito": esito})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_nessun_dato_del_cliente(self):
        r = self.registro()
        for segreto in (CLIENTE, NEGOZIO, "negozio-riservato", SITO, CARICO, "Pompa di", "401"):
            self.assertNotIn(segreto, r.text)
        chiavi = set().union(*(riga.keys() for riga in r.json()["righe"]))
        self.assertFalse(chiavi & {"client", "cliente", "site", "sito", "job", "label", "title", "error", "source"})
        csv = self.c.get("/api/admin/ricerca/registro.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertTrue(csv.headers["content-type"].startswith("text/csv"))
        for segreto in (CLIENTE, NEGOZIO, SITO, CARICO):
            self.assertNotIn(segreto, csv.text)
        self.assertIn(EAN, csv.text)

    def test_esiti_e_motivi(self):
        righe = {riga["id"]: riga for riga in self.registro().json()["righe"]}
        self.assertNotIn(self.ids["resync"], righe)            # i riallineamenti automatici non sono ricerche
        nt = righe[self.ids["non_trovato"]]
        self.assertEqual((nt["esito"], nt["causa"], nt["lingua"]), ("non_trovato", "siti_bloccati", "FR"))
        self.assertEqual([m["motore"] for m in nt["motori_bloccati"]], ["brave"])    # «in pausa» non è un blocco
        self.assertEqual(nt["siti_bloccati"], ["rexel.it"])
        self.assertEqual(righe[self.ids["trovato"]]["origine"], "sonepar.it")
        self.assertEqual(righe[self.ids["solo_prezzo"]]["esito"], "trovato")    # prezzo e categoria li mette il cliente
        self.assertEqual(righe[self.ids["incompleto"]]["mancano"], ["Almeno un'immagine"])
        self.assertEqual(righe[self.ids["rimandato"]]["esito"], "rimandato")
        self.assertTrue(righe[self.ids["rimandato"]]["riprova_alle"])
        self.assertEqual(righe[self.ids["errore"]]["causa"], "pubblicazione")

    def test_filtri_e_pagine(self):
        ids = lambda esito: {riga["id"] for riga in self.registro(esito).json()["righe"]}  # noqa: E731
        problemi = ids("problemi")
        self.assertTrue({self.ids[k] for k in ("non_trovato", "incompleto", "rimandato", "errore")} <= problemi)
        self.assertFalse({self.ids["trovato"], self.ids["solo_prezzo"]} & problemi)
        self.assertEqual(ids("incompleti") & set(self.ids.values()), {self.ids["incompleto"]})
        self.assertTrue({self.ids["trovato"], self.ids["solo_prezzo"]} <= ids("trovati"))
        self.assertEqual(self.c.get("/api/admin/ricerca/registro", params={"esito": "chi"}).status_code, 400)
        primo = self.c.get("/api/admin/ricerca/registro", params={"limit": 2}).json()
        self.assertEqual(len(primo["righe"]), 2)
        self.assertTrue(primo["altre"])
        dopo = self.c.get("/api/admin/ricerca/registro", params={"limit": 2, "prima_di": primo["prossimo"]}).json()
        self.assertTrue(all(riga["id"] < primo["prossimo"] for riga in dopo["righe"]))

    def test_diario(self):
        r = self.c.get(f"/api/admin/ricerca/registro/{self.ids['non_trovato']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["diagnosi"]["causa"], "siti_bloccati")
        self.assertNotIn(CLIENTE, r.text)

    def test_solo_amministratore(self):
        anonimo = TestClient(app)
        self.assertIn(anonimo.get("/api/admin/ricerca/registro").status_code, (401, 403))


class Giri(unittest.TestCase):
    def setUp(self):
        web_search.GIRO.update(codici=0, riposo_fino=0.0)

    tearDown = setUp

    def test_dopo_il_giro_la_pausa(self):
        extra = {"giro_codici": 2, "giro_pausa": 1}
        web_search.conta_codice(extra)
        self.assertFalse(web_search.fine_riposo_giro())
        web_search.conta_codice(extra)
        fine = web_search.fine_riposo_giro()
        self.assertGreater(fine, time.time() + 50)
        self.assertFalse(web_search.fine_riposo_giro(adesso=fine + 1))
        web_search.GIRO["riposo_fino"] = 0
        web_search.conta_codice({"giro_codici": 1, "giro_pausa": 0})     # pausa 0: niente riposo
        self.assertFalse(web_search.fine_riposo_giro())

    def test_il_worker_si_ferma_a_meta_lotto(self):
        from app.db import Base, SessionLocal, engine
        from app.models import Client, Job, JobItem, Site

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        cliente = Client(name="Giri srl")
        sito = Site(client=cliente, name="Giri", base_url="https://giri.example")
        job = Job(site=sito, label="giri", auto_push=False)
        righe = [JobItem(job=job, gtin=g) for g in ("8059893356383", "5712608909306", "97924465")]
        db.add_all([cliente, sito, job, *righe])
        db.commit()
        cercati = []

        async def fetch(_db, chi, gtin, _lang):
            if chi.name == "Giri srl":         # nel database dei test ci sono anche i carichi degli altri
                cercati.append(gtin)
                web_search.conta_codice({"giro_codici": 2, "giro_pausa": 5})
            raise providers.NoSourceFound("nessuno")

        originale, providers.fetch = providers.fetch, fetch
        try:
            asyncio.run(worker._tick())
            self.assertEqual(len(cercati), 2)                 # il terzo aspetta la fine della pausa
            asyncio.run(worker._tick())
            self.assertEqual(len(cercati), 2)
            db.refresh(job)
            self.assertNotEqual(job.status, "done")           # il carico non è finito
            web_search.GIRO["riposo_fino"] = 0
            asyncio.run(worker._tick())
            self.assertEqual(len(cercati), 3)
        finally:
            providers.fetch = originale
            db.close()


CHIRON = f"""<!doctype html><html lang="fr"><head>
<title>Unité extérieur bibloc Samsung</title>
<meta property="og:site_name" content="CHIRON Distribution">
<meta property="og:title" content="Unité extérieur bibloc Samsung">
<meta property="og:image" content="https://www.chironpro.com/50279-large_default/unite-exterieur-bibloc-samsung.jpg">
</head><body id="product">
<section id="main" itemscope itemtype="https://schema.org/Product">
  <h1 class="h1" itemprop="name">Unité extérieur bibloc Samsung</h1>
  <div class="ref"><span class="label">Marque</span> <a href="/brand/samsung">SAMSUNG</a></div>
  <div class="ref"><span class="label">Réf. fournisseur</span> <span>AE040RXEDEG/EU</span></div>
  <div class="ref"><span class="label">ean13</span> <span>{EAN}</span></div>
  <dl class="data-sheet"><dt class="name">Modèle</dt><dd class="value">bibloc</dd></dl>
  <section class="product-accessories">
    <article class="product-miniature" itemscope itemtype="http://schema.org/Product">
      <div itemprop="brand" itemscope itemtype="http://schema.org/Brand"><meta itemprop="name" content="INTUIS"></div>
      <meta itemprop="name" content="Pompe à chaleur HTI70 avec pilote et ballon de découplage Intuis">
      <meta itemprop="gtin13" content="3760188221049">
    </article>
  </section>
</section></body></html>"""


class SchedaChiron(unittest.TestCase):
    def test_non_prende_il_prodotto_collegato_ne_l_insegna(self):
        url = "https://www.chironpro.com/6864-34860-unite-exterieur-bibloc-samsung.html"
        scheda = web_source.estrai(CHIRON, url, EAN, consenti_estero=True, lingua="FR")
        self.assertIsNotNone(scheda)
        self.assertEqual(scheda["title"], "Unité extérieur bibloc Samsung")
        self.assertEqual((scheda.get("brand") or "").upper(), "SAMSUNG")
        self.assertNotIn("INTUIS", json.dumps(scheda).upper().replace("INTUIS.", ""))
        self.assertNotIn("CHIRON", (scheda.get("brand") or "").upper())
        self.assertEqual(scheda.get("mpn"), "AE040RXEDEG/EU")
        self.assertTrue(any("large_default" in u for u in scheda.get("images") or []))

    def test_foto_prestashop_non_sono_segnaposto(self):
        self.assertTrue(web_source._img_utile("https://shop.example/50279-large_default/unite.jpg"))
        self.assertFalse(web_source._img_utile("https://shop.example/img/p/it-default-large_default.jpg"))


DESCRIZIONE_IT = ("Unità esterna della pompa di calore aria-acqua Samsung EHS Split con refrigerante R32, "
                  "alimentazione monofase e potenza termica di 4,4 kW. Lavora insieme al modulo idronico interno "
                  "per riscaldamento, raffrescamento e acqua calda sanitaria, con compressore inverter e "
                  "funzionamento fino a meno venticinque gradi esterni.")


def _pagina_it(nome, descrizione=DESCRIZIONE_IT, foto=("a", "b", "c")):
    return ("<html lang='it'><head><script type='application/ld+json'>" + json.dumps({
        "@context": "https://schema.org", "@type": "Product", "name": nome, "mpn": "AE040RXEDEG",
        "brand": {"@type": "Brand", "name": "Samsung"}, "description": descrizione,
        "image": [f"https://negozio-clima.example/media/catalog/product/ae040-{f}.jpg" for f in foto]})
        + f"</script></head><body class='single-product'><h1>{nome}</h1></body></html>")


class SessioneFinta:
    PAGINE: dict = {}

    def __init__(self, *a, **k):
        self.browser_disponibile = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def apri(self, url, rendi_se_serve=True, ignora_robots=False):
        from app import rete
        return rete.Pagina(url=url, url_finale=url, stato=200, html=self.PAGINE.get(url, ""))


class Arricchimento(unittest.TestCase):
    """Scheda povera trovata con l'EAN (niente descrizione, una foto): si cerca il codice
    produttore e si completa con le pagine che lo riportano, scartando i kit."""

    CHIRON = "https://www.chironpro.com/6864-34860-unite-exterieur-bibloc-samsung.html"
    BUONA = "https://negozio-clima.example/prodotto/samsung-ae040rxedeg-eu"
    KIT = "https://altro-clima.example/prodotto/kit-ehs-split-4kw"

    def setUp(self):
        from app import rete
        self._sessione, rete.Sessione = rete.Sessione, SessioneFinta
        self._foto = web_source.verifica_foto

        async def foto_buone(urls, massimo=6, referer=""):
            return list(urls)[:massimo]

        web_source.verifica_foto = foto_buone
        SessioneFinta.PAGINE = {
            self.CHIRON: CHIRON,
            self.BUONA: _pagina_it("Samsung EHS Split R32 unità esterna monofase 4,4 kW AE040RXEDEG/EU"),
            self.KIT: _pagina_it("Pompa di calore Samsung AE040RXEDEG/EU + AE090RNYDEG/EU",
                                 descrizione="Kit completo con modulo idronico interno. " * 6, foto=("k",)),
        }
        self.domande = []

    def tearDown(self):
        from app import rete
        rete.Sessione = self._sessione
        web_source.verifica_foto = self._foto

    def candidato(self, url, titolo, ancorato=False):
        return {"url": url, "dominio": url.split("/")[2], "titolo": titolo, "punti": 100, "ancorato": ancorato,
                "lingua_url": "", "estero": False, "marketplace": False, "modello": ["ae040rxedeg"],
                "marche": ["samsung"]}

    def apri(self, altri=True):
        async def per_codice(codice):
            self.domande.append(codice)
            return [self.candidato(self.KIT, "Kit Samsung EHS split 4 kW"),
                    self.candidato(self.BUONA, "Samsung AE040RXEDEG/EU unità esterna")]

        return asyncio.run(web_source.apri_candidati(
            EAN, [self.candidato(self.CHIRON, "Unité extérieur bibloc Samsung", True)], lingua="IT",
            altri_candidati=per_codice if altri else None))

    def test_completa_col_codice_produttore(self):
        scheda = self.apri()
        self.assertEqual(self.domande, ["AE040RXEDEG/EU"])
        self.assertIn("venticinque gradi", scheda["description"])
        self.assertNotIn("Kit completo", scheda["description"])
        self.assertGreaterEqual(len(scheda["images"]), 3)
        self.assertFalse(any("ae040-k" in u for u in scheda["images"]))
        pagine = {p["url"]: p for p in scheda["_diagnosi"]["pagine"]}
        self.assertEqual(pagine[self.KIT]["esito"], "altro_prodotto")
        self.assertEqual(pagine[self.BUONA]["fase"], "codice produttore")
        self.assertEqual(pagine[self.BUONA]["esito"], "confermata")
        self.assertIn("codice produttore", scheda["_diagnosi"]["spiegazione"])

    def test_scheda_ricca_non_cerca_altro(self):
        SessioneFinta.PAGINE[self.CHIRON] = _pagina_it(f"Samsung AE040RXEDEG/EU {EAN}").replace(
            '"mpn"', f'"gtin13": "{EAN}", "mpn"')
        self.apri()
        self.assertEqual(self.domande, [])


REXEL = "https://rexel.it/prodotto/esterne-ehs-split-r32-monofase-4--1365849"
SONEPAR = "https://www.sonepar.it/catalog/it-it/products/samsung-smgae040rxedeg-eu-esterne-ehs-split-r32-monofase-4-0-kw-000000000001393004"
DESC = ("Scopri i vantaggi dell'unità esterna EHS SPLIT R32 di Samsung, progettata per garantire efficienza e "
        "facilità d'installazione. Installazione semplice e rapida grazie al design compatto. Risparmio energetico "
        "grazie al refrigerante R32 a basso impatto ambientale.")
REXEL_HTML = f"""<!doctype html><html lang="it"><head><title>ESTERNE EHS SPLIT R32 (MONOFASE) 4. | Rexel Italia</title>
<meta name="description" content="ESTERNE EHS SPLIT R32 (MONOFASE) 4. di SAMSUNG. Acquista online su Rexel.">
<meta property="og:title" content="ESTERNE EHS SPLIT R32 (MONOFASE) 4.">
<meta property="og:image" content="https://files.rexel.it/products/1365800/1365849/0_big.638487750400222438.jpg">
</head><body><header><img src="/img/logo-rexel.svg" alt="Rexel"></header>
<main><h1>ESTERNE EHS SPLIT R32 (MONOFASE) 4.</h1>
<a class="brand" href="/marche/samsung"><img src="https://files.rexel.it/brands/3500/3530/Image-1.637733363511509941.jpg" alt="SAMSUNG"></a>
<div class="codes"><div><span>Cod. Rexel</span> <span>SUAE040RXEDEG/E</span></div>
<div><span>Cod. Produttore</span> <span>AE040RXEDEG/EU</span></div>
<div><span>Cod. EAN</span> <span>{EAN}</span></div></div>
<div class="gallery"><a href="https://files.rexel.it/products/1365800/1365849/0_big.638487750400222438.jpg">
<img src="https://files.rexel.it/products/1365800/1365849/0.638487750400424209.jpg" alt="ESTERNE EHS SPLIT R32 (MONOFASE) 4."></a></div>
<section><h2>Informazioni di prodotto</h2><p>{DESC}</p>
<h3>Caratteristiche principali</h3><ul><li>Marca: Samsung Electronics</li><li>Tipo: Unità esterna EHS SPLIT</li>
<li>Refrigerante: R32</li><li>Configurazione: Monofase</li></ul>
<h3>Produttore</h3><p>SAMSUNG</p></section></main></body></html>"""
IMG = "https://ita-sonita.mo.cloudinary.net/PRODUCT/IMAGE/PictureServer/default.aspx/"
SONEPAR_HTML = f"""<!doctype html><html lang="it"><head><title>SAMSUNG - SMGAE040RXEDEG/EU ESTERNE EHS SPLIT R32 (MONOFASE) 4.0 KW | Sonepar</title>
<meta property="og:title" content="SAMSUNG - SMGAE040RXEDEG/EU ESTERNE EHS SPLIT R32 (MONOFASE) 4.0 KW - AE040RXEDEG/EU">
<meta name="keywords" content="2000001648001"></head><body>
<h1>SAMSUNG - SMGAE040RXEDEG/EU ESTERNE EHS SPLIT R32 (MONOFASE) 4.0 KW - AE040RXEDEG/EU</h1>
<div class="gallery"><img src="{IMG}esterne_ehs_split_r32_(monofase)_4.0_kw.jpg?8475&amp;ts=1" alt="ESTERNE EHS SPLIT R32 (MONOFASE) 4.0 KW">
<img src="{IMG}Ext_LD.jpg?8475&amp;ts=1" alt="ESTERNE EHS SPLIT R32 (MONOFASE) 4.0 KW"></div>
<img src="https://ita-sonita.mo.cloudinary.net/BRAND/IMAGE/files/brand-logo/samsung.png?ts=1&amp;type=bh32" alt="SAMSUNG">
<h2>Dettaglio prodotto</h2><dl><dt>Confezione</dt><dd>1 PZ</dd><dt>EAN</dt><dd>{EAN}</dd>
<dt>Codice prodotto</dt><dd>SMGAE040RXEDEG/EU</dd><dt>Cod. produttore</dt><dd>AE040RXEDEG/EU</dd><dt>Produttore</dt><dd>SMG</dd></dl>
<h2>Descrizione estesa</h2><p>GAMMA POMPE DI CALORE (EHS) - Unita esterna monofase, R32, 5 kW potenza freddo, 4.4 kW potenza caldo</p>
</body></html>"""



class GrossistiItaliani(Arricchimento):
    """Il caso vero di 8801643654689: chironpro (francese, senza descrizione, una foto) col
    codice EAN; Rexel e Sonepar (italiani, senza dati strutturati, «Produttore: SMG») col
    codice produttore. Deve uscire una scheda italiana con descrizione e più foto."""

    def setUp(self):
        super().setUp()
        SessioneFinta.PAGINE.update({REXEL: REXEL_HTML, SONEPAR: SONEPAR_HTML})

    def apri(self, altri=True):
        async def per_codice(codice):
            self.domande.append(codice)
            return [self.candidato(REXEL, "ESTERNE EHS SPLIT R32"), self.candidato(SONEPAR, "SAMSUNG - SMGAE040RXEDEG/EU")]

        return asyncio.run(web_source.apri_candidati(
            EAN, [self.candidato(self.CHIRON, "Unité extérieur bibloc Samsung", True)], lingua="IT",
            altri_candidati=per_codice))

    def test_completa_col_codice_produttore(self):
        scheda = self.apri()
        self.assertEqual(self.domande, ["AE040RXEDEG/EU"])
        self.assertIn("refrigerante R32", scheda["description"])
        self.assertEqual(scheda["title"], "Esterne EHS split R32 (monofase) 4.")
        self.assertTrue(any("cloudinary" in u for u in scheda["images"]))       # le foto di Sonepar
        self.assertTrue(any("rexel" in u for u in scheda["images"]))
        self.assertGreaterEqual(len(scheda["images"]), 3)
        esiti = {p["dominio"].removeprefix("www."): p["esito"] for p in scheda["_diagnosi"]["pagine"]}
        self.assertEqual(esiti, {"chironpro.com": "confermata", "rexel.it": "confermata", "sonepar.it": "confermata"})
        nomi = {v["name"].lower() for v in scheda["attributes"]}
        self.assertFalse(nomi & {"cod. produttore", "produttore", "marca"})
        self.assertNotIn("SMG", scheda["brand"])

    test_scheda_ricca_non_cerca_altro = None


class ConIlBrowser(Arricchimento):
    """Pagina giusta ma povera letta senza JavaScript: si riapre con Chrome e si tiene la
    lettura più ricca (descrizione nella linguetta caricata dopo)."""

    def setUp(self):
        super().setUp()
        self.rese = []
        test = self

        class ConBrowser(SessioneFinta):
            def __init__(inner, *a, **k):
                super().__init__(*a, **k)
                inner.browser_disponibile = True

            async def browser(inner, url):
                from app import rete
                test.rese.append(url)
                html = SessioneFinta.PAGINE.get(url, "")
                if url == test.CHIRON:
                    html = html.replace("</section></body>", "<div class='tab-pane' id='description'><p>"
                                        + DESCRIZIONE_IT + "</p></div></section></body>")
                return rete.Pagina(url=url, url_finale=url, stato=200, html=html, via_browser=True)

        from app import rete
        rete.Sessione = ConBrowser

    def test_riapre_la_pagina_povera(self):
        scheda = self.apri(altri=False)
        self.assertEqual(self.rese, [self.CHIRON])
        self.assertIn("venticinque gradi", scheda["description"])

    test_completa_col_codice_produttore = test_scheda_ricca_non_cerca_altro = None


class Dettagli(unittest.TestCase):
    def test_sigle_e_foto_da_script(self):
        self.assertTrue(web_source._sigla_di("SMG", "Samsung"))
        self.assertFalse(web_source._sigla_di("LG", "Samsung"))
        self.assertTrue(web_source._img_utile(IMG + "Ext_LD.jpg?84&ts=1"))
        self.assertFalse(web_source._img_utile("https://shop.example/img/default.jpg"))
        self.assertEqual(web_source._titolo_leggibile("VALVOLA A SFERA INOX 1/2 F/F DN15", ""),
                         "Valvola a sfera inox 1/2 F/F DN15")
        self.assertEqual(web_source._titolo_leggibile("Grundfos ALPHA2 25-60", "Grundfos"), "Grundfos ALPHA2 25-60")

    def test_scheda_povera_si_tiene_poco(self):
        from app import providers
        from app.db import Base, SessionLocal, engine
        from app.models import EanCache

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        chiamate = []

        async def cerca(gtin, *_a, **_k):
            chiamate.append(gtin)
            return {"title": "Unità", "description": "", "images": ["https://x.example/1.jpg"]}

        originale, web_source.cerca_automatica = web_source.cerca_automatica, cerca
        try:
            asyncio.run(providers._web_con_cache(db, "4006381333931", "IT"))
            asyncio.run(providers._web_con_cache(db, "4006381333931", "IT"))
            self.assertEqual(len(chiamate), 1)                 # appena trovata: dalla cache
            riga = db.scalar(providers.select(EanCache).where(EanCache.gtin == "4006381333931",
                                                              EanCache.lang == providers.chiave_cache("IT")))
            riga.fetched_at = datetime.now(timezone.utc) - timedelta(days=3)
            db.commit()
            dati = asyncio.run(providers._web_con_cache(db, "4006381333931", "IT"))
            self.assertEqual(len(chiamate), 2)                 # povera e vecchia di 3 giorni: si ricerca
            self.assertNotIn("_povera", dati)
        finally:
            web_source.cerca_automatica = originale
            db.close()


class LibreTranslateFinto:
    """Traduce parola per parola, e come quello vero storpia «bibloc» in «Bilancio»."""

    PAROLE = {("fr", "it"): {"Unité": "Unità", "extérieur": "esterna", "bibloc": "Bilancio", "Modèle": "Modello",
                             "Pompe": "Pompa", "chaleur": "calore"},
              ("it", "fr"): {"Unità": "Unité", "esterna": "extérieur", "Bilancio": "Bilan", "Modello": "Modèle",
                             "Pompa": "Pompe", "calore": "chaleur"}}

    salta_in_frase = ()      # parole che, dentro una frase, lascia com'erano (come fa Argos)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        dati = json.loads(request.content)
        if request.url.path.endswith("/detect"):
            testo = dati["q"]
            lingua = "fr" if re.search(r"Unité|extérieur|Modèle|bibloc|chaleur", testo) else "it"
            return httpx.Response(200, json=[{"language": lingua, "confidence": 90}])
        voci = self.PAROLE.get((dati["source"], dati["target"]), {})

        def una(testo: str) -> str:
            da_saltare = self.salta_in_frase if len(testo.split()) > 1 else ()
            return re.sub(r"[^\W\d_]+", lambda m: m.group(0) if m.group(0) in da_saltare
                          else voci.get(m.group(0), m.group(0)), testo)

        return httpx.Response(200, json={"translatedText": [una(t) for t in dati["q"]]})


class AndataERitorno(unittest.TestCase):
    def setUp(self):
        originale = httpx.AsyncClient
        self._client = originale

        self.finto = LibreTranslateFinto()
        finto = self.finto

        class Client(originale):
            def __init__(inner, *a, **k):
                k["transport"] = httpx.MockTransport(finto)
                super().__init__(*a, **k)

        traduzione.httpx.AsyncClient = Client

    def tearDown(self):
        traduzione.httpx.AsyncClient = self._client

    def test_le_parole_storpiate_restano_com_erano(self):
        scheda = {"title": "Unité extérieur bibloc Samsung", "description": "Pompe à chaleur",
                  "attributes": [{"name": "Modèle", "value": "bibloc"}]}
        self.assertTrue(asyncio.run(traduzione.traduci_scheda(scheda, "http://lt.example", verso="it")))
        self.assertEqual(scheda["title"], "Unità esterna bibloc Samsung")
        self.assertNotIn("Bilancio", json.dumps(scheda))
        self.assertEqual(scheda["attributes"], [{"name": "Modello", "value": "bibloc"}])
        self.assertIn("calore", scheda["description"])

    def test_parola_lasciata_in_francese(self):
        self.finto.salta_in_frase = ("extérieur",)
        scheda = {"title": "Unité extérieur bibloc Samsung", "description": "", "attributes": []}
        self.assertTrue(asyncio.run(traduzione.traduci_scheda(scheda, "http://lt.example", verso="it")))
        self.assertEqual(scheda["title"], "Unità esterna bibloc Samsung")


if __name__ == "__main__":
    unittest.main()
