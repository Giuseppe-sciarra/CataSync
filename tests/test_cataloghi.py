"""Cataloghi diretti (idealo, eBay, geizhals…): candidati quando i motori non danno pagine col
codice o sono bloccati; link della scheda dalla pagina di ricerca; pausa a chi ci respinge;
rimando se, coi motori bloccati, nemmeno i cataloghi cavano niente."""
import asyncio
import time
import unittest

import _ambiente  # noqa: F401

from app import web_search, web_source

EAN = "8059893356383"


class Candidati(unittest.TestCase):
    def setUp(self):
        web_search.PAUSE.clear()
        self._pausa, web_search.config.RICERCA_PAUSA = web_search.config.RICERCA_PAUSA, 0

    def tearDown(self):
        web_search.config.RICERCA_PAUSA = self._pausa

    def test_per_lingua_e_fuori_chi_e_in_pausa(self):
        domini = [c["dominio"] for c in web_search.candidati_cataloghi(EAN, "IT")]
        self.assertEqual(domini[0], "go-upc.com")                                    # prima gli archivi EAN
        self.assertIn("idealo.it", domini)
        self.assertIn("google.com", domini)
        self.assertEqual(domini[-1], "geizhals.eu")
        for vietato in ("ebay.it", "trovaprezzi.it", "manomano.it"):                # robots.txt: solo con l'interruttore
            self.assertNotIn(vietato, domini)
        forzati = web_search.candidati_cataloghi(EAN, "IT", forza=True)
        ebay = next(c for c in forzati if c["dominio"] == "ebay.it")
        self.assertTrue(ebay["forza_robots"] and ebay["marketplace"] and ebay["da_catalogo"] == r"/itm/")
        self.assertNotIn("amazon.it", [c["dominio"] for c in forzati])
        web_search.metti_in_pausa("idealo.it", "accesso negato")
        self.assertNotIn("idealo.it", [c["dominio"] for c in web_search.candidati_cataloghi(EAN, "IT")])
        self.assertEqual(web_search.candidati_cataloghi("AE040RXEDEG", "IT"), [])      # non è un EAN
        google = next(c for c in web_search.candidati_cataloghi(EAN, "DE") if c["dominio"] == "google.com")
        self.assertTrue(google["catalogo_esterni"] and not google["marketplace"] and "hl=de" in google["url"])
        diretto = web_search.candidati_cataloghi(EAN, "DE")[0]
        self.assertEqual((diretto["da_catalogo"], diretto["marketplace"]), ("diretto", True))

    def test_segna_catalogo_mette_in_pausa(self):
        web_search.segna_catalogo("idealo.it", False, "accesso negato")
        self.assertTrue(web_search.fine_pausa("idealo.it"))
        web_search.segna_catalogo("idealo.it", True)
        self.assertFalse(web_search.fine_pausa("idealo.it"))
        self.assertIn("idealo.it", [v["motore"] for v in web_search.stato_motori()])

    def test_in_coda_quando_i_motori_non_hanno_il_codice(self):
        async def finto(query, chiave, extra, lingua):
            return web_search.Risposta(risultati=[{"url": "https://www.negozio.it/p/esybox", "titolo": "DAB Esybox Mini 3",
                                                   "testo": "", "motori": ["google"]}], ok=["google"])
        self._f, web_search.MOTORI["searxng"]["funzione"] = web_search.MOTORI["searxng"]["funzione"], finto
        try:
            trovati = asyncio.run(web_search.candidati(EAN, "searxng", "", {"ripiego_ddg": False, "giro_pausa": 0}, "IT"))
        finally:
            web_search.MOTORI["searxng"]["funzione"] = self._f
        self.assertEqual(trovati[0]["dominio"], "negozio.it")
        cataloghi = [c["dominio"] for c in trovati if c.get("da_catalogo")]
        self.assertEqual(cataloghi[0], "go-upc.com")
        self.assertIn("idealo.it", cataloghi)
        self.assertFalse(any(c.get("motori_bloccati") for c in trovati))

    def test_motori_bloccati_restano_i_cataloghi(self):
        async def bloccato(query, chiave, extra, lingua):
            raise web_search.MotoriBloccati("CAPTCHA", [("google", "CAPTCHA")])
        self._f, web_search.MOTORI["searxng"]["funzione"] = web_search.MOTORI["searxng"]["funzione"], bloccato
        try:
            trovati = asyncio.run(web_search.candidati(EAN, "searxng", "", {"ripiego_ddg": False, "giro_pausa": 0}, "IT"))
        finally:
            web_search.MOTORI["searxng"]["funzione"] = self._f
        self.assertTrue(trovati and all(c.get("da_catalogo") and c["motori_bloccati"]["nomi"] == ["google (CAPTCHA)"] for c in trovati))


