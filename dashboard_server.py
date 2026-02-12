"""
ClaimCoach Content Review Dashboard

Beautiful web interface for reviewing and approving articles.
Receives POST notifications when articles are ready for review.
"""

import json
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from content_quality.db import get_db, log_agent_action, init_database
from content_quality.config import DATABASE_PATH, API_PORT

# In-memory background job store
_jobs: Dict[str, Dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    init_database()
    print(f"✓ Database initialized at {DATABASE_PATH}")
    yield


app = FastAPI(
    title="ClaimCoach Review Dashboard",
    description="Review and approve content before publishing",
    version="1.0.0",
    lifespan=lifespan,
)

# Setup templates and static files
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(templates_dir))

# Store recent notifications
recent_notifications = []


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard showing all articles in review."""

    try:
        # Get articles needing review
        with get_db() as db:
            cursor = db.execute("""
                SELECT id, title, slug, target_keyword, target_state,
                       status, seo_score, readability_score,
                       validation_status, validation_notes,
                       word_count, created_at, updated_at,
                       writer_claim, editor_claim
                FROM articles
                WHERE status IN ('review', 'ready_to_publish', 'revision', 'rejected')
                ORDER BY
                    CASE status
                        WHEN 'ready_to_publish' THEN 1
                        WHEN 'review' THEN 2
                        WHEN 'revision' THEN 3
                        WHEN 'rejected' THEN 4
                    END,
                    updated_at DESC
            """)
            articles = [dict(row) for row in cursor.fetchall()]

        # Get summary stats
        with get_db() as db:
            cursor = db.execute("""
                SELECT status, COUNT(*) as count
                FROM articles
                GROUP BY status
            """)
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "articles": articles,
            "status_counts": status_counts,
            "recent_notifications": recent_notifications[-10:],  # Last 10
            "now": datetime.now()
        })
    except Exception as e:
        # Return error page with details
        import traceback
        error_details = traceback.format_exc()
        return HTMLResponse(
            content=f"""
            <html>
                <head><title>Dashboard Error</title></head>
                <body style="font-family: monospace; padding: 20px;">
                    <h1>Dashboard Error</h1>
                    <p><strong>Error:</strong> {str(e)}</p>
                    <h2>Full Traceback:</h2>
                    <pre>{error_details}</pre>
                </body>
            </html>
            """,
            status_code=500
        )


@app.get("/article/{article_id}", response_class=HTMLResponse)
async def review_article(request: Request, article_id: int):
    """Detailed article review page."""

    with get_db() as db:
        cursor = db.execute("""
            SELECT id, title, slug, markdown_content, meta_title, meta_description,
                   target_keyword, target_state, status,
                   seo_score, readability_score, state_accuracy, product_compliance,
                   broken_links_count, math_errors_count,
                   validation_status, validation_notes, revision_notes,
                   revision_count, word_count,
                   published_url, published_at,
                   created_at, updated_at
            FROM articles
            WHERE id = ?
        """, (article_id,))
        article = cursor.fetchone()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    article_dict = dict(article)

    # Parse validation notes
    validation_notes = []
    if article_dict.get("validation_notes"):
        try:
            validation_notes = json.loads(article_dict["validation_notes"])
        except:
            validation_notes = []

    # Get validation history
    with get_db() as db:
        cursor = db.execute("""
            SELECT agent_name, action, details, created_at
            FROM agent_log
            WHERE article_id = ?
            ORDER BY created_at DESC
            LIMIT 20
        """, (article_id,))
        history = [dict(row) for row in cursor.fetchall()]

    # Get CTA variants if any
    with get_db() as db:
        cursor = db.execute("""
            SELECT cta_text, cta_type, position, impressions, clicks, conversions
            FROM cta_variants
            WHERE article_id = ?
        """, (article_id,))
        cta_variants = [dict(row) for row in cursor.fetchall()]

    # Calculate estimated word count from markdown
    word_count = len(article_dict.get("markdown_content", "").split())

    return templates.TemplateResponse("article_review.html", {
        "request": request,
        "article": article_dict,
        "validation_notes": validation_notes,
        "history": history,
        "cta_variants": cta_variants,
        "word_count": word_count
    })


@app.post("/api/article/{article_id}/approve")
async def approve_article(article_id: int, notes: str = Form("")):
    """Approve an article for publishing."""

    with get_db() as db:
        # Update status
        db.execute("""
            UPDATE articles SET
                status = 'ready_to_publish',
                editor_claim = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (article_id,))

    # Log action
    log_agent_action(
        agent_name="human_reviewer",
        action="approved",
        article_id=article_id,
        details={"notes": notes}
    )

    return {"success": True, "message": "Article approved for publishing"}


