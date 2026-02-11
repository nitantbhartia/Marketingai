"""
Shared configuration for ClaimCoach content quality and SEO automation.
"""

import os
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = PROJECT_ROOT / "reference"

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)

# SQLite (local database — co-located on Railway)
DATABASE_PATH = os.environ.get("DATABASE_PATH", "pipeline.db")

# Blog publishing (static files)
BLOG_OUTPUT_DIR = os.environ.get("BLOG_OUTPUT_DIR", "./blog")
SITE_URL = os.environ.get("SITE_URL", "https://claimcoach.app")

# Google Search Console
GOOGLE_SEARCH_CONSOLE_CREDENTIALS = os.environ.get("GSC_CREDENTIALS_JSON", "")

# Reference docs (mounted or stored in repo)
PRODUCT_CONTEXT_PATH = str(REFERENCE_DIR / "PRODUCT_CONTEXT.md")
STATE_RULES_PATH = str(REFERENCE_DIR / "STATE_RULES.md")

# Validation thresholds
MIN_READABILITY_SCORE = int(os.environ.get("MIN_READABILITY_SCORE", "60"))  # Flesch-Kincaid
MIN_SEO_SCORE = int(os.environ.get("MIN_SEO_SCORE", "80"))  # out of 100
MIN_WORD_COUNT = int(os.environ.get("MIN_WORD_COUNT", "1800"))
MAX_WORD_COUNT = int(os.environ.get("MAX_WORD_COUNT", "2200"))
MIN_SPECIFICITY_ITEMS = int(os.environ.get("MIN_SPECIFICITY_ITEMS", "5"))  # dollar amounts, statute citations
MIN_FAQ_QUESTIONS = int(os.environ.get("MIN_FAQ_QUESTIONS", "3"))
META_DESC_MIN_LENGTH = int(os.environ.get("META_DESC_MIN_LENGTH", "140"))
META_DESC_MAX_LENGTH = int(os.environ.get("META_DESC_MAX_LENGTH", "160"))

# Link checking
LINK_CHECK_TIMEOUT = int(os.environ.get("LINK_CHECK_TIMEOUT", "10"))  # seconds
MAX_RETRY_ATTEMPTS = int(os.environ.get("MAX_RETRY_ATTEMPTS", "3"))

# Content refresh thresholds
STALE_CONTENT_DAYS = int(os.environ.get("STALE_CONTENT_DAYS", "90"))
VERY_STALE_CONTENT_DAYS = int(os.environ.get("VERY_STALE_CONTENT_DAYS", "180"))
CANNIBALIZATION_THRESHOLD = float(os.environ.get("CANNIBALIZATION_THRESHOLD", "0.7"))

# API server
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
