# Getting Started with ClaimCoach Content Engine

This guide will help you set up and run the complete ClaimCoach Content Engine.

## Prerequisites

- Python 3.10+
- Ghost CMS instance (for publishing)
- Anthropic API key
- Google Search Console service account (for monitoring)

## Quick Setup (5 minutes)

### Step 1: Clone and Navigate

```bash
git clone <your-repo>
cd Marketingai
git checkout claude/complete-content-engine-DgZxV
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment

Create `.env` file in project root:

```bash
cat > .env << 'EOF'
# Anthropic API (required for agents)
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Ghost CMS (required for publishing)
GHOST_URL=https://claimcoach.app/blog
GHOST_ADMIN_API_KEY=your-ghost-admin-key
GHOST_CONTENT_API_KEY=your-ghost-content-key

# Google Search Console (required for weekly monitoring)
GSC_CREDENTIALS_JSON={"type":"service_account","project_id":"..."}
SITE_URL=https://claimcoach.app

# Database (optional - defaults shown)
DATABASE_PATH=./data/claimcoach_content.db

# Quality thresholds (optional - defaults shown)
MIN_SEO_SCORE=80
MIN_READABILITY_SCORE=60
MIN_WORD_COUNT=1800
MAX_WORD_COUNT=2200
EOF
```

### Step 4: Initialize Database

```bash
python -c "from content_quality.db import init_database; init_database()"
```

You should see: `Database initialized successfully`

### Step 5: Verify Setup

```bash
# Test validators
python -m pytest tests/test_validation.py -v

# Test quality API
python api_server.py &
sleep 2
curl http://localhost:8000/health
pkill -f api_server

# Test agent CLI
python -m pipeline.cli status
```

## Running the System

### Option A: Run Individual Agents (Testing/Development)

```bash
# Run Scout to discover topics
python -m pipeline.cli scout

# Run Quill to write an article
python -m pipeline.cli quill

# Run Sage to review articles
python -m pipeline.cli sage

# Run Ezra to publish
python -m pipeline.cli ezra

# Check pipeline status
python -m pipeline.cli status
```

### Option B: Run Scheduler (Production)

Runs all agents automatically on schedule:

```bash
python -m pipeline.scheduler
```

**Agent Schedule:**
- Scout: Every 8 hours
- Quill: Every 2 hours
- Sage: 3x/day (6am, 2pm, 10pm)
- Ezra: Every 4 hours
- Herald: 2x/day (10am, 6pm)
- Lurker: Every 8 hours
- Morgan: 3x/day (5am, 1pm, 9pm)

Press Ctrl+C to stop.

### Option C: Run Quality API Server (Standalone)

If you want to run just the validation API:

```bash
python api_server.py
```

Visit http://localhost:8000/docs for interactive API documentation.

### Option D: Run Weekly Monitors (Standalone)

```bash
# Run immediately (testing)
python cron_runner.py --now

