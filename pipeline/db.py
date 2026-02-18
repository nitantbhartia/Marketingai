"""SQLite database layer replacing Notion.

Provides the shared state that all agents read from and write to.
Article status transitions are the coordination mechanism.

Schema is aligned with content_quality.db so the dashboard and pipeline
agents share a single articles table.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Generator


class ArticleStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    EDITOR_REVIEW = "editor_review"  # Sage auto-scoring
    REVIEW = "review"               # Human review
    REVISION = "revision"
    READY_TO_PUBLISH = "ready_to_publish"
    DONE = "done"
    AMPLIFIED = "amplified"
    REJECTED = "rejected"


@dataclass
class Article:
    id: int = 0
    product: str = "claimcoach"
    title: str = ""
    slug: str = ""
    target_keyword: str = ""
    target_state: str = ""
    status: str = ArticleStatus.BACKLOG.value
    markdown_content: str = ""
    meta_title: str = ""
    meta_description: str = ""

    # Claim locking
    writer_claim: str = ""
    editor_claim: str = ""
    publisher_claim: str = ""
    herald_claim: str = ""

    # Pipeline-specific fields
    search_volume: int = 0
    keyword_difficulty: float = 0.0
    commercial_intent: float = 0.0
    content_brief: str = ""
    content_category: str = ""
    intent_template: str = ""
    cluster_key: str = ""
    canonical_url: str = ""
    fact_pack: str = ""
    suggested_title: str = ""
    internal_links: str = "[]"
    external_links: str = "[]"

    # Validation results
    validation_status: str = ""
    sage_score: float = 0.0         # Total Sage review score (0-100)
    seo_score: float = 0.0          # SEO sub-score (0-20)
    readability_score: float = 0.0
    word_count: int = 0
    state_accuracy: str = ""
    product_compliance: str = ""
    broken_links_count: int = 0
    math_errors_count: int = 0
    validation_notes: str = ""

    # Revision tracking
    revision_count: int = 0
    revision_notes: str = ""

    # Publishing
    ghost_post_id: str = ""
    published_url: str = ""
    published_at: str = ""
    social_status: str = ""

    # SEO monitoring
    last_gsc_position: float = 0.0
    last_gsc_impressions: int = 0
    last_gsc_clicks: int = 0
    last_gsc_ctr: float = 0.0
    gsc_first_seen_at: str = ""
    refresh_priority: str = ""
    cannibalization_flag: int = 0

    # Timestamps
    created_at: str = ""
    updated_at: str = ""


_ARTICLE_FIELDS = None


def _get_article_fields() -> set[str]:
    global _ARTICLE_FIELDS
    if _ARTICLE_FIELDS is None:
        _ARTICLE_FIELDS = {f.name for f in fields(Article)}
    return _ARTICLE_FIELDS


@dataclass
class Opportunity:
    """Community engagement opportunity found by Lurker."""
    id: str = ""
    platform: str = ""  # reddit, quora, forum
    url: str = ""
    thread_title: str = ""
    subreddit: str = ""
    relevance_score: float = 0.0
    engagement_count: int = 0
    posted_at: str = ""
    found_at: str = ""
    draft_response: str = ""
    status: str = "pending"  # pending, approved, posted, skipped
    related_article_id: str = ""
    posted_by: str = ""


@dataclass
class PipelineMetric:
    """Tracked metrics for Morgan's health checks."""
    id: str = ""
    timestamp: str = ""
    metric_name: str = ""
    metric_value: float = 0.0
    details: str = ""


@dataclass
class SocialPost:
    """Social media post tracking for Herald."""
    id: str = ""
    article_id: str = ""
    platform: str = ""
    post_url: str = ""
    post_content: str = ""
    posted_at: str = ""
    engagement_count: int = 0
    status: str = "draft"  # draft, posted, failed


@dataclass
class FeedbackLesson:
    """Cross-agent learning: lessons extracted from reviews and performance data.

    Produced by Sage (from reviews) and Morgan (from GSC data).
    Consumed by Quill (writing), Scout (topic selection), and others.
    """
    id: str = ""
    source_agent: str = ""   # who produced this lesson
    target_agent: str = ""   # who should consume it
    category: str = ""       # rubric category or "performance", "success_pattern"
    lesson: str = ""         # the actual lesson text
    occurrences: int = 1     # how many times this pattern was seen
    confidence: float = 0.2  # min(1.0, occurrences / 5)
    last_seen: str = ""
    created_at: str = ""


