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
                word_count INTEGER DEFAULT 0,
                state_accuracy TEXT,
                product_compliance TEXT,
                broken_links_count INTEGER DEFAULT 0,
                math_errors_count INTEGER DEFAULT 0,
                validation_notes TEXT,

                -- Revision tracking
                revision_count INTEGER DEFAULT 0,
                revision_notes TEXT,

                -- Publishing
                ghost_post_id TEXT,  -- Deprecated: kept for backward compatibility
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

        # Performance insights (Atlas agent)
        db.execute("""
            CREATE TABLE IF NOT EXISTS performance_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_type TEXT NOT NULL,
                insight_text TEXT NOT NULL,
                confidence_score REAL,
                supporting_data TEXT,
                applied BOOLEAN DEFAULT 0,
                applied_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_insights_type ON performance_insights(insight_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_insights_applied ON performance_insights(applied)")

        # Internal links tracking
        db.execute("""
            CREATE TABLE IF NOT EXISTS internal_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_article_id INTEGER REFERENCES articles(id),
                target_article_id INTEGER REFERENCES articles(id),
                anchor_text TEXT,
                link_quality_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_links_source ON internal_links(source_article_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_links_target ON internal_links(target_article_id)")

        # Competitor tracking (Rival agent)
        db.execute("""
            CREATE TABLE IF NOT EXISTS competitor_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_domain TEXT NOT NULL,
                article_url TEXT UNIQUE NOT NULL,
                title TEXT,
                target_keyword TEXT,
                word_count INTEGER,
                detected_position INTEGER,
                content_hash TEXT,
                last_checked TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_competitor_domain ON competitor_articles(competitor_domain)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_competitor_keyword ON competitor_articles(target_keyword)")

        # CTA variants and performance
        db.execute("""
            CREATE TABLE IF NOT EXISTS cta_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER REFERENCES articles(id),
                cta_text TEXT NOT NULL,
                cta_type TEXT,
                position TEXT,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                conversions INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_cta_article ON cta_variants(article_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cta_active ON cta_variants(is_active)")

        # Content remixes (Remix agent)
        db.execute("""
            CREATE TABLE IF NOT EXISTS content_remixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_article_id INTEGER REFERENCES articles(id),
                remix_type TEXT NOT NULL,
                remix_content TEXT NOT NULL,
                published_url TEXT,
                published_at TIMESTAMP,
                engagement_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_remix_source ON content_remixes(source_article_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_remix_type ON content_remixes(remix_type)")

        # Migrations: Add columns that might be missing in older databases
        _run_migrations(db)


def _run_migrations(db):
    """Run database migrations for schema changes."""
    try:
        # Check if word_count column exists, add if missing
        cursor = db.execute("PRAGMA table_info(articles)")
        columns = [row[1] for row in cursor.fetchall()]

        print(f"DEBUG: Existing columns in articles table: {columns}")

        if 'word_count' not in columns:
            print("DEBUG: word_count column missing, adding it...")
            db.execute("ALTER TABLE articles ADD COLUMN word_count INTEGER DEFAULT 0")
            db.commit()  # Explicit commit
            print("✓ Added word_count column to articles table")
        else:
            print("✓ word_count column already exists")
    except Exception as e:
        print(f"❌ Migration error: {e}")
        import traceback
        traceback.print_exc()


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


def release_claim(article_id: int, claim_field: str):
    """Release a claim on an article."""
    with get_db() as db:
        db.execute(
            f"UPDATE articles SET {claim_field} = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (article_id,)
        )


# Helper functions for new features

def log_performance_insight(insight_type: str, insight_text: str, confidence_score: float, supporting_data: Dict[str, Any]):
    """Log a performance insight discovered by Atlas."""
    with get_db() as db:
        db.execute(
            "INSERT INTO performance_insights (insight_type, insight_text, confidence_score, supporting_data) VALUES (?, ?, ?, ?)",
            (insight_type, insight_text, confidence_score, json.dumps(supporting_data))
        )


def add_internal_link(source_article_id: int, target_article_id: int, anchor_text: str, quality_score: float):
    """Add an internal link between articles."""
    with get_db() as db:
        db.execute(
            "INSERT INTO internal_links (source_article_id, target_article_id, anchor_text, link_quality_score) VALUES (?, ?, ?, ?)",
            (source_article_id, target_article_id, anchor_text, quality_score)
        )


def get_internal_links_for_article(article_id: int) -> List[sqlite3.Row]:
    """Get all internal links pointing to an article."""
    with get_db() as db:
        cursor = db.execute(
            "SELECT * FROM internal_links WHERE target_article_id = ?",
            (article_id,)
        )
        return cursor.fetchall()


def log_competitor_article(domain: str, url: str, title: str, keyword: str, word_count: int, position: int, content_hash: str):
    """Log a competitor article discovered by Rival."""
    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO competitor_articles
            (competitor_domain, article_url, title, target_keyword, word_count, detected_position, content_hash, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (domain, url, title, keyword, word_count, position, content_hash)
        )


def add_cta_variant(article_id: int, cta_text: str, cta_type: str, position: str):
    """Add a CTA variant for an article."""
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO cta_variants (article_id, cta_text, cta_type, position) VALUES (?, ?, ?, ?)",
            (article_id, cta_text, cta_type, position)
        )
        return cursor.lastrowid


def update_cta_metrics(cta_id: int, impressions: int = None, clicks: int = None, conversions: int = None):
    """Update CTA performance metrics."""
    with get_db() as db:
        updates = []
        params = []
        if impressions is not None:
            updates.append("impressions = impressions + ?")
            params.append(impressions)
        if clicks is not None:
            updates.append("clicks = clicks + ?")
            params.append(clicks)
        if conversions is not None:
            updates.append("conversions = conversions + ?")
            params.append(conversions)

        if updates:
            params.append(cta_id)
            db.execute(
                f"UPDATE cta_variants SET {', '.join(updates)} WHERE id = ?",
                tuple(params)
            )


def log_content_remix(source_article_id: int, remix_type: str, remix_content: str, published_url: str = None):
    """Log a content remix created by Remix agent."""
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO content_remixes (source_article_id, remix_type, remix_content, published_url, published_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_article_id, remix_type, remix_content, published_url, datetime.now() if published_url else None)
        )
        return cursor.lastrowid
