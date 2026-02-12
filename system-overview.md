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
| Discover related keywords | Google autocomplete API | HTTP GET (Google Suggest) |
| **Gap Analysis** | Analyze what top-ranking competitor articles lack — state nuances, adjuster tips, dollar amounts, actionable templates | LLM: **strategy model** (Gemini Pro + search grounding), ~600 tokens |
| Generate content brief | For up to 5 unbriefed topics, incorporating gap analysis results | LLM: **strategy model** (Gemini Pro), ~500 tokens |
| Generate title | SEO-optimized title suggestion | LLM: **utility model** (Flash-Lite), ~60 tokens |
| Promote | BACKLOG → TODO if brief is substantial | None (DB update) |

**Claim field**: None (no concurrency needed)

**Gap Analyst**: Uses Gemini Pro with search grounding enabled to discover what existing top-ranking content misses. The gap analysis is injected directly into the content brief so Quill writes articles that fill competitive voids rather than repeating what's already out there.

---

### 2. Quill (runs every hour)

**Job**: Write articles using a 5-phase pipeline. Picks TODO or REVISION articles.

**Claim field**: `writer_claim` (atomic lock via `try_claim`)

**Golden System Prompt**: Role-Based Constraint Prompting that casts the LLM as "Lead Content Strategist and Senior Insurance Adjuster." Enforces:
- **Banned AI-isms**: 27 words/phrases never used (delve, tapestry, leverage, utilize, comprehensive, etc.)
- **Sentence rhythm**: If a sentence is >20 words, the next must be <10
- **Adjuster Insider callouts**: Every section includes a `> **Adjuster Insider:**` blockquote
- **"Why this matters to your wallet"**: Every technical fact must have a dollar-impact sentence
- **Active voice only**: "The adjuster denied the claim" not "The claim was denied"
- **Entity-first SEO**: Primary entities woven into first 100 words

| Phase | What happens | API Call |
|-------|-------------|---------|
| **Claim** | Atomically set `writer_claim`, status → IN_PROGRESS | None (DB) |
| **Phase 0: Entity Mapping** | Extract insurance/legal entities for E-E-A-T (legal terms, insurance concepts, processes, data points, authoritative sources) | LLM: **fast model** (Gemini Flash), ~500 tokens |
| **Phase 1: Outline** | Generate structured outline incorporating entity map (H2s, key points, data, entities per section) | LLM: **strategy model** (Gemini Pro), ~1500 tokens |
| **Phase 2: Draft** | Write 6-7 sections from outline. Each section gets: friction point injection, Adjuster Insider callout instruction, "why this matters to your wallet" requirement. **Temperature: 0.85** for human-feel creative writing. | LLM: **fast model** (Gemini Flash) x6-7 calls, ~1500 tokens each |
| **Phase 2.5: Contrastive Critique** | Flash-Lite "cynical insurance adjuster" identifies 3 problems (most generic passage, most robotic sentence, vaguest claim). Flash then rewrites only those flagged passages. | LLM: **utility model** (Flash-Lite) ~600 tokens + **fast model** (Flash) ~8000 tokens |
| **Phase 3: Self-review** | Deterministic checks + auto-fix | - |
| - Keyword in first 100 words? | Insert if missing (fuzzy `_keyword_match`) | None |
| - ClaimCoach CTA present? | Add if missing | None |
| - FAQ section present? | Generate if missing | LLM: **utility model** (Flash-Lite), ~800 tokens |
| - Meta description? | Generate if missing | LLM: **utility model** (Flash-Lite), ~80 tokens |
| - **AI-isms filter** | Deterministic find-and-replace: 20 word replacements (utilize→use, leverage→use, comprehensive→full, etc.) + strip 27 banned multi-word phrases | None (regex) |
| **Quality gate** | Word count >= 1500? FK >= 40? | None (deterministic) |
| **Submit** | status → REVIEW, release `writer_claim=""` | None (DB) |

**If revision** (article came back from Sage): Attempts targeted fixes first using the `default model`, falls through to full rewrite if issues are too broad.

**Friction Point Injection**: Each middle section receives a unique "insider knowledge" friction point extracted from the content brief or generated per-category. Examples:
- `problem_aware`: "Adjusters often use automated valuation tools that systematically undervalue vehicles by 10-20%"
- `solution_aware`: "Adjusters have internal authority to increase offers by 10-15% without supervisor approval"
- `state_specific`: "Filing a DOI complaint triggers an automatic review — adjusters often settle quickly once they see the complaint number"

