"""
ClaimCoach Content Review Dashboard

Beautiful web interface for reviewing and approving articles.
Receives POST notifications when articles are ready for review.
"""

import json
import os
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from content_quality.db import get_db, log_agent_action, init_database
from content_quality.config import DATABASE_PATH, API_PORT

# In-memory background job store
_jobs: Dict[str, Dict[str, Any]] = {}


def _daily_promote_remaining(cfg, db) -> tuple[int, int, int]:
    """Return (daily_cap, promoted_today, remaining) for backlog->todo promotions."""
    daily_cap = max(1, int(getattr(cfg.pipeline, "daily_promote_cap", 8)))
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    promoted_today = len(
        db.get_metrics(name="promote_to_todo", since=start_of_day, limit=5000)
    )
    remaining = max(0, daily_cap - promoted_today)
    return daily_cap, promoted_today, remaining


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
_SEED_ROOT = Path(__file__).parent / "reference" / "gold_articles"
_PRODUCT_SITES = {
    "claimcoach": "https://claimcoach.app",
    "medbill": "https://billkarma.app",
}


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse DB/API timestamp formats into timezone-aware datetime."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except Exception:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _relative_time(dt: datetime) -> str:
    now = datetime.now(dt.tzinfo or timezone.utc)
    delta = now - dt
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _human_timestamp(value: Any) -> str:
    dt = _parse_timestamp(value)
    if not dt:
        return str(value or "—")
    formatted = dt.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
    return f"{formatted} · {_relative_time(dt)}"


templates.env.filters["humants"] = _human_timestamp


def _get_generation_pause_state() -> Dict[str, Any]:
    """Resolve generation pause state from env/DB/config, in that order."""
    try:
        from pipeline.config import Config, resolve_generation_pause
        from pipeline.db import Database

        cfg = Config.load()
        pdb = Database(cfg.resolve_path(cfg.pipeline.database_path))
        return resolve_generation_pause(db=pdb, pipeline_settings=cfg.pipeline)
    except Exception:
        return {"paused": True, "source": "fallback_safe"}


