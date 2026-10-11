"""Ambiente comune dei test: database SQLite temporaneo, chiavi di prova, niente
coda né pianificatore. Va importato PRIMA di qualunque modulo dell'app."""
import os
import sys
import tempfile

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARTELLA = tempfile.mkdtemp(prefix="catasync-test-")

os.environ.setdefault("DATABASE_URL", f"sqlite:///{CARTELLA}/hub.db")
os.environ.setdefault("SECRET_KEY", "test-only-" + "x" * 40)
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "provaprova123")
os.environ.setdefault("UPLOAD_DIR", os.path.join(CARTELLA, "up"))
os.environ.update(WORKER_ENABLED="0", SCHEDULER_ENABLED="0", COOKIE_SECURE="0")
if not os.environ.get("FERNET_KEY"):
    from cryptography.fernet import Fernet

    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

if RADICE not in sys.path:
    sys.path.insert(0, RADICE)