**Tokens per article**: ~14,000-16,000 (first draft with entity mapping + critique loop), +3,000 per revision round.

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
| **Context-aware CTA** | Flash-Lite identifies the "high-intent moment" (peak frustration paragraph) and generates a CTA matching that emotion, inserted at the correct H2 boundary | LLM: **utility model** (Flash-Lite), ~300 tokens |
| Generate HTML | `python-markdown` → Jinja2 template → `blog/html/{slug}.html` with context-aware CTA injected at matching H2 position | None (library) |
| Update blog index | Append to `blog/index.json` | None (filesystem) |
| Update article | `published_url`, `published_at`, status → DONE | None (DB) |

**Context-Aware CTA**: Instead of purely template-based CTAs, Ezra now uses Flash-Lite to read the article and identify where the reader feels most frustrated with their insurance company. It generates a CTA that mirrors that specific emotion and inserts it at exactly the right H2 boundary in the HTML output. Template CTAs (primary, secondary, newsletter) remain as fallbacks. Only `claimcoach.app` URLs are allowed in generated CTAs (hardcoded allowlist).

**LLM calls**: 1 per article (~300 tokens for context-aware CTA)

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

## Golden System Prompt (Quill's Writing Voice)

Quill uses **Role-Based Constraint Prompting** — the LLM is cast as "Lead Content Strategist and Senior Insurance Adjuster for ClaimCoach." This suppresses AI-isms and forces a "High-Agency, Low-Fluff" tone.

### The "Human" Filter Rules

| Rule | What it does |
|------|-------------|
| **No AI-isms** | 27 banned words/phrases: delve, tapestry, pivotal, unlock, landscape, comprehensive, utilize, leverage, embark, foster, streamline, robust, cutting-edge, paradigm, synergy, game-changer, deep dive, "at the end of the day", "it's important to note", "in today's world", etc. |
| **Sentence rhythm** | If a sentence is >20 words, the next must be <10. Creates natural cadence. |
| **Empathetic coaching** | Use "you" and "we." Acknowledge stress. Don't lecture; coach. |
| **Jargon handling** | Use technical terms but explain them instantly in plain English |
| **No wrapped summaries** | Never "In conclusion" or "To summarize." End with actionable Next Step. |
| **Active voice only** | "The adjuster denied the claim" not "The claim was denied by the adjuster" |
| **Short words** | "Get" not "obtain." "Show" not "demonstrate." "Use" not "utilize." |

### Adjuster Insider Callouts

Every section must include a Markdown blockquote callout:

```markdown
> **Adjuster Insider:** Most adjusters have authority to increase offers by 10-15%
> without supervisor approval. They just won't tell you that.
```

### "Why This Matters to Your Wallet"

For every technical fact, the writer must add one sentence explaining the dollar impact. Don't just say what something is — say what it costs the reader.

### Deterministic AI-Isms Filter (Phase 3)

After all LLM writing is done, a zero-cost regex pass runs:

**Word replacements** (20 rules):
```
utilize → use, leverage → use, comprehensive → full, robust → strong,
streamline → simplify, facilitate → help, implement → set up,
subsequently → then, furthermore → also, additionally → also,
demonstrate → show, obtain → get, commence → start, endeavor → try,
ascertain → find out, in order to → to, due to the fact that → because,
at this point in time → now, prior to → before
```

**Phrase stripping** (27 phrases): Multi-word AI-isms with no simple replacement are removed entirely, preserving surrounding sentence structure.

---

## Temperature Control

LLM calls now support a `temperature` parameter:

| Task Type | Temperature | Rationale |
|-----------|-------------|-----------|
| **SEO writing** (section drafting, monolithic draft, critique refinement) | **0.85** | Higher creativity for human-feel sentence structures while the Golden System Prompt keeps it grounded in insurance facts |
| **Structured/analytical** (outlines, entity extraction, fact checks, scoring) | Provider default | Lower temperature for consistency and accuracy |

---

## Rate Limiting & Budget Tracking

### RPM Limiter (Request-Per-Minute)
- **Gemini**: Minimum 12-second delay between calls (free tier: 5 RPM)
- Thread-safe global lock shared across all agents

### TPM Limiter (Token-Per-Minute)
- **Sliding window**: 60-second window tracking estimated tokens (prompt + response)
- **Threshold**: Parks pipeline for 30s when approaching 85% of 250k TPM limit
- **Token estimation**: ~4 chars per token for English text
- **Prevents**: Hard 429 errors during sustained multi-article runs

### Daily Budget Gates
- **Pro**: 20 calls/day → auto-downgrades to Flash when exhausted
- **Flash**: 250 calls/day
- **Flash-Lite**: 1000 calls/day

---

## LLM Provider & Model Tiers

You're using **Gemini** on Railway with `LLM_PROVIDER=gemini`:

