"""Regole delle release automatiche (.github/scripts/rilascio.py) e versione del portale."""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

import _ambiente  # noqa: F401
from app import config

_spec = importlib.util.spec_from_file_location(
    "rilascio", os.path.join(_ambiente.RADICE, ".github", "scripts", "rilascio.py"))
rilascio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rilascio)

CHANGELOG = ("# Changelog\n\n## 2.0.0 — Prima versione pubblica\n\n### Added\n- Tutto.\n\n"
             "## 1.x\n\nVersioni interne.\n")


class Numero(unittest.TestCase):
    def test_versione_nuova_in_version_txt(self):
        self.assertEqual(rilascio.prossima("2.0.0", [], []), ("2.0.0", False))
        self.assertEqual(rilascio.prossima("2.1.0", ["2.0.0", "2.0.1"], []), ("2.1.0", False))

    def test_senza_numero_nuovo_sale_l_ultima_cifra(self):
        self.assertEqual(rilascio.prossima("2.0.0", ["2.0.0"], ["Correzioni"]), ("2.0.1", True))
        self.assertEqual(rilascio.prossima("2.0.1", ["2.0.0", "2.0.1", "v1.9.9"], []), ("2.0.2", True))

    def test_minor_e_major_nel_messaggio(self):
        self.assertEqual(rilascio.prossima("2.0.3", ["2.0.3"], ["Esportazione CSV [minor]"]), ("2.1.0", True))
        self.assertEqual(rilascio.prossima("2.0.3", ["2.0.3"], ["x", "Riscrittura [MAJOR]"]), ("3.0.0", True))

    def test_version_txt_piu_vecchio_dell_ultima_release(self):
        self.assertEqual(rilascio.prossima("2.0.0", ["2.0.0", "2.4.1"], []), ("2.4.2", True))

    def test_numero_non_valido(self):
        with self.assertRaises(ValueError):
            rilascio.prossima("due", [], [])


class SegnaliNeiCommit(unittest.TestCase):
    """[minor] e [major] valgono solo nel titolo del commit, non nel resto del messaggio."""

    def setUp(self):
        self.prima = os.getcwd()
        self.cartella = tempfile.mkdtemp(prefix="rilascio-")
        os.chdir(self.cartella)
        for comando in (["init", "-q"], ["config", "user.name", "Prova"], ["config", "user.email", "prova@example.com"]):
            subprocess.run(["git", *comando], check=True)

    def tearDown(self):
        os.chdir(self.prima)
        shutil.rmtree(self.cartella, ignore_errors=True)

    def commit(self, *messaggio):
        args = [x for m in messaggio for x in ("-m", m)]
        subprocess.run(["git", "commit", "-q", "--allow-empty", *args], check=True)

    def test_solo_il_titolo_conta(self):
        self.commit("Prima versione")
        subprocess.run(["git", "tag", "2.0.0"], check=True)
        self.commit("Guida delle release", "Spiega [minor] e [major] nel messaggio di commit.")
        voci = rilascio.commit_da("2.0.0", "HEAD")
        self.assertEqual([v[1] for v in voci], ["Guida delle release"])
        self.assertEqual(rilascio.prossima("2.0.0", ["2.0.0"], [v[2] for v in voci]), ("2.0.1", True))
        self.commit("Esportazione CSV [minor]")
        voci = rilascio.commit_da("2.0.0", "HEAD")
        self.assertEqual(rilascio.prossima("2.0.0", ["2.0.0"], [v[2] for v in voci]), ("2.1.0", True))
        self.assertEqual(voci[0][1], "Esportazione CSV")


class Changelog(unittest.TestCase):
    def test_voce_della_versione(self):
        self.assertEqual(rilascio.sezione(CHANGELOG, "2.0.0"), "### Added\n- Tutto.")
        self.assertIsNone(rilascio.sezione(CHANGELOG, "2.0.1"))
        self.assertIsNone(rilascio.sezione(CHANGELOG, "2.0"))

    def test_non_rilasciato_prende_il_numero(self):
        testo = CHANGELOG.replace("# Changelog\n\n", "# Changelog\n\n## Non rilasciato\n\n### Fixed\n- Prezzi.\n\n")
        nuovo, corpo = rilascio.chiudi_voce_aperta(testo, "2.0.1", "2026-10-12")
        self.assertIn("## 2.0.1 — 2026-10-12\n\n### Fixed\n- Prezzi.", nuovo)
        self.assertEqual(corpo, "### Fixed\n- Prezzi.")
        self.assertIsNone(rilascio.chiudi_voce_aperta(CHANGELOG, "2.0.1", "2026-10-12"))

    def test_voce_dai_commit_in_cima(self):
        nuovo = rilascio.aggiungi_voce(CHANGELOG, "2.0.1", "2026-10-12", ["README sistemato"])
        self.assertTrue(nuovo.startswith("# Changelog\n\n## 2.0.1 — 2026-10-12\n\n- README sistemato\n\n## 2.0.0"))

    def test_elenco_delle_ritirate(self):
        testo = "# commento\n3.0.0  uscita per errore\n\nnon-un-numero\n2.9.9 # altro\n"
        self.assertEqual(rilascio.ritirate(testo), ["3.0.0", "2.9.9"])

    def test_oggetto_senza_segnali(self):
        self.assertEqual(rilascio.pulisci_oggetto("Esportazione CSV [minor]"), "Esportazione CSV")

    def test_piede_delle_note(self):
        piede = rilascio.piede("Giuseppe-sciarra/CataSync", "2.0.1", "2.0.0")
        self.assertIn("compare/2.0.0...2.0.1", piede)
        self.assertIn("commits/2.0.0", rilascio.piede("Giuseppe-sciarra/CataSync", "2.0.0", None))


class Versione(unittest.TestCase):
    def test_il_portale_legge_version_txt(self):
        with open(os.path.join(_ambiente.RADICE, "VERSION.txt"), encoding="utf-8") as f:
            self.assertEqual(config.APP_VERSION, f.read().strip())
        self.assertIsNotNone(rilascio.numero(config.APP_VERSION))


if __name__ == "__main__":
    unittest.main()
