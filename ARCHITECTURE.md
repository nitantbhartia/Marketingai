# ClaimCoach Content Engine — Complete System Architecture

## System Overview

This repository contains the **Content Quality & SEO Automation** subsystem of the ClaimCoach Content Engine. It integrates with the agent pipeline to ensure all published content meets quality standards.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REFERENCE DOCS                               │
│  (loaded by agents at runtime — source of truth)                    │
│                                                                     │
│  PRODUCT_CONTEXT.md ──── what ClaimCoach does / doesn't do          │
│  STATE_RULES.md ──────── state-specific insurance regulations       │
└─────────────────────────────────────────────────────────────────────┘
         │                                          │
         ▼                                          ▼
┌─────────────────────────┐      ┌─────────────────────────────────┐
│   AGENT PIPELINE         │      │   QUALITY AUTOMATION             │
│   (separate repo/deploy) │      │   (THIS REPO)                    │
│                          │      │                                   │
│   Scout → Quill → Sage ──┼──►───┤   Sage calls POST /validate/all  │
│   Ezra → Herald → Lurker │      │                                   │
│   Morgan (PM)            │      │   6 pre-publish checks            │
│                          │      │   2 post-publish monitors         │
└──────────┬───────────────┘      └──────────┬────────────────────────┘
           │                                  │
           ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     INFRASTRUCTURE (Railway)                         │
│                                                                     │
│   SQLite ◄──── all agents read/write                                │
│   Blog/  ◄──── Ezra publishes static files (markdown + HTML)       │
│   FastAPI ◄─── quality validation API server                        │
│   Cron jobs ── agent scheduling                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Repository Structure

```
claimcoach-content-quality/
│
├── reference/                      # Reference documents (loaded at runtime)
│   ├── PRODUCT_CONTEXT.md         # What ClaimCoach does/doesn't do
│   └── STATE_RULES.md             # State-specific insurance regulations
│
├── content_quality/                # Quality automation system
│   ├── config.py                  # Shared configuration
│   ├── db.py                      # SQLite helpers & schema
│   │
│   ├── validators/                # Pre-publish validators (1-6)
│   │   ├── product_validator.py  # Prevents feature hallucinations
│   │   ├── state_validator.py    # Cross-references STATE_RULES.md
│   │   ├── seo_scorer.py         # 100-point SEO scoring
│   │   ├── readability_analyzer.py # 8th-grade reading level
│   │   ├── link_checker.py       # Validates all links
│   │   └── math_validator.py     # Catches arithmetic errors
│   │
│   ├── monitors/                  # Post-publish monitors (7-8)
│   │   ├── search_console_monitor.py  # Weekly GSC analysis
│   │   └── content_health_checker.py  # Broken links, cannibalization
│   │
│   └── utils/                     # Shared utilities
│       └── text_utils.py          # Text processing helpers
│
├── agents/                         # Agent integration layer (optional)
│   ├── README.md                  # Agent integration guide
│   └── integration_examples/      # Example agent code
│       ├── sage_integration.py    # How Sage calls validation API
│       └── quill_integration.py   # How Quill uses reference docs
│
├── tests/                         # Test suite
│   ├── test_validation.py        # Validator tests
│   └── test_data/                # Test articles
│
├── data/                          # SQLite database (gitignored)
│   └── claimcoach_content.db     # Created at runtime
│
├── api_server.py                  # FastAPI validation server
├── cron_runner.py                 # Weekly monitor scheduler
├── requirements.txt               # Python dependencies
├── railway.toml                   # Railway deployment config
├── Procfile                       # Multi-service deployment
│
├── README.md                      # Quick start guide
├── ARCHITECTURE.md                # This file - system overview
├── INTEGRATION.md                 # How to integrate with agents
└── DEPLOYMENT.md                  # Railway deployment guide
```

## Data Flow

### Content Creation Flow