def _load_roi_kpis() -> Dict[str, Any]:
    """Load ROI KPIs from pipeline metrics and article data."""
    try:
        from pipeline.config import Config
        from pipeline.db import Database

        cfg = Config.load()
        db = Database(cfg.resolve_path(cfg.pipeline.database_path))
        return db.get_roi_kpis(days=90)
    except Exception:
        return {
            "window_days": 90,
            "estimated_cost_usd": 0.0,
            "llm_calls": 0,
            "published_articles": 0,
            "cost_per_published_usd": 0.0,
            "avg_time_to_index_days": None,
            "indexed_articles": 0,
            "clicks_per_article": {"30d": {"articles": 0, "avg_clicks": 0.0},
                                   "60d": {"articles": 0, "avg_clicks": 0.0},
                                   "90d": {"articles": 0, "avg_clicks": 0.0}},
            "conversion_by_cluster": [],
        }


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    status: Optional[str] = None,
    product: Optional[str] = None,
):
    """Main dashboard showing all articles in review."""

    try:
        allowed_statuses = [
            "editor_review", "review", "ready_to_publish", "revision",
            "rejected", "todo", "in_progress", "done", "amplified",
        ]
        status_filter = None
        if status and status in allowed_statuses:
            status_filter = status
        product_filter = (product or "claimcoach").strip().lower()
        if product_filter not in ("claimcoach", "medbill", "all"):
            product_filter = "claimcoach"

        # Get articles needing review
        with get_db() as db:
            base_query = """
                SELECT id, title, slug, target_keyword, target_state,
                       product,
                       status, sage_score, seo_score, readability_score,
                       validation_status, validation_notes,
                       word_count, revision_count, created_at, updated_at,
                       writer_claim, editor_claim
                FROM articles
                WHERE status IN ('editor_review', 'review', 'ready_to_publish', 'revision',
                                 'rejected', 'todo', 'in_progress', 'done', 'amplified')
            """
            params: tuple = ()
            if status_filter:
                base_query += " AND status = ?"
                params = (status_filter,)
            if product_filter != "all":
                base_query += " AND COALESCE(product, 'claimcoach') = ?"
                params = params + (product_filter,)

            base_query += """
                ORDER BY
                    CASE status
                        WHEN 'in_progress' THEN 1
                        WHEN 'editor_review' THEN 2
                        WHEN 'review' THEN 3
                        WHEN 'ready_to_publish' THEN 4
                        WHEN 'revision' THEN 5
                        WHEN 'todo' THEN 6
                        WHEN 'rejected' THEN 7
                        WHEN 'done' THEN 8
                        WHEN 'amplified' THEN 9
                    END,
                    updated_at DESC
            """
            cursor = db.execute(base_query, params)
            articles = [dict(row) for row in cursor.fetchall()]

        # Get summary stats
        with get_db() as db:
            cursor = db.execute("""
                SELECT status, COUNT(*) as count
                FROM articles
                WHERE (? = 'all' OR COALESCE(product, 'claimcoach') = ?)
                GROUP BY status
            """, (product_filter, product_filter))
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # Get approval threshold from pipeline config
        try:
            from pipeline.config import Config
            cfg = Config.load()
            approval_threshold = cfg.pipeline.approval_score_threshold
            max_revision_rounds = cfg.pipeline.max_revision_rounds
        except Exception:
            approval_threshold = 80
            max_revision_rounds = 5

        # Get recent rate limit events (last 24 hours)
        rate_limit_count = 0
        try:
            with get_db() as db:
                cursor = db.execute("""
                    SELECT COUNT(*) FROM pipeline_metrics
                    WHERE metric_name = 'rate_limit'
                    AND timestamp > datetime('now', '-24 hours')
                """)
                rate_limit_count = cursor.fetchone()[0]
        except Exception:
            pass  # Table may not exist yet

        roi_kpis = _load_roi_kpis()
        active_product_site = (
            _PRODUCT_SITES.get(product_filter) if product_filter in _PRODUCT_SITES else None
        )

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "articles": articles,
            "status_counts": status_counts,
            "status_filter": status_filter or "all",
            "product_filter": product_filter,
            "product_sites": _PRODUCT_SITES,
            "active_product_site": active_product_site,
            "approval_threshold": approval_threshold,
            "max_revision_rounds": max_revision_rounds,
            "rate_limit_count": rate_limit_count,
            "roi_kpis": roi_kpis,
            "generation_pause": _get_generation_pause_state(),
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
            SELECT id, product, title, slug, markdown_content, meta_title, meta_description,
                   target_keyword, target_state, status,
                   sage_score, seo_score, readability_score, state_accuracy, product_compliance,
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

    # Render markdown to HTML for the preview tab
    rendered_html = ""
    if article_dict.get("markdown_content"):
        try:
            from content_quality.utils.text_utils import markdown_to_html
            rendered_html = markdown_to_html(article_dict["markdown_content"])
        except Exception:
            rendered_html = ""

    # Parse validation notes
    validation_notes = []
    if article_dict.get("validation_notes"):
        try:
            validation_notes = json.loads(article_dict["validation_notes"])
        except Exception:
            validation_notes = []

    # Get validation history
    history = []
    try:
        with get_db() as db:
            cursor = db.execute("""
                SELECT agent_name, action, details, created_at
                FROM agent_log
                WHERE article_id = ?
                ORDER BY created_at DESC
                LIMIT 20
            """, (article_id,))
            history = [dict(row) for row in cursor.fetchall()]
    except Exception:
        pass  # agent_log table may not exist yet

    # Get CTA variants if any
    cta_variants = []
    try:
        with get_db() as db:
            cursor = db.execute("""
                SELECT cta_text, cta_type, position, impressions, clicks, conversions
                FROM cta_variants
                WHERE article_id = ?
            """, (article_id,))
            cta_variants = [dict(row) for row in cursor.fetchall()]
    except Exception:
        pass  # cta_variants table may not exist yet

    # Calculate estimated word count from markdown
    word_count = article_dict.get("word_count") or len(
        article_dict.get("markdown_content", "").split()
    )

    # Get approval threshold from pipeline config
    try:
        from pipeline.config import Config
        cfg = Config.load()
        approval_threshold = cfg.pipeline.approval_score_threshold
    except Exception:
        approval_threshold = 90

    # Define lifecycle stages for the tracker
    lifecycle_stages = [
        ("backlog", "Backlog"),
        ("todo", "Queued"),
        ("in_progress", "Writing"),
        ("editor_review", "Sage Scoring"),
        ("review", "Your Review"),
        ("ready_to_publish", "Approved"),
        ("done", "Published"),
        ("amplified", "Amplified"),
    ]

    try:
        max_revision_rounds = cfg.pipeline.max_revision_rounds
    except Exception:
        max_revision_rounds = 5

    return templates.TemplateResponse("article_review.html", {
        "request": request,
        "article": article_dict,
        "rendered_html": rendered_html,
        "validation_notes": validation_notes,
        "history": history,
        "cta_variants": cta_variants,
        "word_count": word_count,
        "approval_threshold": approval_threshold,
        "max_revision_rounds": max_revision_rounds,
        "lifecycle_stages": lifecycle_stages,
    })


