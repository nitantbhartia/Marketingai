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
        checks["critical_rejections"] = self._check_critical_rejections()
        checks["review_timeout"] = self._check_review_timeout()
        checks["refresh_queue"] = self._queue_decay_refreshes()

        # Distill cross-agent lessons from performance data
        checks["learning"] = self._distill_performance_lessons()

        # Meta-learning: is the learning system itself working?
        checks["learning_health"] = self._assess_learning_health()

        # Release stale claim locks (agents that crashed mid-processing).
        # 6h is sufficient — all normal write/review cycles finish in <2h.
        cleared = self.db.clear_stale_claims(hours=6)
        if cleared:
            logger.info(f"Released {cleared} stale claim lock(s)")
            checks["stale_claims"] = {"cleared": cleared, "alert": f"{cleared} stale claim(s) released"}

        # Decay old lessons so stale patterns fade
        decayed = self.db.decay_lessons(older_than_days=30)
        if decayed:
            logger.info(f"Decayed {decayed} stale lessons")

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
        roi = self.db.get_roi_kpis(days=90)

        lines = [
            "# ClaimCoach Content Pipeline — Weekly Report",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Pipeline Status",
            f"- Backlog: {summary.get('backlog', 0)} topics",
            f"- To Do: {summary.get('todo', 0)} articles",
            f"- In Progress: {summary.get('in_progress', 0)} articles",
            f"- Sage Scoring: {summary.get('editor_review', 0)} articles",
            f"- Your Review: {summary.get('review', 0)} articles",
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
            f"- Estimated cost per published article (90d): ${roi.get('cost_per_published_usd', 0):.3f}",
            (
                f"- Avg time to index: {roi.get('avg_time_to_index_days')} days "
                f"({roi.get('indexed_articles', 0)} indexed)"
                if roi.get("avg_time_to_index_days") is not None
                else "- Avg time to index: N/A (insufficient indexed article data)"
            ),
            f"- Avg clicks/article (30d cohort): {roi.get('clicks_per_article', {}).get('30d', {}).get('avg_clicks', 0)}",
            f"- Avg clicks/article (60d cohort): {roi.get('clicks_per_article', {}).get('60d', {}).get('avg_clicks', 0)}",
            f"- Avg clicks/article (90d cohort): {roi.get('clicks_per_article', {}).get('90d', {}).get('avg_clicks', 0)}",
            "",
        ]

        clusters = roi.get("conversion_by_cluster", [])
        if clusters:
            lines.append("### Conversion by Cluster")
            for c in clusters[:6]:
                lines.append(
                    f"- {c.get('cluster')}: {c.get('conversion_rate')}% "
                    f"({c.get('conversions', 0)} conversions / {c.get('clicks', 0)} clicks)"
                )
            lines.append("")

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

    def _check_review_timeout(self) -> dict:
        """Auto-promote articles stuck in REVIEW status for more than 48 hours.

        REVIEW means Sage approved the article and it is waiting for a human
        to click 'publish'. If no human acts within 48 h, Morgan promotes the
        article to READY_TO_PUBLISH so Ezra can pick it up automatically.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        stale_review = self.db.query_articles(
            status=ArticleStatus.REVIEW.value, limit=50
        )
        promoted_ids: list[int] = []
        for article in stale_review:
            updated = article.updated_at or ""
            if updated < cutoff:
                self.db.update_article(
                    article.id,
                    status=ArticleStatus.READY_TO_PUBLISH.value,
                )
                promoted_ids.append(article.id)
                logger.info(
                    f"Auto-promoted article {article.id} from REVIEW to "
                    f"READY_TO_PUBLISH after 48h timeout"
                )

        result: dict[str, Any] = {"auto_promoted": len(promoted_ids)}
        if promoted_ids:
            result["alert"] = (
                f"{len(promoted_ids)} article(s) auto-promoted from REVIEW to "
                "READY_TO_PUBLISH after 48h (no human action received)"
            )
            result["promoted_ids"] = promoted_ids
        else:
            result["status"] = "healthy"
        return result

    def _check_critical_rejections(self) -> dict:
        """Alert when articles have been critically rejected this week.

        Critical rejections (legal/product/state/factual violations that Quill
        could not fix within the revision budget) require manual review.
        """
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        events = self.db.get_metrics(name="critical_rejection", since=week_ago)
        result: dict[str, Any] = {"critical_rejections_this_week": len(events)}
        if events:
            result["alert"] = (
                f"{len(events)} article(s) critically rejected this week — "
                "legal/product/state/factual violations unresolved. Manual review needed."
            )
        else:
            result["status"] = "healthy"
        return result

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
            elif statuses.get(ArticleStatus.EDITOR_REVIEW.value, 0) > 3:
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

    def _queue_decay_refreshes(self) -> dict:
        """Queue stale published articles for refresh as new backlog items."""
        now = datetime.now(timezone.utc)
        age_days = max(7, int(getattr(self.config.pipeline, "refresh_age_days", 30)))
        pos_threshold = float(getattr(self.config.pipeline, "refresh_position_threshold", 12.0))
        clicks_threshold = int(getattr(self.config.pipeline, "refresh_clicks_threshold", 3))
        monthly_cap = max(1, int(getattr(self.config.pipeline, "monthly_refresh_cap", 8)))

        since_30d = (now - timedelta(days=30)).isoformat()
        recent_refresh_events = self.db.get_metrics(name="refresh_queued", since=since_30d, limit=2000)
        queued_recently = len(recent_refresh_events)
        remaining = max(0, monthly_cap - queued_recently)

        result: dict[str, Any] = {
            "monthly_cap": monthly_cap,
            "queued_last_30d": queued_recently,
            "queued_now": 0,
        }
        if remaining <= 0:
            result["status"] = "cap_reached"
            return result

        published = self.db.query_articles(status=ArticleStatus.DONE.value, limit=500)
        existing_refresh = self.db.query_articles(status=ArticleStatus.BACKLOG.value, limit=500)
        refresh_keywords = {
            (a.target_keyword or "").strip().lower()
            for a in existing_refresh
            if (a.content_category or "").strip().lower() == "refresh"
        }

        candidates = []
        for a in published:
            if not a.published_at:
                continue
            pub_dt = self.db._parse_ts(a.published_at)  # internal helper is UTC-safe
            if not pub_dt:
                continue
            if (now - pub_dt).days < age_days:
                continue
            clicks = int(a.last_gsc_clicks or 0)
            pos = float(a.last_gsc_position or 100.0)
            if clicks > clicks_threshold and pos < pos_threshold:
                continue
            candidates.append((clicks, pos, a))

        # Highest leverage first: near-page-one with low clicks, then old low-perf.
        candidates.sort(key=lambda item: (item[1], item[0], item[2].published_at or ""), reverse=False)

        for _, _, article in candidates[:remaining]:
            kw = (article.target_keyword or "").strip()
            if not kw:
                continue
            if kw.lower() in refresh_keywords:
                continue
            refresh_brief = (
                "Refresh an existing published article based on decay/performance signals.\n"
                f"Source URL: {article.published_url or ''}\n"
                f"Keyword: {kw}\n"
                f"Current GSC snapshot: position={article.last_gsc_position or 'NA'}, "
                f"clicks={article.last_gsc_clicks or 0}\n"
                "- Keep canonical intent and URL target aligned.\n"
                "- Update stale facts/citations and improve conversion sections.\n"
            )
            self.db.create_article(
                product=article.product or "claimcoach",
                title=f"[Refresh] {article.title or kw.title()}",
                target_keyword=kw,
                target_state=article.target_state or "",
                status=ArticleStatus.BACKLOG.value,
                content_category="refresh",
                intent_template=article.intent_template or "general_guide",
                cluster_key=article.cluster_key or "",
                canonical_url=article.published_url or article.canonical_url or "",
                fact_pack=article.fact_pack or "",
                content_brief=refresh_brief,
                refresh_priority="HIGH",
                commercial_intent=article.commercial_intent or 0.8,
                search_volume=article.search_volume or 0,
                keyword_difficulty=article.keyword_difficulty or 0.0,
            )
            self.db.record_metric(
                "refresh_queued",
                1,
                json.dumps(
                    {
                        "source_article_id": article.id,
                        "keyword": kw,
                        "position": article.last_gsc_position,
                        "clicks": article.last_gsc_clicks,
                    }
                ),
            )
            refresh_keywords.add(kw.lower())
            result["queued_now"] += 1

        if result["queued_now"] > 0:
            result["status"] = "queued"
        else:
            result["status"] = "none_needed"
        return result

    def _distill_performance_lessons(self) -> dict:
        """Analyze published articles with GSC data and produce lessons for other agents.

        Runs every health check cycle. Looks at real search traffic to find
        what actually works, then stores structured lessons that Quill and
        Scout read on their next run.
        """
        published = self.db.query_articles(
            status=ArticleStatus.DONE.value, limit=200
        )
        articles_with_gsc = [a for a in published if a.last_gsc_clicks > 0]

        result: dict[str, Any] = {
            "published_count": len(published),
            "with_gsc_data": len(articles_with_gsc),
            "lessons_produced": 0,
        }

        if len(articles_with_gsc) < 5:
            result["status"] = "insufficient_data"
            return result

        # ── Word count vs. clicks: what length performs best? ──
        sorted_by_clicks = sorted(articles_with_gsc, key=lambda a: -a.last_gsc_clicks)
        top_quarter = sorted_by_clicks[:max(1, len(sorted_by_clicks) // 4)]
        avg_wc = sum(a.word_count for a in top_quarter) / len(top_quarter)
        self.record_lesson(
            "quill", "performance",
            f"Top-performing articles average {int(avg_wc)} words",
        )
        result["lessons_produced"] += 1

        # ── Category vs. clicks: which categories get organic traffic? ──
        cat_clicks: dict[str, list[int]] = {}
        for a in articles_with_gsc:
            cat = a.content_category or "general"
            cat_clicks.setdefault(cat, []).append(a.last_gsc_clicks)

        if cat_clicks:
            best_cat = max(
                cat_clicks,
                key=lambda c: sum(cat_clicks[c]) / len(cat_clicks[c]),
            )
            avg_clicks = sum(cat_clicks[best_cat]) / len(cat_clicks[best_cat])
            self.record_lesson(
                "scout", "gsc_best_category",
                f"'{best_cat}' gets most organic clicks (avg {avg_clicks:.0f}/article)",
            )
            result["lessons_produced"] += 1

        # ── State coverage: which states' content performs best? ──
        state_clicks: dict[str, list[int]] = {}
        for a in articles_with_gsc:
            if a.target_state:
                state_clicks.setdefault(a.target_state, []).append(a.last_gsc_clicks)

        if state_clicks:
            best_state = max(
                state_clicks,
                key=lambda s: sum(state_clicks[s]) / len(state_clicks[s]),
            )
            self.record_lesson(
                "scout", "gsc_best_state",
                f"'{best_state}' content gets most search traffic",
            )
            result["lessons_produced"] += 1

        # ── SEO score correlation: does Sage's score predict real traffic? ──
        high_seo = [a for a in articles_with_gsc if a.seo_score >= 18]
        low_seo = [a for a in articles_with_gsc if a.seo_score < 14]
        if high_seo and low_seo:
            avg_high = sum(a.last_gsc_clicks for a in high_seo) / len(high_seo)
            avg_low = sum(a.last_gsc_clicks for a in low_seo) / len(low_seo)
            if avg_high > avg_low * 1.5:
                self.record_lesson(
                    "quill", "performance",
                    f"High SEO scores correlate with {avg_high/max(avg_low, 1):.1f}x more clicks",
                )
                result["lessons_produced"] += 1

        # ── Readability correlation ──
        high_read = [a for a in articles_with_gsc if a.readability_score >= 60]
        low_read = [a for a in articles_with_gsc if a.readability_score < 50]
        if high_read and low_read:
            avg_hr = sum(a.last_gsc_clicks for a in high_read) / len(high_read)
            avg_lr = sum(a.last_gsc_clicks for a in low_read) / len(low_read)
            if avg_hr > avg_lr * 1.3:
                self.record_lesson(
                    "quill", "performance",
                    f"Readable articles (FK 60+) get {avg_hr/max(avg_lr, 1):.1f}x more clicks",
                )
                result["lessons_produced"] += 1

        result["status"] = "ok"
        return result

    def _assess_learning_health(self) -> dict:
        """Meta-learning: assess whether the feedback loop is actually working.

        Checks:
        1. Are lessons being produced? (Sage and Morgan writing to the table)
        2. Are lessons being consumed? (high-confidence lessons exist)
        3. Is pass rate improving week-over-week?
        4. Is the lesson store growing or stagnating?
        """
        result: dict[str, Any] = {}

        # Count lessons per target agent
        for agent in ("quill", "scout", "herald", "lurker", "sage"):
            lessons = self.db.get_lessons(agent)
            result[f"{agent}_lessons"] = len(lessons)
            high_conf = [l for l in lessons if l.confidence >= 0.6]
            result[f"{agent}_high_confidence"] = len(high_conf)

        total_lessons = sum(
            result.get(f"{a}_lessons", 0)
            for a in ("quill", "scout", "herald", "lurker", "sage")
        )
        result["total_lessons"] = total_lessons

        # Compare this week's pass rate vs last week's
        now = datetime.now(timezone.utc)
        this_week_start = (now - timedelta(days=7)).isoformat()
        last_week_start = (now - timedelta(days=14)).isoformat()

        this_week_metrics = self.db.get_metrics(
            name="sage_run", since=this_week_start
        )
        last_week_metrics = [
            m for m in self.db.get_metrics(name="sage_run", since=last_week_start)
            if m.timestamp < this_week_start
        ]

        def _pass_rate(metrics):
            approved = reviewed = 0
            for m in metrics:
                try:
                    d = json.loads(m.details) if m.details else {}
                    approved += d.get("approved", 0)
                    reviewed += d.get("approved", 0) + d.get("revision", 0) + d.get("rejected", 0)
                except (json.JSONDecodeError, AttributeError):
                    pass
            return (approved / reviewed * 100) if reviewed > 0 else None

        this_rate = _pass_rate(this_week_metrics)
        last_rate = _pass_rate(last_week_metrics)
        result["pass_rate_this_week"] = this_rate
        result["pass_rate_last_week"] = last_rate

        if this_rate is not None and last_rate is not None:
            delta = this_rate - last_rate
            result["pass_rate_trend"] = round(delta, 1)
            if delta > 5:
                result["status"] = "improving"
                logger.info(
                    f"Learning health: pass rate improving "
                    f"({last_rate:.0f}% → {this_rate:.0f}%)"
                )
            elif delta < -10:
                result["alert"] = (
                    f"Pass rate declining ({last_rate:.0f}% → {this_rate:.0f}%). "
                    f"Lessons may not be effective — review feedback_lessons table."
                )
            else:
                result["status"] = "stable"
        else:
            result["status"] = "insufficient_data"

        # Alert if no lessons exist after the system has been running
        if total_lessons == 0:
            sage_metrics = self.db.get_metrics(name="sage_run", since=this_week_start)
            if len(sage_metrics) >= 3:
                result["alert"] = (
                    "No lessons in feedback_lessons table despite active reviews. "
                    "Learning system may not be recording properly."
                )

        return result
