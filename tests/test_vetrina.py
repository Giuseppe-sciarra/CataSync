"""Pagina pubblica: presentazione, «Richiedi informazioni» spedito via SMTP, privacy, dati legali
e prezzi dall'amministrazione. Il server di posta è finto: si guarda cosa gli arriva."""
import unittest
from unittest import mock

import _ambiente

from fastapi.testclient import TestClient

from app import config, posta, vetrina
from app.main import app
from app.routers import vetrina as rotte


class PostaFinta:
    def __init__(self):
        self.spedite = []
        self.guasta = False

    def __call__(self, messaggio):
        if self.guasta:
            raise posta.PostaErrore("Server di posta non raggiungibile (smtp.example:587): rifiutato")
        self.spedite.append(messaggio)


class PaginaPubblica(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.posta = PostaFinta()
        cls._toppe = [
            mock.patch.object(posta, "_spedisci", cls.posta),
            mock.patch.object(config, "SMTP_HOST", "smtp.example"),
            mock.patch.object(config, "SMTP_FROM", "Portale <noreply@example.com>"),
            mock.patch.object(config, "CONTACT_TO", "vendite@example.com"),
        ]
        for t in cls._toppe:
            t.start()
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})

    @classmethod
    def tearDownClass(cls):
        cls.entra(cls, "admin@example.com")
        cls.c.put("/api/admin/vetrina", json={"attiva": True})
        cls.c.post("/api/auth/logout")
        cls.c.__exit__(None, None, None)
        for t in cls._toppe:
            t.stop()

    def setUp(self):
        rotte._visti.clear()
        rotte._tutti.clear()
        self.posta.spedite.clear()
        self.posta.guasta = False

    def entra(self, email):
        self.c.post("/api/auth/logout")
        r = self.c.post("/api/auth/login", json={"email": email, "password": "provaprova123"})
        assert r.status_code == 200, r.text

    def esci(self):
        self.c.post("/api/auth/logout")

    def manda(self, **campi):
        dati = {"nome": "Mario", "cognome": "Rossi", "email": "mario@example.com", "messaggio": "Vendo caldaie: come funziona?"}
        dati.update(campi)
        return self.c.post("/richiedi-informazioni", data=dati)

    # ------------------------------------------------------------------ pagine
    def test_1_pagina_e_pannello(self):
        self.esci()
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        for testo in ("Dall’EAN", "Richiedi informazioni", 'href="/login"', "/richiedi-informazioni",
                      "/privacy", "30 €", "25 €", "300 € l'anno: risparmi 60 €", "Codice sorgente"):
            self.assertIn(testo, r.text)
        self.assertIn('class="on"', r.text)                       # il codice a barre si vede anche senza JavaScript
        self.assertIn("2 mesi gratis", r.text)                    # 60 € risparmiati = due canoni mensili
        self.assertIn("360 €", r.text)                            # un anno col mensile, nel confronto a barre
        r = self.c.get("/app", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/login"))
        self.entra("admin@example.com")
        self.assertIn('href="/app"', self.c.get("/").text)        # chi è dentro trova «Apri il portale»
        self.assertIn("hub.js", self.c.get("/app").text)

    def test_2_pagina_spenta(self):
        self.entra("admin@example.com")
        self.assertEqual(self.c.put("/api/admin/vetrina", json={"attiva": False}).status_code, 200)
        r = self.c.get("/", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/app")
        self.esci()
        for indirizzo in ("/", "/richiedi-informazioni"):
            r = self.c.get(indirizzo, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (302, "/login"))
        self.assertEqual(self.c.get("/privacy").status_code, 200)   # l'informativa resta
        self.entra("admin@example.com")
        self.c.put("/api/admin/vetrina", json={"attiva": True})

    # ------------------------------------------------------------------ modulo
    def test_3_richiesta_spedita(self):
        self.entra("admin@example.com")
        self.c.put("/api/admin/vetrina", json={"attiva": True, "prezzo_mese": "30", "prezzo_anno_mese": "25"})
        self.esci()
        r = self.c.get("/richiedi-informazioni?piano=annuale&email=luca@example.com")
        self.assertIn('value="luca@example.com"', r.text)
        self.assertIn('value="annuale" checked', r.text)          # dal pulsante «Annuale»: già scelto nel modulo
        self.assertNotIn('value="mensile" checked', r.text)
        r = self.c.get("/richiedi-informazioni?piano=mensile")
        self.assertIn('value="mensile" checked', r.text)
        self.assertNotIn('value="annuale" checked', r.text)
        self.assertNotIn(" checked", self.c.get("/richiedi-informazioni").text)   # senza piano: nessuno scelto
        r = self.manda(nome="", piano="mensile")                  # con un errore il piano scelto resta
        self.assertIn('value="mensile" checked', r.text)
        r = self.manda(nome=" Mario\r\nBcc: x@evil.example ", piano="annuale")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Richiesta inviata", r.text)
        self.assertEqual(len(self.posta.spedite), 1)
        m = self.posta.spedite[0]
        self.assertEqual(m["To"], "vendite@example.com")
        self.assertEqual(m["Reply-To"], "mario@example.com")
        self.assertNotIn("\n", m["Subject"])
        self.assertIsNone(m["Bcc"])
        corpo = m.get_content()
        self.assertIn("Piano che interessa: annuale", corpo)
        self.assertIn("Vendo caldaie: come funziona?", corpo)

    def test_4_errori_e_trappola(self):
        self.esci()
        r = self.manda(nome="", email="non-una-email", messaggio="")
        self.assertEqual(r.status_code, 400)
        for errore in ("Scrivi il tuo nome.", "Serve un indirizzo email valido.", "Scrivi il messaggio."):
            self.assertIn(errore, r.text)
        self.assertIn('value="Rossi"', r.text)                    # quello che era giusto resta
        self.assertEqual(self.manda(messaggio="x" * 5001).status_code, 400)
        r = self.manda(sito_web="https://spam.example")           # il campo nascosto: un programma
        self.assertEqual(r.status_code, 200)
        self.assertIn("Richiesta inviata", r.text)
        self.assertEqual(self.posta.spedite, [])

    def test_5_posta_guasta_e_troppe(self):
        self.esci()
        self.posta.guasta = True
        r = self.manda()
        self.assertEqual(r.status_code, 503)
        self.assertIn("Non siamo riusciti a spedire la richiesta", r.text)
        self.assertIn("Vendo caldaie", r.text)                    # il messaggio non si perde
        self.posta.guasta = False
        for _ in range(4):
            self.assertEqual(self.manda().status_code, 200)
        r = self.manda()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.posta.spedite), 4)

    # ------------------------------------------------------------------ amministrazione
    def test_6_dati_legali_prezzi_privacy(self):
        self.esci()
        self.assertEqual(self.c.get("/api/admin/vetrina").status_code, 401)
        self.entra("admin@example.com")
        dati = self.c.get("/api/admin/vetrina").json()
        self.assertTrue(dati["posta"]["configurata"])
        self.assertEqual(dati["destinatario_effettivo"], "vendite@example.com")
        self.assertNotIn("password", str(dati["posta"]).lower())
        self.assertEqual(self.c.put("/api/admin/vetrina", json={"pec": "non valida"}).status_code, 400)
        self.assertEqual(self.c.put("/api/admin/vetrina", json={"prezzo_mese": "trenta"}).status_code, 400)
        r = self.c.put("/api/admin/vetrina", json={
            "attiva": True, "ragione_sociale": "Esempio srls", "sede_legale": "Via Roma 1, 00100 Roma",
            "partita_iva": "01234567890", "codice_fiscale": "01234567890", "pec": "esempio@pec.example",
            "email": "info@esempio.example", "prezzo_mese": "29,90", "prezzo_anno_mese": "24.90",
            "nota_prezzi": "IVA esclusa", "destinatario": "",
            "privacy_testo": "<h2>La nostra</h2><p onclick=\"x()\">Testo</p><script>alert(1)</script>"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["prezzo_mese"], r.json()["prezzo_anno_mese"]), ("29.9", "24.9"))
        self.esci()
        pagina = self.c.get("/").text
        for testo in ("Esempio srls", "P.IVA e C.F.", "01234567890", "esempio@pec.example", "29,90 €", "24,90 €",
                      "298,80 € l'anno: risparmi 60 €", "358,80 €", "Conviene di più"):
            self.assertIn(testo, pagina)
        self.assertNotIn("mesi gratis", pagina)                   # 60 € non sono un numero intero di canoni da 29,90
        privacy = self.c.get("/privacy").text
        self.assertIn("<h2>La nostra</h2>", privacy)
        self.assertNotIn("<script>alert", privacy)
        self.assertNotIn("onclick", privacy)
        self.entra("admin@example.com")
        self.c.put("/api/admin/vetrina", json={"attiva": True, "ragione_sociale": "Esempio srls", "pec": "esempio@pec.example",
                                               "prezzo_mese": "30", "prezzo_anno_mese": "25", "nota_prezzi": "IVA esclusa"})
        self.esci()
        privacy = self.c.get("/privacy").text
        self.assertIn("Titolare del trattamento", privacy)        # di nuovo l'informativa di serie
        self.assertIn("Esempio srls", privacy)

    def test_7_mail_di_prova(self):
        self.entra("admin@example.com")
        r = self.c.post("/api/admin/vetrina/prova", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.posta.spedite[-1]["To"], "vendite@example.com")
        self.posta.guasta = True
        r = self.c.post("/api/admin/vetrina/prova", json={"a": "altro@example.com"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("non raggiungibile", r.json()["detail"])
        with mock.patch.object(config, "SMTP_HOST", ""):
            self.posta.guasta = False
            r = self.c.post("/api/admin/vetrina/prova", json={})
            self.assertEqual(r.status_code, 502)
            self.assertIn("SMTP_HOST", r.json()["detail"])

    def test_8_codice_a_barre(self):
        # EAN-13 4006381333931: la sequenza di moduli è quella dello standard (parità LGLLGG).
        self.assertEqual(vetrina.moduli_ean13("4006381333931"),
                         "101" "0001101" "0100111" "0101111" "0111101" "0001001" "0110011" "01010"
                         "1000010" "1000010" "1000010" "1110100" "1000010" "1100110" "101")
        self.assertEqual(len(vetrina.barre()), 95)
        self.assertEqual(vetrina.euro(vetrina.prezzo("1.200,5")), "")   # due separatori: non è un numero sicuro
        self.assertEqual(vetrina.euro(vetrina.prezzo("1200,50")), "1.200,50")
        self.assertEqual(vetrina.euro(vetrina.prezzo("30")), "30")


if __name__ == "__main__":
    unittest.main()