@app.post("/api/article/{article_id}/approve")
async def approve_article(article_id: int, request: Request):
    """Approve an article for publishing."""
    body = await request.json()
    notes = body.get("notes", "")

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
async def reject_article(article_id: int, request: Request):
    """Reject an article and request revision."""
    body = await request.json()
    reason = body.get("reason", "")
    if not reason.strip():
        raise HTTPException(status_code=422, detail="Reason is required")

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
                writer_claim = '',
                editor_claim = '',
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

    # Clear stale claims and update status - Ezra will pick it up
    with get_db() as db:
        db.execute("""
            UPDATE articles SET
                status = 'ready_to_publish',
                writer_claim = '',
                editor_claim = '',
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
async def get_stats(product: Optional[str] = None):
    """Get dashboard statistics."""
    product_filter = (product or "all").strip().lower()
    if product_filter not in ("claimcoach", "medbill", "all"):
        product_filter = "all"

    with get_db() as db:
        # Status counts
        cursor = db.execute("""
            SELECT status, COUNT(*) as count
            FROM articles
            WHERE (? = 'all' OR COALESCE(product, 'claimcoach') = ?)
            GROUP BY status
        """, (product_filter, product_filter))
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
            WHERE validation_status IN ('PASS', 'FAIL', 'pass', 'fail')
              AND (? = 'all' OR COALESCE(product, 'claimcoach') = ?)
        """, (product_filter, product_filter))
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
        "roi_kpis": _load_roi_kpis(),
        "notifications_count": len(recent_notifications),
        "generation_pause": _get_generation_pause_state(),
    }


@app.get("/api/pipeline/pause")
async def get_pipeline_pause():
    """Get current global generation pause state."""
    return _get_generation_pause_state()


