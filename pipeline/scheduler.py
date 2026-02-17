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

# Agents that use the simpler Agent base class (config dict, no db arg)
SIMPLE_AGENTS = {"atlas", "rival", "remix"}


def _build_simple_config(config: Config) -> dict[str, Any]:
    """Convert Config dataclass to dict for simple Agent base class."""
    return {
        "anthropic_api_key": config.anthropic.api_key,
        "blog_output_dir": config.blog.output_dir,
        "site_url": config.blog.site_url,
        "min_articles_for_analysis": config.atlas.min_articles_for_analysis,
        "min_confidence_score": config.atlas.min_confidence_score,
        "competitor_domains": config.rival.competitor_domains,
        "target_keywords": config.rival.target_keywords,
        "max_competitor_checks": config.rival.max_competitor_checks,
        "remix_types": config.remix.remix_types,
        "max_remixes_per_run": config.remix.max_remixes_per_run,
    }


def _run_simple_agent(agent_name: str, config: Config) -> dict[str, Any]:
    """Run agents that use the simpler Agent base class."""
    config_dict = _build_simple_config(config)

    if agent_name == "atlas":
        if not config.atlas.enabled:
            return {"status": "skipped", "reason": "atlas_disabled"}
        from pipeline.agents.atlas import Atlas
        agent = Atlas(config_dict)
    elif agent_name == "rival":
        if not config.rival.enabled:
            return {"status": "skipped", "reason": "rival_disabled"}
        from pipeline.agents.rival import Rival
        agent = Rival(config_dict)
    elif agent_name == "remix":
        if not config.remix.enabled:
            return {"status": "skipped", "reason": "remix_disabled"}
        from pipeline.agents.remix import Remix
        agent = Remix(config_dict)
    else:
        raise ValueError(f"Unknown simple agent: {agent_name}")

    return agent.run()


def run_agent(agent_name: str, config: Config, db: Database) -> dict[str, Any]:
    """Run a single agent and return its result."""
    logger.info(f"Running {agent_name}...")

    try:
        if agent_name in SIMPLE_AGENTS:
            result = _run_simple_agent(agent_name, config)
        else:
            agent_cls = AGENT_MAP.get(agent_name)
            if not agent_cls:
                raise ValueError(
                    f"Unknown agent: {agent_name}. "
                    f"Available: {sorted(list(AGENT_MAP) + list(SIMPLE_AGENTS))}"
                )
            agent = agent_cls(config=config, db=db)
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
    pipeline_order = [
        "scout", "quill", "sage", "ezra", "herald", "lurker", "morgan",
        "atlas", "rival", "remix",
    ]

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
        "atlas": config.schedule.atlas,
        "rival": config.schedule.rival,
        "remix": config.schedule.remix,
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
