"""
API Server for ClaimCoach Content Quality Pre-Publish Gates

Exposes endpoints for all validation checks.
Called by Sage agent before approving articles for publication.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import uvicorn

from content_quality.config import API_HOST, API_PORT
from content_quality.validators.product_validator import validate_product_claims
from content_quality.validators.state_validator import validate_state_rules
from content_quality.validators.seo_scorer import score_seo
from content_quality.validators.readability_analyzer import analyze_readability
from content_quality.validators.link_checker import check_links
from content_quality.validators.math_validator import validate_math
from content_quality.db import init_database

# Initialize FastAPI app
app = FastAPI(
    title="ClaimCoach Content Quality API",
    description="Pre-publish validation gates for content quality and SEO",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request models
class ArticleInput(BaseModel):
    """Input model for article validation."""
    article_markdown: str
    target_keyword: str
    meta_title: str
    meta_description: str
    slug: str
    target_state: Optional[str] = None


class ValidationResult(BaseModel):
    """Output model for validation results."""
    overall_status: str  # "PASS" or "FAIL"
    state_validation: dict
    product_validation: dict
    seo_score: dict
    readability: dict
    links: dict
    math: dict
    summary: str
    revision_notes: List[str]


# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_database()


# Health check endpoint
@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "ClaimCoach Content Quality API",
        "version": "1.0.0"
    }


@app.get("/health")
async def health_check():
    """Detailed health check."""
    return {
        "status": "healthy",
        "validators": {
            "product_claim": "ready",
            "state_regulation": "ready",
            "seo_scorer": "ready",
            "readability": "ready",
            "link_checker": "ready",
            "math_validator": "ready",
        }
    }


# Individual validation endpoints
@app.post("/validate/state-rules")
async def validate_state_endpoint(article: ArticleInput):
    """Validate state-specific regulations."""
    try:
        result = validate_state_rules(article.article_markdown, article.target_state)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/product-claims")
async def validate_product_endpoint(article: ArticleInput):
    """Validate product claims."""
    try:
        result = validate_product_claims(article.article_markdown)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/seo")
async def validate_seo_endpoint(article: ArticleInput):
    """Score SEO optimization."""
    try:
        result = score_seo({
            "article_markdown": article.article_markdown,
            "target_keyword": article.target_keyword,
            "meta_title": article.meta_title,
            "meta_description": article.meta_description,
            "slug": article.slug,
        })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/readability")
async def validate_readability_endpoint(article: ArticleInput):
    """Analyze readability."""
    try:
        result = analyze_readability(article.article_markdown)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/links")
async def validate_links_endpoint(article: ArticleInput):
    """Check all links."""
    try:
        # Note: Ghost API client could be passed here if available
        result = check_links(article.article_markdown, ghost_api_client=None)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/math")
async def validate_math_endpoint(article: ArticleInput):
    """Validate mathematical claims."""
    try:
        result = validate_math(article.article_markdown)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate/all", response_model=ValidationResult)
async def validate_all_endpoint(article: ArticleInput):
    """
    Run all 6 pre-publish validation checks.

    This is the primary endpoint Sage calls before approving an article.
    Returns combined result with per-system breakdown.
    """
    try:
        # Run all validators
        state_result = validate_state_rules(article.article_markdown, article.target_state)
        product_result = validate_product_claims(article.article_markdown)
        seo_result = score_seo({
            "article_markdown": article.article_markdown,
            "target_keyword": article.target_keyword,
            "meta_title": article.meta_title,
            "meta_description": article.meta_description,
            "slug": article.slug,
        })
        readability_result = analyze_readability(article.article_markdown)
        link_result = check_links(article.article_markdown, ghost_api_client=None)
        math_result = validate_math(article.article_markdown)

        # Overall pass/fail
        all_passed = all([
            state_result["status"] in ["PASS", "WARN"],  # WARN is acceptable for state check
            product_result["status"] == "PASS",
            seo_result["status"] == "PASS",
            readability_result["status"] == "PASS",
            link_result["status"] == "PASS",
            math_result["status"] == "PASS",
        ])

        # Compile revision notes for Quill if any check failed
        revision_notes = []

        # State validation errors
        if state_result["status"] == "FAIL":
            for issue in state_result.get("issues", []):
                if issue.get("severity") == "ERROR":
                    revision_notes.append(f"STATE ERROR: {issue['message']}")

        # Product claim violations
        if product_result["status"] != "PASS":
            for violation in product_result.get("hard_violations", []):
                revision_notes.append(
                    f"PRODUCT CLAIM VIOLATION ({violation['pattern_category']}): "
                    f"{violation['matched_text']} → {violation['suggestion']}"
                )

        # SEO issues
        if seo_result["status"] != "PASS":
            for suggestion in seo_result.get("suggestions", [])[:5]:  # Top 5
                revision_notes.append(f"SEO: {suggestion}")

        # Readability issues
        if readability_result["status"] != "PASS":
            for issue in readability_result.get("issues", [])[:3]:  # Top 3
                revision_notes.append(f"READABILITY: {issue.get('fix', 'Improve readability')}")

        # Broken links
        if link_result["status"] != "PASS":
            for link in link_result.get("internal", []) + link_result.get("external", []):
                if link.get("severity") == "ERROR":
                    revision_notes.append(f"BROKEN LINK: {link['url']} — {link['message']}")

        # Math errors
        if math_result["status"] != "PASS":
            for issue in math_result.get("issues", [])[:5]:  # Top 5
                revision_notes.append(f"MATH ERROR: {issue['message']}")

        # Build summary
        failure_count = len(revision_notes)
        seo_score = seo_result.get("total_score", 0)
        readability_score = readability_result.get("flesch_reading_ease", 0)

        if all_passed:
            summary = (
                f"✓ PASSED all checks. "
                f"SEO: {seo_score}/100, "
                f"Readability: {readability_score:.0f}/100"
            )
        else:
            summary = (
                f"✗ FAILED — {failure_count} issues found. "
                f"SEO: {seo_score}/100, "
                f"Readability: {readability_score:.0f}/100"
            )

        return ValidationResult(
            overall_status="PASS" if all_passed else "FAIL",
            state_validation=state_result,
            product_validation=product_result,
            seo_score=seo_result,
            readability=readability_result,
            links=link_result,
            math=math_result,
            summary=summary,
            revision_notes=revision_notes,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation error: {str(e)}")


# Entry point for running the server
if __name__ == "__main__":
    print(f"Starting ClaimCoach Content Quality API on {API_HOST}:{API_PORT}")
    print(f"API docs available at http://{API_HOST}:{API_PORT}/docs")

    uvicorn.run(
        "api_server:app",
        host=API_HOST,
        port=API_PORT,
        reload=True,  # Enable auto-reload in development
        log_level="info"
    )