@app.post("/api/pipeline/pause")
async def set_pipeline_pause(request: Request):
    """Set runtime generation pause state (persisted in pipeline_metrics)."""
    payload = await request.json()
    paused = bool(payload.get("paused", True))
    reason = str(payload.get("reason", "dashboard_toggle")).strip()[:120]
    try:
        from pipeline.config import Config
        from pipeline.db import Database

        cfg = Config.load()
        pdb = Database(cfg.resolve_path(cfg.pipeline.database_path))
        pdb.record_metric(
            "pipeline_pause",
            1.0 if paused else 0.0,
            json.dumps(
                {
                    "paused": paused,
                    "reason": reason,
                    "source": "dashboard",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
        state = _get_generation_pause_state()
        return {"success": True, **state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _safe_seed_path(product: str, filename: str) -> Path:
    p = (product or "").strip().lower()
    if p not in ("claimcoach", "medbill"):
        raise HTTPException(status_code=400, detail="Invalid product")
    base = (_SEED_ROOT / p).resolve()
    path = (base / filename).resolve()
    if not str(path).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Invalid seed path")
    if path.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Seed files must be .md")
    return path


@app.get("/seeds", response_class=HTMLResponse)
async def seeds_page(request: Request, product: Optional[str] = None):
    product_filter = (product or "claimcoach").strip().lower()
    if product_filter not in ("claimcoach", "medbill"):
        product_filter = "claimcoach"
    directory = _SEED_ROOT / product_filter
    seeds = []
    if directory.exists():
        for p in sorted(directory.glob("*.md")):
            title = p.stem
            try:
                with p.open("r", encoding="utf-8") as f:
                    first = f.readline().strip()
                if first.startswith("# "):
                    title = first[2:].strip()
            except Exception:
                pass
            seeds.append({"file": p.name, "title": title})
    return templates.TemplateResponse(
        "seeds.html",
        {
            "request": request,
            "product_filter": product_filter,
            "seeds": seeds,
            "product_sites": _PRODUCT_SITES,
            "active_product_site": _PRODUCT_SITES.get(product_filter),
        },
    )


@app.get("/api/seed/{product}/{filename:path}")
async def get_seed(product: str, filename: str):
    path = _safe_seed_path(product, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Seed not found")
    return {"product": product, "file": filename, "content": path.read_text(encoding="utf-8")}


@app.post("/api/seed/{product}/{filename:path}")
async def save_seed(product: str, filename: str, request: Request):
    payload = await request.json()
    content = str(payload.get("content", ""))
    path = _safe_seed_path(product, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"success": True, "file": filename}


@app.post("/api/seed/promote/{article_id}")
async def promote_article_to_seed(article_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT title, markdown_content, product FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    title = (row["title"] or f"article-{article_id}").strip()
    content = (row["markdown_content"] or "").strip()
    product = (row["product"] or "claimcoach").strip().lower()
    if product not in ("claimcoach", "medbill"):
        product = "claimcoach"
    slug = (
        title.lower()
        .replace(" ", "-")
        .replace("/", "-")
    )
    slug = "".join(ch for ch in slug if ch.isalnum() or ch in "-_")[:80] or f"article-{article_id}"
    path = _safe_seed_path(product, f"{slug}.md")
    path.write_text(content, encoding="utf-8")
    return {"success": True, "product": product, "file": path.name}


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

        daily_cap, promoted_today, remaining = _daily_promote_remaining(cfg, db)
        if remaining <= 0:
            return {
                "status": "done",
                "promoted": 0,
                "articles": [],
                "daily_cap": daily_cap,
                "promoted_today": promoted_today,
                "remaining": 0,
                "message": "Daily promote cap reached",
            }

        backlog = db.query_articles(
            status=ArticleStatus.BACKLOG.value,
            limit=min(10, remaining),
            order_by=(
                "commercial_intent DESC, "
                "search_volume DESC, "
                "keyword_difficulty ASC, "
                "created_at ASC"
            ),
        )

        promoted = []
        for article in backlog:
            db.update_article(article.id, status=ArticleStatus.TODO.value)
            db.record_metric("promote_to_todo", 1, str(article.id))
            promoted.append({"id": article.id, "keyword": article.target_keyword})

        return {
            "status": "done",
            "promoted": len(promoted),
            "articles": promoted,
            "daily_cap": daily_cap,
            "promoted_today": promoted_today,
            "remaining": max(0, remaining - len(promoted)),
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

    # Reset only stale in_progress articles (avoid stealing active work).
    stale = db.get_stuck_articles(hours=3)
    stale_in_progress = [a for a in stale if a.status == ArticleStatus.IN_PROGRESS.value]
    for article in stale_in_progress:
        logger.info(f"Resetting stale in_progress article {article.id} back to todo")
        db.update_article(article.id, status=ArticleStatus.TODO.value, writer_claim="")

    todo_count = db.count_articles(status=ArticleStatus.TODO.value)
    if todo_count > 0:
        logger.info(f"Recovered {todo_count} stuck articles to todo")
        return

    # Respect daily promote cap before auto-promoting from backlog.
    daily_cap, promoted_today, remaining = _daily_promote_remaining(cfg, db)
    if remaining <= 0:
        logger.info(
            "Daily promote cap reached (%s/%s); skipping auto-promote",
            promoted_today,
            daily_cap,
        )
        return

    # Auto-promote from backlog
    backlog = db.query_articles(
        status=ArticleStatus.BACKLOG.value,
        limit=min(3, remaining),
        order_by=(
            "commercial_intent DESC, "
            "search_volume DESC, "
            "keyword_difficulty ASC, "
            "created_at ASC"
        ),
    )
    for article in backlog:
        db.update_article(article.id, status=ArticleStatus.TODO.value)
        db.record_metric("promote_to_todo", 1, str(article.id))
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
    from pipeline.db import Database

    cfg = Config.load()
    db = Database(cfg.resolve_path(cfg.pipeline.database_path))

    # Check has_llm the same way agents do
    has_llm = bool(cfg.anthropic.api_key or cfg.gemini.api_key)

    return {
        "llm_provider": cfg.llm_provider,
        "has_llm": has_llm,
        "anthropic_api_key_set": bool(cfg.anthropic.api_key),
        "gemini_api_key_set": bool(cfg.gemini.api_key),
        "gemini_default_model": cfg.gemini.default_model,
        "approval_threshold": cfg.pipeline.approval_score_threshold,
        "max_revision_rounds": cfg.pipeline.max_revision_rounds,
        "fact_check_mode": "ai_deep_check" if has_llm else "regex_only_capped_10",
        "env_LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
        "env_ANTHROPIC_API_KEY_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
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
    """Clear stale editor_claim on articles stuck in editor_review.

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
            WHERE status = 'editor_review'
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


# ──────────────────────────────────────────────────────────────
# Interactive Conversion Tools — API + standalone pages
# ──────────────────────────────────────────────────────────────
from tools.data import (
    export_tool_data_json,
    calculate_sales_tax,
    calculate_checklist_results,
    calculate_fairness_score,
    STATE_SALES_TAX,
)

_TOOL_DATA_JSON = export_tool_data_json()

# FAQ data for standalone tool pages
_TOOL_FAQS = {
    "sales_tax_calculator": [
        {"q": "Is sales tax always included in total loss settlements?", "a": "In most states, insurers are required to include sales tax. However, some states (like Illinois and Ohio) require you to purchase a replacement vehicle within 30 days and provide documentation before reimbursing sales tax."},
        {"q": "What if I'm keeping the totaled vehicle?", "a": "If you keep the vehicle (retain salvage), the sales tax calculation changes. You'll owe tax on the settlement amount minus the salvage value, since that's the net amount you'd spend on a replacement."},
        {"q": "Does this apply to third-party claims?", "a": "Yes. Whether your own insurer (first-party) or the at-fault driver's insurer (third-party) is paying, sales tax on the replacement vehicle should be included in the settlement."},
        {"q": "What if my insurer refuses to pay sales tax?", "a": "File a complaint with your state's Department of Insurance. In most states, failing to include sales tax violates fair claims settlement practices. You can also cite your state's specific regulation in a demand letter."},
    ],
    "settlement_checklist": [
        {"q": "What should a total loss settlement include?", "a": "At minimum: the actual cash value (ACV) of your vehicle, sales tax on a replacement, title and registration fees, and any applicable state-specific items. Many offers also miss comparable vehicle adjustments, dealer fees, and loss of use compensation."},
        {"q": "How do I know if my insurer used accurate comparable vehicles?", "a": "Request their valuation report. Check that comps match your vehicle's trim, mileage, condition, and options. If their comps have higher mileage or lower trim, your ACV should be adjusted upward."},
        {"q": "Can I dispute missing line items?", "a": "Absolutely. Write a formal demand letter listing each missing item with dollar amounts and legal citations. Most adjusters have authority to increase offers by 10-15% without supervisor approval."},
    ],
    "fairness_quiz": [
        {"q": "How is the fairness score calculated?", "a": "The score starts at 50 (they at least made an offer). Including sales tax adds 15 points. Each standard line item adds 7 points. Missing 3 or more items triggers an additional penalty. The maximum score is 100."},
        {"q": "What score should I be aiming for?", "a": "A fair offer typically scores 75-100. Below 60 means several common line items are missing. Below 40 means your offer is likely thousands of dollars below fair value."},
        {"q": "Is this score legally binding?", "a": "No. This is an educational estimate to help you understand whether your offer may be below fair value. For legal advice specific to your situation, consult an attorney."},
    ],
    "car_worth_estimator": [
        {"q": "Which valuation source is most accurate?", "a": "Use multiple sources and average them. KBB, NADA, and Edmunds each use different methodologies. Having 2-3 independent valuations strengthens your negotiation position."},
        {"q": "Why might my car be worth more than KBB says?", "a": "KBB uses national averages. Your local market, low mileage, excellent condition, desirable color, or aftermarket upgrades can all push the value higher. Regional supply shortages also affect prices."},
    ],
}

_TOOL_META = {
    "sales_tax_calculator": {
        "title": "Sales Tax Recovery Calculator",
        "description": "Calculate how much sales tax your insurer owes you on your total loss settlement. Free instant results for all 50 states.",
        "slug": "sales-tax-calculator",
    },
    "settlement_checklist": {
        "title": "Total Loss Settlement Checklist",
        "description": "Check every line item that should be in your total loss settlement offer. See what's missing and how much you could be owed.",
        "slug": "settlement-checklist",
    },
    "fairness_quiz": {
        "title": "Is My Insurance Offer Fair?",
        "description": "Answer 5 quick questions to find out if your total loss settlement is fair or if you're leaving money on the table.",
        "slug": "fairness-quiz",
    },
    "car_worth_estimator": {
        "title": "What's My Totaled Car Worth?",
        "description": "Get an independent estimate of your totaled vehicle's value to compare against your insurer's offer.",
        "slug": "car-worth-estimator",
    },
}


@app.get("/api/tools/data")
async def tools_data_api():
    """JSON data for client-side tool widgets."""
    return json.loads(_TOOL_DATA_JSON)


@app.post("/api/tools/calculate-tax")
async def calculate_tax_api(request: Request):
    """Server-side sales tax calculation."""
    body = await request.json()
    result = calculate_sales_tax(
        settlement_amount=float(body.get("amount", 0)),
        state=body.get("state", ""),
        keeping_vehicle=body.get("keeping_vehicle", False),
        salvage_value=float(body.get("salvage_value", 0)),
    )
    return result


@app.post("/api/tools/checklist")
async def checklist_api(request: Request):
    """Server-side checklist gap calculation."""
    body = await request.json()
    result = calculate_checklist_results(
        checked_items=body.get("checked", []),
        state=body.get("state", ""),
        offer_amount=float(body.get("amount", 0)),
    )
    return result


@app.post("/api/tools/fairness")
async def fairness_api(request: Request):
    """Server-side fairness score calculation."""
    body = await request.json()
    result = calculate_fairness_score(
        state=body.get("state", ""),
        offer_amount=float(body.get("amount", 0)),
        includes_sales_tax=body.get("includes_tax", False),
        included_items=body.get("included_items", []),
    )
    return result


@app.get("/tools", response_class=HTMLResponse)
async def tools_index(request: Request):
    """Index page listing all interactive tools."""
    html = """<!DOCTYPE html><html lang="en"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Free Insurance Settlement Tools | ClaimCoach</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
    body{margin:0;padding:0;font-family:'Inter',sans-serif;background:#f8fafc;color:#1e293b}
    .wrap{max-width:720px;margin:60px auto;padding:0 24px}
    h1{font-size:32px;font-weight:800;margin:0 0 8px}
    .sub{color:#64748b;font-size:16px;margin:0 0 32px}
    .card{display:block;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:24px;margin-bottom:16px;text-decoration:none;color:inherit;transition:box-shadow .15s}
    .card:hover{box-shadow:0 4px 12px rgba(0,0,0,.08)}
    .card h2{font-size:18px;font-weight:700;margin:0 0 6px;color:#0f172a}
    .card p{font-size:14px;color:#64748b;margin:0}
    .card .arrow{float:right;color:#2563eb;font-size:20px;font-weight:700}
    </style></head><body><div class="wrap">
    <h1>Free Settlement Tools</h1>
    <p class="sub">Interactive calculators to help you understand your total loss settlement.</p>"""
    for tool_id, meta in _TOOL_META.items():
        html += f'<a class="card" href="/tools/{meta["slug"]}"><span class="arrow">&rarr;</span><h2>{meta["title"]}</h2><p>{meta["description"]}</p></a>'
    html += "</div></body></html>"
    return HTMLResponse(content=html)


@app.get("/tools/{slug}", response_class=HTMLResponse)
async def tool_page(request: Request, slug: str):
    """Standalone full-version tool page."""
    # Find tool by slug
    tool_id = None
    meta = None
    for tid, m in _TOOL_META.items():
        if m["slug"] == slug:
            tool_id = tid
            meta = m
            break
    if not tool_id:
        raise HTTPException(status_code=404, detail="Tool not found")

    return templates.TemplateResponse("tools/tool_page.html", {
        "request": request,
        "title": meta["title"],
        "description": meta["description"],
        "slug": slug,
        "tool_id": tool_id,
        "tool_data_json": _TOOL_DATA_JSON,
        "faqs": _TOOL_FAQS.get(tool_id, []),
        "state": "",
        "year": datetime.now().year,
    })


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