```
1. Scout (external)
   └─► Writes topics to SQLite articles table (status: 'backlog')

2. Quill (external)
   ├─► Reads reference/PRODUCT_CONTEXT.md
   ├─► Reads reference/STATE_RULES.md
   ├─► Claims article from SQLite (status: 'todo' → 'in_progress')
   ├─► Calls Claude API with reference docs in context
   └─► Writes draft to SQLite (status: 'review')

3. Sage (external)
   ├─► Reads article from SQLite (status: 'review')
   ├─► Calls THIS REPO's API: POST /validate/all
   │   │
   │   └─► Content Quality API (THIS REPO)
   │       ├─► Product Validator (checks PRODUCT_CONTEXT.md)
   │       ├─► State Validator (checks STATE_RULES.md)
   │       ├─► SEO Scorer (100-point system)
   │       ├─► Readability Analyzer (Flesch-Kincaid)
   │       ├─► Link Checker (HTTP status)
   │       └─► Math Validator (arithmetic)
   │
   │       Returns: {
   │           "overall_status": "PASS" | "FAIL",
   │           "revision_notes": [...],
   │           "seo_score": 87,
   │           ...
   │       }
   │
   ├─► Writes validation results to SQLite
   │   └─► If PASS: status = 'ready_to_publish'
   │   └─► If FAIL: status = 'revision', includes revision_notes

4. Quill (revision loop)
   ├─► Reads article from SQLite (status: 'revision')
   ├─► Fixes issues per revision_notes
   └─► Re-submits to Sage (status: 'review')

5. Ezra (external)
   ├─► Reads article from SQLite (status: 'ready_to_publish')
   ├─► Publishes to static files (markdown + HTML)
   ├─► Submits to Google Search Console
   └─► Updates SQLite (status: 'done', published_url, published_at)

6. Herald (external)
   ├─► Reads article from SQLite (status: 'done', social_status: NULL)
   ├─► Drafts social promotion
   └─► Updates SQLite (social_status: 'amplified')
```

### Weekly Monitoring Flow

```
Monday 6am UTC: Search Console Monitor (THIS REPO)
   ├─► Pulls GSC data via Google API
   ├─► Identifies opportunities:
   │   ├─► Almost page one (positions 4-10)
   │   ├─► High impressions, low CTR
   │   ├─► New ranking keywords
   │   └─► Declining articles
   ├─► Writes to gsc_snapshots table
   └─► Sets refresh_priority flags in articles table

Monday 8am UTC: Content Health Checker (THIS REPO)
   ├─► Crawls all published articles
   ├─► Detects:
   │   ├─► Broken links (writes to broken_links table)
   │   ├─► Content cannibalization (TF-IDF similarity)
   │   └─► Stale content (age + ranking)
   └─► Sets flags in articles table

Morgan (external)
   ├─► Reads flags and priorities from SQLite
   ├─► Spawns agents to fix issues:
   │   ├─► Quill to refresh stale articles
   │   ├─► Quill to rewrite low-CTR titles
   │   └─► Scout to create articles for new keywords
   └─► Sends Telegram summary to human
```

## Database Schema

### Core Table: articles

```sql
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Content
    title TEXT NOT NULL,
    slug TEXT UNIQUE,
    target_keyword TEXT,
    target_state TEXT,                    -- NULL if not state-specific
    markdown_content TEXT,
    meta_title TEXT,
    meta_description TEXT,

    -- Pipeline status
    status TEXT NOT NULL DEFAULT 'backlog',
        -- backlog → todo → in_progress → review → revision → ready_to_publish → done

    -- Claim locking (prevents race conditions)
    writer_claim TEXT,                    -- e.g. "quill-1707004821-x7k2"
    editor_claim TEXT,
    publisher_claim TEXT,
    herald_claim TEXT,

    -- Validation results (written by THIS REPO)
    validation_status TEXT,               -- PASS | FAIL
    seo_score INTEGER,                    -- 0-100
    readability_score REAL,               -- Flesch-Kincaid
    state_accuracy TEXT,                  -- PASS | WARN | FAIL
    product_compliance TEXT,              -- PASS | FAIL
    broken_links_count INTEGER DEFAULT 0,
    math_errors_count INTEGER DEFAULT 0,
    validation_notes TEXT,                -- JSON array of issues

    -- Revision tracking
    revision_count INTEGER DEFAULT 0,
    revision_notes TEXT,                  -- Instructions for Quill

    -- Publishing
    published_url TEXT,
    published_at TIMESTAMP,
    social_status TEXT,                   -- NULL | amplified

    -- SEO monitoring (updated by THIS REPO weekly)
    last_gsc_position REAL,
    last_gsc_impressions INTEGER,
    last_gsc_clicks INTEGER,
    last_gsc_ctr REAL,
    refresh_priority TEXT,               -- HIGH | MEDIUM | LOW | NULL
    cannibalization_flag BOOLEAN DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Supporting Tables (all managed by THIS REPO)

- **gsc_snapshots**: Historical Search Console data
- **broken_links**: Broken link tracking by article
- **cannibalization_pairs**: Similar articles (TF-IDF > 0.7)
- **agent_log**: Activity log for debugging

See `content_quality/db.py` for full schema.

## API Endpoints

### Primary Endpoint (used by Sage)

**`POST /validate/all`**

Runs all 6 pre-publish validators and returns combined result.

**Request:**
```json
{
  "article_markdown": "# Full article content...",
  "target_keyword": "total loss settlement california",
  "meta_title": "California Total Loss Guide",
  "meta_description": "Learn about total loss...",
  "slug": "california-total-loss-guide",
  "target_state": "California"
}
```

**Response:**
```json
{
  "overall_status": "PASS" | "FAIL",
  "state_validation": { "status": "PASS", "issues": [] },
  "product_validation": { "status": "PASS", "hard_violations": [] },
  "seo_score": { "total_score": 87, "status": "PASS", "checks": {...} },
  "readability": { "flesch_reading_ease": 64.2, "status": "PASS" },
  "links": { "status": "PASS", "internal": [], "external": [] },
  "math": { "status": "PASS", "issues": [] },
  "summary": "✓ PASSED all checks. SEO: 87/100, Readability: 64/100",
  "revision_notes": []
}
```

### Individual Validators

- `POST /validate/state-rules` - State regulation accuracy
- `POST /validate/product-claims` - Product claim validity
- `POST /validate/seo` - SEO score (0-100)
- `POST /validate/readability` - Reading level analysis
- `POST /validate/links` - Link validation
- `POST /validate/math` - Math verification

### Health Check

- `GET /health` - Service health status

**Full API docs:** `http://localhost:8000/docs` (when running)

