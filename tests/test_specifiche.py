"""TD Product Specs: le specifiche scritte a mano sul sito restano, il portale completa le voci
che mancano e mette per prime quelle che il sito usa come filtri. WooCommerce è finto."""
import asyncio
import unittest

import _ambiente  # noqa: F401

from app import wc_rest


class Etichette(unittest.TestCase):
    def test_chiave_come_il_plugin(self):
        self.assertEqual(wc_rest.chiave_etichetta(" Marca: "), "marca")
        self.assertEqual(wc_rest.chiave_etichetta("Potenza  (BTU)"), "potenza (btu)")
        self.assertEqual(wc_rest.chiave_etichetta("Velocità"), "velocita")
        self.assertEqual(wc_rest.chiave_etichetta("<b>Colore</b> &amp; finitura"), "colore & finitura")

    def test_filtri_con_asterisco(self):
        filtri = ["Marca", "Potenza*", "Gas refrigerante"]
        self.assertTrue(wc_rest.etichetta_nei_filtri("marca", filtri))
        self.assertTrue(wc_rest.etichetta_nei_filtri("Potenza in raffreddamento", filtri))
        self.assertTrue(wc_rest.etichetta_nei_filtri("Gas refrigerante:", filtri))
        self.assertFalse(wc_rest.etichetta_nei_filtri("Classe energetica", filtri))
        self.assertFalse(wc_rest.etichetta_nei_filtri("", filtri))

    def test_prima_quelle_dei_filtri_poi_il_limite(self):
        righe = [{"name": f"Voce {i}", "value": str(i)} for i in range(10)]
        righe.append({"name": "Gas refrigerante", "value": "R-32"})
        righe.append({"name": "Potenza frigorifera", "value": "3,5 kW", "key": True})
        scelte = wc_rest.taglia_specifiche(righe, 3, ["Gas refrigerante"])
        self.assertEqual([r["name"] for r in scelte], ["Gas refrigerante", "Potenza frigorifera", "Voce 0", "Voce 1"])
        # senza filtri tutto come prima
        self.assertEqual([r["name"] for r in wc_rest.taglia_specifiche(righe, 2)], ["Potenza frigorifera", "Voce 0"])
        self.assertEqual(len(wc_rest.taglia_specifiche(righe, 0, ["Gas refrigerante"])), 12)   # 0 = tutte


