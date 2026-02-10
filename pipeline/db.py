"""SQLite database layer replacing Notion.

Provides the shared state that all agents read from and write to.
Article status transitions are the coordination mechanism.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Generator


class ArticleStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    REVISION = "revision"
    READY_TO_PUBLISH = "ready_to_publish"
    DONE = "done"
    AMPLIFIED = "amplified"
    REJECTED = "rejected"


@dataclass
class Article:
    id: str = ""
    title: str = ""
    status: str = ArticleStatus.BACKLOG.value
    keyword: str = ""
    search_volume: int = 0
    keyword_difficulty: float = 0.0
    commercial_intent: float = 0.0
    content_brief: str = ""
    target_state: str = ""
    content_category: str = ""
    content: str = ""
    meta_description: str = ""
    slug: str = ""
    writer_claim: str = ""
    editor_claim: str = ""
    publisher_claim: str = ""
    herald_claim: str = ""
    social_status: str = ""
    revision_notes: str = ""
    revision_count: int = 0
    published_url: str = ""
    seo_score: float = 0.0
    readability_score: float = 0.0
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""
    suggested_title: str = ""
    internal_links: str = "[]"  # JSON array
    external_links: str = "[]"  # JSON array


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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'backlog',
    keyword TEXT NOT NULL DEFAULT '',
    search_volume INTEGER DEFAULT 0,
    keyword_difficulty REAL DEFAULT 0.0,
    commercial_intent REAL DEFAULT 0.0,
    content_brief TEXT DEFAULT '',
    target_state TEXT DEFAULT '',
    content_category TEXT DEFAULT '',
    content TEXT DEFAULT '',
    meta_description TEXT DEFAULT '',
    slug TEXT DEFAULT '',
    writer_claim TEXT DEFAULT '',
    editor_claim TEXT DEFAULT '',
    publisher_claim TEXT DEFAULT '',
    herald_claim TEXT DEFAULT '',
    social_status TEXT DEFAULT '',
    revision_notes TEXT DEFAULT '',
    revision_count INTEGER DEFAULT 0,
    published_url TEXT DEFAULT '',
    seo_score REAL DEFAULT 0.0,
    readability_score REAL DEFAULT 0.0,
    word_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT DEFAULT '',
    suggested_title TEXT DEFAULT '',
    internal_links TEXT DEFAULT '[]',
    external_links TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(keyword);
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
"""


class Database:
    """SQLite-backed shared state replacing Notion."""

    def __init__(self, db_path: str | Path = "pipeline.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

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

    # ── Article CRUD ─────────────────────────────────────────

    def create_article(self, **kwargs) -> Article:
        article = Article(**kwargs)
        if not article.id:
            article.id = self._new_id()
        now = self._now()
        article.created_at = article.created_at or now
        article.updated_at = now

        d = asdict(article)
        cols = ", ".join(d.keys())
        placeholders = ", ".join("?" for _ in d)
        with self._connect() as conn:
            conn.execute(f"INSERT INTO articles ({cols}) VALUES ({placeholders})", list(d.values()))
        return article

    def get_article(self, article_id: str) -> Article | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            return None
        return Article(**dict(row))

    def update_article(self, article_id: str, **kwargs) -> Article | None:
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
        return [Article(**dict(r)) for r in rows]

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

    # ── Claim Locking ────────────────────────────────────────

    def try_claim(
        self, article_id: str, claim_field: str, claim_id: str, new_status: str
    ) -> bool:
        """Attempt to claim an article. Returns True if successful."""
        with self._connect() as conn:
            # Check current claim is empty
            row = conn.execute(
                f"SELECT {claim_field} FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            if row is None:
                return False
            if row[0] and row[0] != "":
                return False

            # Set claim and status
            conn.execute(
                f"UPDATE articles SET {claim_field} = ?, status = ?, updated_at = ? WHERE id = ?",
                (claim_id, new_status, self._now(), article_id),
            )

        # Verify the claim stuck
        article = self.get_article(article_id)
        if article is None:
            return False
        return getattr(article, claim_field) == claim_id

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
                     AND status NOT IN (?, ?, ?)
                   ORDER BY updated_at ASC""",
                (cutoff, ArticleStatus.BACKLOG.value, ArticleStatus.DONE.value, ArticleStatus.AMPLIFIED.value),
            ).fetchall()
        return [Article(**dict(r)) for r in rows]

    def get_articles_published_this_week(self) -> list[Article]:
        """Get articles published in the current week."""
        from datetime import timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE status = ? AND published_at >= ?",
                (ArticleStatus.DONE.value, week_ago),
            ).fetchall()
        return [Article(**dict(r)) for r in rows]
