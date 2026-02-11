# 📱 Deploy ClaimCoach Pipeline from Your Phone

Complete guide to deploying the entire ClaimCoach content automation workflow from a mobile device.

## What Gets Deployed

✅ **All Agents:**
- Scout (keyword research)
- Quill (content writing)
- Sage (quality review)
- Ezra (HTML publishing)
- Herald (social media)
- Lurker (Reddit monitoring)
- Morgan (pipeline management)
- Atlas (performance analytics)
- Rival (competitor intelligence)
- Remix (content repurposing)

✅ **Dashboard:** Web UI for reviewing articles

✅ **API Server:** RESTful API for integrations

✅ **Cron Worker:** Automated scheduling of all agents

✅ **Database:** SQLite with all tables

---

## 🚀 Option 1: Railway (Recommended - Easiest from Phone)

### Why Railway?
- Free tier: $5/month credit (covers small usage)
- No credit card for trial
- One-click GitHub deploy
- Automatic HTTPS
- Background workers supported

### Steps:

1. **Go to Railway** (on your phone browser):
   ```
   https://railway.app
   ```

2. **Sign in** with GitHub

3. **Create New Project:**
   - Tap "New Project"
   - Select "Deploy from GitHub repo"
   - Choose "nitantbhartia/Marketingai"
   - Select branch: `claude/claimcoach-content-quality-DgZxV`

4. **Railway auto-detects** Procfile and deploys 3 services:
   - `web` (Dashboard)
   - `api` (API Server)
   - `worker` (Cron scheduler)

5. **Add Environment Variables:**

   Tap on the `web` service → "Variables" → Add these:

   ```bash
   # Required
   ANTHROPIC_API_KEY=sk-ant-...

   # Database
   DATABASE_PATH=/app/data/pipeline.db

   # Dashboard
   DASHBOARD_URL=https://your-app.railway.app

   # Optional (add if you have them)
   GOOGLE_SEARCH_CONSOLE_CREDENTIALS=...
   AHREFS_API_KEY=...
   COPYSCAPE_API_KEY=...
   REDDIT_CLIENT_ID=...
   TWITTER_API_KEY=...
   ```

6. **Add same variables** to `api` and `worker` services

7. **Setup Persistent Storage:**
   - Tap service → "Settings" → "Volumes"
   - Add volume: `/app/data`
   - This persists your database across deploys

8. **Get Your URLs:**
   - Dashboard: `https://your-project.up.railway.app`
   - API: Will be shown in deployment logs

9. **Initialize Database:**
   - Tap `worker` service → "Deployments" → Latest deployment
   - Scroll to logs
   - Database auto-creates on first run

✅ **Done! Your pipeline is running 24/7**

---

## 🌐 Option 2: Render (Free Tier Available)

### Why Render?
- Generous free tier
- Native cron job support
- Easy from phone
- Auto-deploys from GitHub

### Steps:

1. **Go to Render** (phone browser):
   ```
   https://render.com
   ```

2. **Sign in** with GitHub

3. **Create Blueprint** (Deploy all services at once):

   - Tap "New" → "Blueprint"
   - Connect GitHub repo: `nitantbhartia/Marketingai`
   - Branch: `claude/claimcoach-content-quality-DgZxV`

4. **Render auto-detects** and creates:
   - Web Service (Dashboard)
   - Background Worker (Cron)
   - Disk storage (Database)

