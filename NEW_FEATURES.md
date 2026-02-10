# NextLevel Features - Implementation Complete ✅

Five powerful new features to take your content automation to the next level.

## Feature Summary

| Feature | Agent/Tool | Purpose | Impact |
|---------|-----------|---------|--------|
| **Performance Feedback Loop** | Atlas | Learn what works, optimize strategy | Agents improve themselves over time |
| **Internal Link Optimization** | Link Optimizer | Auto-suggest contextual links | Build topical authority automatically |
| **Competitor Intelligence** | Rival | Monitor competitors, find gaps | Dominate your content category |
| **Dynamic CTAs** | Ezra (enhanced) | Personalized conversion optimization | Turn traffic into leads/customers |
| **Content Remixing** | Remix | One article → multiple formats | 10x content output |

---

## 1. Atlas - Performance Analyst 📊

### What It Does

Analyzes your published content performance and generates actionable insights to improve future content.

**Creates a feedback loop:**
- Identifies top-performing articles
- Discovers patterns in what works
- Generates insights with confidence scores
- Automatically improves content strategy

### Usage

```bash
# Run performance analysis
python -m pipeline.cli run atlas

# Or scheduled (weekly recommended)
# Runs Monday 6am by default
```

### Insights Generated

1. **Word Count Patterns**: "Top articles average 2,400 words, 25% longer than lower-ranked articles"
2. **SEO Score Correlation**: "Articles in top 5 average 87/100 SEO score vs 72/100 for positions 6-20"
3. **State Performance**: "California content performs best with 145 avg clicks/article"
4. **Keyword Types**: "'total loss settlement' keywords rank best (avg position 3.2)"
5. **Content Structure**: "80% of top articles include FAQ sections"

### Configuration

```yaml
atlas:
  enabled: true
  min_articles_for_analysis: 10  # Need at least this many to generate insights
  min_confidence_score: 0.7       # Only save insights with 70%+ confidence
```

### Database Tables

- `performance_insights` - Stores discovered patterns and recommendations
- Tracks: insight_type, confidence_score, supporting_data, applied status

### Example Output

```json
{
  "insights_generated": 5,
  "patterns_discovered": 12,
  "recommendations": [
    "Top-ranking articles average 2,400 words. Consider targeting 2,400-word articles.",
    "California content performs best with 145 avg clicks/article. Prioritize California content.",
    "'total loss' keywords rank best (avg position 3.2). Focus on these keyword types."
  ]
}
```

---

## 2. Internal Link Optimizer 🔗

### What It Does

Automatically suggests and creates contextual internal links between related articles.

**Benefits:**
- Builds topical authority
- Improves site structure
- Boosts SEO through internal linking
- Distributes page authority

### Usage

```python
from content_quality.validators.internal_link_optimizer import optimize_article_links

# Optimize links for an article
result = optimize_article_links(
    article_id=123,
    markdown_content="Article content...",
    target_keyword="total loss settlement"
)

# Returns:
# {
#   "suggestions": [...],
#   "updated_markdown": "...",  # With links injected
#   "links_added": 5
# }
```

### How It Works

1. **Analyzes Content**: Extracts key phrases and topics
2. **Finds Candidates**: Identifies related published articles
3. **Calculates Relevance**: Scores each potential link (0.0-1.0)
4. **Suggests Anchors**: Finds natural linking opportunities
5. **Injects Links**: Adds markdown links to content

### Link Quality Scoring

```python
# Factors (total = 1.0):
- Keyword overlap: 0.4 weight
- State matching: 0.2 weight
- Key phrase overlap: 0.3 weight
- Title mention: 0.1 weight bonus
```

### Configuration

Minimum quality score: 0.6 (default)
Max links per article: 5 (default)

### Database Table

- `internal_links` - Tracks all internal links
- Columns: source_article_id, target_article_id, anchor_text, link_quality_score

---

## 3. Rival - Competitor Monitor 🎯

### What It Does

Monitors competitor content and automatically creates "better version" briefs for your writers.

**Workflow:**
1. Scrapes competitor blogs
2. Tracks their content
3. Identifies gaps in your coverage
4. Creates briefs to outrank them

### Usage

```bash
# Run competitor analysis
python -m pipeline.cli run rival

# Recommended: Daily at 8am
```

### Configuration

```yaml
rival:
  enabled: true
  competitor_domains:
    - "competitor1.com"
    - "competitor2.com"
    - "competitor3.com"
  max_competitor_checks: 20  # Articles per run
```

### What It Tracks

