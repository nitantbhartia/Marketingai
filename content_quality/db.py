"""
Database helper functions for ClaimCoach content pipeline.
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

from content_quality.config import DATABASE_PATH


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # access columns by name
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read performance
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s on lock
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Initialize database with schema."""
    with get_db() as db:
        # Core content pipeline table
        db.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                slug TEXT UNIQUE,
                target_keyword TEXT,
                target_state TEXT,
                status TEXT NOT NULL DEFAULT 'backlog',
                markdown_content TEXT,
                meta_title TEXT,
                meta_description TEXT,

                -- Claim locking
                writer_claim TEXT,
                editor_claim TEXT,
                publisher_claim TEXT,
                herald_claim TEXT,

                -- Validation results
                validation_status TEXT,
                seo_score INTEGER,
                readability_score REAL,
                state_accuracy TEXT,
                product_compliance TEXT,
                broken_links_count INTEGER DEFAULT 0,
                math_errors_count INTEGER DEFAULT 0,
                validation_notes TEXT,

                -- Revision tracking
                revision_count INTEGER DEFAULT 0,
                revision_notes TEXT,

                -- Publishing
                ghost_post_id TEXT,
                published_url TEXT,
                published_at TIMESTAMP,
                social_status TEXT,

                -- SEO monitoring
                last_gsc_position REAL,
                last_gsc_impressions INTEGER,
                last_gsc_clicks INTEGER,
                last_gsc_ctr REAL,
                refresh_priority TEXT,
                cannibalization_flag BOOLEAN DEFAULT 0,

                -- Timestamps
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_articles_writer_claim ON articles(writer_claim)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_articles_target_state ON articles(target_state)")

        # Search Console historical data
        db.execute("""
            CREATE TABLE IF NOT EXISTS gsc_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER REFERENCES articles(id),
                query TEXT,
                page_url TEXT,
                position REAL,
                impressions INTEGER,
                clicks INTEGER,
                ctr REAL,
                snapshot_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_gsc_date ON gsc_snapshots(snapshot_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_gsc_article ON gsc_snapshots(article_id)")

        # Broken link tracking
        db.execute("""
            CREATE TABLE IF NOT EXISTS broken_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER REFERENCES articles(id),
                broken_url TEXT NOT NULL,
                http_status TEXT,
                first_detected DATE NOT NULL,
                resolved BOOLEAN DEFAULT 0,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Cannibalization pairs
        db.execute("""
            CREATE TABLE IF NOT EXISTS cannibalization_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_1_id INTEGER REFERENCES articles(id),
                article_2_id INTEGER REFERENCES articles(id),
                similarity_score REAL,
                resolved BOOLEAN DEFAULT 0,
                resolution_action TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Agent activity log
        db.execute("""
            CREATE TABLE IF NOT EXISTS agent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                action TEXT NOT NULL,
                article_id INTEGER REFERENCES articles(id),
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_agent_log_agent ON agent_log(agent_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_agent_log_created ON agent_log(created_at)")


def claim_article(article_id: int, claim_field: str, claim_id: str) -> bool:
    """
    Atomic claim locking for agents. Returns True if claim succeeded.
    Uses SQLite's built-in transaction isolation to prevent race conditions.

    Args:
        article_id: Article ID to claim
        claim_field: Field name (writer_claim, editor_claim, publisher_claim, herald_claim)
        claim_id: Unique claim identifier (e.g., "quill-1707004821-x7k2")

    Returns:
        True if claim succeeded, False otherwise
    """
    with get_db() as db:
        # Only claim if field is empty
        cursor = db.execute(
            f"UPDATE articles SET {claim_field} = ?, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = ? AND ({claim_field} IS NULL OR {claim_field} = '')",
            (claim_id, article_id)
        )
        return cursor.rowcount == 1


def log_agent_action(
    agent_name: str,
    action: str,
    article_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None
):
    """Log agent activity for debugging and Morgan's oversight."""
    with get_db() as db:
        db.execute(
            "INSERT INTO agent_log (agent_name, action, article_id, details) VALUES (?, ?, ?, ?)",
            (agent_name, action, article_id, json.dumps(details) if details else None)
        )


def update_article_validation(
    article_id: int,
    validation_status: str,
    seo_score: int,
    readability_score: float,
    state_accuracy: str,
    product_compliance: str,
    broken_links_count: int,
    math_errors_count: int,
    validation_notes: List[str]
):
    """Update article with validation results."""
    with get_db() as db:
        db.execute(
            """
            UPDATE articles SET
                validation_status = ?,
                seo_score = ?,
                readability_score = ?,
                state_accuracy = ?,
                product_compliance = ?,
                broken_links_count = ?,
                math_errors_count = ?,
                validation_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                validation_status,
                seo_score,
                readability_score,
                state_accuracy,
                product_compliance,
                broken_links_count,
                math_errors_count,
                json.dumps(validation_notes),
                article_id
            )
        )


def get_article_by_id(article_id: int) -> Optional[sqlite3.Row]:
    """Get article by ID."""
    with get_db() as db:
        cursor = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        return cursor.fetchone()


def get_article_by_slug(slug: str) -> Optional[sqlite3.Row]:
    """Get article by slug."""
    with get_db() as db:
        cursor = db.execute("SELECT * FROM articles WHERE slug = ?", (slug,))
        return cursor.fetchone()


def get_articles_by_status(status: str) -> List[sqlite3.Row]:
    """Get all articles with given status."""
    with get_db() as db:
        cursor = db.execute(
            "SELECT * FROM articles WHERE status = ? ORDER BY updated_at DESC",
            (status,)
        )
        return cursor.fetchall()
