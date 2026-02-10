# Agent Integration Guide

This document explains how to integrate the ClaimCoach Content Quality system with your agent pipeline.

## Overview

The quality system provides two main integration points:

1. **Reference Documents** - Loaded by Quill at article creation
2. **Validation API** - Called by Sage before publishing

```
Quill (agent) ─────► Loads reference/*.md ─────► Writes article
                                                       │
                                                       ▼
Sage (agent) ─────► Calls POST /validate/all ─────► Reviews results
                                                       │
                                                       ▼
                                              PASS: publish
                                              FAIL: revision
```

## Integration Point 1: Reference Documents (Quill)

### Purpose

Quill loads reference documents into Claude's system prompt to ensure articles are factually accurate from the start.

### Documents to Load

1. **`reference/PRODUCT_CONTEXT.md`** - What ClaimCoach does/doesn't do
2. **`reference/STATE_RULES.md`** - State-specific insurance regulations

### Implementation Example

```python
# quill.py

import os
from pathlib import Path

# Paths to reference docs
REFERENCE_DIR = Path(__file__).parent.parent / "reference"
PRODUCT_CONTEXT_PATH = REFERENCE_DIR / "PRODUCT_CONTEXT.md"
STATE_RULES_PATH = REFERENCE_DIR / "STATE_RULES.md"

def load_reference_docs(target_state=None):
    """Load reference docs for article writing."""

    # Always load product context
    with open(PRODUCT_CONTEXT_PATH) as f:
        product_context = f.read()

    # Load full state rules
    with open(STATE_RULES_PATH) as f:
        state_rules_full = f.read()

    # If targeting specific state, extract that section
    if target_state:
        state_rules = extract_state_section(state_rules_full, target_state)
    else:
        state_rules = state_rules_full

    return {
        "product_context": product_context,
        "state_rules": state_rules
    }

def extract_state_section(full_text, state_name):
    """Extract specific state section from STATE_RULES.md."""
    lines = full_text.split('\n')
    section_lines = []
    in_section = False

    for line in lines:
        # Start of target state section
        if line.startswith(f'## {state_name}'):
            in_section = True
            section_lines.append(line)
        # End of section (next state or end of file)
        elif in_section and line.startswith('## ') and not line.startswith(f'## {state_name}'):
            break
        elif in_section:
            section_lines.append(line)

    return '\n'.join(section_lines)

def write_article(article_id):
    """Write article using Claude API."""

    # Get article details from database
    article = get_article_from_db(article_id)

    # Load reference docs
    refs = load_reference_docs(article['target_state'])

    # Build system prompt
    system_prompt = f"""
You are writing a blog article for ClaimCoach, a tool that helps people
understand their auto insurance total loss settlements.

TARGET KEYWORD: {article['target_keyword']}
TARGET STATE: {article['target_state'] or 'General (all states)'}

WORD COUNT: 1800-2200 words
READING LEVEL: 8th grade (Flesch Reading Ease 60+)

## PRODUCT CONTEXT (CRITICAL - DO NOT VIOLATE)

{refs['product_context']}

## STATE REGULATIONS (BE FACTUALLY ACCURATE)

{refs['state_rules']}

## ARTICLE REQUIREMENTS

1. Title: Compelling, includes target keyword
2. Structure: H1 → multiple H2s → H3s as needed
3. Include:
   - FAQ section (3+ questions)
   - At least 3 internal links to other ClaimCoach articles
   - At least 2 external links to authoritative sources (.gov, .edu, KBB, etc.)
   - Call-to-action linking to claimcoach.app
   - Examples with specific dollar amounts
4. Tone: Helpful, empathetic, not salesy
5. SEO: Use target keyword in title, first 100 words, and 2+ H2 headings

CRITICAL RULES:
- NEVER claim ClaimCoach does things it doesn't do (see PRODUCT CONTEXT)
- BE FACTUALLY ACCURATE about state regulations (see STATE REGULATIONS)
- Use simple language (avoid jargon, long sentences, complex words)
- Include specific numbers, statutes, and examples
- DO NOT guarantee outcomes or make legal claims

Now write the article for: "{article['title']}"
"""

    # Call Claude API
    response = anthropic_client.messages.create(
        model="claude-haiku-4.5-20251001",
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Write a complete article about: {article['title']}"}
        ]
    )

    article_content = response.content[0].text

    # Save to database
    save_article_draft(article_id, article_content)

    return article_content
```

### Key Points

1. **Always load both reference docs** - Even for general articles, product context is critical
2. **Extract state-specific sections** - Don't send entire STATE_RULES.md for state articles (saves tokens)
3. **Include in system prompt** - Not in user message (better compliance)
4. **Update status in database** - Set status to 'review' after writing

