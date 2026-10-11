"""Abbonamento e quota.

Regola: cercare le schede e vedere le anteprime è sempre concesso, così il cliente
può lavorare e valutare. Quello che si blocca è l'esportazione sul sito, che è la parte
che vale. Il controllo sta in tre punti e non uno solo:

  1. quando si crea un'importazione con pubblicazione automatica
  2. quando si preme Pubblica su un'importazione ferma in anteprima
  3. dentro il worker, appena prima di ogni singola chiamata al sito

Il terzo è quello che conta davvero: se l'abbonamento scade mentre una coda da 800 EAN
sta girando, la coda si ferma da sola invece di svuotarsi lo stesso.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Client, Job, JobItem, Site

ACTIVE_STATES = ("active", "trial")

LABELS = {
    "active": "Attivo",
    "trial": "In prova",
    "suspended": "Sospeso",
    "expired": "Scaduto",
}


@dataclass
class Entitlement:
    allowed: bool
    reason: str
    status: str
    plan: str
    expires_at: datetime | None
    quota: int
    used: int

    @property
    def remaining(self) -> int | None:
        return None if not self.quota else max(0, self.quota - self.used)


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def used_this_month(db: Session, client_id: int) -> int:
    """Prodotti NUOVI arrivati sul sito dal primo del mese.

    Gli aggiornamenti non consumano quota: il cliente paga per i prodotti che
    mette a catalogo, non per tenerli aggiornati. Altrimenti il giro notturno
    gli brucerebbe il piano da solo."""
    return int(
        db.scalar(
            select(func.count(JobItem.id))
            .join(Job, Job.id == JobItem.job_id)
            .join(Site, Site.id == Job.site_id)
            .where(
                Site.client_id == client_id,
                JobItem.status == "pushed",
                JobItem.wc_action == "created",
                JobItem.updated_at >= _month_start(),
            )
        )
        or 0
    )


def check(db: Session, client: Client) -> Entitlement:
    """Stato completo, pronto sia per il gate sia per la UI."""
    used = used_this_month(db, client.id)
    expires = client.subscription_until
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    base = dict(
        status=client.subscription_status,
        plan=client.plan_name,
        expires_at=expires,
        quota=client.monthly_quota,
        used=used,
    )

    if not client.is_active:
        return Entitlement(False, "Account sospeso. Contatta l'amministratore del portale.", **base)

    if client.subscription_status not in ACTIVE_STATES:
        label = LABELS.get(client.subscription_status, client.subscription_status)
        return Entitlement(
            False,
            f"Abbonamento {label.lower()}: le schede si scaricano e si vedono, ma non si pubblicano sul sito.",
            **base,
        )

    if expires and expires < datetime.now(timezone.utc):
        return Entitlement(
            False,
            "Abbonamento scaduto il " + expires.strftime("%d/%m/%Y") + ": pubblicazione sospesa.",
            **base,
        )

    if client.monthly_quota and used >= client.monthly_quota:
        return Entitlement(
            False,
            f"Quota del mese esaurita ({used}/{client.monthly_quota} prodotti). Riparte il primo del mese prossimo.",
            **base,
        )

    return Entitlement(True, "", **base)


def as_json(ent: Entitlement) -> dict:
    return {
        "allowed": ent.allowed,
        "reason": ent.reason,
        "status": ent.status,
        "status_label": LABELS.get(ent.status, ent.status),
        "plan": ent.plan,
        "expires_at": ent.expires_at.isoformat() if ent.expires_at else None,
        "quota": ent.quota,
        "used": ent.used,
        "remaining": ent.remaining,
    }
