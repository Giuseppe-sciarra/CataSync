"""Le frasi che il server manda all'interfaccia hanno la loro traduzione nel catalogo
(static/i18n/catalog.js): senza, in inglese o in francese resterebbero in italiano."""
import json
import os
import re
import unittest

import _ambiente  # noqa: F401
from app import diagnosi, providers, rete, web_search, web_source

NOMI_PROPRI = {"robots.txt", "SerpAPI", "CAPTCHA"}


def catalogo() -> dict:
    with open(os.path.join(_ambiente.RADICE, "static/i18n/catalog.js"), encoding="utf-8") as f:
        testo = f.read()
    return json.loads(re.sub(r"^\s*window\.APP_TRANSLATIONS\s*=\s*", "", testo).rstrip().rstrip(";"))


class Catalogo(unittest.TestCase):
    def test_frasi_del_server_tradotte(self):
        voci = catalogo()
        frasi = [
            *diagnosi.ESITI_PAGINA.values(), *diagnosi.CAUSE.values(), *rete.NOMI_BLOCCO.values(),
            *web_source._LIVELLI.values(),
            *(m[k] for m in web_search.MOTORI.values() for k in ("nome", "note")),
            *(p[k] for p in providers.PRESET.values() for k in ("name", "descrizione")),
            *(web_search._motivo_leggibile(m) for m in ("Suspended: access denied", "Suspended: too many requests")),
        ]
        mancanti = [f for f in frasi if f not in voci and f not in NOMI_PROPRI]
        self.assertEqual(mancanti, [])

    def test_ogni_voce_in_tre_lingue(self):
        for chiave, voce in catalogo().items():
            self.assertEqual(set(voce), {"en", "fr", "de"}, chiave)


if __name__ == "__main__":
    unittest.main()
