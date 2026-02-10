# ClaimCoach Content Quality & SEO Automation

Automated content validation and SEO monitoring system for ClaimCoach blog content.

> **📖 Complete Documentation:**
> - **[ARCHITECTURE.md](./ARCHITECTURE.md)** - Complete system overview and data flow
> - **[INTEGRATION.md](./INTEGRATION.md)** - How to integrate with agent pipeline
> - **[DEPLOYMENT.md](./DEPLOYMENT.md)** - Railway deployment guide

## Overview

This repository contains the **Content Quality & SEO Automation** subsystem of the ClaimCoach Content Engine. It integrates with the agent pipeline (Scout → Quill → Sage → Ezra → Herald → Lurker → Morgan) to ensure all published content meets quality standards.

**8 Automated Systems:**
- ✅ **Pre-Publish Validators (1-6):** State accuracy, product claims, SEO, readability, links, math
- ✅ **Post-Publish Monitors (7-8):** Search Console analysis, content health checks

## Quick Links

| Document | Description |
|----------|-------------|
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | Complete system architecture, data flow, integration points |
| **[INTEGRATION.md](./INTEGRATION.md)** | How agents integrate with this system |
| **[DEPLOYMENT.md](./DEPLOYMENT.md)** | Railway deployment instructions (3 options) |
| **[agents/](./agents/)** | Integration examples for Quill, Sage, Morgan |
| **[reference/](./reference/)** | PRODUCT_CONTEXT.md, STATE_RULES.md (loaded by agents) |

## Architecture

```
CONTENT PIPELINE:

  Article Draft (SQLite)
         │
         ▼
  ┌─────────────────────┐
  │  PRE-PUBLISH GATE   │
  │                     │
  │  1. State Validator │
  │  2. Product Validator│
  │  3. SEO Scorer     │
  │  4. Readability    │
  │  5. Link Checker   │
  │  6. Math Validator │
  │                     │
  │  ALL PASS? ────────►│──► Publish to Ghost
  │  ANY FAIL? ────────►│──► Back for revision
  └─────────────────────┘

  POST-PUBLISH (Weekly):

  7. Search Console Monitor (Mondays 6am UTC)
  8. Content Health Checker (Mondays 8am UTC)
```

## Components

### Pre-Publish Validators (1-6)

#### 1. Product Claim Validator (Priority 1)
**Purpose:** Prevent feature hallucinations and overpromises

**Catches:**
- ❌ "ClaimCoach negotiates with insurers"
- ❌ "Upload your settlement letter"
- ❌ "We guarantee you'll get more money"
- ❌ "Track your claim status"

**Allows:**
- ✅ "ClaimCoach analyzes your settlement"
- ✅ "Helps you identify missing line items"
- ✅ "Free to analyze — no credit card required"

**API Endpoint:** `POST /validate/product-claims`

---

#### 2. State Regulation Validator (Priority 2)
**Purpose:** Catch factually incorrect state-specific claims

**Validates:**
- Total loss thresholds (TLF vs TLT, percentages)
- Dispute timelines
- Sales tax requirements
- Diminished value eligibility (first-party vs third-party)
- Statute of limitations
- Fault systems (no-fault, at-fault, choice)

**Example Error Caught:**
> "Florida has a 75% threshold" ❌ (Actually 80%)

**API Endpoint:** `POST /validate/state-rules`

---

#### 3. SEO Scorer (Priority 3)
**Purpose:** 100-point SEO optimization score

**Checks (100 points total):**
- Title & Meta (25 pts): Keyword in title, title length 50-60 chars, meta description 140-160 chars
- Content Structure (30 pts): Keyword in first 100 words, keyword in 2+ H2s, proper heading hierarchy
- Links (20 pts): 3+ internal links, 2+ external authoritative links
- Quality Signals (15 pts): FAQ section with 3+ questions, CTA present, image alt text
- Word Count (10 pts): 1800-2200 words

**Minimum Passing Score:** 80/100

**API Endpoint:** `POST /validate/seo`

---

#### 4. Readability Analyzer (Priority 4)
**Purpose:** Ensure 8th-grade reading level

**Metrics:**
- Flesch Reading Ease (target: 60+)
- Average sentence length (target: < 20 words)
- Complex vocabulary (target: < 8% of words)

**Suggests Replacements:**
- "automobile" → "car"
- "utilize" → "use"
- "approximately" → "about"

**API Endpoint:** `POST /validate/readability`

---

#### 5. Link Checker (Priority 5)
**Purpose:** Validate all links before publishing

**Checks:**
- Internal links point to published posts (not drafts)
- External links return HTTP 200
- No timeout or connection errors

**API Endpoint:** `POST /validate/links`

---

#### 6. Math Validator (Priority 6)
**Purpose:** Catch arithmetic errors

