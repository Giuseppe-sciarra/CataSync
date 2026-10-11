import unittest

import _ambiente  # noqa: F401
from app import web_search


class Domande(unittest.TestCase):
    def test_searxng_prima_nella_lingua_poi_ovunque(self):
        self.assertEqual(web_search.formulazioni("8059893356383", "IT", "searxng"),
                         [("8059893356383", "IT"), ("8059893356383", ""), ('"8059893356383"', "")])
        self.assertEqual(web_search.formulazioni("8059893356383", "EN", "brave")[0], ("8059893356383", "EN"))

    def test_codice_produttore_fra_virgolette(self):
        self.assertEqual(web_search.formulazioni("97924465", "IT", "searxng")[0], ('"97924465"', "IT"))

    def test_parametri_di_tracciamento(self):
        self.assertEqual(web_search.senza_tracciamento("https://x.it/p?id=5&utm_source=g&srsltid=AU7g"),
                         "https://x.it/p?id=5")
        self.assertEqual(web_search.senza_tracciamento("https://x.it/p"), "https://x.it/p")

    def test_motivi_dei_motori(self):
        self.assertEqual(web_search._motivo_leggibile("Suspended: CAPTCHA"), "CAPTCHA")
        self.assertEqual(web_search._motivo_leggibile("Suspended: access denied"), "accesso negato")
        self.assertEqual(web_search._motivo_leggibile("Suspended: too many requests"), "troppe richieste")

    def test_prima_le_pagine_col_codice(self):
        risultati = [
            {"url": "https://www.espn.com/nfl", "titolo": "NFL Scores", "testo": "", "motori": ["bing"]},
            {"url": "https://www.negozio.it/p/dab-esybox", "titolo": "DAB Esybox Pop 8059893356383",
             "testo": "EAN 8059893356383", "motori": ["yandex"]},
        ]
        ordinati = web_search.ordina(risultati, "8059893356383", "IT")
        self.assertIn("negozio.it", ordinati[0]["url"])


if __name__ == "__main__":
    unittest.main()
