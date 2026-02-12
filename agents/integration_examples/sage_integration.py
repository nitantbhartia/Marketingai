"""
Sage Integration Example

Shows how Sage agent calls validation API and processes results.
"""

import os
import sys
import json
import requests
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from content_quality.db import get_db, update_article_validation, log_agent_action


# Validation API URL (use Railway internal hostname in production)
VALIDATION_API_URL = os.environ.get(
    "VALIDATION_API_URL",
    "http://localhost:8000"  # Default for local testing
)


def review_article(article_id):
    """
    Review article by calling quality validation API.

    Steps:
    1. Get article from database
    2. Call validation API
    3. Save validation results
    4. Decide: approve or revise
    5. Update database status

    Args:
        article_id: Article ID from database

    Returns:
        Dict with decision and details
    """
    print(f"\nReviewing article {article_id}...")

    # Step 1: Get article from database
    with get_db() as db:
        cursor = db.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        )
        article = dict(cursor.fetchone())

    if not article:
        print(f"✗ Article {article_id} not found")
        return {"decision": "ERROR", "error": "Article not found"}

    print(f"✓ Found article: {article['title']}")

    # Step 2: Prepare validation request
    validation_request = {
        "article_markdown": article['markdown_content'],
        "target_keyword": article['target_keyword'],
        "meta_title": article['meta_title'],
        "meta_description": article['meta_description'],
        "slug": article['slug'],
        "target_state": article.get('target_state'),
    }

    print(f"✓ Prepared validation request")

    # Step 3: Call validation API
    try:
        print(f"  Calling {VALIDATION_API_URL}/validate/all...")

        response = requests.post(
            f"{VALIDATION_API_URL}/validate/all",
            json=validation_request,
            timeout=30  # Allow up to 30s for link checks
        )
        response.raise_for_status()
        result = response.json()

        print(f"✓ Validation API responded: {result['overall_status']}")

    except requests.exceptions.RequestException as e:
        # API error - log and fail safely
        print(f"✗ Validation API error: {e}")
        log_agent_action(
            agent_name='sage',
            action='validation_api_error',
            article_id=article_id,
            details={'error': str(e)}
        )
        return {
            "decision": "ERROR",
            "error": str(e)
        }

    # Step 4: Save validation results to database
    update_article_validation(
        article_id=article_id,
        validation_status=result['overall_status'],
        seo_score=result['seo_score']['total_score'],
        readability_score=result['readability']['flesch_reading_ease'],
        state_accuracy=result['state_validation']['status'],
        product_compliance=result['product_validation']['status'],
        broken_links_count=count_broken_links(result['links']),
        math_errors_count=len(result['math']['issues']),
        validation_notes=json.dumps(result['revision_notes'])
    )

    print(f"✓ Saved validation results to database")

    # Step 5: Make decision
    if result['overall_status'] == 'PASS':
        # Approve for publishing
        return approve_article(article_id, result)
    else:
        # Send back for revision
        return request_revision(article_id, article, result)


def count_broken_links(links_result):
    """Count broken links from validation result."""
    broken = 0
    for link in links_result.get('internal', []) + links_result.get('external', []):
        if link.get('severity') == 'ERROR':
            broken += 1
    return broken


