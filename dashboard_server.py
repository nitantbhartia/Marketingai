"""
ClaimCoach Content Review Dashboard

Beautiful web interface for reviewing and approving articles.
Receives POST notifications when articles are ready for review.
"""

import json
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
                WHERE status IN ('review', 'ready_to_publish', 'revision')
                ORDER BY
                    CASE status
                        WHEN 'ready_to_publish' THEN 1
                        WHEN 'review' THEN 2
                        WHEN 'revision' THEN 3
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


# Manual agent trigger endpoints
@app.get("/trigger/scout")
async def trigger_scout():
    """Manually trigger Scout agent to create topics."""
    try:
        from pipeline.scheduler import run_agent
        from pipeline.config import Config
        from pipeline.db import Database

        cfg = Config.load()
        db = Database(cfg.resolve_path(cfg.pipeline.database_path))
        result = run_agent("scout", cfg, db)

        return {
            "status": "success",
            "agent": "scout",
            "result": result
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "agent": "scout",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


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
            "status": "success",
            "promoted": len(promoted),
            "articles": promoted,
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


@app.get("/trigger/quill")
async def trigger_quill():
    """Manually trigger Quill agent to write articles."""
    try:
        from pipeline.scheduler import run_agent
        from pipeline.config import Config
        from pipeline.db import Database

        cfg = Config.load()
        db = Database(cfg.resolve_path(cfg.pipeline.database_path))
        result = run_agent("quill", cfg, db)

        return {
            "status": "success",
            "agent": "quill",
            "result": result
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "agent": "quill",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@app.get("/trigger/sage")
async def trigger_sage():
    """Manually trigger Sage agent to review articles."""
    try:
        from pipeline.scheduler import run_agent
        from pipeline.config import Config
        from pipeline.db import Database

        cfg = Config.load()
        db = Database(cfg.resolve_path(cfg.pipeline.database_path))
        result = run_agent("sage", cfg, db)

        return {
            "status": "success",
            "agent": "sage",
            "result": result
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "agent": "sage",
            "error": str(e),
            "traceback": traceback.format_exc()
        }


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

