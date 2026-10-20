"""Verifica in due passaggi (app, email, passkey, codici di riserva) e avviso di manutenzione
nella pagina di accesso. Niente rete: la posta è finta e la passkey la firma un autenticatore
scritto qui, con una chiave P-256 vera."""
import hashlib
import json
import os
import re
import struct
import time
import unittest
from datetime import datetime, timedelta, timezone

import _ambiente

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url

from app import due_fattori, manutenzione, posta
from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password

PASSWORD = "provaprova123"


def _crea_utente(email: str, ruolo: str = "client") -> None:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == email).first():
            db.add(User(email=email, password_hash=hash_password(PASSWORD), role=ruolo))
            db.commit()
    finally:
        db.close()


class Autenticatore:
    """Un autenticatore WebAuthn minimo: registra (attestazione «none») e firma gli accessi."""

    def __init__(self, rp_id: str, origine: str):
        self.chiave = ec.generate_private_key(ec.SECP256R1())
        self.id = os.urandom(16)
        self.rp_id, self.origine, self.contatore = rp_id, origine, 0

    def _client(self, tipo: str, sfida: str) -> bytes:
        return json.dumps({"type": tipo, "challenge": sfida, "origin": self.origine, "crossOrigin": False}).encode()

    def crea(self, opzioni: dict) -> dict:
        numeri = self.chiave.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numeri.x.to_bytes(32, "big"), -3: numeri.y.to_bytes(32, "big")})
        dati = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x01 | 0x04 | 0x40])
                + struct.pack(">I", self.contatore) + b"\x00" * 16
                + struct.pack(">H", len(self.id)) + self.id + cose)
        attestazione = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": dati})
        return {
            "id": bytes_to_base64url(self.id), "rawId": bytes_to_base64url(self.id), "type": "public-key",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": bytes_to_base64url(self._client("webauthn.create", opzioni["challenge"])),
                "attestationObject": bytes_to_base64url(attestazione),
                "transports": ["internal"],
            },
        }

    def usa(self, opzioni: dict, origine: str | None = None) -> dict:
        self.contatore += 1
        client = json.dumps({"type": "webauthn.get", "challenge": opzioni["challenge"],
                             "origin": origine or self.origine, "crossOrigin": False}).encode()
        dati = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x01 | 0x04]) + struct.pack(">I", self.contatore)
        firma = self.chiave.sign(dati + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": bytes_to_base64url(self.id), "rawId": bytes_to_base64url(self.id), "type": "public-key",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": bytes_to_base64url(client),
                "authenticatorData": bytes_to_base64url(dati),
                "signature": bytes_to_base64url(firma),
                "userHandle": None,
            },
        }