| Tier | Model | Used by | Purpose |
|------|-------|---------|---------|
| **Strategy** | gemini-2.5-pro | Scout (briefs + gap analysis), Quill (outlines), Sage (fact check) | High-trust decisions, search grounding enabled |
| **Default** | gemini-2.5-flash | Quill (revisions), Lurker (response drafts) | Primary work |
| **Fast** | gemini-2.5-flash | Quill (entity mapping, section drafting at temp 0.85, critique refinement) | Bulk content |
| **Utility** | gemini-2.5-flash-lite | Scout (titles), Quill (FAQ/meta, contrastive critique), Sage (originality), Herald (social), Ezra (context-aware CTA) | Lightweight tasks |

---

## Quill's Full Writing Pipeline (Detailed Flow)

```
Article claimed (TODO/REVISION → IN_PROGRESS)
    │
    ▼
Phase 0: Entity Mapping (Flash, ~500 tokens)
    │  Extract: Legal Terms, Insurance Concepts, Processes, Data Points, Sources
    │
    ▼
Phase 1: Outline Generation (Pro, ~1500 tokens)
    │  Entity map injected into outline prompt
    │  5-7 H2 sections with entities assigned per section
    │
    ▼
Phase 2: Section-by-Section Drafting (Flash x6-7, ~1500 tokens each, temp=0.85)
    │  Each section gets:
    │    - Full outline context
    │    - Product context + state rules
    │    - Friction point injection (middle sections)
    │    - "Include Adjuster Insider blockquote" instruction
    │    - "Why this matters to your wallet" instruction
    │    - Previous section tail (500 chars) for continuity
    │
    ▼
Phase 2.5: Contrastive Critique Loop
    │  Step 1: Flash-Lite "cynical adjuster" identifies 3 problems (~600 tokens)
    │    - Most GENERIC passage (quoted)
    │    - Most ROBOTIC sentence (quoted)
    │    - Most VAGUE claim (quoted)
    │  Step 2: Flash rewrites ONLY the 3 flagged passages (~8000 tokens)
    │    - Validates refined article isn't >30% shorter
    │
    ▼
Phase 3: Self-Review & Auto-Fix (deterministic, 0 LLM tokens)
    │  Check 1: Keyword in first 100 words → insert if missing
    │  Check 2: ClaimCoach CTA present → add if missing
    │  Check 3: FAQ section present → generate if missing (Flash-Lite)
    │  Check 4: Meta description → generate/trim/extend if needed (Flash-Lite)
    │  Check 5: Keyword in meta description → prepend if missing
    │  Check 6: AI-isms filter → regex replace 20 words + strip 27 phrases
    │
    ▼
Quality Gate: word count >= 1500, FK >= 40
    │
    ▼
Submit to Sage (IN_PROGRESS → REVIEW, writer_claim released)
```

---

## Ezra's CTA Strategy (4 Variants)

| Position | Type | How it works |
|----------|------|-------------|
| **Sidebar** | Primary (template) | State-specific: "Get Your Free {State} Settlement Analysis" or generic fallback |
| **Inline** | Secondary (template) | Keyword-aware: "Not Sure If Your Offer Is Fair?" (settlement), "Declared a Total Loss?" (total loss), generic fallback |
| **Bottom** | Newsletter (template) | "Insurance Tips in Your Inbox" → subscribe CTA |
| **Contextual** | Context-aware (LLM) | Flash-Lite identifies the "high-intent moment" — the paragraph where the reader is most frustrated — and generates a CTA that mirrors that emotion. Inserted at the matching H2 boundary in the HTML. Only `claimcoach.app` URLs allowed. |

---

## Scout's Research Pipeline (Detailed Flow)

```
Phase 1: Seed Topics
    │  63 predefined keywords + 120 state variations + 30 vehicle variations
    │
    ▼
Phase 2: Keyword Discovery
    │  Google Autocomplete API with modifiers ("how to", "what is", "best way to", etc.)
    │
    ▼
Phase 3: AI Brief Generation (up to 5 per run)
    │  For each unbriefed topic:
    │    Step 1: Gap Analysis (Pro + search grounding, ~600 tokens)
    │      - What top-ranking articles lack
    │      - State-specific nuances missing
    │      - Dollar amounts and templates missing
    │    Step 2: Content Brief (Pro, ~500 tokens)
    │      - Gap analysis injected into brief prompt
    │      - Performance insights from GSC data
    │    Step 3: Title Suggestion (Flash-Lite, ~60 tokens)
    │
    ▼
Phase 4: Promote to TODO
    │  Articles with substantive AI-generated briefs promoted from BACKLOG → TODO
```
