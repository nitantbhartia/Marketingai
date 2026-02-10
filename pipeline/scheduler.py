"""Pipeline orchestrator with APScheduler-based scheduling.

Runs all agents on their configured schedules, or allows
individual/all agents to be run on-demand.
"""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline.config import Config
from pipeline.db import Database
from pipeline.agents.scout import ScoutAgent
from pipeline.agents.quill import QuillAgent
from pipeline.agents.sage import SageAgent
from pipeline.agents.ezra import EzraAgent
from pipeline.agents.herald import HeraldAgent
from pipeline.agents.lurker import LurkerAgent
from pipeline.agents.morgan import MorganAgent

logger = logging.getLogger(__name__)

AGENT_MAP = {
    "scout": ScoutAgent,
    "quill": QuillAgent,
    "sage": SageAgent,
    "ezra": EzraAgent,
    "herald": HeraldAgent,
    "lurker": LurkerAgent,
    "morgan": MorganAgent,
}


def run_agent(agent_name: str, config: Config, db: Database) -> dict[str, Any]:
    """Run a single agent and return its result."""
    agent_cls = AGENT_MAP.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Unknown agent: {agent_name}. Available: {list(AGENT_MAP)}")

    agent = agent_cls(config=config, db=db)
    logger.info(f"Running {agent_name}...")

    try:
        result = agent.run()
        logger.info(f"{agent_name} completed: {result}")
        return result
    except Exception as e:
        logger.error(f"{agent_name} failed: {e}", exc_info=True)
        db.record_metric(f"{agent_name}_error", 1, str(e))
        return {"status": "error", "error": str(e)}


def run_all_agents(config: Config, db: Database) -> dict[str, Any]:
    """Run all agents in pipeline order (for manual/one-shot execution)."""
    results = {}
    pipeline_order = ["scout", "quill", "sage", "ezra", "herald", "lurker", "morgan"]

    for agent_name in pipeline_order:
        results[agent_name] = run_agent(agent_name, config, db)

    return results


def _make_job(agent_name: str, config: Config, db: Database):
    """Create a job function for the scheduler."""
    def job():
        run_agent(agent_name, config, db)
    job.__name__ = f"run_{agent_name}"
    return job


def start_scheduler(config: Config, db: Database) -> None:
    """Start the APScheduler with all agents on their configured schedules."""
    scheduler = BlockingScheduler()

    schedule_map = {
        "scout": config.schedule.scout,
        "quill": config.schedule.quill,
        "sage": config.schedule.sage,
        "ezra": config.schedule.ezra,
        "herald": config.schedule.herald,
        "lurker": config.schedule.lurker,
        "morgan": config.schedule.morgan,
    }

    for agent_name, cron_expr in schedule_map.items():
        parts = cron_expr.split()
        if len(parts) != 5:
            logger.warning(f"Invalid cron expression for {agent_name}: {cron_expr}")
            continue

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        job_fn = _make_job(agent_name, config, db)
        scheduler.add_job(job_fn, trigger, id=agent_name, name=agent_name)
        logger.info(f"Scheduled {agent_name}: {cron_expr}")

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Pipeline scheduler starting. Press Ctrl+C to stop.")
    scheduler.start()
