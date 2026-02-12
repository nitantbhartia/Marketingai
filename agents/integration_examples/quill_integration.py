"""
Quill Integration Example

Shows how Quill agent loads reference documents and writes articles.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from content_quality.config import PRODUCT_CONTEXT_PATH, STATE_RULES_PATH
from content_quality.db import get_db, claim_article, log_agent_action
from content_quality.utils.text_utils import word_count


def load_reference_docs(target_state=None):
    """
    Load reference documents for article writing.

    Args:
        target_state: Optional state name (e.g., "California")

    Returns:
        Dict with 'product_context' and 'state_rules' keys
    """
    # Load product context (always needed)
    with open(PRODUCT_CONTEXT_PATH) as f:
        product_context = f.read()

    # Load state rules
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

    return '\n'.join(section_lines) if section_lines else f"No data found for {state_name}"


def build_writing_prompt(article, refs):
    """
    Build Claude API system prompt with reference docs.

    Args:
        article: Dict from database with article details
        refs: Reference docs from load_reference_docs()

    Returns:
        System prompt string
    """
    return f"""
You are writing a blog article for ClaimCoach, a tool that helps people
understand their auto insurance total loss settlements.

TARGET KEYWORD: {article['target_keyword']}
TARGET STATE: {article.get('target_state') or 'General (all states)'}
WORD COUNT: 1800-2200 words
READING LEVEL: 8th grade (Flesch Reading Ease 60+)

## PRODUCT CONTEXT (CRITICAL - DO NOT VIOLATE)

{refs['product_context']}

## STATE REGULATIONS (BE FACTUALLY ACCURATE)

{refs['state_rules']}

## ARTICLE REQUIREMENTS

1. **Title**: Compelling, includes target keyword
2. **Structure**: H1 → multiple H2s → H3s as needed
3. **Must Include**:
   - FAQ section (3+ questions)
   - At least 3 internal links to other ClaimCoach articles
   - At least 2 external links to authoritative sources (.gov, .edu, KBB, etc.)
   - Call-to-action linking to claimcoach.app
   - Examples with specific dollar amounts
4. **Tone**: Helpful, empathetic, not salesy
5. **SEO**: Use target keyword in:
   - Title
   - First 100 words
   - At least 2 H2 headings

## CRITICAL RULES

- NEVER claim ClaimCoach does things it doesn't do (see PRODUCT CONTEXT)
- BE FACTUALLY ACCURATE about state regulations (see STATE REGULATIONS)
- Use simple language (avoid jargon, long sentences, complex words)
- Include specific numbers, statutes, and examples
- DO NOT guarantee outcomes or make legal claims
- Keep sentences under 20 words on average
- Use active voice

Now write the article.
"""


def write_article(article_id):
    """
    Main function: Write article using Claude API.

    Steps:
    1. Claim article from database
    2. Load reference docs
    3. Build prompt
    4. Call Claude API
    5. Save draft to database
    6. Update status to 'review'

    Args:
        article_id: Article ID from database

    Returns:
        Article content (markdown)
    """
    # Step 1: Claim article
    claim_id = f"quill-{int(time.time())}-{os.urandom(2).hex()}"

    success = claim_article(
        article_id=article_id,
        claim_field='writer_claim',
        claim_id=claim_id
    )

    if not success:
        print(f"Failed to claim article {article_id} - already claimed by another agent")
        return None

    print(f"✓ Claimed article {article_id} with {claim_id}")

    # Step 2: Get article details from database
    with get_db() as db:
        cursor = db.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        )
        article = dict(cursor.fetchone())

    # Step 3: Load reference docs
    refs = load_reference_docs(article.get('target_state'))

    print(f"✓ Loaded reference docs (state: {article.get('target_state') or 'general'})")

    # Step 4: Build prompt
    system_prompt = build_writing_prompt(article, refs)

    # Step 5: Call Claude API (pseudo-code - use your actual API client)
    print(f"✓ Calling Claude API to write article...")

    # Example using Anthropic SDK:
    # import anthropic
    # client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # response = client.messages.create(
    #     model="claude-haiku-4.5-20251001",
    #     max_tokens=4096,
    #     system=system_prompt,
    #     messages=[
    #         {"role": "user", "content": f"Write a complete article about: {article['title']}"}
    #     ]
    # )
    # article_content = response.content[0].text

    # For this example, use placeholder
    article_content = f"# {article['title']}\n\n[Article content would be generated by Claude API here]"

    print(f"✓ Article generated ({len(article_content)} characters)")

    # Step 6: Generate metadata
    meta_title = article['title'][:60]  # Truncate to 60 chars
    meta_description = f"Learn about {article['target_keyword']}."[:160]  # Truncate to 160 chars
    slug = article['title'].lower().replace(' ', '-')[:50]

    # Step 7: Quality gate — same bar as the main Quill pipeline
    wc = word_count(article_content)
    if wc < 1500:
        print(f"✗ Quality gate failed: word count {wc} below minimum 1500")
        with get_db() as db:
            db.execute("""
                UPDATE articles SET
                    markdown_content = ?,
                    status = 'todo',
                    writer_claim = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (article_content, article_id))
        return None

    # Step 8: Save to database
    with get_db() as db:
        db.execute("""
            UPDATE articles SET
                markdown_content = ?,
                meta_title = ?,
                meta_description = ?,
                slug = ?,
                word_count = ?,
                status = 'review',
                writer_claim = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (article_content, meta_title, meta_description, slug, wc, article_id))

    print(f"✓ Saved draft to database (status: review, {wc} words)")

    # Step 8: Log action
    log_agent_action(
        agent_name='quill',
        action='draft_completed',
        article_id=article_id,
        details={
            'word_count': len(article_content.split()),
            'model': 'claude-haiku-4.5',
            'target_state': article.get('target_state')
        }
    )

    print(f"✓ Logged action to agent_log")

    return article_content


def test():
    """Test reference doc loading."""
    print("\n" + "="*60)
    print("Testing Quill Integration")
    print("="*60 + "\n")

    # Test 1: Load general reference docs
    print("Test 1: Loading general reference docs...")
    refs = load_reference_docs()
    assert len(refs['product_context']) > 0
    assert len(refs['state_rules']) > 0
    assert "ClaimCoach" in refs['product_context']
    print("✓ General reference docs loaded\n")

    # Test 2: Load California-specific docs
    print("Test 2: Loading California-specific reference docs...")
    refs_ca = load_reference_docs(target_state="California")
    assert "California" in refs_ca['state_rules']
    assert "ClaimCoach" in refs_ca['product_context']
    print("✓ California reference docs loaded\n")

    # Test 3: Build prompt
    print("Test 3: Building writing prompt...")
    test_article = {
        'title': 'Test Article',
        'target_keyword': 'test keyword',
        'target_state': 'California'
    }
    prompt = build_writing_prompt(test_article, refs_ca)
    assert 'California' in prompt
    assert 'test keyword' in prompt
    assert 'PRODUCT CONTEXT' in prompt
    print("✓ Writing prompt built successfully\n")

    print("="*60)
    print("All tests passed!")
    print("="*60 + "\n")


if __name__ == "__main__":
    import time

    if "--test" in sys.argv:
        test()
    else:
        # Example usage
        # article_id = 47  # Get from command line or database query
        print("This is an integration example. Use --test to run tests.")
        print("\nTo use in production:")
        print("  article = get_article_from_db(article_id)")
        print("  content = write_article(article['id'])")