class Link(unittest.TestCase):
    def test_primo_link_fatto_come_una_scheda(self):
        html = ('<a href="/sch/i.html?_nkw=x">cerca</a><a href="/help">aiuto</a>'
                f'<div class="s-item"><a href="https://www.ebay.it/itm/123456?hash=abc#x">DAB Esybox</a></div>')
        self.assertEqual(web_source.link_scheda_catalogo(html, f"https://www.ebay.it/sch/i.html?_nkw={EAN}", r"/itm/"),
                         "https://www.ebay.it/itm/123456?hash=abc")
        self.assertEqual(web_source.link_scheda_catalogo('<a href="https://altro.it/itm/1">x</a>', "https://www.ebay.it/sch", r"/itm/"), "")


    def test_link_esterni_della_vetrina(self):
        html = ('<a href="/search?tbm=shop&q=x">altro</a>'
                '<a href="https://www.google.com/url?q=https://www.negozio.it/p/esybox&amp;sa=U">A</a>'
                '<a href="https://www.google.com/aclk?adurl=https://www.rexel.it/prodotto/1&amp;x=1">B</a>'
                '<a href="https://www.negozio.it/p/esybox?utm=2">doppione</a>'
                '<a href="https://www.youtube.com/watch?v=1">video</a>'
                '<a href="https://www.ebay.it/itm/5">eBay</a>')
        self.assertEqual(web_source.link_esterni_catalogo(html, "https://www.google.com/search?tbm=shop&q=1"),
                         ["https://www.negozio.it/p/esybox", "https://www.rexel.it/prodotto/1", "https://www.ebay.it/itm/5"])


class Rimando(unittest.TestCase):
    """Motori bloccati, cataloghi a vuoto: MotoriBloccati, non «non trovato»."""

    def test_cataloghi_a_vuoto_sotto_blocco(self):
        async def soli_cataloghi(gtin, *a, **k):
            fuori = web_search.candidati_cataloghi(gtin, "IT")
            for c in fuori:
                c["motori_bloccati"] = {"nomi": ["google (CAPTCHA)"], "ko": [["google", "CAPTCHA"]], "riprova_alle": None}
            return fuori

        async def niente(gtin, trovati, *a, **k):
            err = web_source.NonTrovato("pagine respinte")
            err.diagnosi = {"causa": "siti_bloccati", "pagine": []}
            raise err

        self._c, web_search.candidati = web_search.candidati, soli_cataloghi
        self._a, web_source.apri_candidati = web_source.apri_candidati, niente
        try:
            with self.assertRaises(web_search.MotoriBloccati) as ctx:
                asyncio.run(web_source.cerca_automatica(EAN, "searxng", "", {}, lingua="IT"))
        finally:
            web_search.candidati, web_source.apri_candidati = self._c, self._a
        self.assertEqual(ctx.exception.ko, [("google", "CAPTCHA")])
        self.assertEqual(ctx.exception.diagnosi["causa"], "siti_bloccati")


if __name__ == "__main__":
    unittest.main()