# Articles table matches the content_quality schema so dashboard and
# pipeline agents operate on the same table.  Pipeline-specific columns
# are added via _run_article_migrations().
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL DEFAULT 'claimcoach',
    title TEXT NOT NULL DEFAULT '',
    slug TEXT UNIQUE,
    target_keyword TEXT DEFAULT '',
    target_state TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'backlog',
    markdown_content TEXT DEFAULT '',
    meta_title TEXT DEFAULT '',
    meta_description TEXT DEFAULT '',

    -- Claim locking
    writer_claim TEXT DEFAULT '',
    editor_claim TEXT DEFAULT '',
    publisher_claim TEXT DEFAULT '',
    herald_claim TEXT DEFAULT '',

    -- Pipeline planning fields
    content_brief TEXT DEFAULT '',
    search_volume INTEGER DEFAULT 0,
    keyword_difficulty REAL DEFAULT 0.0,
    commercial_intent REAL DEFAULT 0.0,
    content_category TEXT DEFAULT '',
    intent_template TEXT DEFAULT '',
    cluster_key TEXT DEFAULT '',
    canonical_url TEXT DEFAULT '',
    fact_pack TEXT DEFAULT '',
    suggested_title TEXT DEFAULT '',
    internal_links TEXT DEFAULT '[]',
    external_links TEXT DEFAULT '[]',

    -- Validation results
    validation_status TEXT DEFAULT '',
    sage_score REAL DEFAULT 0.0,
    seo_score REAL DEFAULT 0.0,
    readability_score REAL DEFAULT 0.0,
    word_count INTEGER DEFAULT 0,
    state_accuracy TEXT DEFAULT '',
    product_compliance TEXT DEFAULT '',
    broken_links_count INTEGER DEFAULT 0,
    math_errors_count INTEGER DEFAULT 0,
    validation_notes TEXT DEFAULT '',

    -- Revision tracking
    revision_count INTEGER DEFAULT 0,
    revision_notes TEXT DEFAULT '',

    -- Publishing
    ghost_post_id TEXT DEFAULT '',
    published_url TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    social_status TEXT DEFAULT '',

    -- SEO monitoring
    last_gsc_position REAL DEFAULT 0.0,
    last_gsc_impressions INTEGER DEFAULT 0,
    last_gsc_clicks INTEGER DEFAULT 0,
    last_gsc_ctr REAL DEFAULT 0.0,
    gsc_first_seen_at TEXT DEFAULT '',
    refresh_priority TEXT DEFAULT '',
    cannibalization_flag INTEGER DEFAULT 0,

    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_target_keyword ON articles(target_keyword);
CREATE INDEX IF NOT EXISTS idx_articles_writer_claim ON articles(writer_claim);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    thread_title TEXT DEFAULT '',
    subreddit TEXT DEFAULT '',
    relevance_score REAL DEFAULT 0.0,
    engagement_count INTEGER DEFAULT 0,
    posted_at TEXT DEFAULT '',
    found_at TEXT NOT NULL,
    draft_response TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    related_article_id TEXT DEFAULT '',
    posted_by TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_opportunities_status ON opportunities(status);