For each competitor article:
- URL and title
- Target keyword (extracted)
- Word count
- Content hash (for change detection)
- Detected ranking position

### Opportunity Detection

Creates briefs when:
1. **Keyword Gap**: Competitor ranks for keyword you don't cover
2. **Better Version**: Your article ranks lower than theirs
3. **New Content**: They publish fresh content

### Example Brief Created

```markdown
Write a comprehensive guide on 'total loss settlement florida' that outranks competitor.com.

Competitor article: https://competitor.com/florida-total-loss
Competitor word count: 1,800

Requirements:
- Target keyword: total loss settlement florida
- Make it 20% longer and more comprehensive than competitor
- Include specific examples and step-by-step instructions
- Add FAQ section addressing common questions
- Use clear, helpful tone
- Include practical tips the competitor didn't cover

Target word count: 2,160
```

### Database Table

- `competitor_articles` - Tracks all competitor content
- Columns: competitor_domain, article_url, title, target_keyword, word_count, detected_position, content_hash

---

## 4. Dynamic CTAs (Ezra Enhanced) 💰

### What It Does

Generates personalized CTAs based on article content, state, and keyword.

**CTA Types:**
1. **Primary** (Sidebar): State-specific settlement analysis
2. **Secondary** (Inline): Content-aware offers
3. **Newsletter** (Bottom): Email signup

### Features

- **State-Specific**: "Get Your Free California Settlement Analysis"
- **Keyword-Aware**: Different CTAs for "settlement" vs "total loss" keywords
- **A/B Ready**: Multiple variants tracked in database
- **Performance Tracked**: Impressions, clicks, conversions per variant

### Example CTAs Generated

**For California + "total loss settlement" keyword:**

```html
<!-- Primary CTA (Sidebar) -->
<div class="cta cta-sidebar">
  <h3>Get Your Free California Settlement Analysis</h3>
  <p>See if your California total loss offer is fair in under 5 minutes.</p>
  <a href="https://claimcoach.app" class="button">Analyze Your Offer</a>
</div>

<!-- Secondary CTA (Inline) -->
<div class="cta cta-inline">
  <h3>Declared a Total Loss?</h3>
  <p>Get a detailed breakdown of what your vehicle is actually worth.</p>
  <a href="https://claimcoach.app" class="button">Get Your Valuation</a>
</div>

<!-- Newsletter CTA (Bottom) -->
<div class="cta cta-newsletter">
  <h2>Insurance Tips in Your Inbox</h2>
  <p>Get weekly tips on navigating total loss claims and maximizing settlements.</p>
  <a href="https://claimcoach.app/newsletter" class="button">Subscribe Free</a>
</div>
```

### Database Table

- `cta_variants` - Tracks all CTA variations and performance
- Columns: article_id, cta_text, cta_type, position, impressions, clicks, conversions, is_active

### Integration

CTAs are automatically generated and injected when Ezra publishes an article. No manual work required.

---

## 5. Remix - Content Repurposer 📱

### What It Does

Transforms one published article into multiple platform-specific formats.

**One article becomes:**
- Twitter thread (8-10 tweets)
- LinkedIn post (1,300 chars)
- Email newsletter (subject + body)
- YouTube video script (5-7 minutes)

### Usage

```bash
# Remix recent articles
python -m pipeline.cli run remix

# Recommended: Every 6 hours
```

### Output Formats

#### Twitter Thread
```json
[
  {"tweet_number": 1, "text": "🚗 How to Check Your Total Loss Settlement"},
  {"tweet_number": 2, "text": "Thread 🧵 Everything you need to know..."},
  ...
  {"tweet_number": 10, "text": "Read more: https://claimcoach.app/blog/article"}
]
```

#### LinkedIn Post
```
How to Check Your Total Loss Settlement

A comprehensive guide for anyone dealing with total loss insurance claims.

Key takeaways inside ⬇️

[3-5 insights with professional tone]

Read the full guide: https://claimcoach.app/blog/article

#Insurance #TotalLoss #Claims
```

#### Email Newsletter
```json
{
  "subject": "How to Check Your Total Loss Settlement",
  "preheader": "Make sure you're getting a fair offer",
  "body_html": "<h2>...</h2><p>...</p>",
  "cta_url": "https://claimcoach.app/blog/article"
}
```

#### YouTube Script
```
VIDEO TITLE: How to Check Your Total Loss Settlement

[0:00] HOOK:
"What if I told you that insurance companies often underpay..."

[0:15] INTRO:
"Hey everyone, welcome back..."

[0:45] MAIN CONTENT:
[Key points with visual suggestions]

[5:00] CALL TO ACTION:
"For the complete guide..."

[5:30] OUTRO:
"Thanks for watching! Subscribe for more..."
```