def approve_article(article_id, validation_result):
    """Approve article for publishing."""
    # Update status to ready_to_publish and release editor claim
    with get_db() as db:
        db.execute("""
            UPDATE articles SET
                status = 'ready_to_publish',
                editor_claim = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (article_id,))

    # Log action
    log_agent_action(
        agent_name='sage',
        action='approved_for_publishing',
        article_id=article_id,
        details={
            'seo_score': validation_result['seo_score']['total_score'],
            'readability_score': validation_result['readability']['flesch_reading_ease']
        }
    )

    print(f"✓ Approved article {article_id} for publishing")
    print(f"  SEO Score: {validation_result['seo_score']['total_score']}/100")
    print(f"  Readability: {validation_result['readability']['flesch_reading_ease']:.1f}/100")

    return {
        "decision": "APPROVED",
        "seo_score": validation_result['seo_score']['total_score'],
        "readability_score": validation_result['readability']['flesch_reading_ease'],
        "summary": validation_result['summary']
    }


def request_revision(article_id, article, validation_result):
    """Request revision from Quill."""
    # Format revision notes
    revision_notes = format_revision_notes(validation_result['revision_notes'])

    # Update database — release both claims so Quill can pick it up
    with get_db() as db:
        db.execute("""
            UPDATE articles SET
                status = 'revision',
                revision_notes = ?,
                revision_count = revision_count + 1,
                writer_claim = '',
                editor_claim = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (revision_notes, article_id))

    # Log action
    log_agent_action(
        agent_name='sage',
        action='rejected_for_revision',
        article_id=article_id,
        details={
            'validation_status': validation_result['overall_status'],
            'issue_count': len(validation_result['revision_notes']),
            'seo_score': validation_result['seo_score']['total_score'],
            'revision_count': article['revision_count'] + 1
        }
    )

    print(f"✗ Requested revision for article {article_id}")
    print(f"  Issues found: {len(validation_result['revision_notes'])}")
    print(f"  SEO Score: {validation_result['seo_score']['total_score']}/100")
    print(f"  Revision count: {article['revision_count'] + 1}")

    return {
        "decision": "REVISION_NEEDED",
        "revision_notes": revision_notes,
        "issue_count": len(validation_result['revision_notes']),
        "seo_score": validation_result['seo_score']['total_score'],
        "summary": validation_result['summary']
    }


def format_revision_notes(notes):
    """Format revision notes for Quill to understand."""
    if not notes:
        return ""

    formatted = "## Revision Required\n\n"
    formatted += "Please fix the following issues:\n\n"

    for i, note in enumerate(notes, 1):
        formatted += f"{i}. {note}\n"

    formatted += "\n**Important:** Only fix the issues listed above. Do not change anything else."

    return formatted


def test():
    """Test Sage integration with mock data."""
    print("\n" + "="*60)
    print("Testing Sage Integration")
    print("="*60 + "\n")

    # Test 1: Test validation API connection
    print("Test 1: Testing validation API connection...")
    try:
        response = requests.get(f"{VALIDATION_API_URL}/health", timeout=5)
        if response.status_code == 200:
            print(f"✓ Validation API is healthy at {VALIDATION_API_URL}\n")
        else:
            print(f"⚠ Validation API returned status {response.status_code}\n")
    except requests.exceptions.RequestException as e:
        print(f"✗ Could not connect to validation API: {e}")
        print(f"  Make sure API server is running: python api_server.py\n")
        return

    # Test 2: Test validation with good article
    print("Test 2: Testing validation with good article...")
    good_article = {
        "article_markdown": """
# How to Check Your Total Loss Settlement in California

Getting a fair settlement is important. ClaimCoach analyzes your offer in under 5 minutes.

## What Is a Total Loss?

A total loss happens when repairs cost too much. California uses the Total Loss Formula (TLF).

## How ClaimCoach Helps

ClaimCoach helps you identify missing line items. We show you what insurers commonly leave off.

[Try ClaimCoach](https://claimcoach.app)

## FAQ

### How long does it take?
ClaimCoach analyzes in under 5 minutes.

### What if I find issues?
Contact your adjuster to discuss the gaps we identified.

### Does ClaimCoach negotiate?
No, we help you identify gaps so you can discuss them.
""",
        "target_keyword": "total loss settlement california",
        "meta_title": "How to Check Your Total Loss Settlement in California",
        "meta_description": "Learn how to check if your California total loss settlement is fair. ClaimCoach helps identify missing items.",
        "slug": "california-total-loss-settlement",
        "target_state": "California"
    }

    response = requests.post(
        f"{VALIDATION_API_URL}/validate/all",
        json=good_article,
        timeout=30
    )

    if response.status_code == 200:
        result = response.json()
        print(f"✓ Validation completed: {result['overall_status']}")
        print(f"  SEO Score: {result['seo_score']['total_score']}/100")
        print(f"  Summary: {result['summary']}\n")
    else:
        print(f"✗ Validation failed with status {response.status_code}\n")

    print("="*60)
    print("Tests completed!")
    print("="*60 + "\n")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    else:
        print("This is an integration example. Use --test to run tests.")
        print("\nTo use in production:")
        print("  result = review_article(article_id)")
        print("  if result['decision'] == 'APPROVED':")
        print("      # Ezra can publish")
