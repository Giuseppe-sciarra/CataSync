import asyncio
import unittest

import httpx

import _ambiente  # noqa: F401
from app import rete


class SoloInternet(unittest.TestCase):
    def test_indirizzi_interni(self):
        interni = ["127.0.0.1", "10.1.2.3", "192.168.1.10", "172.16.0.5", "169.254.169.254", "100.64.1.1",
                   "0.0.0.0", "[::1]", "localhost", "db", "searxng", "nas.local", "router.lan"]
        for host in interni:
            self.assertFalse(asyncio.run(rete.host_pubblico(host)), host)
        self.assertTrue(asyncio.run(rete.host_pubblico("8.8.8.8")))

    def test_sessione_non_apre_la_rete_interna(self):
        async def prova():
            async with rete.Sessione("IT", "") as s:
                return await s.apri("http://192.168.1.1/admin")
        pagina = asyncio.run(prova())
        self.assertFalse(pagina.leggibile)
        self.assertIn("rete interna", pagina.motivo())


class Robots(unittest.TestCase):
    def test_rfc9309(self):
        r = rete.Robots.da_testo(
            "User-agent: *\nDisallow: /privato/\nAllow: /privato/ok\nDisallow: /*.pdf$\n\n"
            "User-agent: tdsynccatalog\nDisallow: /cerca\n")
        # c'è un gruppo per noi: vale solo quello
        self.assertFalse(r.consente("https://x.it/cerca?q=1"))
        self.assertTrue(r.consente("https://x.it/privato/pagina"))
        generico = rete.Robots.da_testo("User-agent: *\nDisallow: /privato/\nAllow: /privato/ok\nDisallow: /*.pdf$\n")
        self.assertFalse(generico.consente("https://x.it/privato/pagina"))
        self.assertTrue(generico.consente("https://x.it/privato/ok"))       # vince la regola più lunga
        self.assertFalse(generico.consente("https://x.it/scheda.pdf"))
        self.assertTrue(generico.consente("https://x.it/scheda.pdf?x=1"))   # $ = fine del percorso


class Blocchi(unittest.TestCase):
    def test_riconosce_le_barriere(self):
        cf = "<html><head><title>Just a moment...</title></head><body>cf-chl challenge-platform</body></html>"
        self.assertEqual(rete.riconosci_blocco(403, {"server": "cloudflare"}, cf), "cloudflare")
        self.assertEqual(rete.riconosci_blocco(403, {"cf-mitigated": "challenge"}, ""), "cloudflare")
        self.assertEqual(rete.riconosci_blocco(403, {"x-datadome": "1"}, ""), "datadome")
        self.assertEqual(rete.riconosci_blocco(429, {}, "slow down"), "limite")
        self.assertEqual(rete.riconosci_blocco(200, {}, "<html><title>Pompa DAB</title></html>"), "")


class Codifica(unittest.TestCase):
    def test_utf8_vince_sul_meta_sbagliato(self):
        html = '<html><head><meta charset="iso-8859-2"></head><body>vorgedämmt</body></html>'.encode("utf-8")
        risposta = httpx.Response(200, headers={"content-type": "text/html"}, content=html)
        self.assertIn("vorgedämmt", rete.decodifica(risposta))

    def test_latin1_vero(self):
        html = '<html><head><meta charset="iso-8859-1"></head><body>è un telefono</body></html>'.encode("latin-1")
        risposta = httpx.Response(200, headers={"content-type": "text/html"}, content=html)
        self.assertIn("è un telefono", rete.decodifica(risposta))


if __name__ == "__main__":
    unittest.main()
