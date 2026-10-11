import json
import unittest

import _ambiente  # noqa: F401
from app import web_source as w

EAN = "8059893356383"

CATEGORIA = f"""<html><head><title>Sistema pressurizzazione Archivi - Negozio</title></head>
<body class="archive tax-product_cat woocommerce">
<ul class="products">
<li class="product type-product post-1"><a href="https://negozio.example/prodotto/altra-pompa/">Altra</a>
  <div>EAN 8012345678907</div></li>
<li class="product type-product post-2">
  <a href="https://negozio.example/prodotto/dab-esybox-pop/"><img src="/x.jpg"></a>
  <h2><a href="https://negozio.example/prodotto/dab-esybox-pop/">DAB Esybox Pop</a></h2>
  <div><span>Codice EAN</span> {EAN}</div>
  <a href="https://negozio.example/?add-to-cart=2">Aggiungi al carrello</a></li>
</ul></body></html>"""

SCHEDA = f"""<html lang="it"><head><title>DAB Esybox Pop | Negozio</title>
<script type="application/ld+json">{json.dumps({"@context": "https://schema.org", "@type": "Product",
  "name": "DAB", "gtin13": EAN, "brand": {"@type": "Brand", "name": "DAB"},
  "description": "Il sistema di pressurizzazione mantiene costante la pressione dell'acqua in casa, "
                 "con motore raffreddato dal liquido e inverter integrato per il risparmio energetico.",
  "image": "https://negozio.example//negozio.example/cdn/esybox.jpg"})}</script>
<meta property="og:image" content="https://negozio.example/cdn/esybox-og.jpg"></head>
<body class="single-product"><h1>DAB Esybox Pop sistema di pressurizzazione 0,4 kW</h1></body></html>"""


class PagineDiElenco(unittest.TestCase):
    def test_categoria_riconosciuta_e_link_seguito(self):
        url = "https://negozio.example/categoria-prodotto/pressurizzazione/"
        self.assertTrue(w.pagina_elenco(CATEGORIA, url))
        self.assertEqual(w.link_dal_elenco(CATEGORIA, url, EAN), "https://negozio.example/prodotto/dab-esybox-pop/")
        motivo: list[str] = []
        self.assertIsNone(w.estrai(CATEGORIA, url, EAN, motivo=motivo, consenti_estero=True))
        self.assertIn("categoria", motivo[0])

    def test_scheda_non_e_un_elenco(self):
        self.assertEqual(w.pagina_elenco(SCHEDA, "https://negozio.example/prodotto/dab-esybox-pop/"), "")

    def test_tanti_ean_diversi_sono_un_listino(self):
        righe = "".join(f"<tr><td>Articolo {i}</td><td>{c}</td></tr>" for i, c in enumerate(
            ["8012345678907", "5712608909306", "5710626495436", "4005176364778", "8021183018585"]))
        html = f"<html><body class='homePage'><table>{righe}</table></body></html>"
        self.assertTrue(w.pagina_elenco(html, "https://x.example/famiglia.htm"))


class Scheda(unittest.TestCase):
    def test_estrai(self):
        scheda = w.estrai(SCHEDA, "https://negozio.example/prodotto/dab-esybox-pop/", EAN, consenti_estero=True)
        self.assertIsNotNone(scheda)
        # nome «povero» (solo la marca) nei dati strutturati: vince l'<h1>
        self.assertTrue(scheda["title"].startswith("DAB Esybox Pop"))
        self.assertEqual(scheda["conferma"], "dichiarato")
        self.assertEqual(scheda["lingua_pagina"], "IT")
        # indirizzo della foto rotto e riparato, più quella di Open Graph
        self.assertIn("https://negozio.example/cdn/esybox.jpg", scheda["images"])
        self.assertIn("https://negozio.example/cdn/esybox-og.jpg", scheda["images"])

    def test_altro_codice_dichiarato(self):
        motivo: list[str] = []
        self.assertIsNone(w.estrai(SCHEDA, "https://negozio.example/p/", "5712608909306", motivo=motivo))
        self.assertIn("altro codice", motivo[0])

    def test_ripara_url_e_varianti(self):
        self.assertEqual(w.ripara_url("https://a.it//a.it/cdn/x.jpg?v=1"), "https://a.it/cdn/x.jpg?v=1")
        titolo = "Scaldasalviette Cordivari Termoarredo Lisa 22 1160X450 Int. 400 Acciaio cromato"
        self.assertTrue(w._variante_diversa(
            "https://x.it/media/316x316-scaldasalviette-cordivari-termoarredo-lisa-22-700x400-int-350-bianco.jpg", titolo))
        self.assertFalse(w._variante_diversa(
            "https://x.it/media/640x640-scaldasalviette-cordivari-termoarredo-lisa-22-1160x450-int-400-acciaio-cromato.jpg",
            titolo))


if __name__ == "__main__":
    unittest.main()