**Validates:**
- Percentage calculations ("8% of $15,000 is...")
- Addition/subtraction
- Line item range consistency
- Ranges match PRODUCT_CONTEXT.md

**Example Error Caught:**
> "8% of $20,000 is $1,800" ❌ (Actually $1,600)

**API Endpoint:** `POST /validate/math`

---

### Combined Validation Endpoint

**`POST /validate/all`** — Runs all 6 validators and returns:

```json
{
  "overall_status": "PASS" | "FAIL",
  "state_validation": {...},
  "product_validation": {...},
  "seo_score": {...},
  "readability": {...},
  "links": {...},
  "math": {...},
  "summary": "✓ PASSED all checks. SEO: 85/100, Readability: 65/100",
  "revision_notes": []
}
```

**This is the primary endpoint Sage agent calls before approving articles.**

---

### Post-Publish Monitors (7-8)

#### 7. Search Console Monitor (Priority 7)
**Schedule:** Every Monday at 6am UTC

**Identifies:**
- **Almost Page One:** Articles ranking positions 4-10 with 50+ impressions → Queue for refresh
- **High Impressions, Low CTR:** Getting impressions but < 2% CTR → Rewrite title/meta
- **New Ranking Keywords:** Ranking for keywords we didn't target → Consider new article
- **Declining Articles:** Dropped 3+ positions week-over-week → Investigate

**Output:** Updates `articles` table with `refresh_priority` and GSC metrics

---

#### 8. Content Health Checker (Priority 8)
**Schedule:** Every Monday at 8am UTC

**Checks:**
1. **Broken Links:** Crawls all published posts, checks HTTP status
2. **Cannibalization:** TF-IDF similarity > 70% between articles → Merge or differentiate
3. **Stale Content:** Articles > 90 days old + ranking 5-15 → Refresh

**Output:** Updates `broken_links`, `cannibalization_pairs`, and `articles.refresh_priority`

---

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
# Ghost CMS
export GHOST_URL="https://claimcoach.app/blog"
export GHOST_ADMIN_API_KEY="your-admin-api-key"
export GHOST_CONTENT_API_KEY="your-content-api-key"

# Google Search Console
export GSC_CREDENTIALS_JSON='{"type": "service_account", ...}'
export SITE_URL="https://claimcoach.app"

# Database (optional, defaults to ./data/claimcoach_content.db)
export DATABASE_PATH="./data/claimcoach_content.db"
```

### 3. Initialize Database

```bash
python -c "from content_quality.db import init_database; init_database()"
```

---

## Usage

### Run API Server (Pre-Publish Gates)

```bash
python api_server.py
```

API will be available at `http://localhost:8000`

API docs at `http://localhost:8000/docs`

### Run Weekly Monitors (Cron Jobs)

```bash
# Run scheduler (production)
python cron_runner.py

# Run all jobs immediately (testing)
python cron_runner.py --now
```

### Run Individual Monitors

```bash
# Search Console report
python -m content_quality.monitors.search_console_monitor

# Content health check
python -m content_quality.monitors.content_health_checker
```

---

## Testing

### Run Test Suite

```bash
pip install pytest
python -m pytest tests/test_validation.py -v
```

### Test Individual Validators

```python
from content_quality.validators.product_validator import validate_product_claims

article = "ClaimCoach analyzes your settlement in under 5 minutes."
result = validate_product_claims(article)

print(result["status"])  # "PASS" or "FAIL"
print(result["hard_violations"])  # List of violations
```

### Test API Endpoints

```bash
curl -X POST http://localhost:8000/validate/all \
  -H "Content-Type: application/json" \
  -d '{
    "article_markdown": "# California Total Loss...",
    "target_keyword": "total loss california",
    "meta_title": "California Total Loss Guide",
    "meta_description": "Learn about total loss settlements in California...",
    "slug": "california-total-loss-guide",
    "target_state": "California"
  }'
```

---

## Database Schema

### Articles Table
Primary content pipeline table (replaces Notion)

**Key Fields:**
- `status`: backlog | todo | in_progress | review | revision | ready_to_publish | done
- `validation_status`: PASS | FAIL
- `seo_score`: 0-100
- `readability_score`: Flesch-Kincaid reading ease
- `state_accuracy`: PASS | WARN | FAIL
- `product_compliance`: PASS | FAIL
- `refresh_priority`: HIGH | MEDIUM | LOW | NULL
- `cannibalization_flag`: Boolean

### GSC Snapshots Table
Historical Search Console data for week-over-week comparison

### Broken Links Table
Tracks broken links by article with first_detected date

### Cannibalization Pairs Table
Tracks similar articles (TF-IDF similarity > 70%)

### Agent Log Table
Activity log for debugging and oversight (Morgan agent)

---

## Railway Deployment

### 1. Create Railway Project

```bash
railway login
railway init
```

### 2. Configure Services

The `railway.toml` defines two services:
- **content-quality-api** — API server for pre-publish gates
- **content-monitor** — Cron jobs for weekly monitoring