# Run on schedule (production)
python cron_runner.py
```

**Monitor Schedule:**
- Search Console Monitor: Monday 6am UTC
- Content Health Checker: Monday 8am UTC

## Your First Article

Let's create your first article end-to-end:

### 1. Add a Topic to the Backlog

```bash
python -c "
from content_quality.db import get_db
with get_db() as db:
    db.execute('''
        INSERT INTO articles (title, slug, target_keyword, target_state, status)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        'How to Check Your Total Loss Settlement in California',
        'california-total-loss-settlement',
        'total loss settlement california',
        'California',
        'todo'
    ))
    print('✓ Topic added to database')
"
```

### 2. Run Quill to Write the Article

```bash
python -m pipeline.cli quill
```

Quill will:
- Claim the article
- Load reference docs (PRODUCT_CONTEXT.md, STATE_RULES.md)
- Call Claude API to write
- Save draft with status='review'

### 3. Run Sage to Review

```bash
# Start quality API server first (in another terminal)
python api_server.py &

# Run Sage
python -m pipeline.cli sage
```

Sage will:
- Call POST /validate/all
- Check all 6 validators
- Either approve (status='ready_to_publish') or request revision (status='revision')

### 4. If Revision Needed, Quill Fixes It

```bash
python -m pipeline.cli quill
```

Quill will pick up the revision, fix issues, and resubmit.

### 5. Run Ezra to Publish

```bash
python -m pipeline.cli ezra
```

Ezra will:
- Publish to Ghost CMS
- Submit to Google Search Console
- Update database (status='done')

### 6. Check Your Published Article

```bash
python -c "
from content_quality.db import get_db
with get_db() as db:
    cursor = db.execute('''
        SELECT title, published_url, seo_score, readability_score
        FROM articles WHERE status = 'done'
    ''')
    for row in cursor.fetchall():
        print(f'✓ {row[0]}')
        print(f'  URL: {row[1]}')
        print(f'  SEO: {row[2]}/100, Readability: {row[3]:.1f}/100')
"
```

## Monitoring and Debugging

### Check Pipeline Status

```bash
python -m pipeline.cli status
```

### Query Database

```bash
# Articles by status
python -c "
from content_quality.db import get_db
with get_db() as db:
    cursor = db.execute('SELECT status, COUNT(*) FROM articles GROUP BY status')
    for row in cursor.fetchall():
        print(f'{row[0]}: {row[1]}')
"

# Recent agent activity
python -c "
from content_quality.db import get_db
with get_db() as db:
    cursor = db.execute('''
        SELECT agent_name, action, COUNT(*) as count
        FROM agent_log
        WHERE created_at > datetime('now', '-24 hours')
        GROUP BY agent_name, action
    ''')
    for row in cursor.fetchall():
        print(f'{row[0]}: {row[1]} ({row[2]}x)')
"

# Failed validations
python -c "
from content_quality.db import get_db
with get_db() as db:
    cursor = db.execute('''
        SELECT title, validation_notes
        FROM articles WHERE validation_status = 'FAIL'
    ''')
    for row in cursor.fetchall():
        print(f'✗ {row[0]}')
        print(f'  Notes: {row[1]}')
"
```

### View Logs

```bash
# Agent logs (if running scheduler)
tail -f logs/agent.log

# Quality API logs
tail -f logs/api.log
```

### Test Individual Components

```bash
# Test validators
python -m pytest tests/test_validation.py::TestProductValidator -v
python -m pytest tests/test_validation.py::TestStateValidator -v
python -m pytest tests/test_validation.py::TestSEOScorer -v

# Test agent integration
python agents/integration_examples/quill_integration.py --test
python agents/integration_examples/sage_integration.py --test
```

## Configuration

### Agent Configuration

Edit `config.yaml` (copy from `config.example.yaml`):

```yaml
database:
  path: ./data/claimcoach_content.db

anthropic:
  api_key: ${ANTHROPIC_API_KEY}
  default_model: claude-haiku-4.5-20251001

agents:
  scout:
    enabled: true
    schedule: "0 */8 * * *"  # Every 8 hours
    model: claude-haiku-4.5-20251001

  quill:
    enabled: true
    schedule: "0 */2 * * *"  # Every 2 hours
    model: claude-haiku-4.5-20251001
    max_articles_per_run: 1

  sage:
    enabled: true
    schedule: "0 6,14,22 * * *"  # 3x/day
    model: claude-sonnet-4.5-20250929
    validation_api_url: http://localhost:8000

  ezra:
    enabled: true
    schedule: "0 */4 * * *"  # Every 4 hours
    max_publishes_per_run: 2

  herald:
    enabled: true
    schedule: "0 10,18 * * *"  # 2x/day

  lurker:
    enabled: true
    schedule: "0 */8 * * *"  # Every 8 hours

  morgan:
    enabled: true
    schedule: "0 5,13,21 * * *"  # 3x/day
```

### Quality Thresholds

Edit `.env` or set environment variables:

```bash
# SEO
MIN_SEO_SCORE=80              # Minimum score to pass (0-100)

# Readability
MIN_READABILITY_SCORE=60      # Flesch Reading Ease minimum

# Word count
MIN_WORD_COUNT=1800
MAX_WORD_COUNT=2200

# Content requirements
MIN_FAQ_QUESTIONS=3           # FAQ section
MIN_SPECIFICITY_ITEMS=5       # Dollar amounts, statutes

# Meta
META_DESC_MIN_LENGTH=140
META_DESC_MAX_LENGTH=160
```

## Common Issues

### Issue: "Database is locked"

**Solution:**
```python
# Increase timeout in content_quality/config.py
PRAGMA_BUSY_TIMEOUT = 10000  # 10 seconds instead of 5
```

### Issue: "Anthropic API key not found"

**Solution:**
```bash
# Make sure .env file exists and is loaded
export ANTHROPIC_API_KEY=your-key-here

# Or add to ~/.bashrc
echo 'export ANTHROPIC_API_KEY=your-key' >> ~/.bashrc
source ~/.bashrc
```

### Issue: "Validation API connection refused"

**Solution:**
```bash
# Start API server first
python api_server.py &

# Then run Sage
python -m pipeline.cli sage
```

### Issue: "Ghost API authentication failed"

**Solution:**
```bash
# Verify Ghost keys are correct
curl -H "Authorization: Ghost YOUR_ADMIN_KEY" \
  https://claimcoach.app/blog/ghost/api/admin/posts/

# Check key format (should be hex:hex)
echo $GHOST_ADMIN_API_KEY | grep -E '^[a-f0-9]{24}:[a-f0-9]{64}$'
```

### Issue: "Agent claims article but doesn't process"

**Solution:**
```python
# Clear stale claims (articles claimed but not processed in 1+ hours)
python -c "
from content_quality.db import get_db
with get_db() as db:
    db.execute('''
        UPDATE articles
        SET writer_claim = NULL, editor_claim = NULL, publisher_claim = NULL
        WHERE updated_at < datetime('now', '-1 hour')
        AND status IN ('in_progress', 'review', 'ready_to_publish')
    ''')
    print('✓ Cleared stale claims')
"
```

## Next Steps

1. **Deploy to Railway** — See [DEPLOYMENT.md](./DEPLOYMENT.md)
2. **Customize Reference Docs** — Edit `reference/PRODUCT_CONTEXT.md` and `reference/STATE_RULES.md`
3. **Configure Agents** — Edit `config.yaml` to adjust schedules and models
4. **Set Up Monitoring** — Connect Morgan to Telegram for alerts
5. **Scale Up** — Adjust agent schedules for higher/lower volume

## Resource Links

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — Complete system architecture
- **[INTEGRATION.md](./INTEGRATION.md)** — How agents integrate
- **[DEPLOYMENT.md](./DEPLOYMENT.md)** — Railway deployment guide
- **[README.md](./README.md)** — Overview and quick reference

## Support

For issues:
1. Check `agent_log` table in database
2. Review validation notes for failed articles
3. Test individual components (validators, agents)
4. Check environment variables are set correctly

---

**Happy Content Automating! 🚀**
