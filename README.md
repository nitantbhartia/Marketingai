# ClaimCoach Content Engine — Complete System

Autonomous AI content marketing pipeline with automated quality validation and SEO monitoring for ClaimCoach.

> **📖 Complete Documentation:**
> - **[ARCHITECTURE.md](./ARCHITECTURE.md)** - Complete system architecture and data flow
> - **[INTEGRATION.md](./INTEGRATION.md)** - How agents integrate with quality system
> - **[DEPLOYMENT.md](./DEPLOYMENT.md)** - Railway deployment guide

## System Overview

This repository contains the **complete ClaimCoach Content Engine**, combining:

1. **Agent Pipeline** — 7 autonomous agents that research, write, review, publish, and promote content
2. **Quality Validation** — 6 pre-publish validators ensuring accuracy and SEO
3. **Content Monitoring** — 2 post-publish monitors for performance tracking

```
┌─────────────────────────────────────────────┐
│  REFERENCE DOCS                              │
│  - PRODUCT_CONTEXT.md (what ClaimCoach does) │
│  - STATE_RULES.md (insurance regulations)    │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│  AGENT PIPELINE (7 agents)                   │
│  Scout → Quill → Sage → Ezra → Herald        │
│                           ↓                  │
│              Lurker ← Morgan                 │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│  QUALITY SYSTEM (8 validators + monitors)    │
│  - 6 Pre-publish validators                 │
│  - 2 Post-publish monitors                  │
│  - FastAPI validation server                │
└─────────────────────────────────────────────┘
```

## Components

### Agent Pipeline (`pipeline/`)

**7 Autonomous Agents:**

| Agent | Role | Schedule |
|-------|------|----------|
| **Scout** | Keyword research & topic discovery | Every 8 hours |
| **Quill** | Article writer using Claude API | Every 2 hours |
| **Sage** | Quality reviewer (calls validation API) | 3x/day |
| **Ezra** | Publisher (static files) | Every 4 hours |
| **Herald** | Social media promoter | 2x/day |
| **Lurker** | Reddit/forum opportunity scanner | Every 8 hours |
| **Morgan** | PM/orchestrator | 3x/day |

**Usage:**
```bash
# CLI interface
python -m pipeline.cli scout  # Run Scout
python -m pipeline.cli quill  # Run Quill
python -m pipeline.cli status # Check pipeline status

# Or use scheduler
python -m pipeline.scheduler  # Runs all agents on schedule
```

### Quality System (`content_quality/`)

**6 Pre-Publish Validators:**
1. **Product Claim Validator** — Prevents feature hallucinations
2. **State Regulation Validator** — Cross-references STATE_RULES.md
3. **SEO Scorer** — 100-point optimization scoring
4. **Readability Analyzer** — Ensures 8th-grade reading level
5. **Link Checker** — Validates all internal/external links
6. **Math Validator** — Catches arithmetic errors

**2 Post-Publish Monitors:**
7. **Search Console Monitor** — Weekly GSC analysis (Monday 6am)
8. **Content Health Checker** — Broken links, cannibalization (Monday 8am)

**API Server:**
```bash
python api_server.py
# Available at http://localhost:8000/docs
```

**Primary Endpoint:** `POST /validate/all`

### Reference Documents (`reference/`)

- **PRODUCT_CONTEXT.md** — What ClaimCoach does/doesn't do (loaded by agents)
- **STATE_RULES.md** — Insurance regulations for 10 states (loaded by agents)

These are loaded by Quill at article creation and used by validators.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
# Anthropic API
export ANTHROPIC_API_KEY="your-key"

# Blog publishing (static files)
export BLOG_OUTPUT_DIR="./blog"
export SITE_URL="https://claimcoach.app"

# Google Search Console
export GSC_CREDENTIALS_JSON='{"type": "service_account", ...}'

# Database
export DATABASE_PATH="./data/claimcoach_content.db"
```

### 3. Initialize Database

```bash
python -c "from content_quality.db import init_database; init_database()"
```

### 4. Run the System

**Option A: Run agents via CLI**
```bash
# Run individual agents
python -m pipeline.cli scout    # Discovers topics
python -m pipeline.cli quill    # Writes articles
python -m pipeline.cli sage     # Reviews quality
python -m pipeline.cli ezra     # Publishes to static files

# Check status
python -m pipeline.cli status
```

**Option B: Run scheduler (all agents on schedule)**
```bash
python -m pipeline.scheduler
```

**Option C: Run quality API server**
```bash
python api_server.py
# API docs at http://localhost:8000/docs
```

**Option D: Run weekly monitors**
```bash
python cron_runner.py --now  # Run immediately (testing)
python cron_runner.py        # Run on schedule (production)
```

## Documentation

- **[README.md](./README.md)** — This file (quick start)
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — Complete system architecture
- **[INTEGRATION.md](./INTEGRATION.md)** — Agent integration guide
- **[DEPLOYMENT.md](./DEPLOYMENT.md)** — Railway deployment guide
- **[reference/PRODUCT_CONTEXT.md](./reference/PRODUCT_CONTEXT.md)** — Product capabilities reference
- **[reference/STATE_RULES.md](./reference/STATE_RULES.md)** — Insurance regulations reference

## Cost Estimate

### AI API Costs (Anthropic)

- Scout: ~$0.10/run (Haiku)
- Quill: ~$0.15/article (Haiku, 2000 words)
- Sage: Uses validation API (deterministic, no AI cost)
- **Total: ~$10-15/month** for 3-5 articles/week

### Infrastructure (Railway)

- API server: $5/month
- Cron jobs: $0 (included)
- PostgreSQL (optional): $5/month
- **Total: ~$5-10/month**

**Complete System: ~$15-25/month** for autonomous content engine

---

**For complete documentation, see [ARCHITECTURE.md](./ARCHITECTURE.md)**
