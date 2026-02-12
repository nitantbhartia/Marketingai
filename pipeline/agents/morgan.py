"""Morgan agent — project manager.

Monitors pipeline health, spawns agents to fix bottlenecks,
and generates weekly reports.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus

logger = logging.getLogger(__name__)


class MorganAgent(BaseAgent):
    name = "morgan"

    def run(self) -> dict[str, Any]:
        """Run all health checks and return a pipeline status report."""
        checks = {}

        checks["backlog"] = self._check_backlog()
        checks["pipeline_flow"] = self._check_pipeline_flow()
        checks["revision_loops"] = self._check_revision_loops()
        checks["publishing_cadence"] = self._check_publishing_cadence()
        checks["social_amplification"] = self._check_social_amplification()
        checks["quality_trends"] = self._check_quality_trends()

        # Overall health
        alerts = []
        spawn_actions = []
        for name, result in checks.items():
            if result.get("alert"):
                alerts.append(f"{name}: {result['alert']}")
            if result.get("spawn"):
                spawn_actions.append(result["spawn"])

        health = "healthy" if not alerts else "needs_attention"
        if len(alerts) >= 3:
            health = "critical"

        summary = self.db.get_pipeline_summary()

        report = {
            "health": health,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_summary": summary,
            "checks": checks,
            "alerts": alerts,
            "spawn_actions": spawn_actions,
        }

        # Record the health check metric
        self.db.record_metric(
            "morgan_health_check",
            len(alerts),
            json.dumps(report, default=str),
        )

        logger.info(f"Pipeline health: {health} ({len(alerts)} alerts)")
        for alert in alerts:
            logger.warning(f"ALERT: {alert}")

        return report

    def generate_weekly_report(self) -> str:
        """Generate a human-readable weekly report."""
        summary = self.db.get_pipeline_summary()
        published_this_week = self.db.get_articles_published_this_week()
        stuck = self.db.get_stuck_articles(hours=24)

        # Get quality metrics
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        sage_metrics = self.db.get_metrics(name="sage_run", since=week_ago)

        total_reviewed = 0
        total_approved = 0
        for m in sage_metrics:
            try:
                details = json.loads(m.details) if m.details else {}
                total_reviewed += details.get("approved", 0) + details.get("revision", 0) + details.get("rejected", 0)
                total_approved += details.get("approved", 0)
            except (json.JSONDecodeError, AttributeError):
                pass

        pass_rate = (total_approved / total_reviewed * 100) if total_reviewed > 0 else 0

        lines = [
            "# ClaimCoach Content Pipeline — Weekly Report",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Pipeline Status",
            f"- Backlog: {summary.get('backlog', 0)} topics",
            f"- To Do: {summary.get('todo', 0)} articles",
            f"- In Progress: {summary.get('in_progress', 0)} articles",
            f"- In Review: {summary.get('review', 0)} articles",
            f"- In Revision: {summary.get('revision', 0)} articles",
            f"- Ready to Publish: {summary.get('ready_to_publish', 0)} articles",
            f"- Published (Done): {summary.get('done', 0)} articles",
            f"- Amplified: {summary.get('amplified', 0)} articles",
            f"- Rejected: {summary.get('rejected', 0)} articles",
            f"- **Total:** {summary.get('total', 0)} articles",
            "",
            "## This Week",
            f"- Articles published: {len(published_this_week)}",
            f"- Articles reviewed: {total_reviewed}",
            f"- First-draft pass rate: {pass_rate:.0f}%",
            "",
        ]

        if published_this_week:
            lines.append("### Published Articles")
            for a in published_this_week:
                lines.append(f"- [{a.title}]({a.published_url}) — keyword: {a.target_keyword}")
            lines.append("")

        if stuck:
            lines.append("## Stuck Articles (24h+)")
            for a in stuck:
                lines.append(f"- {a.title} — status: {a.status} — last updated: {a.updated_at}")
            lines.append("")

        # Alerts
        health_result = self.run()
        if health_result["alerts"]:
            lines.append("## Alerts")
            for alert in health_result["alerts"]:
                lines.append(f"- {alert}")
            lines.append("")

        lines.append("## Recommended Actions")
        for action in health_result.get("spawn_actions", []):
            lines.append(f"- Spawn **{action}**")
        if not health_result.get("spawn_actions"):
            lines.append("- No actions needed — pipeline is healthy")

        return "\n".join(lines)

    def _check_backlog(self) -> dict:
        """Are there enough topics in the backlog?"""
        count = self.db.count_articles(ArticleStatus.BACKLOG.value)
        min_required = self.config.pipeline.min_backlog_topics
        result: dict[str, Any] = {"backlog_count": count, "min_required": min_required}

        if count < min_required:
            result["alert"] = f"Backlog low: {count}/{min_required} topics"
            result["spawn"] = "scout"
        else:
            result["status"] = "healthy"

        return result

    def _check_pipeline_flow(self) -> dict:
        """Are articles stuck in any status for 24+ hours?"""
        stuck = self.db.get_stuck_articles(hours=24)
        result: dict[str, Any] = {"stuck_count": len(stuck)}

        if stuck:
            statuses = {}
            for a in stuck:
                statuses[a.status] = statuses.get(a.status, 0) + 1

            result["stuck_by_status"] = statuses
            result["alert"] = f"{len(stuck)} articles stuck for 24h+: {statuses}"

            # Determine which agent to spawn based on where things are stuck
            if statuses.get(ArticleStatus.TODO.value, 0) > 2:
                result["spawn"] = "quill"
            elif statuses.get(ArticleStatus.REVIEW.value, 0) > 3:
                result["spawn"] = "sage"
            elif statuses.get(ArticleStatus.READY_TO_PUBLISH.value, 0) > 2:
                result["spawn"] = "ezra"
        else:
            result["status"] = "healthy"

        return result

    def _check_revision_loops(self) -> dict:
        """Any article on revision round 3?"""
        max_rounds = self.config.pipeline.max_revision_rounds
        articles = self.db.query_articles(status=ArticleStatus.REVISION.value, limit=50)
        high_revision = [a for a in articles if a.revision_count >= max_rounds - 1]

        result: dict[str, Any] = {
            "in_revision": len(articles),
            "near_max_rounds": len(high_revision),
        }

        if high_revision:
            result["alert"] = (
                f"{len(high_revision)} articles near max revision rounds "
                f"(round {max_rounds}). Human review needed."
            )
            titles = [a.title for a in high_revision[:5]]
            result["articles"] = titles
        else:
            result["status"] = "healthy"

        return result

    def _check_publishing_cadence(self) -> dict:
        """Are we hitting 3-5 articles/week?"""
        published = self.db.get_articles_published_this_week()
        target = self.config.pipeline.articles_per_week_target

        result: dict[str, Any] = {
            "published_this_week": len(published),
            "target": target,
        }

        if len(published) < target - 1:  # Allow 1 under target
            result["alert"] = (
                f"Publishing behind: {len(published)}/{target} articles this week"
            )
            result["spawn"] = "quill"
        else:
            result["status"] = "on_track"

        return result

    def _check_social_amplification(self) -> dict:
        """Are published articles getting promoted within 24h?"""
        done_articles = self.db.query_articles(status=ArticleStatus.DONE.value, limit=50)
        unpromoted_old = []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        for a in done_articles:
            if not a.social_status and a.published_at and a.published_at < cutoff:
                unpromoted_old.append(a)

        result: dict[str, Any] = {"unpromoted_over_24h": len(unpromoted_old)}

        if unpromoted_old:
            result["alert"] = (
                f"{len(unpromoted_old)} published articles not promoted within 24h"
            )
            result["spawn"] = "herald"
        else:
            result["status"] = "healthy"

        return result

    def _check_quality_trends(self) -> dict:
        """Is Sage's pass rate dropping below 50%?"""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        metrics = self.db.get_metrics(name="sage_run", since=week_ago)

        total_reviewed = 0
        total_approved = 0
        for m in metrics:
            try:
                details = json.loads(m.details) if m.details else {}
                total_reviewed += (
                    details.get("approved", 0)
                    + details.get("revision", 0)
                    + details.get("rejected", 0)
                )
                total_approved += details.get("approved", 0)
            except (json.JSONDecodeError, AttributeError):
                pass

        result: dict[str, Any] = {
            "reviewed_this_week": total_reviewed,
            "approved_this_week": total_approved,
        }

        if total_reviewed > 0:
            pass_rate = total_approved / total_reviewed
            result["pass_rate"] = round(pass_rate * 100, 1)
            if pass_rate < 0.5:
                result["alert"] = (
                    f"Quality dropping: {result['pass_rate']}% pass rate "
                    f"(target: 50%+). Review Quill's writing prompt."
                )
        else:
            result["status"] = "no_data"

        return result
