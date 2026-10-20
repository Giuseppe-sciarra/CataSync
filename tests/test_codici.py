import unittest

import _ambiente  # noqa: F401
from app import codici


class Codici(unittest.TestCase):
    def test_checksum(self):
        self.assertTrue(codici.checksum_gtin("8059893356383"))
        self.assertFalse(codici.checksum_gtin("8059893356384"))
        self.assertTrue(codici.checksum_gtin("05710626495436"))   # GTIN-14 con lo zero davanti

    def test_classifica(self):
        self.assertEqual(codici.classifica("8059893356383"), ("ean", "8059893356383"))
        self.assertEqual(codici.classifica("97924465"), ("mpn", "97924465"))       # Grundfos: non è un EAN-8
        self.assertEqual(codici.classifica("FAST 14 FI"), ("mpn", "FAST 14 FI"))
        self.assertEqual(codici.classifica("2001234567893")[0], "interno")          # prefisso 20-29
        self.assertIsNone(codici.classifica("ab"))
        # Righe che non sono codici: non si cercano (consumano ricerche e fanno scattare i blocchi).
        self.assertIsNone(codici.classifica("docker inspect searxng --format"))
        self.assertIsNone(codici.classifica("GAMMA POMPE DI CALORE (EHS) - Unita esterna monofase"))
        self.assertIsNone(codici.classifica("DAB ESYBOX"))
        self.assertEqual(codici.classifica("8801643654689 SAMSUNG"), ("ean", "8801643654689"))
        self.assertEqual(codici.classifica("EAN: 8801643654689"), ("ean", "8801643654689"))
        self.assertEqual(codici.classifica("AE040RXEDEG/EU"), ("mpn", "AE040RXEDEG/EU"))
        self.assertEqual(codici.classifica("ESYBOX"), ("mpn", "ESYBOX"))

    def test_parse(self):
        buoni, scarti = codici.parse_codici("8059893356383, 5712608909306\n97924465\n\nxx\n2001234567893")
        self.assertEqual(buoni, ["8059893356383", "5712608909306", "97924465"])
        self.assertEqual(len(scarti), 2)


if __name__ == "__main__":
    unittest.main()
