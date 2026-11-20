"""Svuota la cache delle pagine dei siti (FastCGI di Nginx) dopo che il portale ci ha scritto.

Ogni scrittura riuscita sul sito (wc_rest._request) segna il sito. Quando per QUIETE secondi non
ne arrivano altre, si chiede una volta sola al mu-plugin «CataSync» (wordpress/catasync.php) di cancellare
le pagine di quel sito: un carico da cinquanta prodotti fa uno svuotamento, non cinquanta.
Solo per i siti con lo svuotamento acceso nelle impostazioni.
"""
import asyncio
import logging
import time

from . import wc_rest
from .db import SessionLocal
from .models import Site

log = logging.getLogger("catasync.cache")

QUIETE = 15.0      # secondi senza scritture prima di svuotare
GIRO = 5.0         # ogni quanto si controlla

# id del sito → momento dell'ultima scrittura (time.monotonic). shortcut: in memoria del processo;
# con più processi ognuno svuota per le scritture sue, che va bene lo stesso.
_segnati: dict[int, float] = {}


def segna(site) -> None:
    if getattr(site, "cache_purge", False) and getattr(site, "id", None):
        _segnati[site.id] = time.monotonic()


async def giro(adesso: float | None = None) -> list[int]:
    """Svuota i siti fermi da almeno QUIETE secondi. Restituisce gli id svuotati (o tentati)."""
    adesso = time.monotonic() if adesso is None else adesso
    pronti = [sid for sid, quando in list(_segnati.items()) if adesso - quando >= QUIETE]
    for sid in pronti:
        _segnati.pop(sid, None)
        db = SessionLocal()
        try:
            site = db.get(Site, sid)
            if not site or not site.cache_purge:
                continue
            esito = await wc_rest.svuota_cache(site)
            log.info("Cache svuotata su %s: %s file su %s", site.base_url, esito.get("svuotati"), esito.get("letti"))
        except wc_rest.SiteError as exc:
            log.warning("Cache non svuotata sul sito %s: %s", sid, exc)
        finally:
            db.close()
    return pronti


async def run_forever() -> None:
    while True:
        await asyncio.sleep(GIRO)
        try:
            await giro()
        except Exception:      # un giro andato male non deve fermare i successivi
            log.exception("Svuotamento della cache non riuscito")