class Totp(unittest.TestCase):
    def test_vettori_rfc6238(self):
        # Segreto «12345678901234567890» dell'RFC, SHA1: le ultime sei cifre dei vettori ufficiali.
        segreto = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(due_fattori.codice_totp(segreto, 59 // 30), "287082")
        self.assertEqual(due_fattori.codice_totp(segreto, 1111111109 // 30), "081804")
        self.assertEqual(due_fattori.codice_totp(segreto, 2000000000 // 30), "279037")

    def test_finestra_e_riuso(self):
        segreto = due_fattori.nuovo_segreto()
        adesso = time.time()
        passo = int(adesso // 30)
        codice = due_fattori.codice_totp(segreto, passo - 1)       # telefono indietro di 30 secondi
        self.assertEqual(due_fattori.verifica_totp(segreto, codice, 0, adesso), passo - 1)
        self.assertIsNone(due_fattori.verifica_totp(segreto, codice, passo - 1, adesso))   # già usato
        lontano = due_fattori.codice_totp(segreto, passo - 3)
        self.assertIsNone(due_fattori.verifica_totp(segreto, lontano, 0, adesso))
        self.assertIsNone(due_fattori.verifica_totp(segreto, "12345", 0, adesso))

    def test_uri_e_qr(self):
        uri = due_fattori.uri_otpauth("ABCDEFGH", "mario@example.com", "TD Sync: Catalog")
        self.assertTrue(uri.startswith("otpauth://totp/TD%20Sync%20%20Catalog%3Amario%40example.com?secret=ABCDEFGH"))
        self.assertTrue(due_fattori.qr_svg(uri).startswith("data:image/svg+xml"))
        self.assertEqual(due_fattori.email_mascherata("giuseppe@example.com"), "g••••••e@example.com")


class Manutenzione(unittest.TestCase):
    def test_stati_e_frasi(self):
        fuso = manutenzione._fuso()
        dati = {"attiva": True, "giorno": "2030-01-10", "dalle": "22:00", "alle": "01:30"}
        prima = datetime(2030, 1, 9, 12, 0, tzinfo=fuso)
        self.assertEqual(manutenzione.stato(dati, prima), "programmata")
        self.assertEqual(manutenzione.stato(dati, datetime(2030, 1, 11, 0, 30, tzinfo=fuso)), "in_corso")
        self.assertEqual(manutenzione.stato(dati, datetime(2030, 1, 11, 1, 30, tzinfo=fuso)), "finita")
        self.assertEqual(manutenzione.stato({**dati, "attiva": False}, prima), "spenta")
        self.assertEqual(manutenzione.stato({**dati, "alle": ""}, prima), "incompleta")
        self.assertEqual(
            manutenzione.frase(dati, prima),
            "Giovedì 10 gennaio, dalle 22:00 alle 01:30 del giorno dopo, il portale sarà fermo per manutenzione: "
            "in quelle ore i servizi non funzioneranno")
        stessa = {**dati, "alle": "23:30"}
        self.assertTrue(manutenzione.frase(stessa, prima).startswith("Giovedì 10 gennaio, dalle 22:00 alle 23:30, il portale"))
        self.assertTrue(manutenzione.frase(stessa, datetime(2030, 1, 10, 22, 5, tzinfo=fuso)).startswith("Fino alle 23:30"))
        self.assertIsNone(manutenzione.per_la_pagina(SessionLocal(), datetime(2031, 1, 1, tzinfo=fuso)))

    def test_frasi_tradotte_e_uguali_nel_pannello(self):
        from test_catalogo import catalogo

        voci = catalogo()
        hub = open(os.path.join(_ambiente.RADICE, "static", "hub.js"), encoding="utf-8").read()
        for frase in (manutenzione.FRASE_STESSO_GIORNO, manutenzione.FRASE_A_CAVALLO, manutenzione.FRASE_IN_CORSO):
            self.assertIn(frase, voci)
            self.assertIn("'" + frase + "'", hub)


class Giro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.posta = []
        cls._originali = (posta.invia, posta.configurata)

        async def invia_finta(destinatario, oggetto, testo, **_):
            cls.posta.append({"a": destinatario, "oggetto": oggetto, "testo": testo})

        posta.invia = invia_finta
        posta.configurata = lambda: True
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.c.headers.update({"Origin": "http://testserver"})
        for email in ("due@example.com", "mail@example.com", "chiave@example.com"):
            _crea_utente(email)

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)
        posta.invia, posta.configurata = cls._originali

    def password(self, email):
        self.c.post("/api/auth/logout")
        r = self.c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ultimo_codice(self):
        return re.search(r"^\s+(\d{6})$", self.posta[-1]["testo"], re.M).group(1)

    def test_app_e_codici_di_riserva(self):
        c = self.c
        self.assertFalse(self.password("due@example.com")["due_fattori"])
        self.assertFalse(c.get("/api/auth/me").json()["due_fattori"])
        # Attivarla chiede la password; riscritta, vale dieci minuti.
        self.assertEqual(c.post("/api/auth/2fa/app/inizia", json={}).status_code, 403)
        self.assertEqual(c.post("/api/auth/2fa/app/inizia", json={"password": "sbagliata"}).status_code, 400)
        self.assertEqual(c.post("/api/auth/2fa/app/inizia", json={"password": PASSWORD}).status_code, 200)
        r = c.post("/api/auth/2fa/app/inizia", json={})     # un nuovo QR, senza riscriverla
        self.assertEqual(r.status_code, 200, r.text)
        segreto = r.json()["segreto"].replace(" ", "")
        self.assertEqual(c.post("/api/auth/2fa/app/conferma", json={"codice": "000000"}).status_code, 400)
        codice = due_fattori.codice_totp(segreto, int(time.time() // 30))
        r = c.post("/api/auth/2fa/app/conferma", json={"codice": codice})
        self.assertEqual(r.status_code, 200, r.text)
        riserva = r.json()["codici_riserva"]
        self.assertEqual(len(riserva), 10)
        self.assertTrue(c.get("/api/auth/me").json()["due_fattori"])

        # La password da sola non apre più il pannello.
        r = self.password("due@example.com")
        self.assertEqual(r["metodi"], ["app"])
        self.assertFalse(c.get("/api/auth/me").json()["authenticated"])
        self.assertEqual(c.get("/app", follow_redirects=False).status_code, 302)
        self.assertEqual(c.post("/api/auth/2fa/verifica", json={"metodo": "app", "codice": codice}).status_code, 400)  # già usato
        r = c.post("/api/auth/2fa/verifica", json={"metodo": "riserva", "codice": " " + riserva[0].upper() + " "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["riserva_rimasti"], 9)
        self.assertTrue(c.get("/api/auth/me").json()["authenticated"])

        # Lo stesso codice di riserva non vale due volte.
        self.password("due@example.com")
        r = c.post("/api/auth/2fa/verifica", json={"metodo": "riserva", "codice": riserva[0]})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/auth/2fa/verifica", json={"metodo": "riserva", "codice": riserva[1]})
        self.assertEqual(r.status_code, 200)

        # Spegnerla chiede la password; spento l'ultimo modo, spariscono anche i codici.
        self.assertEqual(c.post("/api/auth/2fa/app/disattiva", json={"password": "sbagliata"}).status_code, 400)
        r = c.post("/api/auth/2fa/app/disattiva", json={"password": PASSWORD})
        self.assertFalse(r.json()["attiva"])
        self.assertEqual(r.json()["riserva_rimasti"], 0)
        self.assertFalse(self.password("due@example.com")["due_fattori"])

    def test_codice_via_email(self):
        c = self.c
        self.password("mail@example.com")
        r = c.post("/api/auth/2fa/email/inizia", json={"lingua": "en", "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("is your code for", self.posta[-1]["oggetto"])   # nella lingua dell'interfaccia
        self.assertEqual(c.post("/api/auth/2fa/email/inizia", json={}).status_code, 429)   # troppo presto
        r = c.post("/api/auth/2fa/email/conferma", json={"codice": self.ultimo_codice()})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["email"])

        r = self.password("mail@example.com")
        self.assertEqual(r["metodi"], ["email"])
        self.assertEqual(r["email"], "m••l@example.com")
        # Appena attivato si può già rientrare: la pausa vale solo fra due codici per lo stesso scopo.
        self.assertEqual(c.post("/api/auth/2fa/email/invia", json={"lingua": "it"}).status_code, 200)
        self.assertEqual(c.post("/api/auth/2fa/email/invia", json={"lingua": "it"}).status_code, 429)
        self.assertIn("Il codice per entrare in", self.posta[-1]["testo"])
        codice = self.ultimo_codice()
        sbagliato = "000000" if codice != "000000" else "111111"
        self.assertEqual(c.post("/api/auth/2fa/verifica", json={"metodo": "email", "codice": sbagliato}).status_code, 400)
        r = c.post("/api/auth/2fa/verifica", json={"metodo": "email", "codice": codice})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(c.get("/api/auth/me").json()["authenticated"])

    def test_passkey_e_reset_dell_amministratore(self):
        c = self.c
        chiave = Autenticatore("testserver", "http://testserver")
        self.password("chiave@example.com")
        opzioni = c.post("/api/auth/2fa/passkey/inizia", json={"password": PASSWORD}).json()
        self.assertEqual(opzioni["rp"]["id"], "testserver")
        r = c.post("/api/auth/2fa/passkey/conferma", json={"credenziale": chiave.crea(opzioni), "nome": "  Mac  dello studio "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["passkey"][0]["nome"], "Mac dello studio")
        self.assertEqual(len(r.json()["codici_riserva"]), 10)
        # La stessa sfida non si riusa.
        self.assertEqual(c.post("/api/auth/2fa/passkey/conferma", json={"credenziale": chiave.crea(opzioni)}).status_code, 400)

        self.assertEqual(self.password("chiave@example.com")["metodi"], ["passkey"])
        opzioni = c.post("/api/auth/2fa/passkey/opzioni").json()
        # Firmata per un altro sito: rifiutata.
        falsa = chiave.usa(opzioni, origine="https://phishing.example")
        self.assertEqual(c.post("/api/auth/2fa/passkey/verifica", json={"credenziale": falsa}).status_code, 400)
        opzioni = c.post("/api/auth/2fa/passkey/opzioni").json()
        r = c.post("/api/auth/2fa/passkey/verifica", json={"credenziale": chiave.usa(opzioni)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(c.get("/api/auth/me").json()["authenticated"])

        # L'amministratore la vede e la toglie a chi ha perso tutto.
        self.c.post("/api/auth/logout")
        self.assertEqual(c.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code, 200)
        utenti = {u["email"]: u for u in c.get("/api/admin/users").json()}
        self.assertEqual(utenti["chiave@example.com"]["due_fattori"], ["passkey"])
        r = c.post(f"/api/admin/users/{utenti['chiave@example.com']['id']}/2fa/reset")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.password("chiave@example.com")["due_fattori"])

    def test_avviso_nella_pagina_di_accesso(self):
        c = self.c
        c.post("/api/auth/logout")
        self.assertNotIn('id="avviso-manutenzione"', c.get("/login").text)
        c.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
        domani = (manutenzione.adesso() + timedelta(days=1)).date().isoformat()
        r = c.put("/api/admin/manutenzione", json={"attiva": True, "giorno": domani, "dalle": "22:00", "alle": "23:30",
                                                   "nota": "Aggiorniamo <b>il</b> server"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["stato"], "programmata")
        pagina = c.get("/login").text
        self.assertIn('id="avviso-manutenzione"', pagina)
        self.assertIn("dalle 22:00 alle 23:30", pagina)
        self.assertIn("Aggiorniamo &lt;b&gt;il&lt;/b&gt; server", pagina)   # la nota non è HTML
        r = c.get("/", follow_redirects=False)                                 # la radice: pannello se loggati
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/app"))

        for sbagliato in ({"giorno": "2020-01-01"}, {"alle": "22:00"}, {"dalle": "25:00"}, {"giorno": ""}):
            r = c.put("/api/admin/manutenzione", json={"attiva": True, "giorno": domani, "dalle": "22:00", "alle": "23:30", **sbagliato})
            self.assertEqual(r.status_code, 400, sbagliato)
        # Spento, si salva anche a metà; e la pagina di accesso torna pulita.
        self.assertEqual(c.put("/api/admin/manutenzione", json={"attiva": False, "giorno": domani}).status_code, 200)
        self.assertNotIn('id="avviso-manutenzione"', c.get("/login").text)

        # Il cliente non può toccarlo.
        _crea_utente("curioso@example.com")
        self.password("curioso@example.com")
        self.assertEqual(c.put("/api/admin/manutenzione", json={"attiva": False}).status_code, 403)


class Tentativi(unittest.TestCase):
    """Il tetto ai codici sbagliati sta nel database e i codici si consumano in modo atomico."""

    def setUp(self):
        _crea_utente("tetto@example.com")
        self.db = SessionLocal()
        self.utente = self.db.query(User).filter(User.email == "tetto@example.com").one()
        due_fattori.azzera_tentativi(self.utente)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_tetto_nel_database(self):
        from app import security

        for _ in range(due_fattori.ERRORI_MASSIMI):
            self.assertTrue(due_fattori.prenota_tentativo(self.db, self.utente))
        security._attempts.clear()      # svuotare la memoria non azzera niente
        self.assertFalse(due_fattori.prenota_tentativo(self.db, self.utente))
        # Passata la finestra si riparte.
        self.utente.errori_2fa_dal = datetime.now(timezone.utc) - timedelta(seconds=due_fattori.ERRORI_FINESTRA + 5)
        self.db.commit()
        self.assertTrue(due_fattori.prenota_tentativo(self.db, self.utente))

    def test_stesso_codice_in_parallelo(self):
        codici = due_fattori.nuovi_codici_riserva(self.utente)
        self.utente.totp_ultimo_passo = 0
        self.db.commit()
        # Due richieste che hanno letto l'utente nello stesso momento.
        altro = SessionLocal()
        copia = altro.query(User).filter(User.id == self.utente.id).one()
        self.assertTrue(due_fattori.usa_codice_riserva(self.db, self.utente, codici[0]))
        self.assertFalse(due_fattori.usa_codice_riserva(altro, copia, codici[0]))
        self.assertTrue(due_fattori.consuma_passo(self.db, self.utente, 100))
        self.assertFalse(due_fattori.consuma_passo(self.db, self.utente, 100))
        altro.close()

    def test_tetto_alle_email(self):
        self.utente.invii_email, self.utente.invii_email_dal = 0, None
        for _ in range(due_fattori.EMAIL_ORA):
            self.assertFalse(due_fattori.troppi_invii(self.utente))
            due_fattori.prepara_codice_email(self.utente, "accesso")
        self.assertTrue(due_fattori.troppi_invii(self.utente))


class RigaDiComando(unittest.TestCase):
    def test_togli(self):
        _crea_utente("riga@example.com")
        db = SessionLocal()
        utente = db.query(User).filter(User.email == "riga@example.com").one()
        utente.email_2fa = True
        due_fattori.nuovi_codici_riserva(utente)
        db.commit()
        db.close()
        self.assertEqual(due_fattori._togli_da_riga_di_comando("RIGA@example.com"), 0)
        db = SessionLocal()
        utente = db.query(User).filter(User.email == "riga@example.com").one()
        self.assertFalse(due_fattori.attiva(utente))
        self.assertEqual(due_fattori.riserva_rimasti(utente), 0)
        db.close()
        self.assertEqual(due_fattori._togli_da_riga_di_comando("nessuno@example.com"), 1)


if __name__ == "__main__":
    unittest.main()