## Integration Point 2: Validation API (Sage)

### Purpose

Sage calls the validation API to check articles before publishing. API returns pass/fail with specific revision notes.

### API Endpoint

**`POST http://content-quality-api:8000/validate/all`**

(Use Railway's internal hostname, not public URL)

### Implementation Example

```python
# sage.py

import requests
import json
from typing import Dict, Any

VALIDATION_API_URL = os.environ.get(
    "VALIDATION_API_URL",
    "http://content-quality-api:8000"
)

def review_article(article_id: int) -> Dict[str, Any]:
    """
    Review article by calling quality validation API.

    Returns:
        Dict with validation results and decision (publish or revise)
    """

    # Get article from database
    article = get_article_from_db(article_id)

    # Prepare validation request
    validation_request = {
        "article_markdown": article['markdown_content'],
        "target_keyword": article['target_keyword'],
        "meta_title": article['meta_title'],
        "meta_description": article['meta_description'],
        "slug": article['slug'],
        "target_state": article['target_state'],
    }

    # Call validation API
    try:
        response = requests.post(
            f"{VALIDATION_API_URL}/validate/all",
            json=validation_request,
            timeout=30  # Allow up to 30s for link checks
        )
        response.raise_for_status()
        result = response.json()
    except requests.exceptions.RequestException as e:
        # API error - log and fail safely
        log_error(f"Validation API error: {e}")
        return {
            "decision": "ERROR",
            "error": str(e)
        }

    # Update database with validation results
    update_article_validation(
        article_id=article_id,
        validation_status=result['overall_status'],
        seo_score=result['seo_score']['total_score'],
        readability_score=result['readability']['flesch_reading_ease'],
        state_accuracy=result['state_validation']['status'],
        product_compliance=result['product_validation']['status'],
        broken_links_count=len([l for l in result['links'].get('internal', []) + result['links'].get('external', []) if l.get('severity') == 'ERROR']),
        math_errors_count=len(result['math']['issues']),
        validation_notes=json.dumps(result['revision_notes'])
    )

    # Make decision
    if result['overall_status'] == 'PASS':
        # Approve for publishing
        update_article_status(article_id, 'ready_to_publish')

        log_agent_action(
            agent_name='sage',
            action='approved_for_publishing',
            article_id=article_id,
            details={
                'seo_score': result['seo_score']['total_score'],
                'readability_score': result['readability']['flesch_reading_ease']
            }
        )

        return {
            "decision": "APPROVED",
            "seo_score": result['seo_score']['total_score'],
            "summary": result['summary']
        }

    else:
        # Send back for revision
        revision_notes = format_revision_notes(result['revision_notes'])

        update_article_status(
            article_id,
            status='revision',
            revision_notes=revision_notes,
            revision_count=article['revision_count'] + 1
        )

        log_agent_action(
            agent_name='sage',
            action='rejected_for_revision',
            article_id=article_id,
            details={
                'validation_status': result['overall_status'],
                'issue_count': len(result['revision_notes']),
                'seo_score': result['seo_score']['total_score']
            }
        )

        return {
            "decision": "REVISION_NEEDED",
            "revision_notes": revision_notes,
            "issue_count": len(result['revision_notes']),
            "summary": result['summary']
        }

def format_revision_notes(notes: list) -> str:
    """Format revision notes for Quill to understand."""

    if not notes:
        return ""

    formatted = "## Revision Required\n\n"
    formatted += "Please fix the following issues:\n\n"

    for i, note in enumerate(notes, 1):
        formatted += f"{i}. {note}\n"

    formatted += "\n**Important:** Only fix the issues listed above. Do not change anything else."

    return formatted

def update_article_validation(article_id: int, **kwargs):
    """Update article validation results in database."""
    from content_quality.db import update_article_validation as db_update
    db_update(article_id, **kwargs)

def update_article_status(article_id: int, status: str, **kwargs):
    """Update article status in database."""
    from content_quality.db import get_db

    with get_db() as db:
        set_clauses = ['status = ?', 'updated_at = CURRENT_TIMESTAMP']
        params = [status]

        for key, value in kwargs.items():
            set_clauses.append(f'{key} = ?')
            params.append(value)

        params.append(article_id)

        db.execute(
            f"UPDATE articles SET {', '.join(set_clauses)} WHERE id = ?",
            tuple(params)
        )

def log_agent_action(agent_name: str, action: str, article_id: int, details: dict):
    """Log agent action."""
    from content_quality.db import log_agent_action as db_log
    db_log(agent_name, action, article_id, details)
```

### Response Structure

```python
{
    # Overall status
    "overall_status": "PASS" | "FAIL",

    # Summary message
    "summary": "✓ PASSED all checks. SEO: 87/100, Readability: 64/100",

    # Specific revision notes (if FAIL)
    "revision_notes": [
        "PRODUCT CLAIM VIOLATION: Remove 'ClaimCoach negotiates' → Replace with 'ClaimCoach analyzes'",
        "SEO: Add 1 more internal link (need 3, have 2)",
        "READABILITY: Break up long sentences. Average is 24 words (target: <20)"
    ],

    # Detailed results per validator
    "state_validation": {
        "status": "PASS",
        "score": 100,
        "issues": [],
        "states_referenced": ["California"]
    },

    "product_validation": {
        "status": "FAIL",
        "hard_violations": [
            {
                "pattern_category": "feature_hallucination",
                "matched_text": "ClaimCoach negotiates with insurers",
                "line_number": 42,
                "suggestion": "Remove this claim. ClaimCoach does NOT negotiate."
            }
        ],
        "soft_warnings": []
    },

    "seo_score": {
        "total_score": 87,
        "status": "PASS",
        "checks": {
            "keyword_in_title": {"passed": true, "points_earned": 8},
            "internal_links_count": {"passed": false, "points_earned": 0},
            // ... all other checks
        },
        "suggestions": [
            "Add 1+ more internal links (need 3, have 2)"
        ]
    },

    "readability": {
        "status": "PASS",
        "flesch_reading_ease": 64.2,
        "issues": []
    },

    "links": {
        "status": "PASS",
        "internal": [
            {"url": "https://claimcoach.app/blog/article-1", "status": "OK"}
        ],
        "external": [
            {"url": "https://insurance.ca.gov", "status": 200}
        ]
    },

    "math": {
        "status": "PASS",
        "issues": []
    }
}
```

### Key Points

1. **Use internal Railway hostname** - Not public URL (faster, free)
2. **Set reasonable timeout** - Link checking can take 10-30 seconds
3. **Save all validation results** - Write to database for analytics
4. **Handle API errors gracefully** - Don't block pipeline if API is down
5. **Format revision notes clearly** - Make it easy for Quill to understand

## Integration Point 3: Database (All Agents)

### Shared SQLite Database

All agents read/write to the same SQLite database for coordination.

### Connection Example

```python
from content_quality.db import get_db, claim_article, log_agent_action

# Read articles
with get_db() as db:
    cursor = db.execute(
        "SELECT * FROM articles WHERE status = 'todo' ORDER BY RANDOM() LIMIT 1"
    )
    article = cursor.fetchone()

# Atomic claim (prevents race conditions)
success = claim_article(
    article_id=article['id'],
    claim_field='writer_claim',
    claim_id='quill-1707350400-x7k2'
)

if success:
    # You own this article now
    process_article(article['id'])

    # Log your action
    log_agent_action(
        agent_name='quill',
        action='draft_completed',
        article_id=article['id'],
        details={'word_count': 2034}
    )
```

### Database Schema Reference

See `content_quality/db.py` for full schema and helper functions.

## Integration Point 4: Weekly Monitors (Morgan)

### Reading Monitor Results

Weekly monitors (Search Console, Content Health) write flags to the database. Morgan reads these to spawn actions.

### Example: Processing GSC Insights

```python
# morgan.py

def process_gsc_insights():
    """Process Search Console insights and spawn actions."""

    from content_quality.db import get_db

    with get_db() as db:
        # Find articles needing refresh
        cursor = db.execute("""
            SELECT id, title, slug, last_gsc_position, last_gsc_impressions
            FROM articles
            WHERE refresh_priority = 'HIGH'
            AND status = 'done'
            ORDER BY last_gsc_impressions DESC
        """)

        high_priority = cursor.fetchall()

    for article in high_priority:
        # Spawn Quill to refresh this article
        spawn_quill_refresh(
            article_id=article['id'],
            reason=f"Almost page one (position {article['last_gsc_position']:.1f}, {article['last_gsc_impressions']} impressions)"
        )

        # Clear flag
        with get_db() as db:
            db.execute(
                "UPDATE articles SET refresh_priority = NULL WHERE id = ?",
                (article['id'],)
            )

def spawn_quill_refresh(article_id: int, reason: str):
    """Queue article for Quill to refresh."""

    with get_db() as db:
        # Set status back to 'todo' with refresh instructions
        db.execute("""
            UPDATE articles SET
                status = 'todo',
                revision_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            f"REFRESH: {reason}. Add 1-2 new sections, update examples, refresh date.",
            article_id
        ))

    log_agent_action(
        agent_name='morgan',
        action='queued_for_refresh',
        article_id=article_id,
        details={'reason': reason}
    )
```

### Example: Processing Broken Links

```python
def process_broken_links():
    """Process broken links detected by Content Health Checker."""

    from content_quality.db import get_db

    with get_db() as db:
        cursor = db.execute("""
            SELECT DISTINCT a.id, a.title, COUNT(bl.id) as broken_count
            FROM articles a
            JOIN broken_links bl ON a.id = bl.article_id
            WHERE bl.resolved = 0
            GROUP BY a.id
            HAVING broken_count > 0
        """)

        articles_with_broken_links = cursor.fetchall()

    for article in articles_with_broken_links:
        # Spawn Quill to fix broken links
        spawn_quill_fix_links(
            article_id=article['id'],
            broken_count=article['broken_count']
        )
```

## Integration Point 5: Reference Doc Updates

### When to Update Reference Docs

1. **PRODUCT_CONTEXT.md**
   - When ClaimCoach adds/removes features
   - When messaging changes
   - When new line items are added

2. **STATE_RULES.md**
   - When state regulations change
   - When new states are added
   - When statutes are updated

### Update Process

```bash
# 1. Edit reference doc
vim reference/STATE_RULES.md

# 2. Commit and push
git add reference/STATE_RULES.md
git commit -m "Update Florida threshold from 75% to 80% (statute changed)"
git push

# 3. Deploy to Railway (auto-deploy if connected to GitHub)
railway up

# 4. No restart needed - agents load at runtime
```

### Notifying Agents of Updates

If you need to re-validate existing articles after reference doc updates:

```python
# Flag all Florida articles for re-validation
with get_db() as db:
    db.execute("""
        UPDATE articles SET
            status = 'review',
            revision_notes = 'Florida regulations updated - please re-validate'
        WHERE target_state = 'Florida'
        AND status = 'done'
    """)
```

## Error Handling

### API Timeouts

```python
try:
    response = requests.post(validation_url, json=data, timeout=30)
except requests.exceptions.Timeout:
    # Log error, skip validation for now
    log_error("Validation API timeout - skipping validation")
    # Option 1: Set status to manual_review
    # Option 2: Retry later
    # Option 3: Proceed without validation (risky)
```

### Database Locks

```python
from content_quality.db import get_db
import time

for attempt in range(3):
    try:
        with get_db() as db:
            db.execute("UPDATE articles SET status = ? WHERE id = ?", (status, article_id))
        break  # Success
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e) and attempt < 2:
            time.sleep(1)  # Wait 1 second and retry
        else:
            raise  # Give up after 3 attempts
```

### Reference Doc Missing

```python
from pathlib import Path

def load_reference_doc_safe(path: Path) -> str:
    """Load reference doc with fallback."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        log_error(f"Reference doc not found: {path}")
        # Return empty or default content
        return "# Reference doc not available\n\nPlease check deployment."
```

## Testing Integration

### Test Quill Reference Loading

```python
from agents.quill import load_reference_docs

refs = load_reference_docs(target_state="California")
assert "California" in refs['state_rules']
assert "ClaimCoach does NOT" in refs['product_context']
print("✓ Reference docs loaded successfully")
```

### Test Sage Validation Call

```python
import requests

response = requests.post(
    "http://localhost:8000/validate/all",
    json={
        "article_markdown": "# Test Article\n\nClaimCoach analyzes your settlement.",
        "target_keyword": "test",
        "meta_title": "Test Article",
        "meta_description": "This is a test article for testing.",
        "slug": "test-article",
        "target_state": None
    }
)

assert response.status_code == 200
result = response.json()
assert "overall_status" in result
print(f"✓ Validation API working: {result['summary']}")
```

### Test Database Integration

```python
from content_quality.db import get_db, claim_article, log_agent_action

# Create test article
with get_db() as db:
    db.execute("""
        INSERT INTO articles (title, slug, status, target_keyword)
        VALUES (?, ?, ?, ?)
    """, ("Test", "test", "todo", "test"))
    article_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

# Test claim
success = claim_article(article_id, 'writer_claim', 'test-claim-123')
assert success == True
print("✓ Article claimed successfully")

# Test logging
log_agent_action('test_agent', 'test_action', article_id, {'foo': 'bar'})
print("✓ Agent action logged successfully")
```

## Complete Integration Example

See `agents/integration_examples/` for complete working examples of:
- `quill_integration.py` - Complete Quill implementation
- `sage_integration.py` - Complete Sage implementation
- `morgan_integration.py` - Complete Morgan implementation

## Support

For integration questions:
1. Review example code in `agents/integration_examples/`
2. Check `ARCHITECTURE.md` for system overview
3. Test with `/docs` endpoint for API debugging
4. Query `agent_log` table for troubleshooting