### Configuration

```yaml
remix:
  enabled: true
  remix_types:
    - twitter
    - linkedin
    - email
    - youtube
  max_remixes_per_run: 3  # Articles to remix per run
```

### Database Table

- `content_remixes` - Stores all content variations
- Columns: source_article_id, remix_type, remix_content, published_url, engagement_score

### AI-Powered

Uses Claude Haiku API to generate platform-optimized content. Falls back to template-based generation if API unavailable.

---

## Quick Start

### 1. Update Database Schema

```bash
# Run migrations to add new tables
python -c "from content_quality.db import init_database; init_database()"
```

This creates 5 new tables:
- performance_insights
- internal_links
- competitor_articles
- cta_variants
- content_remixes

### 2. Configure Agents

Edit `config.yaml`:

```yaml
# Add competitor domains for Rival
rival:
  competitor_domains:
    - "example.com"

# Configure Remix platforms
remix:
  remix_types:
    - twitter
    - linkedin
    - email
```

### 3. Run Agents

```bash
# Performance analysis (needs 10+ published articles)
python -m pipeline.cli run atlas

# Competitor monitoring
python -m pipeline.cli run rival

# Content remixing
python -m pipeline.cli run remix

# All new agents
python -m pipeline.cli run all
```

### 4. Check Results

```bash
# View insights
sqlite3 data/claimcoach_content.db "SELECT * FROM performance_insights ORDER BY created_at DESC LIMIT 5"

# View competitor tracking
sqlite3 data/claimcoach_content.db "SELECT domain, COUNT(*) FROM competitor_articles GROUP BY domain"

# View remixes
sqlite3 data/claimcoach_content.db "SELECT remix_type, COUNT(*) FROM content_remixes GROUP BY remix_type"
```

---

## Cost Impact

**AI API Usage:**
- Atlas: No API calls (deterministic analysis)
- Rival: No API calls (web scraping + pattern matching)
- Remix: ~$0.05/article for all 4 formats (Haiku)

**Total additional cost:** ~$5-10/month for 100 articles

**ROI:** Massive - these features generate insights and content that would take hours manually.

---

## Integration with Existing Pipeline

All new features integrate seamlessly:

1. **Atlas** runs weekly, feeds insights back to Quill
2. **Internal Links** can be called by Sage during review
3. **Rival** creates briefs that Quill picks up from todo
4. **Dynamic CTAs** automatically injected by Ezra
5. **Remix** runs after Ezra publishes

No changes needed to existing agents!

---

## Architecture

### New Files Created

```
pipeline/agents/
  ├── atlas.py         # Performance analyst (380 lines)
  ├── rival.py         # Competitor monitor (420 lines)
  └── remix.py         # Content repurposer (510 lines)

content_quality/validators/
  └── internal_link_optimizer.py  # Link suggestion (380 lines)

# Enhanced
pipeline/agents/ezra.py  # Now with dynamic CTAs
```

### Database Schema Changes

```sql
-- Performance insights
CREATE TABLE performance_insights (...)

-- Internal linking
CREATE TABLE internal_links (...)

-- Competitor tracking
CREATE TABLE competitor_articles (...)

-- CTA performance
CREATE TABLE cta_variants (...)

-- Content remixes
CREATE TABLE content_remixes (...)
```

### CLI Commands Added

```bash
python -m pipeline.cli run atlas  # Performance analysis
python -m pipeline.cli run rival  # Competitor monitoring
python -m pipeline.cli run remix  # Content remixing
```

---

## Next Steps

### Week 1: Test & Tune
- Run Atlas once you have 10+ published articles
- Configure Rival with 3-5 competitor domains
- Test Remix on your best-performing articles

### Week 2: Optimize
- Review Atlas insights and adjust content strategy
- Use Rival briefs to fill content gaps
- Distribute remixed content across platforms

### Week 3: Scale
- Add more competitors to Rival
- Increase Remix frequency
- Track CTA performance and optimize

### Month 2: Automate
- Schedule Atlas weekly
- Schedule Rival daily
- Schedule Remix every 6 hours
- Let the system optimize itself!

---

## Support

For issues or questions:
1. Check agent logs: `logs/agent.log`
2. Inspect database: `sqlite3 data/claimcoach_content.db`
3. Review validation output from each agent

---

**Built and shipped while you slept! 😴🚀**

All 5 features implemented, tested, and documented.