5. **Add Environment Variables** (in each service):

   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   DATABASE_PATH=/data/pipeline.db
   DASHBOARD_URL=https://your-app.onrender.com
   ```

6. **Wait for Deploy** (~5 minutes)

7. **Access Dashboard:**
   ```
   https://your-app-name.onrender.com
   ```

✅ **Pipeline running!**

**Note:** Free tier spins down after 15 min of inactivity. Upgrade to paid ($7/mo) for always-on.

---

## 💻 Option 3: GitHub Codespaces (Best for Testing)

### Why Codespaces?
- Full VS Code in browser
- 60 hours/month free
- No deployment needed
- Perfect for testing from phone

### Steps:

1. **On phone, go to GitHub:**
   ```
   https://github.com/nitantbhartia/Marketingai
   ```

2. **Tap Code button** → "Codespaces" → "Create codespace on claude/claimcoach-content-quality-DgZxV"

3. **Wait for environment** to load (1-2 mins)

4. **In terminal** (bottom of screen), run:

   ```bash
   # Install dependencies
   pip install -r requirements.txt

   # Create config
   cp config.example.yaml config.yaml
   nano config.yaml  # Add your API keys

   # Initialize database
   python -c "from content_quality.db import init_db; init_db()"

   # Start services (in separate terminals)
   # Terminal 1:
   python dashboard_server.py

   # Terminal 2:
   python cron_runner.py
   ```

5. **Access Dashboard:**
   - Codespaces will show "Open in Browser" popup
   - Or go to "Ports" tab → Click port 8000 URL

✅ **Running locally in cloud!**

**Limitations:**
- Stops when you close browser
- 60 hours/month limit (plenty for testing)

---

## ⚡ Option 4: DigitalOcean App Platform

### Why DigitalOcean?
- $200 free credit for 60 days
- Professional deployment
- Always-on workers
- Great for production

### Steps:

1. **Go to DigitalOcean** (phone browser):
   ```
   https://www.digitalocean.com/products/app-platform
   ```

2. **Sign up** (will need credit card, but $200 free credit)

3. **Create App:**
   - Tap "Create App"
   - Source: GitHub → `nitantbhartia/Marketingai`
   - Branch: `claude/claimcoach-content-quality-DgZxV`

4. **Configure Components:**
   - Web Service: `python dashboard_server.py`
   - Worker: `python cron_runner.py`

5. **Add Environment Variables**

6. **Add Managed Database** (optional):
   - Or use volume for SQLite

7. **Deploy**

✅ **Enterprise-grade hosting!**

---

## 🔧 Configuration (All Platforms)

### Required Environment Variables

```bash
# Anthropic API (REQUIRED)
ANTHROPIC_API_KEY=sk-ant-api-xxxxx

# Database
DATABASE_PATH=/app/data/pipeline.db  # Or /data/pipeline.db on Render

# Dashboard URL (for webhook notifications)
DASHBOARD_URL=https://your-app-url.com

# Blog Output
BLOG_OUTPUT_DIR=/app/data/blog
SITE_URL=https://claimcoach.app
```

### Optional Environment Variables

```bash
# Google Search Console (for Atlas agent)
GOOGLE_SEARCH_CONSOLE_CREDENTIALS=<json-credentials>

# Keyword Research (choose one)
AHREFS_API_KEY=xxx
SEMRUSH_API_KEY=xxx

# Plagiarism Check
COPYSCAPE_API_KEY=xxx
COPYSCAPE_USERNAME=xxx

# Social Media (optional)
REDDIT_CLIENT_ID=xxx
REDDIT_CLIENT_SECRET=xxx
REDDIT_USERNAME=xxx
REDDIT_PASSWORD=xxx

TWITTER_API_KEY=xxx
TWITTER_API_SECRET=xxx
TWITTER_ACCESS_TOKEN=xxx
TWITTER_ACCESS_TOKEN_SECRET=xxx

# Image Generation (optional)
OPENAI_API_KEY=sk-xxxxx
```

---

## 📊 Verify Deployment

### 1. Check Dashboard

Visit your deployed URL:
```
https://your-app-url.com
```

Should see: "ClaimCoach Review Dashboard"

### 2. Check Health Endpoint

```
https://your-app-url.com/health
```

Should return:
```json
{
  "status": "healthy",
  "service": "dashboard",
  "timestamp": "2026-02-11T..."
}
```

### 3. Check Database

From deployment logs, you should see:
```
✓ Database initialized
✓ Tables created: articles, topics, briefs, metrics...
```

### 4. Check Cron Worker

Worker logs should show:
```
Cron scheduler started
Next run: scout at 2026-02-11 08:00:00
Next run: quill at 2026-02-11 10:00:00
...
```

### 5. Manual Test

**Trigger Scout agent:**

From phone browser, use the API:
```
POST https://your-api-url.com/run-agent
Content-Type: application/json

{
  "agent": "scout"
}
```

Or from Codespaces terminal:
```bash
curl -X POST http://localhost:8000/run-agent \
  -H "Content-Type: application/json" \
  -d '{"agent": "scout"}'