class Unione(unittest.TestCase):
    A_MANO = [{"label": "Marca", "value": "Innova"}, {"label": "Colore", "value": "<strong>Bianco</strong>"}]
    PORTALE = [{"label": "marca:", "value": "INNOVA srl"}, {"label": "Potenza", "value": "2,35 kW"},
               {"label": "Colore", "value": "Grigio"}]

    def test_solo_se_vuoto_completa(self):
        dopo = wc_rest.unisci_specifiche(self.A_MANO, self.PORTALE)
        self.assertEqual(dopo, [{"label": "Marca", "value": "Innova"}, {"label": "Colore", "value": "<strong>Bianco</strong>"},
                                {"label": "Potenza", "value": "2,35 kW"}])

    def test_sempre_aggiorna_ma_non_toglie(self):
        dopo = wc_rest.unisci_specifiche(self.A_MANO + [{"label": "Garanzia", "value": "5 anni"}], self.PORTALE, sovrascrivi=True)
        self.assertEqual([(r["label"], r["value"]) for r in dopo],
                         [("Marca", "INNOVA srl"), ("Colore", "Grigio"), ("Garanzia", "5 anni"), ("Potenza", "2,35 kW")])

    def test_stessa_voce_di_un_filtro_con_altra_etichetta(self):
        a_mano = [{"label": "Finitura/Colore", "value": "Bianco"}, {"label": "Potenza (BTU)", "value": "18000"}]
        portale = [{"label": "Colore", "value": "Grigio"}, {"label": "Potenza", "value": "18000 Btu"},
                   {"label": "Gas refrigerante", "value": "R-32"}]
        gruppi = [["Colore", "Finitura/Colore"], ["Potenza", "Potenza (BTU)"]]
        self.assertEqual([(r["label"], r["value"]) for r in wc_rest.unisci_specifiche(a_mano, portale, gruppi=gruppi)],
                         [("Finitura/Colore", "Bianco"), ("Potenza (BTU)", "18000"), ("Gas refrigerante", "R-32")])
        self.assertEqual([(r["label"], r["value"]) for r in wc_rest.unisci_specifiche(a_mano, portale, True, gruppi)],
                         [("Finitura/Colore", "Grigio"), ("Potenza (BTU)", "18000 Btu"), ("Gas refrigerante", "R-32")])
        # due voci nuove dello stesso filtro (Potenza*) entrano tutte e due: sono valori diversi
        nuove = [{"label": "Potenza caldo", "value": "4,9 kW"}, {"label": "Potenza freddo", "value": "4,6 kW"}]
        self.assertEqual(len(wc_rest.unisci_specifiche([{"label": "Marca", "value": "TCL"}], nuove, gruppi=[["Potenza*"]])), 3)

    def test_righe_dal_meta(self):
        p = {"meta_data": [{"key": "_altro", "value": "x"},
                           {"key": wc_rest.TD_SPECS_META_KEY, "value": {"0": {"label": "Marca", "value": "Innova"}, "3": {"label": "", "value": ""}}}]}
        self.assertEqual(wc_rest.specifiche_plugin(p), [{"label": "Marca", "value": "Innova"}])
        self.assertEqual(wc_rest.specifiche_plugin({"meta_data": []}), [])


class WooSpecifiche:
    """Un prodotto «scritto a mano» col suo EAN e le sue specifiche nel plugin."""

    def __init__(self, filtri=None, plugin=True):
        self.filtri = filtri or []
        self.plugin = plugin
        self.chiamate = []
        self.prodotto = {
            "id": 90, "name": "Climatizzatore portatile", "sku": "", "global_unique_id": "4006381333931",
            "description": "<p>Scritta a mano</p>", "short_description": "", "regular_price": "499", "categories": [{"id": 5}],
            "images": [{"src": "https://img.example/1.jpg"}], "attributes": [], "brands": [{"id": 1}],
            "meta_data": [{"key": wc_rest.TD_SPECS_META_KEY, "value": [{"label": "Marca", "value": "De Longhi"},
                                                                     {"label": "Colore", "value": "Bianco"}]}],
            "permalink": "https://negozio.example/p/90",
        }

    async def __call__(self, site, method, path, params=None, json_body=None, base=wc_rest.BASE):
        self.chiamate.append((method, base + path, json_body))
        if base == wc_rest.PLUGIN_SPECS:
            if not self.plugin:
                raise wc_rest.PluginMancante("manca")
            return {"filtri": [{"nome": f, "etichette": [f]} for f in self.filtri]}, {}
        if path == "/products" and method == "GET":
            return [dict(self.prodotto)], {}
        if path == "/products/90" and method == "PUT":
            self.prodotto.update(json_body)
            return dict(self.prodotto), {}
        return {}, {}


