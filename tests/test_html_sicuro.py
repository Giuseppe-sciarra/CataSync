import unittest

import _ambiente  # noqa: F401
from app.html_sicuro import pulisci_html, scheda_sicura, svg_attivo, testo_puro, tipo_immagine


class Descrizioni(unittest.TestCase):
    def test_testo_semplice_resta_com_e(self):
        self.assertEqual(pulisci_html("Pompa R&D, 5 bar"), "Pompa R&D, 5 bar")

    def test_via_script_eventi_iframe_link(self):
        sporco = ('<p onclick="x()">Pressurizzazione <b>costante</b></p><script>alert(1)</script>'
                  '<img src=x onerror=alert(2)><iframe src="//evil"></iframe><a href="javascript:x">testo</a>')
        pulito = pulisci_html(sporco)
        for vietato in ("<script", "onclick", "onerror", "<iframe", "<img", "<a ", "javascript:"):
            self.assertNotIn(vietato, pulito)
        self.assertIn("<p>Pressurizzazione <b>costante</b></p>", pulito)
        self.assertIn("testo", pulito)

    def test_tag_non_chiusi_e_testo_con_minore(self):
        self.assertEqual(pulisci_html("<p>non chiuso <strong>grassetto"), "<p>non chiuso <strong>grassetto</strong></p>")
        self.assertEqual(pulisci_html("<p>Pressione <5 bar & oltre</p>"), "<p>Pressione &lt;5 bar &amp; oltre</p>")

    def test_nomi_e_specifiche_in_testo_puro(self):
        self.assertEqual(testo_puro("Pompa &lt;script&gt;x&lt;/script&gt; <b>DAB</b>"), "Pompa x DAB")
        scheda = scheda_sicura({"title": "<img src=x onerror=1>Nome", "description": "<p onclick=1>ok</p>",
                                "attributes": [{"name": "<i>Peso</i>", "value": "5 kg"}],
                                "images": ["javascript:alert(1)", "https://example.com/a.jpg"]})
        self.assertEqual(scheda["title"], "Nome")
        self.assertEqual(scheda["description"], "<p>ok</p>")
        self.assertEqual(scheda["attributes"][0]["name"], "Peso")
        self.assertEqual(scheda["images"], ["https://example.com/a.jpg"])


class ImmaginiCaricate(unittest.TestCase):
    def test_svg_attivo(self):
        self.assertFalse(svg_attivo(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'))
        for cattivo in (b"<svg onload=\"alert(1)\"/>", b"<svg/onload=alert(1)>", b"<svg><script>x</script></svg>",
                        b'<svg><a href="javascript:x">a</a></svg>', b"<svg><foreignObject/></svg>"):
            self.assertTrue(svg_attivo(cattivo), cattivo)

    def test_tipo_dai_byte(self):
        self.assertEqual(tipo_immagine(b"\x89PNG\r\n\x1a\n" + b"\0" * 20), ".png")
        self.assertEqual(tipo_immagine(b"\x00\x00\x01\x00" + b"\0" * 20), ".ico")
        self.assertEqual(tipo_immagine(b"\xff\xd8\xff\xe0" + b"\0" * 20), ".jpg")
        self.assertEqual(tipo_immagine(b"RIFF\0\0\0\0WEBPVP8 "), ".webp")
        self.assertEqual(tipo_immagine(b'<?xml version="1.0"?><svg></svg>'), ".svg")
        self.assertEqual(tipo_immagine(b"ciao"), "")


if __name__ == "__main__":
    unittest.main()