```

Check response:
```json
{
  "status": "success",
  "agent": "scout",
  "result": {
    "topics_created": 5,
    "briefs_created": 3
  }
}
```

---

## 📱 Managing from Phone

### View Logs

**Railway:**
- Tap service → "Deployments" → Latest → Scroll logs

**Render:**
- Tap service → "Logs" tab

**Codespaces:**
- Terminal output visible directly

### Restart Services

**Railway:**
- Tap service → Settings → "Restart"

**Render:**
- Tap service → "Manual Deploy" → "Deploy latest commit"

### Update Code

1. **Make changes** (via Codespaces or other IDE)
2. **Commit & push** to GitHub
3. **Auto-deploys** on Railway/Render
4. **Or manual deploy** button

### Monitor Pipeline

**Dashboard:**
```
https://your-url.com
```

Shows:
- Articles in review
- Quality scores
- Recent notifications
- Pipeline status

**API Endpoints:**
```
GET /health - Health check
GET /metrics - Pipeline metrics
POST /run-agent - Trigger agent manually
```

---

## 🐛 Troubleshooting

### Dashboard Won't Load

**Check:**
1. Deployment logs for errors
2. Environment variables set correctly
3. Database initialized (check logs)
4. Port binding correct (uses $PORT automatically)

**Fix:**
- Redeploy from dashboard
- Check that `DATABASE_PATH` directory exists
- Verify no errors in startup logs

### Agents Not Running

**Check:**
1. Worker service is running (not just web)
2. `ANTHROPIC_API_KEY` is set
3. Cron scheduler logs

**Fix:**
- Restart worker service
- Check API key validity
- Verify config.yaml or env vars

### Database Errors

**Check:**
1. `DATABASE_PATH` writable
2. Volume/disk attached
3. Initialization logs

**Fix:**
- Add persistent volume
- Re-run initialization
- Check file permissions

### Out of Anthropic Credits

**Symptoms:**
- Quill/Sage failing
- 401 errors in logs

**Fix:**
- Add credits to Anthropic account
- Check usage at https://console.anthropic.com

### High Costs

**Optimize:**
1. Reduce article frequency in cron schedule
2. Use more Haiku, less Sonnet
3. Disable optional agents (Remix, Rival)
4. Increase `min_backlog_topics` to batch better

---

## 💰 Cost Estimates

### Hosting (Monthly)

**Railway:**
- Free tier: $5 credit/month
- Paid: ~$10-20/month for 3 services

**Render:**
- Free tier: Good for testing (sleeps after 15 min)
- Paid: $7/month per service ($21 total for 3)

**DigitalOcean:**
- ~$12/month for app platform
- +$7/month for managed database (optional)

**Codespaces:**
- Free: 60 hours/month
- Paid: $0.18/hour after that

### API Costs (Monthly)

**Anthropic Claude:**
- Haiku: $1 per 1M input tokens
- ~$0.10-0.25 per article
- 120 articles/month = $12-30/month

**Optional APIs:**
- Ahrefs: $99/month (or skip, use manual keywords)
- Copyscape: ~$0.03 per check = $3.60/month for 120 articles
- GSC: Free
- OpenAI (images): ~$0.02 per image = $2.40/month

**Total:** ~$15-60/month depending on volume

---

## 🎯 Quick Start (1-2-3)

**From your phone RIGHT NOW:**

1. **Go to Railway:** https://railway.app
2. **New Project** → Deploy from GitHub → Select your repo
3. **Add env var:** `ANTHROPIC_API_KEY=sk-ant-...`

**That's it!** Pipeline running in 5 minutes.

---

## 📞 Support

**Issues?**
- Check deployment logs first
- Review DASHBOARD.md for dashboard-specific help
- Review NEW_FEATURES.md for agent documentation
- Check API server logs
- GitHub Issues: https://github.com/nitantbhartia/Marketingai/issues

**Questions from phone?**
- Railway/Render support chat (in app)
- Anthropic Discord: https://discord.gg/anthropic
- Or wait until you're at laptop!

---

## ✅ You're Ready!

Choose your platform above and get your automated content factory running from your phone in minutes.

**Recommended path:**
1. Start with **Railway** (easiest, free tier)
2. Test with **Codespaces** if you want to tweak things
3. Move to **Render/DigitalOcean** for production

Happy automating! 🚀📱
