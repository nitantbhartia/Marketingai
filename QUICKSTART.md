# 🚀 QUICKSTART - Deploy in 5 Minutes from Your Phone

**You're on your phone. You want the whole ClaimCoach pipeline running NOW.**

Here's the fastest way:

---

## ⚡ FASTEST: Railway (2 clicks + API key)

### Step 1: Deploy

**On your phone browser, go to:**
```
https://railway.app/new/template/claimcoach
```

Or manually:
1. Go to https://railway.app
2. Sign in with GitHub
3. Click "New Project"
4. Choose "Deploy from GitHub repo"
5. Select: `nitantbhartia/Marketingai`
6. Branch: `claude/claimcoach-content-quality-DgZxV`
7. Click "Deploy Now"

### Step 2: Add API Key

1. Wait 30 seconds for deploy
2. Click on the `web` service
3. Go to "Variables" tab
4. Add: `ANTHROPIC_API_KEY` = `sk-ant-api-xxxxx` (your key)
5. Click "Add"
6. Service auto-restarts

### Step 3: Add to Other Services

1. Repeat step 2 for `api` and `worker` services
2. Add same `ANTHROPIC_API_KEY` to each

### Step 4: Done!

Click on `web` service → "Settings" → You'll see a URL like:
```
https://claimcoach-production-xxxx.up.railway.app
```

**Tap it. You'll see your dashboard!** 🎉

---

## What's Running?

✅ **Dashboard** (web service)
- Review and approve articles
- See all scores and metadata
- Publish with one click

✅ **API Server** (api service)
- RESTful API for integrations
- Webhook endpoints
- Manual agent triggers

✅ **Cron Worker** (worker service)
- Runs all 10 agents on schedule:
  - Scout: Every 8 hours (finds keywords)
  - Quill: Every 2 hours (writes articles)
  - Sage: 3x/day (reviews quality)
  - Ezra: Every 4 hours (publishes HTML)
  - Atlas: Weekly (analytics)
  - Rival: Daily (competitor research)
  - Remix: Every 6 hours (repurposes content)
  - Morgan: 3x/day (pipeline management)
  - Herald: 2x/day (social posts)
  - Lurker: Every 8 hours (Reddit monitoring)

✅ **Database** (SQLite)
- All articles, topics, briefs
- Performance metrics
- Content remixes
- CTA tracking

---

## First Time Using It?

### 1. Wait 10 Minutes

Let Scout and Quill run their first cycles.

### 2. Check Dashboard

Go to your Railway URL. You should see:
- Stats cards at top
- Recent notifications
- Articles (if any are ready)

### 3. Trigger Scout Manually (Optional)

From your phone, you can trigger agents manually via API:

**Using your browser:**
```
https://your-api-url.railway.app/run-agent?agent=scout
```

Just replace `your-api-url` with your actual API service URL from Railway.

### 4. Watch It Work!

Agents run automatically now. Check back in a few hours and you'll see:
- Scout creating topics
- Quill writing articles
- Sage reviewing them
- Articles appearing in dashboard for your approval
- Ezra publishing approved articles

---

## Cost Alert! 💰

**Railway Free Tier:**
- $5/month free credit
- Should cover ~2-3 weeks of light usage
- After that: ~$10-20/month

**Anthropic API:**
- ~$0.10-0.25 per article
- 120 articles/month = $12-30/month

**Total: ~$15-50/month**

To reduce costs:
- Edit cron schedules (less frequent)
- Disable optional agents (Remix, Rival, Lurker)
- Use only Haiku models (cheaper)

---

## Manage from Phone

### View Logs
1. Open Railway app on phone
2. Tap a service
3. Go to "Deployments" tab
4. Tap latest deployment
5. Scroll to see logs

### Restart a Service
1. Tap service
2. Go to "Settings"
3. Scroll down
4. Tap "Restart"

### Update Code
1. Make changes on GitHub (via browser or Codespaces)
2. Commit & push
3. Railway auto-deploys in ~2 minutes

### Add More Env Vars
1. Tap service
2. "Variables" tab
3. Add variable
4. Service auto-restarts

---

## Need More Help?

**Full deployment guide:** See `DEPLOY.md`

**Dashboard documentation:** See `DASHBOARD.md`

**Agent documentation:** See `NEW_FEATURES.md`

**Troubleshooting:**
- Check service logs in Railway
- Make sure API key is correct
- Verify database initialized (check logs for "✓ Database initialized")

---

## Can't Use Railway?

**Alternative 1: Render**
- Same process, different platform
- Free tier available (sleeps after 15 min inactivity)
- See DEPLOY.md for full instructions

**Alternative 2: GitHub Codespaces**
- Perfect for testing
- 60 hours/month free
- Full VS Code in browser
- See DEPLOY.md for instructions

**Alternative 3: Wait for Laptop**
- Run locally with full control
- No hosting costs
- Just run: `python dashboard_server.py`

---

## 🎯 TL;DR

1. **Railway.app** on phone
2. **Deploy from GitHub**
3. **Add** `ANTHROPIC_API_KEY` to all 3 services
4. **Open** your web service URL
5. **Done!** Pipeline running 24/7

**Questions?** Read DEPLOY.md for detailed explanations.

**Ready to build?** Your content factory is now running! 🚀