CREATE TABLE IF NOT EXISTS pipeline_metrics (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL DEFAULT 0.0,
    details TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON pipeline_metrics(metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON pipeline_metrics(timestamp);

CREATE TABLE IF NOT EXISTS social_posts (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    post_url TEXT DEFAULT '',
    post_content TEXT DEFAULT '',
    posted_at TEXT DEFAULT '',
    engagement_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft'
);

CREATE INDEX IF NOT EXISTS idx_social_article ON social_posts(article_id);

CREATE TABLE IF NOT EXISTS cta_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER REFERENCES articles(id),
    cta_text TEXT NOT NULL DEFAULT '',
    cta_type TEXT DEFAULT '',
    position TEXT DEFAULT '',
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cta_variants_article ON cta_variants(article_id);

CREATE TABLE IF NOT EXISTS feedback_lessons (
    id TEXT PRIMARY KEY,
    source_agent TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    category TEXT NOT NULL,
    lesson TEXT NOT NULL,
    occurrences INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.2,
    last_seen TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(target_agent, category, lesson)
);

CREATE INDEX IF NOT EXISTS idx_lessons_target ON feedback_lessons(target_agent);
CREATE INDEX IF NOT EXISTS idx_lessons_confidence ON feedback_lessons(confidence);
"""

# Pipeline-specific columns that may be missing if the articles table
# was originally created by content_quality.db.init_database().
_PIPELINE_COLUMN_MIGRATIONS = {
    "product": "TEXT DEFAULT 'claimcoach'",
    "content_brief": "TEXT DEFAULT ''",
    "search_volume": "INTEGER DEFAULT 0",
    "keyword_difficulty": "REAL DEFAULT 0.0",
    "commercial_intent": "REAL DEFAULT 0.0",
    "content_category": "TEXT DEFAULT ''",
    "intent_template": "TEXT DEFAULT ''",
    "cluster_key": "TEXT DEFAULT ''",
    "canonical_url": "TEXT DEFAULT ''",
    "fact_pack": "TEXT DEFAULT ''",
    "suggested_title": "TEXT DEFAULT ''",
    "internal_links": "TEXT DEFAULT '[]'",
    "external_links": "TEXT DEFAULT '[]'",
    "sage_score": "REAL DEFAULT 0.0",
    "gsc_first_seen_at": "TEXT DEFAULT ''",
}


class Database:
    """SQLite-backed shared state replacing Notion."""

    def __init__(self, db_path: str | Path = "pipeline.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._run_article_migrations(conn)

    def _run_article_migrations(self, conn: sqlite3.Connection) -> None:
        """Add pipeline-specific columns if missing (e.g. table was created by content_quality)."""
        cursor = conn.execute("PRAGMA table_info(articles)")
        existing = {row[1] for row in cursor.fetchall()}
        for col, col_type in _PIPELINE_COLUMN_MIGRATIONS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {col_type}")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())[:12]

    @staticmethod
    def _parse_ts(ts: str | None) -> datetime | None:
        if not ts:
            return None
        try:
            raw = ts.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    # ── Article CRUD ─────────────────────────────────────────

    def create_article(self, **kwargs) -> Article:
        article = Article(**kwargs)
        now = self._now()
        article.created_at = article.created_at or now
        article.updated_at = now

        d = asdict(article)
        # Let SQLite autoincrement handle the id
        d.pop("id", None)
        # Empty slug must be NULL to satisfy UNIQUE constraint
        if not d.get("slug"):
            d["slug"] = None

        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" for _ in d)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO articles ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            article.id = cursor.lastrowid
        return article

    def get_article(self, article_id: int) -> Article | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_article(row)

    def update_article(self, article_id: int, **kwargs) -> Article | None:
        valid = _get_article_fields()
        invalid = set(kwargs.keys()) - valid - {"updated_at"}
        if invalid:
            raise ValueError(
                f"update_article(): unknown fields {invalid}. "
                f"Valid fields: {sorted(valid)}"
            )
        kwargs["updated_at"] = self._now()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [article_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE articles SET {sets} WHERE id = ?", vals)
        return self.get_article(article_id)

    def query_articles(
        self,
        status: str | None = None,
        writer_claim_empty: bool = False,
        limit: int = 100,
        order_by: str = "created_at ASC",
    ) -> list[Article]:
        conditions = []
        params: list = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if writer_claim_empty:
            conditions.append("(writer_claim = '' OR writer_claim IS NULL)")

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM articles WHERE {where} ORDER BY {order_by} LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_article(r) for r in rows]

    def count_articles(self, status: str | None = None) -> int:
        if status:
            sql = "SELECT COUNT(*) FROM articles WHERE status = ?"
            params: tuple = (status,)
        else:
            sql = "SELECT COUNT(*) FROM articles"
            params = ()
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def get_published_articles(self) -> list[Article]:
        """Get all articles with status 'done' — used for internal linking."""
        return self.query_articles(status=ArticleStatus.DONE.value, limit=1000)

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> Article:
        """Convert a DB row to an Article, ignoring unknown columns."""
        known = _get_article_fields()
        d = {k: v for k, v in dict(row).items() if k in known}
        # Coerce None → default for non-nullable dataclass fields
        for k, v in d.items():
            if v is None:
                if isinstance(getattr(Article, k, None), int):
                    d[k] = 0
                elif isinstance(getattr(Article, k, None), float):
                    d[k] = 0.0
                else:
                    d[k] = ""
        return Article(**d)

    # ── Claim Locking ────────────────────────────────────────

    def try_claim(
        self, article_id: int, claim_field: str, claim_id: str, new_status: str
    ) -> bool:
        """Attempt to atomically claim an article. Returns True if successful.

        Uses a conditional UPDATE so the check-and-set is a single statement,
        preventing two concurrent callers from both winning the claim.
        """
        # Validate claim_field against known columns to prevent SQL injection
        valid_fields = {"writer_claim", "editor_claim", "publisher_claim", "herald_claim"}
        if claim_field not in valid_fields:
            raise ValueError(f"Invalid claim field: {claim_field}")

        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE articles SET {claim_field} = ?, status = ?, updated_at = ? "
                f"WHERE id = ? AND ({claim_field} = '' OR {claim_field} IS NULL)",
                (claim_id, new_status, self._now(), article_id),
            )
            if cursor.rowcount == 0:
                return False

        return True

    # ── Opportunity CRUD ─────────────────────────────────────

    def create_opportunity(self, **kwargs) -> Opportunity:
        opp = Opportunity(**kwargs)
        if not opp.id:
            opp.id = self._new_id()
        opp.found_at = opp.found_at or self._now()

        d = asdict(opp)
        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" for _ in d)
        with self._connect() as conn:
            conn.execute(f"INSERT INTO opportunities ({cols}) VALUES ({placeholders})", list(d.values()))
        return opp

    def query_opportunities(
        self, status: str | None = None, limit: int = 50
    ) -> list[Opportunity]:
        conditions = []
        params: list = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM opportunities WHERE {where} ORDER BY found_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [Opportunity(**dict(r)) for r in rows]

    # ── Metrics ──────────────────────────────────────────────

    def record_metric(self, name: str, value: float, details: str = "") -> None:
        m = PipelineMetric(
            id=self._new_id(),
            timestamp=self._now(),
            metric_name=name,
            metric_value=value,
            details=details,
        )
        d = asdict(m)
        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" for _ in d)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO pipeline_metrics ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )

    def get_metrics(
        self, name: str | None = None, since: str | None = None, limit: int = 100
    ) -> list[PipelineMetric]:
        conditions = []
        params: list = []
        if name:
            conditions.append("metric_name = ?")
            params.append(name)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM pipeline_metrics WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
        return [PipelineMetric(**dict(r)) for r in rows]

    def get_roi_kpis(self, days: int = 90) -> dict:
        """Compute ROI KPIs for dashboard/reporting."""
        now = datetime.now(timezone.utc)
        since_dt = now - timedelta(days=max(1, days))
        since = since_dt.isoformat()

        with self._connect() as conn:
            # Cost: from estimated per-call llm_call metrics.
            cost_row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(metric_value), 0.0) AS total_cost,
                    COUNT(*) AS llm_calls
                FROM pipeline_metrics
                WHERE metric_name = 'llm_call' AND timestamp >= ?
                """,
                (since,),
            ).fetchone()
            total_cost = float(cost_row["total_cost"] or 0.0)
            llm_calls = int(cost_row["llm_calls"] or 0)

            pub_row = conn.execute(
                """
                SELECT COUNT(*) AS published_count
                FROM articles
                WHERE status IN ('done', 'amplified')
                  AND published_at IS NOT NULL
                  AND published_at != ''
                  AND published_at >= ?
                """,
                (since,),
            ).fetchone()
            published_count = int(pub_row["published_count"] or 0)
            cost_per_published = (
                total_cost / published_count if published_count > 0 else 0.0
            )

            # Index latency: time from publish -> first observed GSC visibility.
            latency_rows = conn.execute(
                """
                SELECT published_at, gsc_first_seen_at
                FROM articles
                WHERE published_at IS NOT NULL AND published_at != ''
                  AND gsc_first_seen_at IS NOT NULL AND gsc_first_seen_at != ''
                """
            ).fetchall()
            latencies: list[float] = []
            for row in latency_rows:
                published_at = self._parse_ts(row["published_at"])
                first_seen = self._parse_ts(row["gsc_first_seen_at"])
                if not published_at or not first_seen:
                    continue
                delta_days = (first_seen - published_at).total_seconds() / 86400
                if delta_days >= 0:
                    latencies.append(delta_days)
            avg_time_to_index = round(sum(latencies) / len(latencies), 2) if latencies else None

            # Clicks/article at 30/60/90 days (cohort averages using current 7d clicks snapshot).
            clicks_by_age: dict[str, dict[str, float | int]] = {}
            for age in (30, 60, 90):
                cutoff = (now - timedelta(days=age)).isoformat()
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS n,
                        COALESCE(AVG(last_gsc_clicks), 0.0) AS avg_clicks
                    FROM articles
                    WHERE status IN ('done', 'amplified')
                      AND published_at IS NOT NULL
                      AND published_at != ''
                      AND published_at <= ?
                    """,
                    (cutoff,),
                ).fetchone()
                clicks_by_age[f"{age}d"] = {
                    "articles": int(row["n"] or 0),
                    "avg_clicks": round(float(row["avg_clicks"] or 0.0), 2),
                }

            # Conversion rate by content cluster/category.
            cluster_rows = []
            try:
                cluster_rows = conn.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(a.content_category, ''), 'uncategorized') AS cluster,
                        COUNT(DISTINCT a.id) AS articles,
                        COALESCE(SUM(c.impressions), 0) AS impressions,
                        COALESCE(SUM(c.clicks), 0) AS clicks,
                        COALESCE(SUM(c.conversions), 0) AS conversions
                    FROM articles a
                    LEFT JOIN cta_variants c ON c.article_id = a.id
                    WHERE a.status IN ('done', 'amplified')
                      AND a.published_at IS NOT NULL
                      AND a.published_at != ''
                    GROUP BY cluster
                    ORDER BY conversions DESC, clicks DESC
                    """
                ).fetchall()
            except Exception:
                cluster_rows = []

            conversion_by_cluster = []
            for row in cluster_rows:
                clicks = int(row["clicks"] or 0)
                impressions = int(row["impressions"] or 0)
                conversions = int(row["conversions"] or 0)
                rate = (conversions / clicks) if clicks > 0 else (
                    conversions / impressions if impressions > 0 else 0.0
                )
                conversion_by_cluster.append(
                    {
                        "cluster": row["cluster"],
                        "articles": int(row["articles"] or 0),
                        "impressions": impressions,
                        "clicks": clicks,
                        "conversions": conversions,
                        "conversion_rate": round(rate * 100, 2),
                    }
                )

        return {
            "window_days": days,
            "estimated_cost_usd": round(total_cost, 4),
            "llm_calls": llm_calls,
            "published_articles": published_count,
            "cost_per_published_usd": round(cost_per_published, 4),
            "avg_time_to_index_days": avg_time_to_index,
            "indexed_articles": len(latencies),
            "clicks_per_article": clicks_by_age,
            "conversion_by_cluster": conversion_by_cluster,
        }

    # ── Social Posts ─────────────────────────────────────────

    def create_social_post(self, **kwargs) -> SocialPost:
        post = SocialPost(**kwargs)
        if not post.id:
            post.id = self._new_id()
        d = asdict(post)
        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" for _ in d)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO social_posts ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
        return post

    def get_social_posts_for_article(self, article_id: str) -> list[SocialPost]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM social_posts WHERE article_id = ?", (article_id,)
            ).fetchall()
        return [SocialPost(**dict(r)) for r in rows]

    # ── Feedback Lessons ─────────────────────────────────────

    def upsert_lesson(
        self,
        source_agent: str,
        target_agent: str,
        category: str,
        lesson: str,
    ) -> FeedbackLesson:
        """Insert or reinforce a lesson. Deduplicates on (target_agent, category, lesson)."""
        now = self._now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, occurrences FROM feedback_lessons "
                "WHERE target_agent = ? AND category = ? AND lesson = ?",
                (target_agent, category, lesson),
            ).fetchone()

            if existing:
                new_occ = existing["occurrences"] + 1
                confidence = min(1.0, new_occ / 5)
                conn.execute(
                    "UPDATE feedback_lessons SET occurrences = ?, confidence = ?, "
                    "last_seen = ?, source_agent = ? WHERE id = ?",
                    (new_occ, confidence, now, source_agent, existing["id"]),
                )
                return FeedbackLesson(
                    id=existing["id"], source_agent=source_agent,
                    target_agent=target_agent, category=category,
                    lesson=lesson, occurrences=new_occ,
                    confidence=confidence, last_seen=now,
                )
            else:
                lesson_id = self._new_id()
                conn.execute(
                    "INSERT INTO feedback_lessons "
                    "(id, source_agent, target_agent, category, lesson, "
                    "occurrences, confidence, last_seen, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, 0.2, ?, ?)",
                    (lesson_id, source_agent, target_agent, category,
                     lesson, now, now),
                )
                return FeedbackLesson(
                    id=lesson_id, source_agent=source_agent,
                    target_agent=target_agent, category=category,
                    lesson=lesson, occurrences=1, confidence=0.2,
                    last_seen=now, created_at=now,
                )

    def get_lessons(
        self,
        target_agent: str,
        category: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 30,
    ) -> list[FeedbackLesson]:
        """Get lessons for a specific agent, ordered by confidence then occurrences."""
        conditions = ["target_agent = ?"]
        params: list = [target_agent]
        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_confidence > 0:
            conditions.append("confidence >= ?")
            params.append(min_confidence)

        where = " AND ".join(conditions)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_lessons WHERE {where} "
                "ORDER BY confidence DESC, occurrences DESC LIMIT ?",
                params,
            ).fetchall()
        return [FeedbackLesson(**dict(r)) for r in rows]

    def decay_lessons(self, older_than_days: int = 30) -> int:
        """Reduce occurrences of stale lessons. Delete those that reach zero.

        Called by Morgan during health checks to prevent outdated lessons
        from dominating. Returns number of lessons decayed.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._connect() as conn:
            # Decrement occurrences for old lessons
            conn.execute(
                "UPDATE feedback_lessons SET occurrences = occurrences - 1, "
                "confidence = MAX(0, (occurrences - 1.0) / 5.0) "
                "WHERE last_seen < ?",
                (cutoff,),
            )
            # Delete lessons that have decayed to zero
            cursor = conn.execute(
                "DELETE FROM feedback_lessons WHERE occurrences <= 0"
            )
            return cursor.rowcount

    # ── Pipeline Stats ───────────────────────────────────────

    def get_pipeline_summary(self) -> dict:
        """Get counts per status — used by Morgan and CLI dashboard."""
        summary = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM articles GROUP BY status"
            ).fetchall()
            for row in rows:
                summary[row["status"]] = row["cnt"]
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            summary["total"] = total
        return summary

    def get_stuck_articles(self, hours: int = 24) -> list[Article]:
        """Find articles stuck in a status for too long."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM articles
                   WHERE updated_at < ?
                     AND status NOT IN (?, ?, ?, ?)
                   ORDER BY updated_at ASC""",
                (
                    cutoff,
                    ArticleStatus.BACKLOG.value,
                    ArticleStatus.DONE.value,
                    ArticleStatus.AMPLIFIED.value,
                    ArticleStatus.REJECTED.value,
                ),
            ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def clear_stale_claims(self, hours: int = 24) -> int:
        """Release claim locks held longer than *hours*.

        Returns the number of articles whose claims were cleared.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cleared = 0
        with self._connect() as conn:
            for claim_col in ("writer_claim", "editor_claim", "publisher_claim", "herald_claim"):
                cur = conn.execute(
                    f"UPDATE articles SET {claim_col} = '' "
                    f"WHERE {claim_col} != '' AND updated_at < ?",
                    (cutoff,),
                )
                cleared += cur.rowcount
            conn.commit()
        return cleared

    def get_reviewed_articles(self, limit: int = 30) -> list[Article]:
        """Get recent articles that have been reviewed by Sage (have revision_notes)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE revision_notes != '' "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def get_articles_published_this_week(self) -> list[Article]:
        """Get articles published in the current week."""
        from datetime import timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE status = ? AND published_at >= ?",
                (ArticleStatus.DONE.value, week_ago),
            ).fetchall()
        return [self._row_to_article(r) for r in rows]