@app.post("/api/article/{article_id}/reject")
async def reject_article(article_id: int, reason: str = Form(...)):
    """Reject an article and request revision."""

    with get_db() as db:
        # Update status and add revision notes
        cursor = db.execute("""
            SELECT revision_notes
            FROM articles
            WHERE id = ?
        """, (article_id,))
        row = cursor.fetchone()

        existing_notes = row[0] if row and row[0] else ""

        # Append new revision request
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_notes = f"{existing_notes}\n\n[{timestamp}] HUMAN REVIEW:\n{reason}"

        db.execute("""
            UPDATE articles SET
                status = 'revision',
                revision_notes = ?,
                revision_count = revision_count + 1,
                editor_claim = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_notes, article_id))

    # Log action
    log_agent_action(
        agent_name="human_reviewer",
        action="rejected",
        article_id=article_id,
        details={"reason": reason}
    )

    return {"success": True, "message": "Article sent back for revision"}


@app.post("/api/article/{article_id}/publish")
async def publish_article(article_id: int):
    """Manually trigger publishing for an article."""

    # Just update status - Ezra will pick it up
    with get_db() as db:
        db.execute("""
            UPDATE articles SET
                status = 'ready_to_publish',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (article_id,))

    log_agent_action(
        agent_name="human_reviewer",
        action="queued_for_publish",
        article_id=article_id
    )

    return {"success": True, "message": "Article queued for publishing"}


