Here's a complete step-by-step breakdown of how the system works:

---

## Pipeline Overview

7 agents run on a schedule, passing articles through a production pipeline using a SQLite database with atomic claim locking for concurrency safety.

```
Scout → Quill → Sage → (revision loop) → Ezra → Herald
                                          Lurker (parallel)
                                          Morgan (monitoring)
```

---

## Article Lifecycle (Status Transitions)

```
BACKLOG  →  TODO  →  IN_PROGRESS  →  REVIEW  →  READY_TO_PUBLISH  →  DONE  →  AMPLIFIED
  Scout      Scout      Quill          Sage         Ezra               Herald
                                         ↓
                                      REVISION ←→ (back to Quill, max 5 rounds)
                                         ↓
                                      REJECTED (terminal)
```

---

## Agent-by-Agent Breakdown

### 1. Scout (runs every 6 hours)

**Job**: Discover topics, write content briefs, promote to writing queue.

| Step | What happens | API Call |
|------|-------------|---------|
| Load seed topics | Hardcoded insurance keywords → create BACKLOG articles | None |
| Discover related keywords | Reddit autocomplete API | HTTP GET (Reddit) |
| Generate content brief | For up to 5 unbriefed topics | LLM: **strategy model** (Gemini Pro), ~500 tokens |
| Generate title | SEO-optimized title suggestion | LLM: **utility model** (Flash-Lite), ~60 tokens |
| Promote | BACKLOG → TODO if brief is substantial | None (DB update) |

**Claim field**: None (no concurrency needed)

---

### 2. Quill (runs every hour)

**Job**: Write articles using a 3-phase pipeline. Picks TODO or REVISION articles.

**Claim field**: `writer_claim` (atomic lock via `try_claim`)

| Phase | What happens | API Call |
|-------|-------------|---------|
| **Claim** | Atomically set `writer_claim`, status → IN_PROGRESS | None (DB) |
| **Phase 1: Outline** | Generate structured outline (H2s, key points, data) | LLM: **strategy model** (Gemini Pro), ~1500 tokens |
| **Phase 2: Draft** | Write 6-7 sections from outline | LLM: **fast model** (Gemini Flash) x6-7 calls, ~1500 tokens each |
| **Phase 3: Self-review** | Deterministic checks + auto-fix | - |
| - Keyword in first 100 words? | Insert if missing (fuzzy `_keyword_match`) | None |
| - ClaimCoach CTA present? | Add if missing | None |
| - FAQ section present? | Generate if missing | LLM: **utility model** (Flash-Lite), ~800 tokens |
| - Meta description? | Generate if missing | LLM: **utility model** (Flash-Lite), ~80 tokens |
| **Quality gate** | Word count >= 1500? FK >= 40? | None (deterministic) |
| **Submit** | status → REVIEW, release `writer_claim=""` | None (DB) |

**If revision** (article came back from Sage): Attempts targeted fixes first using the `default model`, falls through to full rewrite if issues are too broad.

**Tokens per article**: ~10,000-12,000 (first draft), +3,000 per revision round.

---

### 3. Sage (runs every hour at :30)

**Job**: Score articles against a 100-point rubric. Gate to publishing.

**Claim field**: `editor_claim`

#### The 100-Point Scoring Rubric

| Category | Max Pts | Method | How it works |
|----------|---------|--------|-------------|
| **Plagiarism** | 20 | Copyscape → LLM → skip | Tier 1: Copyscape API ($0.03/check). Tier 2: LLM originality check (Gemini Flash-Lite, 300 tokens) — checks for boilerplate, generic filler, keyword specificity. Tier 3: 14/20 skip. **Only runs on first review** — score carried forward on revisions. |
| **SEO** | 20 | Deterministic | +3 keyword in title, +3 keyword in meta, +2 keyword in first 100 words (fuzzy match), +2 keyword in 2+ H2s, +3 external links (>=2), +2 FAQ present, +2 meta desc 150-160 chars, +1 heading structure (>=5 H2s), +2 keyword density |
| **Readability** | 15 | Flesch-Kincaid formula | +10 if FK >= 60, +5 if FK >= 50. +5 if avg sentence <= 25 words, +2.5 if <= 30. Markdown stripped before calculation. |
| **Factual Accuracy** | 20 | Regex + LLM | Start at 20, -5 per regex match against ~15 false claim patterns (from PRODUCT_CONTEXT.md). Then LLM fact-check (strategy model, 500 tokens) deducts -2 per AI-found issue. **Without LLM: capped at 10/20.** |
| **Internal Links** | 10 | DB lookup | Validates links against published articles. Full marks if all valid, 5 if partial, **8 grace period** if <= 3 published articles exist. |
| **Word Count** | 5 | Calibrated thresholds | 5 pts if 1800-2200 words, 3 pts if 1500-2500, 0 otherwise. Thresholds calibrated by Morgan from GSC data. |
| **CTA** | 5 | Regex | +2.5 if "claimcoach" mentioned, +2.5 if "claimcoach.app" linked. |
| **Legal Compliance** | 5 | Flagged phrase list | 5/5 if no flagged phrases found, 0/5 if any match (~15 phrases: "you are legally entitled", "guaranteed results", "claimcoach negotiates for you", etc.) |

#### Decision Logic

| Condition | Decision | New Status |
|-----------|----------|------------|
| Score >= 90 | **APPROVED** | READY_TO_PUBLISH |
| Score < 90, revision_count < 5 | **REVISION** | REVISION (back to Quill) |
| Score < 90, revision_count >= 5 | **REJECTED** | REJECTED (terminal) |

