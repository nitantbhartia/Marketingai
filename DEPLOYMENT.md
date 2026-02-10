# Railway Deployment Guide

## Overview

The ClaimCoach Content Quality system can be deployed to Railway in two ways:

### Option A: Single Service + Railway Cron (Recommended)
- Deploy API server as a web service
- Use Railway's native cron feature for weekly monitors
- **Pros**: More efficient, only runs monitors when needed
- **Cons**: Requires setting up two separate deployments

### Option B: Two Services (Simpler)
- Deploy API server as web service
- Deploy cron runner as worker service
- **Pros**: Easier setup, everything in one project
- **Cons**: Worker runs continuously (uses more resources)

---

## Option A: Single Service + Railway Cron (Recommended)

### Step 1: Deploy API Server

1. **Create Railway Project**
   ```bash
   # Install Railway CLI
   npm install -g @railway/cli

   # Login
   railway login

   # Create new project
   railway init
   ```

2. **Link to GitHub**
   - Go to Railway dashboard
   - New Project → Deploy from GitHub repo
   - Select your repository
   - Select branch: `claude/claimcoach-content-quality-DgZxV`

3. **Set Environment Variables**

   In Railway dashboard → Variables:
   ```bash
   # Required
   BLOG_OUTPUT_DIR=/data/blog
   SITE_URL=https://claimcoach.app

   # Google Search Console (JSON as single-line string)
   GSC_CREDENTIALS_JSON={"type":"service_account","project_id":"..."}

   # Database
   DATABASE_PATH=/data/claimcoach_content.db

   # API Config (optional - has defaults)
   API_HOST=0.0.0.0
   API_PORT=8000
   MIN_SEO_SCORE=80
   MIN_READABILITY_SCORE=60
   ```

4. **Add Persistent Volume**

   Railway dashboard → Your Service → Settings → Volumes:
   - Click "New Volume"
   - Mount Path: `/data`
   - Size: 1 GB (more than enough for SQLite)

5. **Deploy**
   ```bash
   railway up
   ```

   Or push to GitHub (if linked) and Railway will auto-deploy.

6. **Verify Deployment**
   ```bash
   # Get your Railway URL
   railway domain

   # Test health endpoint
   curl https://your-app.railway.app/health

   # View API docs
   open https://your-app.railway.app/docs
   ```

### Step 2: Set Up Weekly Cron Jobs

Railway now supports native cron jobs. Create two separate cron deployments:

#### Cron Job 1: Search Console Monitor

1. **Create New Service** in same Railway project
   - Service Name: "search-console-monitor"
   - Type: Cron Job

2. **Configure Cron**
   - Schedule: `0 6 * * 1` (Every Monday at 6am UTC)
   - Command: `python -m content_quality.monitors.search_console_monitor`

3. **Copy Environment Variables**
   - Use same variables as API service (Railway can share env vars)

4. **Deploy**

#### Cron Job 2: Content Health Checker

1. **Create New Service** in same Railway project
   - Service Name: "content-health-checker"
   - Type: Cron Job

2. **Configure Cron**
   - Schedule: `0 8 * * 1` (Every Monday at 8am UTC)
   - Command: `python -m content_quality.monitors.content_health_checker`

3. **Copy Environment Variables**
   - Use same variables as API service

4. **Deploy**

**Final Architecture:**
```
Railway Project: claimcoach-content-quality
├── Service 1: content-quality-api (web, always on)
├── Service 2: search-console-monitor (cron, Monday 6am UTC)
└── Service 3: content-health-checker (cron, Monday 8am UTC)
```

---

## Option B: Two Services (Simpler Setup)

### Step 1: Deploy from Railway Dashboard

1. **Create Railway Project**
   - Go to https://railway.app/
   - New Project → Deploy from GitHub repo
   - Select: `nitantbhartia/Marketingai`
   - Branch: `claude/claimcoach-content-quality-DgZxV`

2. **Railway will detect the Procfile and create 2 services:**
   - `web`: API server (port 8000)
   - `worker`: Cron runner (runs continuously)

### Step 2: Configure Environment Variables

Set these for BOTH services (or use shared variables):

```bash
GHOST_URL=https://claimcoach.app/blog
GHOST_ADMIN_API_KEY=your-admin-api-key
GHOST_CONTENT_API_KEY=your-content-api-key
SITE_URL=https://claimcoach.app
GSC_CREDENTIALS_JSON={"type":"service_account",...}
DATABASE_PATH=/data/claimcoach_content.db
```

### Step 3: Add Shared Volume