## Integration Points

### For Agent Developers

#### Quill Integration
```python
# Load reference docs at runtime
from content_quality.config import PRODUCT_CONTEXT_PATH, STATE_RULES_PATH

with open(PRODUCT_CONTEXT_PATH) as f:
    product_context = f.read()

with open(STATE_RULES_PATH) as f:
    state_rules = f.read()

# Include in Claude API system prompt
system_prompt = f"""
You are writing a blog article about insurance.

PRODUCT CONTEXT (what to say/not say):
{product_context}

STATE REGULATIONS:
{state_rules}

...
"""
```

#### Sage Integration
```python
import requests

# Call validation API
response = requests.post(
    "http://content-quality-api:8000/validate/all",
    json={
        "article_markdown": article["markdown_content"],
        "target_keyword": article["target_keyword"],
        "meta_title": article["meta_title"],
        "meta_description": article["meta_description"],
        "slug": article["slug"],
        "target_state": article["target_state"],
    }
)

result = response.json()

if result["overall_status"] == "PASS":
    # Update article status to ready_to_publish
    update_article(article_id, status="ready_to_publish")
else:
    # Send back for revision
    update_article(
        article_id,
        status="revision",
        revision_notes=result["revision_notes"],
        validation_status="FAIL",
        seo_score=result["seo_score"]["total_score"],
        readability_score=result["readability"]["flesch_reading_ease"],
    )
```

See `agents/integration_examples/` for complete examples.

## Environment Variables

### Required

```bash
# Blog publishing (static files)
BLOG_OUTPUT_DIR=./blog
SITE_URL=https://claimcoach.app

# Google Search Console
GSC_CREDENTIALS_JSON={"type":"service_account",...}

# Database
DATABASE_PATH=/data/claimcoach_content.db
```

### Optional (have defaults)

```bash
API_HOST=0.0.0.0
API_PORT=8000
MIN_SEO_SCORE=80
MIN_READABILITY_SCORE=60
MIN_WORD_COUNT=1800
MAX_WORD_COUNT=2200
LINK_CHECK_TIMEOUT=10
```

## Deployment Architecture

### Railway Services

```
claimcoach-content-quality (Railway Project)
│
├── content-quality-api (Service 1 - Web)
│   ├── Runs: api_server.py
│   ├── Port: 8000
│   ├── Always on
│   ├── Volume: /data (for SQLite)
│   └── Cost: ~$5/month
│
├── search-console-monitor (Service 2 - Cron)
│   ├── Runs: content_quality/monitors/search_console_monitor.py
│   ├── Schedule: 0 6 * * 1 (Monday 6am UTC)
│   └── Cost: $0 (included)
│
└── content-health-checker (Service 3 - Cron)
    ├── Runs: content_quality/monitors/content_health_checker.py
    ├── Schedule: 0 8 * * 1 (Monday 8am UTC)
    └── Cost: $0 (included)
```

