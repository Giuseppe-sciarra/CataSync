import unittest

import _ambiente  # noqa: F401
from app import lingue


class Lingua(unittest.TestCase):
    def test_testo(self):
        casi = {
            "IT": "La pompa di calore garantisce il massimo risparmio energetico grazie al compressore inverter.",
            "EN": "The booster pump is designed for domestic water supply and provides constant pressure with a quiet motor.",
            "FR": "Le surpresseur est conçu pour les installations domestiques et assure une pression constante dans votre maison.",
            "DE": "Die Pumpe ist für den Einsatz in Heizungsanlagen geeignet und arbeitet mit einem sehr effizienten Motor.",
            "ES": "El grupo de presión está diseñado para el suministro de agua en viviendas y también para edificios.",
        }
        for attesa, testo in casi.items():
            self.assertEqual(lingue.dal_testo(testo)[0], attesa, testo)

    def test_parole_comuni_a_italiano_e_spagnolo(self):
        # «su», «con», «del», «al» sono italiane quanto spagnole: una pagina italiana resta italiana.
        testo = ("Stai cercando lo scaldasalviette? Su Elettroclimaweb trovi il miglior prezzo del web, "
                 "spedizioni veloci con corriere espresso e assistenza al tuo servizio!")
        self.assertEqual(lingue.della_pagina('<html lang="it">', testo, "https://x.it/a", "x.it"), "IT")

    def test_frasi_da_negozio(self):
        tecnico = "La pompa di calore garantisce il massimo risparmio energetico grazie al compressore inverter."
        self.assertEqual(lingue.ripulisci_da_negozio(tecnico + " Risparmia il 20% con noi."), tecnico)


if __name__ == "__main__":
    unittest.main()
