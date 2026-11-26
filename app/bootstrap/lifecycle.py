"""Bootstrap / lifecycle."""

import asyncio
import os
import stripe
from app.bootstrap.runtime import app
from app.bootstrap.runtime import logger
from app.bootstrap.settings import ENABLE_LEGACY_FOLLOWUP_WORKER
from app.bootstrap.settings import STRIPE_WEBHOOK_SECRET
from app.database import SessionLocal
from app.jobs.service import enqueue_trust_data_cleanup_job
from app.settlements import run_settlement_cycle


def _run_settlement_worker_cycle():
    from app.features.verification.service import _verification_satisfies

    with SessionLocal() as db:
        return run_settlement_cycle(db, _verification_satisfies)


async def _settlement_worker():
    while True:
        try:
            await asyncio.to_thread(_run_settlement_worker_cycle)
        except Exception:
            logger.exception("Study settlement worker cycle failed")
        await asyncio.sleep(60)


async def stop_settlement_worker():
    """Stop every application-owned worker, retaining the original hook name."""
    tasks = []
    for name in ("settlement_worker", "survey_start_followup_worker"):
        task = getattr(app.state, name, None)
        if task is not None:
            task.cancel()
            tasks.append(task)
            setattr(app.state, name, None)
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def start_survey_followup_worker() -> None:
    from app.features.jobs.followups import _survey_start_followup_worker

    logger.info('Release configuration: stripe_mode=%s webhook_configured=%s settlement_worker=%s',
        'live' if str(stripe.api_key or '').startswith(('sk_live_', 'rk_live_')) else
        ('test' if str(stripe.api_key or '').startswith(('sk_test_', 'rk_test_')) else 'missing'),
        bool(STRIPE_WEBHOOK_SECRET), os.environ.get('INSIGHTA_ENABLE_SETTLEMENT_WORKER', '1'))
    # Finish initialization before spawning tasks: a failed startup must not
    # leave a settlement worker running without a completed application lifespan.
    db = SessionLocal()
    try:
        enqueue_trust_data_cleanup_job(db)
        db.commit()
    finally:
        db.close()
    if os.environ.get("INSIGHTA_ENABLE_SETTLEMENT_WORKER", "1").lower() in {"1", "true", "yes", "on"}:
        task = getattr(app.state, "settlement_worker", None)
        if task is None or task.done():
            app.state.settlement_worker = asyncio.create_task(_settlement_worker())
    if ENABLE_LEGACY_FOLLOWUP_WORKER:
        task = getattr(app.state, "survey_start_followup_worker", None)
        if task is None or task.done():
            app.state.survey_start_followup_worker = asyncio.create_task(_survey_start_followup_worker())


__all__ = ['_run_settlement_worker_cycle', '_settlement_worker', 'stop_settlement_worker', 'start_survey_followup_worker']