After every decision: `editor_claim=""` (always released), `revision_notes` appended with full breakdown, lessons recorded for Quill.

**LLM calls per article**: ~800 tokens (originality + fact check on first review), 0 on revisions (plagiarism reused, only fact check re-runs).

---

### 4. Ezra (runs every 4 hours)

**Job**: Publish approved articles as static HTML + markdown.

**Claim field**: `publisher_claim`

| Step | What happens | API Call |
|------|-------------|---------|
| Claim article | `try_claim` on READY_TO_PUBLISH | None (DB) |
| Generate slug | From title, max 80 chars | None |
| Save markdown | `blog/posts/{slug}.md` with YAML frontmatter | None (filesystem) |
| Generate CTA variants | Primary (sidebar), secondary (inline), newsletter (bottom) | None (template-based) |
| Generate HTML | `python-markdown` → Jinja2 template → `blog/html/{slug}.html` | None (library) |
| Update blog index | Append to `blog/index.json` | None (filesystem) |
| Update article | `published_url`, `published_at`, status → DONE | None (DB) |

**LLM calls**: 0 (purely deterministic publishing)

---

### 5. Herald (runs at 10 AM and 6 PM)

**Job**: Generate social media posts for published articles.

**Claim field**: `herald_claim`

| Step | What happens | API Call |
|------|-------------|---------|
| Learn from engagement | Analyze past amplified articles for platform/category performance | None (DB) |
| Generate social content | Reddit/Twitter/Facebook post variants | LLM: **utility model** (Flash-Lite), ~1500 tokens OR template fallback |
| Save drafts | Store in `social_posts` table (status="draft") | None (DB) |
| Optional: auto-post | If Reddit/Twitter API configured + within spam limits | HTTP POST (Reddit/Twitter API) |
| Update article | `social_status="amplified"`, status → AMPLIFIED | None (DB) |

**Anti-spam**: Max 1 post per subreddit per week, min 5 helpful comments per 1 link share.

---

### 6. Lurker (runs every 8 hours)

**Job**: Find Reddit engagement opportunities where ClaimCoach can provide value.

**Claim field**: None (doesn't modify articles)

| Step | What happens | API Call |
|------|-------------|---------|
| Learn from outcomes | Check past opportunity approval rates per subreddit | None (DB) |
| Search Reddit | ~5 queries x 7 target subreddits | HTTP GET (Reddit public JSON API, no auth) |
| Score opportunities | Relevance terms + engagement + recency + learned boosts | None (deterministic) |
| Draft responses | For opportunities scoring >= 0.6 | LLM: **default model** (Flash), ~400 tokens each |
| Save opportunities | `opportunities` table with draft_response | None (DB) |

---

### 7. Morgan (runs at 7 AM, 1 PM, 7 PM)

**Job**: Pipeline health monitoring, lesson distillation, calibration.

**LLM calls**: 0 (purely deterministic)

| Check | What it does | Action |
|-------|-------------|--------|
| Backlog health | Count BACKLOG >= 15? | Alert if low, suggest Scout run |
| Pipeline flow | Any article stuck 24h+? | Alert, identify bottleneck agent |
| Revision loops | Any article at revision round >= 4? | Alert for human review |
| Publishing cadence | Articles published this week vs target (7) | Alert if behind |
| Social amplification | DONE articles > 24h unpromoted? | Suggest Herald run |
| Quality trends | Sage pass rate < 50%? | Alert to review Quill prompts |
| Distill lessons | Analyze GSC data → calibrate word count, category performance | Record lessons for Quill/Scout |
| Decay lessons | Fade lessons not seen in 30 days | Decrement occurrences, delete stale |

---

## Concurrency: The Claim System

Each agent uses atomic SQL claims to prevent two instances from working on the same article:

```sql
UPDATE articles SET writer_claim = 'quill-1708765432-a1b2c3', status = 'in_progress'
WHERE id = 42 AND (writer_claim = '' OR writer_claim IS NULL)
-- rowcount == 1 → claimed successfully
-- rowcount == 0 → already claimed by another process
```

4 claim fields: `writer_claim` (Quill), `editor_claim` (Sage), `publisher_claim` (Ezra), `herald_claim` (Herald).

---

## Cross-Agent Learning

Sage records lessons for Quill after every review (e.g., "Keyword not in meta description", "Sentences averaging 32 words"). Quill loads these lessons on its next run and incorporates them into writing prompts. Morgan calibrates thresholds from real GSC performance data. Lessons have confidence scores (0.2 at first occurrence, 1.0 after 5+ occurrences) and decay after 30 days of inactivity.

---

## LLM Provider & Model Tiers

You're using **Gemini** on Railway with `LLM_PROVIDER=gemini`:

| Tier | Model | Used by | Purpose |
|------|-------|---------|---------|
| **Strategy** | gemini-2.5-pro | Scout (briefs), Quill (outlines), Sage (fact check) | High-trust decisions |
| **Default** | gemini-2.5-flash | Quill (revisions), Lurker (response drafts) | Primary work |
| **Fast** | gemini-2.5-flash | Quill (section drafting) | Bulk content |
| **Utility** | gemini-2.5-flash-lite | Scout (titles), Quill (FAQ/meta), Sage (originality), Herald (social) | Lightweight tasks |

**Rate limiting**: 12s between Gemini calls (5 RPM). Budget gates: Pro 20/day, Flash 250/day, Flash-Lite 1000/day. Pro auto-downgrades to Flash when budget exhausted.
