"""
ClaimCoach Content Review Dashboard

Beautiful web interface for reviewing and approving articles.
Receives POST notifications when articles are ready for review.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from content_quality.db import get_db, log_agent_action
from content_quality.config import DATABASE_PATH, API_PORT

app = FastAPI(
    title="ClaimCoach Review Dashboard",
    description="Review and approve content before publishing",
    version="1.0.0"
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