### 3. Set Environment Variables in Railway Dashboard

Required:
- `GHOST_URL`
- `GHOST_ADMIN_API_KEY`
- `GHOST_CONTENT_API_KEY`
- `GSC_CREDENTIALS_JSON`
- `SITE_URL`

### 4. Deploy

```bash
railway up
```

### 5. Create Persistent Volume

Railway dashboard → Services → content-quality-api → Variables → Volumes:
- Mount path: `/app/data`
- Name: `claimcoach-content-db`

---

## Reference Files

### STATE_RULES.md
Contains verified state-specific insurance regulations:
- Total loss thresholds (TLF vs TLT, percentages)
- Sales tax rules
- Diminished value eligibility
- Statute of limitations
- Fault systems
- Key statutes

**Covers:** CA, TX, FL, NY, GA, NC, PA, IL, OH, MI

### PRODUCT_CONTEXT.md
Defines what ClaimCoach DOES and DOES NOT do:
- ✅ Analyzes settlements
- ✅ Identifies missing line items
- ❌ Does NOT negotiate
- ❌ Does NOT file disputes
- ❌ Does NOT have document upload

Also includes:
- Typical line item ranges (sales tax, title, registration, loss of use)
- Approved and prohibited language

---

## Integration with Content Pipeline

### Sage Agent Integration

Before approving an article for publication, Sage calls:

```python
POST /validate/all

# If overall_status == "PASS":
#     → Ezra publishes to Ghost
# If overall_status == "FAIL":
#     → Send revision_notes back to Quill
```

### Quill Agent Integration

Quill reads `revision_notes` from validation response and:
1. Makes required corrections
2. Re-submits for validation
3. Increments `revision_count` in database

### Morgan Agent Integration

Morgan (PM agent) monitors:
- Weekly reports in `agent_log` table
- Articles with `refresh_priority` set
- Broken links in `broken_links` table
- Cannibalization pairs in `cannibalization_pairs` table

---

## Performance

**Expected validation times:**
- Product Claim Validator: < 200ms
- State Regulation Validator: < 500ms
- SEO Scorer: < 300ms
- Readability Analyzer: < 200ms
- Link Checker: 2-10s (network dependent)
- Math Validator: < 300ms

**Total validation time (all 6):** 3-12 seconds

**Weekly monitor times:**
- Search Console Monitor: 1-2 minutes
- Content Health Checker: 5-10 minutes (depends on article count)

---

## Troubleshooting

### Database Locked Errors
SQLite uses WAL mode with 5s busy timeout. If still getting locks:
```python
# Increase timeout in db.py
conn.execute("PRAGMA busy_timeout=10000")  # 10 seconds
```

### GSC API Errors
Check credentials:
```bash
python -c "import json; print(json.loads('$GSC_CREDENTIALS_JSON')['client_email'])"
```

### Link Checker Timeouts
Increase timeout in config.py:
```python
LINK_CHECK_TIMEOUT = 15  # seconds
```

### Missing Dependencies
```bash
pip install --upgrade -r requirements.txt
```

---

## Monitoring & Alerts

### Morgan Agent Queries

```sql
-- Pipeline health
SELECT status, COUNT(*) FROM articles GROUP BY status;

-- Failed validations
SELECT title, seo_score, readability_score, validation_notes
FROM articles WHERE validation_status = 'FAIL';

-- Refresh priorities
SELECT title, refresh_priority, last_gsc_position, last_gsc_impressions
FROM articles WHERE refresh_priority IS NOT NULL
ORDER BY CASE refresh_priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END;

-- Broken links
SELECT a.title, bl.broken_url, bl.http_status
FROM broken_links bl JOIN articles a ON bl.article_id = a.id
WHERE bl.resolved = 0;

-- Cannibalization
SELECT a1.title, a2.title, cp.similarity_score
FROM cannibalization_pairs cp
JOIN articles a1 ON cp.article_1_id = a1.id
JOIN articles a2 ON cp.article_2_id = a2.id
WHERE cp.resolved = 0 ORDER BY cp.similarity_score DESC;
```

---

## Contributing

### Adding a New Validator

1. Create validator in `content_quality/validators/`
2. Add endpoint in `api_server.py`
3. Add to `/validate/all` combined check
4. Write tests in `tests/test_validation.py`
5. Update this README

### Adding New State Rules

Edit `reference/STATE_RULES.md` and update `STATE_FACTS` dict in `state_validator.py`

### Adding Product Features

Update `reference/PRODUCT_CONTEXT.md` when ClaimCoach capabilities change

---

## License

Proprietary - ClaimCoach Internal Use Only

---

## Support

For issues or questions:
- Check logs: `railway logs -s content-quality-api`
- Run health check: `curl http://your-app.railway.app/health`
- Query agent_log table for debugging