### Agent Services (separate deployment)

Agents (Scout, Quill, Sage, Ezra, Herald, Lurker, Morgan) are typically deployed separately, either:
- As Railway cron jobs in a different project
- As standalone scripts with their own scheduling
- As part of your main ClaimCoach application

They integrate with this repo by:
1. **Reading** from shared SQLite database
2. **Calling** the validation API (`/validate/all`)
3. **Loading** reference docs from `reference/` directory

## Development Workflow

### Local Setup

```bash
# 1. Clone and install
git clone <repo>
cd claimcoach-content-quality
pip install -r requirements.txt

# 2. Initialize database
python -c "from content_quality.db import init_database; init_database()"

# 3. Run API server
python api_server.py

# 4. Test validators
python -m pytest tests/ -v

# 5. Run monitors manually
python -m content_quality.monitors.search_console_monitor
python -m content_quality.monitors.content_health_checker
```

### Testing Article Validation

```bash
# Add test article to database
python -c "
from content_quality.db import get_db
with get_db() as db:
    db.execute('''
        INSERT INTO articles (title, slug, status, target_keyword, markdown_content)
        VALUES (?, ?, ?, ?, ?)
    ''', ('Test Article', 'test-article', 'review', 'test keyword', '# Test\n\nContent here.'))
"

# Call validation API
curl -X POST http://localhost:8000/validate/all \
  -H "Content-Type: application/json" \
  -d @test_article.json
```

### Updating Reference Docs

```bash
# Edit reference docs
vim reference/STATE_RULES.md
vim reference/PRODUCT_CONTEXT.md

# Changes take effect immediately (loaded at runtime by agents)
# No restart required for agents that load these files
```

## Monitoring & Maintenance

### Check Validation API Health

```bash
curl http://localhost:8000/health
```

### Query Database for Pipeline Status

```sql
-- Pipeline overview
SELECT status, COUNT(*) FROM articles GROUP BY status;

-- Failed validations
SELECT title, seo_score, validation_notes
FROM articles WHERE validation_status = 'FAIL';

-- Refresh priorities
SELECT title, refresh_priority, last_gsc_position
FROM articles WHERE refresh_priority IS NOT NULL;

-- Recent agent activity
SELECT agent_name, action, COUNT(*) as count
FROM agent_log
WHERE created_at > datetime('now', '-7 days')
GROUP BY agent_name, action;
```

### Monitor Weekly Jobs

```bash
# Check logs for cron jobs
railway logs -s search-console-monitor
railway logs -s content-health-checker

# Verify snapshots are being created
SELECT COUNT(*) FROM gsc_snapshots WHERE snapshot_date = DATE('now', '-7 days');
```

## Performance Metrics

### Validation Times

| Check | Expected Time |
|-------|--------------|
| Product Claim Validator | < 200ms |
| State Regulation Validator | < 500ms |
| SEO Scorer | < 300ms |
| Readability Analyzer | < 200ms |
| Link Checker | 2-10s (network) |
| Math Validator | < 300ms |
| **Total (/validate/all)** | **3-12 seconds** |

### Weekly Monitor Times

| Monitor | Expected Time |
|---------|--------------|
| Search Console Monitor | 1-2 minutes |
| Content Health Checker | 5-10 minutes |

### Database Size

- Articles table: ~1KB per article
- GSC snapshots: ~500 bytes per row
- Expected size with 100 articles + 1 year of GSC data: ~5MB

## Security

### API Security
- No authentication required (internal Railway network only)
- CORS configured for ClaimCoach domains only (production)
- Rate limiting via Railway's built-in protections

### Database Security
- SQLite file not exposed externally
- Only accessible via Railway private network
- Regular backups via Railway volume snapshots

### API Keys
- GSC credentials stored as Railway env vars
- Never committed to git (.gitignore includes .env)
- Rotated quarterly (recommended)

## Troubleshooting

See `DEPLOYMENT.md` for common issues and solutions.

## Related Documentation

- **README.md** - Quick start guide
- **DEPLOYMENT.md** - Railway deployment instructions
- **INTEGRATION.md** - How to integrate with agents
- **reference/PRODUCT_CONTEXT.md** - Product capabilities reference
- **reference/STATE_RULES.md** - State regulation reference

## Support

For issues with this system:
1. Check Railway logs: `railway logs`
2. Query agent_log table for debugging
3. Test individual validators at `/docs` endpoint
4. Review validation_notes in articles table