**Important**: Both services need access to the same SQLite database.

Railway dashboard → Settings → Volumes:
1. Create volume for web service: `/data`
2. Create volume for worker service: `/data`
3. Or use Railway's shared volumes feature (if available)

**Note**: If services can't share volumes, you may need to:
- Use Option A (cron jobs instead)
- Or use PostgreSQL instead of SQLite

### Step 4: Deploy

Railway will auto-deploy both services. Monitor logs:
```bash
railway logs -s web
railway logs -s worker
```

---

## Option C: Alternative - Use PostgreSQL Instead of SQLite

If sharing SQLite volumes between services is problematic:

1. **Add PostgreSQL to Railway Project**
   - Railway dashboard → New → Database → PostgreSQL
   - Railway will auto-provision and provide `DATABASE_URL`

2. **Update code to use PostgreSQL**
   ```python
   # content_quality/db.py - replace SQLite with PostgreSQL
   import psycopg2
   from psycopg2.extras import RealDictCursor

   DATABASE_URL = os.environ.get("DATABASE_URL")
   ```

3. **Migration**: Convert SQLite schema to PostgreSQL
   - Update `init_database()` in `db.py`
   - Change SQLite types to PostgreSQL types
   - Update `get_db()` context manager

**Trade-offs:**
- ✅ Better for multi-service deployments
- ✅ More scalable
- ❌ Costs ~$5/month on Railway
- ❌ Requires code changes

---

## Deploying Static Blog Files

The Ezra agent publishes articles as static files (markdown + HTML) to the `/data/blog` directory. You can deploy these files to any static hosting platform:

### Option 1: Netlify

1. **Install Netlify CLI**
   ```bash
   npm install -g netlify-cli
   ```

2. **Deploy blog directory**
   ```bash
   cd /data/blog
   netlify deploy --prod --dir=.
   ```

3. **Or set up continuous deployment**
   - Connect your GitHub repo to Netlify
   - Build command: (none - files are pre-generated)
   - Publish directory: `blog/`

### Option 2: Vercel

1. **Install Vercel CLI**
   ```bash
   npm install -g vercel
   ```

2. **Deploy blog directory**
   ```bash
   cd /data/blog
   vercel --prod
   ```

### Option 3: GitHub Pages

1. **Add to repository**
   ```bash
   git add blog/
   git commit -m "Add published blog posts"
   git push origin main
   ```

2. **Enable GitHub Pages**
   - Repository Settings → Pages
   - Source: Deploy from branch
   - Branch: main → /blog folder
   - Save

Your blog will be live at: `https://your-username.github.io/your-repo/`

---

## Getting Google Search Console Credentials

1. **Create Service Account**
   - Go to https://console.cloud.google.com/
   - Create new project or select existing
   - Enable "Google Search Console API"
   - Create Service Account
   - Download JSON credentials

2. **Add Service Account to Search Console**
   - Go to https://search.google.com/search-console
   - Select property (claimcoach.app)
   - Settings → Users and permissions
   - Add service account email as user (permission: Full)

3. **Format for Railway**
   - Copy entire JSON content
   - Minify to single line (remove newlines)
   - Set as `GSC_CREDENTIALS_JSON` environment variable

---

## Verifying Deployment

### 1. Test API Server

```bash
# Health check
curl https://your-app.railway.app/health

# Test validation
curl -X POST https://your-app.railway.app/validate/product-claims \
  -H "Content-Type: application/json" \
  -d '{"article_markdown": "ClaimCoach analyzes your settlement."}'
```

### 2. Check Database

```bash
# Railway CLI
railway run python -c "from content_quality.db import get_db; \
  with get_db() as db: \
    cursor = db.execute('SELECT COUNT(*) FROM articles'); \
    print(f'Articles: {cursor.fetchone()[0]}')"
```

### 3. Monitor Cron Jobs

Railway dashboard → Service → Deployments → Logs

Look for:
```
[Monday 6am UTC] Running Search Console Monitor
✓ Search Console report generated successfully

[Monday 8am UTC] Running Content Health Checker
✓ Content health check completed successfully
```

### 4. Test API Endpoints

Visit: `https://your-app.railway.app/docs`

Interactive API documentation with "Try it out" buttons.

---

## Troubleshooting

### Database Locked Errors

If SQLite shows "database is locked":
```python
# Increase timeout in content_quality/config.py
PRAGMA_BUSY_TIMEOUT = 10000  # 10 seconds
```

### Worker Service Using Too Much Memory