class Pubblicazione(unittest.TestCase):
    SCHEDA = {
        "gtin": "4006381333931", "title": "Climatizzatore", "description": "<p>Dal web</p>",
        "attributes": [{"name": "Voce A", "value": "1"}, {"name": "Voce B", "value": "2"}, {"name": "Voce C", "value": "3"},
                       {"name": "Gas refrigerante", "value": "R-290"}, {"name": "Colore", "value": "Grigio"}],
    }

    def pubblica(self, woo, politica="fill", filtri=None):
        sito = type("Sito", (), {"id": 7})()
        originale = wc_rest._request
        wc_rest._request = woo
        try:
            opzioni = {"write_policy": {"attributes": politica}, "specs_meta": True, "specs_limit": 2,
                       "filtri_specifiche": filtri or []}
            return asyncio.run(wc_rest.push_product(sito, dict(self.SCHEDA), opzioni))
        finally:
            wc_rest._request = originale

    def specifiche_scritte(self, woo):
        put = [c[2] for c in woo.chiamate if c[0] == "PUT"]
        if not put:
            return None
        for m in put[-1].get("meta_data") or []:
            if m["key"] == wc_rest.TD_SPECS_META_KEY:
                return [(r["label"], r["value"]) for r in m["value"]]
        return None

    def test_completa_quelle_a_mano_e_mette_i_filtri(self):
        woo = WooSpecifiche()
        r = self.pubblica(woo, filtri=["Gas refrigerante"])
        self.assertEqual(r["action"], "updated")
        # a mano restano (Colore resta «Bianco»), poi le voci nuove: prima quella del filtro, poi 2 dal limite
        self.assertEqual(self.specifiche_scritte(woo), [("Marca", "De Longhi"), ("Colore", "Bianco"),
                                                       ("Gas refrigerante", "R-290"), ("Voce A", "1"), ("Voce B", "2")])
        put = [c[2] for c in woo.chiamate if c[0] == "PUT"][-1]
        self.assertNotIn("description", put)          # la descrizione a mano non si tocca
        # gli attributi WooCommerce (il prodotto non ne aveva) seguono le specifiche del plugin
        self.assertEqual([(a["name"], a["options"]) for a in put["attributes"]],
                         [("Marca", ["De Longhi"]), ("Colore", ["Bianco"]), ("Gas refrigerante", ["R-290"]),
                          ("Voce A", ["1"]), ("Voce B", ["2"])])
        self.assertEqual(wc_rest.testo_specifica('<a href="x">2 anni</a><br>garanzia &amp; assistenza'), "2 anni | garanzia & assistenza")

    def test_sempre_aggiorna_le_stesse_voci(self):
        woo = WooSpecifiche()
        self.pubblica(woo, politica="overwrite", filtri=["Colore"])
        self.assertEqual(self.specifiche_scritte(woo)[:2], [("Marca", "De Longhi"), ("Colore", "Grigio")])

    def test_mai_non_scrive(self):
        woo = WooSpecifiche()
        self.pubblica(woo, politica="skip")
        self.assertIsNone(self.specifiche_scritte(woo))

    def test_niente_da_aggiungere_niente_scrittura(self):
        woo = WooSpecifiche()
        woo.prodotto["attributes"] = [{"id": 0, "name": "Voce A", "options": ["1"], "variation": False}]
        woo.prodotto["meta_data"][0]["value"] = [{"label": n, "value": v} for n, v in
                                                 [("Voce A", "1"), ("Voce B", "2"), ("Voce C", "3"), ("Gas refrigerante", "x"), ("Colore", "y")]]
        r = self.pubblica(woo)
        self.assertIn("Niente da aggiornare con le politiche impostate.", r["warnings"])
        self.assertFalse([c for c in woo.chiamate if c[0] == "PUT"])

    def test_filtri_letti_dal_plugin_e_senza_plugin(self):
        sito = type("Sito", (), {"id": 991})()
        originale = wc_rest._request
        try:
            wc_rest._request = WooSpecifiche(filtri=["Marca", "Potenza"])
            self.assertEqual(asyncio.run(wc_rest.etichette_filtro(sito)), [["Marca"], ["Potenza"]])
            self.assertEqual(wc_rest.etichette_filtro_note(991), [["Marca"], ["Potenza"]])
            wc_rest._request = WooSpecifiche(plugin=False)
            self.assertEqual(asyncio.run(wc_rest.filtri_specifiche(sito)), [])
        finally:
            wc_rest._request = originale
            wc_rest._cache_filtri.pop(991, None)


if __name__ == "__main__":
    unittest.main()
