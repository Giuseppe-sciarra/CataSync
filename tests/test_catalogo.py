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

    def test_messaggi_dell_abbonamento(self):
        from datetime import datetime, timedelta, timezone

        from app import billing
        from app.models import Client

        voci = catalogo()
        modelli = [re.compile("^" + re.escape(k).replace(r"\{0\}", ".+?").replace(r"\{1\}", ".+?") + "$")
                   for k in voci if "{0}" in k]
        ieri = datetime.now(timezone.utc) - timedelta(days=1)
        messaggi = [
            billing.scrittura_bloccata(Client(name="x", is_active=False, subscription_status="active")),
            billing.scrittura_bloccata(Client(name="x", is_active=True, subscription_status="suspended")),
            billing.scrittura_bloccata(Client(name="x", is_active=True, subscription_status="expired")),
            billing.scrittura_bloccata(Client(name="x", is_active=True, subscription_status="active", subscription_until=ieri)),
            "Quota del mese esaurita (5/5 prodotti). Riparte il primo del mese prossimo.",
        ]
        for m in messaggi:
            self.assertTrue(m)
            self.assertTrue(m in voci or any(r.match(m) for r in modelli), m)

    def test_ogni_voce_in_tre_lingue(self):
        for chiave, voce in catalogo().items():
            self.assertEqual(set(voce), {"en", "fr", "de"}, chiave)


if __name__ == "__main__":
    unittest.main()