@app.post("/api/article/{article_id}/retry")
async def retry_article(article_id: int):
    """Send a rejected article back to Quill as a revision.

    Preserves the existing content and Sage's review notes so Quill
    can use the feedback to improve the article rather than starting
    from scratch.
    """

    with get_db() as db:
        cursor = db.execute("SELECT status FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Article not found")

        # Send to 'revision' (not 'todo') so Quill uses _try_revision()
        # which includes the existing content + review notes in its prompt.
        # Reset revision_count so it doesn't immediately hit max rounds.
        db.execute("""
            UPDATE articles SET
                status = 'revision',
                writer_claim = '',
                editor_claim = '',
                revision_count = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (article_id,))

    log_agent_action(
        agent_name="human_reviewer",
        action="retried",
        article_id=article_id,
        details={"previous_status": row[0]}
    )

    return {"success": True, "message": "Article sent back for revision — Quill will improve it using Sage's feedback"}


@app.post("/api/notifications/article-ready")
async def notify_article_ready(request: Request):
    """
    Webhook endpoint for agents to POST when article is ready for review.

    Expected payload:
    {
        "article_id": 123,
        "title": "Article Title",
        "status": "review",
        "seo_score": 85,
        "readability_score": 72.5,
        "validation_status": "PASS"
    }
    """
    payload = await request.json()

    notification = {
        "timestamp": datetime.now().isoformat(),
        "type": "article_ready",
        "data": payload
    }

    recent_notifications.append(notification)

    # Keep only last 50 notifications
    if len(recent_notifications) > 50:
        recent_notifications.pop(0)

    return {
        "success": True,
        "message": f"Notification received for article {payload.get('article_id')}"
    }


@app.get("/api/stats")
async def get_stats():
    """Get dashboard statistics."""

    with get_db() as db:
        # Status counts
        cursor = db.execute("""
            SELECT status, COUNT(*) as count
            FROM articles
            GROUP BY status
        """)
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # Recent activity
        cursor = db.execute("""
            SELECT agent_name, action, COUNT(*) as count
            FROM agent_log
            WHERE created_at > datetime('now', '-24 hours')
            GROUP BY agent_name, action
        """)
        recent_activity = [dict(row) for row in cursor.fetchall()]

        # Average scores
        cursor = db.execute("""
            SELECT
                AVG(seo_score) as avg_seo,
                AVG(readability_score) as avg_readability,
                COUNT(*) as total_reviewed
            FROM articles
            WHERE validation_status IS NOT NULL
        """)
        row = cursor.fetchone()
        scores = {
            "avg_seo": round(row[0], 1) if row[0] else 0,
            "avg_readability": round(row[1], 1) if row[1] else 0,
            "total_reviewed": row[2]
        }

    return {
        "status_counts": status_counts,
        "recent_activity": recent_activity,
        "scores": scores,
        "notifications_count": len(recent_notifications)
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "dashboard",
        "timestamp": datetime.now().isoformat()
    }


# ── Background job helpers ─────────────────────────────────

_JOB_TIMEOUT = 180  # seconds — kill jobs that run longer than 3 minutes


def _start_agent_job(agent_name: str, pre_hook=None) -> str:
    """Run a pipeline agent in a background thread. Returns job_id.

    Args:
        pre_hook: Optional callable(cfg, db) to run before the agent (e.g. auto-promote).
    """
    job_id = uuid.uuid4().hex[:8]
    started = datetime.now()
    _jobs[job_id] = {
        "status": "running",
        "agent": agent_name,
        "result": None,
        "started_at": started.isoformat(),
    }

    def _run():
        try:
            from pipeline.scheduler import run_agent
            from pipeline.config import Config
            from pipeline.db import Database

            cfg = Config.load()
            db = Database(cfg.resolve_path(cfg.pipeline.database_path))

            if pre_hook:
                pre_hook(cfg, db)

            result = run_agent(agent_name, cfg, db)
            _jobs[job_id] = {"status": "done", "agent": agent_name, "result": result}
        except Exception as e:
            _jobs[job_id] = {
                "status": "error",
                "agent": agent_name,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    def _watchdog():
        """Kill the job entry if the thread exceeds _JOB_TIMEOUT."""
        import time
        time.sleep(_JOB_TIMEOUT)
        job = _jobs.get(job_id)
        if job and job["status"] == "running":
            _jobs[job_id] = {
                "status": "error",
                "agent": agent_name,
                "error": f"Job timed out after {_JOB_TIMEOUT}s",
            }

    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_watchdog, daemon=True).start()
    return job_id


@app.get("/trigger/status/{job_id}")
async def trigger_status(job_id: str):
    """Poll for background job status."""
    job = _jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return job


# ── Manual agent trigger endpoints ─────────────────────────

@app.get("/trigger/scout")
async def trigger_scout():
    """Manually trigger Scout agent (runs in background)."""
    job_id = _start_agent_job("scout")
    return {"status": "started", "agent": "scout", "job_id": job_id}


@app.get("/trigger/promote")
async def trigger_promote():
    """Promote top backlog topics to 'todo' so Quill can pick them up."""
    try:
        from pipeline.config import Config
        from pipeline.db import Database, ArticleStatus

        cfg = Config.load()
        db = Database(cfg.resolve_path(cfg.pipeline.database_path))

        backlog = db.query_articles(
            status=ArticleStatus.BACKLOG.value,
            limit=10,
            order_by="commercial_intent DESC, keyword_difficulty ASC",
        )

        promoted = []
        for article in backlog:
            db.update_article(article.id, status=ArticleStatus.TODO.value)
            promoted.append({"id": article.id, "keyword": article.target_keyword})

        return {
            "status": "done",
            "promoted": len(promoted),
            "articles": promoted,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def _auto_promote_for_quill(cfg, db):
    """Promote backlog → todo if no todo articles exist, so Quill has work."""
    from pipeline.db import ArticleStatus
    import logging

    logger = logging.getLogger("trigger.quill")
    todo_count = db.count_articles(status=ArticleStatus.TODO.value)
    if todo_count > 0:
        logger.info(f"Quill pre-check: {todo_count} todo articles available")
        return

    # Also check for stuck in_progress articles and reset them
    in_progress = db.query_articles(status=ArticleStatus.IN_PROGRESS.value, limit=50)
    for article in in_progress:
        logger.info(f"Resetting stuck article {article.id} back to todo")
        db.update_article(article.id, status=ArticleStatus.TODO.value, writer_claim="")

    todo_count = db.count_articles(status=ArticleStatus.TODO.value)
    if todo_count > 0:
        logger.info(f"Recovered {todo_count} stuck articles to todo")
        return

    # Auto-promote from backlog
    backlog = db.query_articles(
        status=ArticleStatus.BACKLOG.value,
        limit=3,
        order_by="commercial_intent DESC, keyword_difficulty ASC",
    )
    for article in backlog:
        db.update_article(article.id, status=ArticleStatus.TODO.value)
        logger.info(f"Auto-promoted article {article.id}: {article.target_keyword}")

    if not backlog:
        logger.warning("No backlog articles to promote — Quill will be idle")


@app.get("/trigger/quill")
async def trigger_quill():
    """Manually trigger Quill agent (runs in background).

    Auto-promotes backlog articles to 'todo' if none are available,
    and resets any stuck 'in_progress' articles first.
    """
    job_id = _start_agent_job("quill", pre_hook=_auto_promote_for_quill)
    return {"status": "started", "agent": "quill", "job_id": job_id}


@app.get("/trigger/sage")
async def trigger_sage():
    """Manually trigger Sage agent (runs in background)."""
    job_id = _start_agent_job("sage")
    return {"status": "started", "agent": "sage", "job_id": job_id}


@app.get("/trigger/brief-topics")
async def brief_topics():
    """AI-brief topics that have generic briefs - using content_quality.db schema."""
    try:
        from content_quality.db import get_db
        from content_quality.config import DATABASE_PATH
        from anthropic import Anthropic
        import os

        # Debug info
        debug = {
            "db_path": DATABASE_PATH,
            "db_path_env": os.getenv("DATABASE_PATH"),
            "db_exists": os.path.exists(DATABASE_PATH),
        }

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return {
                "status": "error",
                "error": "ANTHROPIC_API_KEY not configured",
                "debug": debug
            }

        client = Anthropic(api_key=api_key)

        # Find topics with generic/short briefs using content_quality.db
        with get_db() as db:
            cursor = db.execute("""
                SELECT id, title, target_keyword, target_state, markdown_content
                FROM articles
                WHERE status = 'backlog'
                  AND (markdown_content IS NULL OR length(markdown_content) < 200
                       OR markdown_content LIKE 'Write a comprehensive%')
                LIMIT 50
            """)
            articles = cursor.fetchall()

        debug["backlog_found"] = len(articles)
        briefed = 0
        errors = []

        for article in articles:
            try:
                # Generate AI brief
                prompt = f"""Create a detailed content brief for an article about: {article['target_keyword']}

Target state: {article['target_state'] or 'General US'}

Provide:
1. Article angle/hook
2. Key points to cover (5-7 bullet points)
3. Target word count: 1800-2000
4. SEO focus
5. Unique value proposition

Format as a clear, actionable brief for a writer."""

                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}]
                )

                brief = response.content[0].text

                # Update using content_quality.db
                with get_db() as db:
                    db.execute(
                        "UPDATE articles SET markdown_content = ? WHERE id = ?",
                        (brief, article['id'])
                    )

                briefed += 1

                if briefed >= 10:
                    break
            except Exception as e:
                errors.append(f"Article {article['id']}: {str(e)}")

        return {
            "status": "success",
            "briefed": briefed,
            "checked": len(articles),
            "errors": errors if errors else None,
            "debug": debug
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.get("/debug/config")
async def debug_config():
    """Debug endpoint to see resolved config and env vars."""
    import os
    from pipeline.config import Config
    cfg = Config.load()
    return {
        "llm_provider": cfg.llm_provider,
        "gemini_api_key_set": bool(cfg.gemini.api_key),
        "gemini_default_model": cfg.gemini.default_model,
        "env_LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
        "env_GEMINI_API_KEY_set": bool(os.environ.get("GEMINI_API_KEY")),
    }


@app.get("/debug/articles")
async def debug_articles():
    """Debug: show article statuses and writer_claim values."""
    from content_quality.db import get_db
    with get_db() as db:
        cursor = db.execute("""
            SELECT status, COUNT(*) as count FROM articles GROUP BY status
        """)
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        cursor = db.execute("""
            SELECT id, target_keyword, status, writer_claim, editor_claim
            FROM articles
            WHERE status != 'backlog'
            LIMIT 20
        """)
        return {
            "status_counts": status_counts,
            "non_backlog_articles": [dict(row) for row in cursor.fetchall()],
        }


@app.get("/debug/fix-stuck")
async def fix_stuck_articles():
    """Reset stuck in_progress articles back to todo."""
    from content_quality.db import get_db
    with get_db() as db:
        cursor = db.execute("""
            UPDATE articles
            SET status = 'todo', writer_claim = ''
            WHERE status = 'in_progress'
        """)
        fixed = cursor.rowcount
    return {"status": "success", "reset_to_todo": fixed}


@app.get("/debug/fix-stuck-reviews")
async def fix_stuck_reviews():
    """Clear stale editor_claim on articles stuck in review.

    When Sage crashes mid-review, the editor_claim stays locked and
    prevents future Sage runs from processing the article.  This
    endpoint releases those stale claims so the next Sage run can
    pick them up.
    """
    from content_quality.db import get_db
    with get_db() as db:
        cursor = db.execute("""
            UPDATE articles
            SET editor_claim = ''
            WHERE status = 'review'
              AND editor_claim != ''
              AND editor_claim IS NOT NULL
        """)
        fixed = cursor.rowcount
    return {"status": "success", "stale_review_claims_cleared": fixed}


@app.get("/debug/database")
async def debug_database():
    """Debug endpoint to see what's in the database."""
    from content_quality.db import get_db
    from content_quality.config import DATABASE_PATH
    import os
    
    with get_db() as db:
        # Get tables
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        # Get total articles
        total = 0
        status_counts = {}
        sample_articles = []
        
        if 'articles' in tables:
            cursor = db.execute("SELECT COUNT(*) FROM articles")
            total = cursor.fetchone()[0]
            
            cursor = db.execute("SELECT status, COUNT(*) FROM articles GROUP BY status")
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}
            
            cursor = db.execute("SELECT id, title, target_keyword, status FROM articles LIMIT 5")
            sample_articles = [dict(row) for row in cursor.fetchall()]
    
    return {
        "db_path": DATABASE_PATH,
        "db_exists": os.path.exists(DATABASE_PATH),
        "tables": tables,
        "total_articles": total,
        "status_counts": status_counts,
        "sample_articles": sample_articles
    }


if __name__ == "__main__":
    import argparse
    import os
    import uvicorn

    parser = argparse.ArgumentParser(description="ClaimCoach Review Dashboard")
    parser.add_argument("--port", type=int, help="Port to run on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    # Priority: CLI arg > ENV var > config default
    port = args.port or int(os.getenv("PORT", API_PORT))

    print("=" * 60)
    print("  ClaimCoach Content Review Dashboard")
    print("=" * 60)
    print(f"  Dashboard: http://localhost:{port}/")
    print(f"  Database:  {DATABASE_PATH}")
    print("=" * 60)
    print()

    uvicorn.run(
        app,
        host=args.host,
        port=port,
        log_level="info"
    )