Option B's continuous worker may idle at ~100MB RAM. To reduce:
```python
# In cron_runner.py, increase sleep interval
time.sleep(300)  # Check every 5 minutes instead of 1
```

### GSC API Quota Exceeded

Free tier: 100 requests/day. Each report uses ~2-5 requests.
- Weekly runs: well within limits
- If exceeded: space out requests with `time.sleep(1)` between calls

### Blog Output Directory Not Writable

If Ezra fails to publish:
```bash
# Ensure blog directory exists and is writable
railway run mkdir -p /data/blog/posts /data/blog/html
railway run chmod 755 /data/blog

# Check permissions
railway run ls -la /data/blog
```

---

## Cost Estimate

### Railway Pricing (as of 2024)

**Option A (Recommended):**
- Web service: $5/month (Hobby plan, always on)
- 2 Cron jobs: $0 (included in Hobby plan)
- Volume: $0 (first 1GB free)
- **Total: ~$5/month**

**Option B (Two Services):**
- Web service: $5/month
- Worker service: $5/month (always running)
- Volume: $0
- **Total: ~$10/month**

**Option C (With PostgreSQL):**
- Web service: $5/month
- PostgreSQL: $5/month
- Cron jobs: $0
- **Total: ~$10/month**

**Recommendation**: Use Option A for best cost/performance ratio.

---

## Monitoring & Maintenance

### 1. Set Up Alerts

Railway dashboard → Service → Settings → Notifications:
- Email alerts for deployment failures
- Memory usage alerts (if >80%)
- HTTP error rate alerts

### 2. Monitor Logs

```bash
# Real-time logs
railway logs --tail

# Service-specific logs
railway logs -s content-quality-api

# Cron job logs
railway logs -s search-console-monitor
```

### 3. Database Maintenance

Monthly:
```bash
railway run python -c "
from content_quality.db import get_db
with get_db() as db:
    db.execute('VACUUM')  # Compact database
    db.execute('ANALYZE')  # Update query planner stats
"
```

### 4. Check Agent Activity

```bash
railway run python -c "
from content_quality.db import get_db
with get_db() as db:
    cursor = db.execute('''
        SELECT agent_name, action, COUNT(*) as count
        FROM agent_log
        WHERE created_at > datetime('now', '-7 days')
        GROUP BY agent_name, action
    ''')
    for row in cursor.fetchall():
        print(f'{row[0]}: {row[1]} ({row[2]}x)')
"
```

---

## Security Best Practices

1. **API Keys**: Never commit to git (already in .gitignore)
2. **Database**: Not publicly accessible (internal Railway network only)
3. **CORS**: Currently allows all origins - restrict in production:
   ```python
   # api_server.py
   allow_origins=["https://claimcoach.app", "https://admin.claimcoach.app"]
   ```
4. **Rate Limiting**: Add rate limits to API:
   ```bash
   pip install slowapi
   ```

---

## Next Steps After Deployment

1. **Integrate with Sage Agent**
   - Update Sage to call: `https://your-app.railway.app/validate/all`
   - Parse `revision_notes` and send back to Quill

2. **Populate Initial Articles**
   ```bash
   railway run python -c "
   from content_quality.db import get_db
   with get_db() as db:
       db.execute('''
           INSERT INTO articles (title, slug, status, target_keyword)
           VALUES ('Test Article', 'test-article', 'backlog', 'test keyword')
       ''')
   "
   ```

3. **Test End-to-End**
   - Draft article in SQLite
   - Call `/validate/all`
   - Review `revision_notes`
   - Publish to static files (via Ezra agent)
   - Deploy blog directory to Netlify/Vercel/GitHub Pages

4. **Monitor First Week**
   - Check cron jobs run on Monday
   - Verify GSC data appears in database
   - Review broken links report
   - Check cannibalization detection

---

## Support

- Railway Docs: https://docs.railway.app/
- Railway Discord: https://discord.gg/railway
- Netlify Docs: https://docs.netlify.com/
- Vercel Docs: https://vercel.com/docs
- GitHub Pages Docs: https://docs.github.com/pages
- GSC API Docs: https://developers.google.com/webmaster-tools/

---

## Summary

✅ **Recommended Deployment Path: Option A**

1. Deploy API as web service on Railway
2. Set up 2 cron jobs for weekly monitors
3. Add persistent volume for SQLite
4. Set environment variables
5. Test with `/health` and `/docs` endpoints
6. Monitor logs for first week

**Total setup time**: 15-20 minutes
**Monthly cost**: ~$5
