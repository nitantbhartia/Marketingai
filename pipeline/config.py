"""Configuration management for the content pipeline."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml


def resolve_generation_pause(
    db=None, pipeline_settings=None
) -> Dict[str, Any]:
    """Resolve generation pause state from env -> DB -> config.

    Shared by BaseAgent.generation_paused() and the dashboard server so the
    resolution logic lives in exactly one place.

    Returns {"paused": bool, "source": str}.
    """
    env_val = os.getenv("PIPELINE_PAUSE_GENERATION")
    if env_val is not None:
        paused = env_val.strip().lower() in {"1", "true", "yes", "on"}
        return {"paused": paused, "source": "env"}

    if db is not None:
        try:
            rows = db.get_metrics(name="pipeline_pause", limit=1)
            if rows:
                row = rows[0]
                paused = bool(float(row.metric_value or 0.0) > 0.0)
                details = row.details or ""
                if details:
                    try:
                        payload = json.loads(details)
                        if "paused" in payload:
                            paused = bool(payload.get("paused"))
                    except Exception:
                        pass
                return {"paused": paused, "source": "dashboard_override"}
        except Exception:
            pass

    config_val = bool(
        getattr(pipeline_settings, "pause_generation", False)
    ) if pipeline_settings else True
    return {"paused": config_val, "source": "config"}


@dataclass
class AnthropicConfig:
    api_key: str = ""
    # Per-agent model assignments (cost-optimized defaults)
    # Quill (writer): Sonnet for quality long-form content
    quill_model: str = "claude-sonnet-4-5-20250929"
    # Sage (reviewer): Sonnet for better reasoning on quality checks
    sage_model: str = "claude-sonnet-4-5-20250929"
    # Scout (research): Haiku — structured keyword processing
    scout_model: str = "claude-haiku-4-5-20251001"
    # Morgan (PM): Haiku — pipeline monitoring is structured logic
    morgan_model: str = "claude-haiku-4-5-20251001"
    # Herald (social): Haiku — social post drafting is lightweight
    herald_model: str = "claude-haiku-4-5-20251001"
    # Lurker (community): Haiku — Reddit scanning and response drafting
    lurker_model: str = "claude-haiku-4-5-20251001"


@dataclass
class GeminiConfig:
    api_key: str = ""
    default_model: str = "gemini-2.5-flash"
    # Pro model for high-trust tasks: outlines, briefs, fact checks (100 RPD)
    strategy_model: str = "gemini-2.5-pro"
    # Flash-Lite for repetitive utility tasks: meta descriptions, alt-text (1000 RPD)
    utility_model: str = "gemini-2.5-flash-lite"
    # Per-agent model assignments (default_model = bulk/volume work)
    quill_model: str = "gemini-2.5-flash"
    sage_model: str = "gemini-2.5-flash"
    scout_model: str = "gemini-2.5-flash"
    morgan_model: str = "gemini-2.5-flash"
    herald_model: str = "gemini-2.5-flash"
    lurker_model: str = "gemini-2.5-flash"
    # Daily request budgets per tier (paid tier — effectively unlimited)
    pro_daily_budget: int = 1000
    flash_daily_budget: int = 10000
    flash_lite_daily_budget: int = 10000
    # Minimum seconds between API calls (paid tier = 2000 RPM)
    rate_limit_delay: float = 0.5
    # Enable Google Search grounding for Pro calls (insurance law freshness)
    search_grounding: bool = True


@dataclass
class SerpConfig:
    """SERP analysis configuration for real-time search data."""
    # SerpAPI key (paid, ~$50/mo for 5k queries)
    serpapi_key: str = ""
    # Google Custom Search Engine (free, 100 queries/day)
    google_cse_key: str = ""
    google_cse_id: str = ""
    # Enable/disable SERP analysis (autocomplete always runs as free fallback)
    enabled: bool = True


@dataclass
class ImageConfig:
    """Image resolution configuration for converting placeholders to real URLs."""
    # Unsplash API key (optional — Source API works without it for low volume)
    unsplash_access_key: str = ""
    # Image dimensions (optimized for OG/social sharing)
    width: int = 1200
    height: int = 630
    # Cache resolved URLs to avoid re-fetching
    cache_dir: str = ".image_cache"
    # Enable/disable image resolution entirely
    enabled: bool = True


@dataclass
class BlogConfig:
    """Static blog publishing configuration."""
    output_dir: str = "./blog"
    site_url: str = "https://claimcoach.app"


@dataclass
class CopyscapeConfig:
    api_key: str = ""
    username: str = ""


@dataclass
class RedditConfig:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""


@dataclass
class TwitterConfig:
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    access_token_secret: str = ""


@dataclass
class PipelineSettings:
    pause_generation: bool = True
    database_path: str = "pipeline.db"
    product_context_path: str = "reference/PRODUCT_CONTEXT.md"
    medbill_product_context_path: str = "reference/MEDBILL_PRODUCT_CONTEXT.md"
    state_rules_path: str = "reference/STATE_RULES.md"
    seo_template_path: str = "reference/SEO_ARTICLE_TEMPLATE.md"
    blog_output_dir: str = "output/blog"
    min_backlog_topics: int = 15
    articles_per_week_target: int = 7
    max_articles_per_run: int = 3
    daily_article_cap: int = 8
    daily_article_cap_by_product: dict[str, int] = field(default_factory=dict)
    daily_promote_cap: int = 8
    max_word_count_by_product: dict[str, int] = field(
        default_factory=lambda: {"medbill": 2500}
    )
    min_word_count: int = 1200
    min_word_count_by_product: dict[str, int] = field(default_factory=dict)
    products: list[str] = field(default_factory=lambda: ["claimcoach", "medbill"])
    max_revision_rounds: int = 5
    sage_min_score_gain_for_revision: float = 3.0
    sage_low_progress_rounds: int = 2
    sage_low_progress_route: str = "review"
    quill_stale_recovery_hours: int = 4
    approval_score_threshold: int = 80
    dashboard_url: str = ""
    # Cost-control switches
    quill_use_strategy_on_high_trust_only: bool = True
    quill_enable_contrastive_critique: bool = False
    sage_deep_fact_check_on_high_trust_only: bool = True
    quill_use_fact_pack: bool = True
    quill_use_winner_memory: bool = True
    scout_enable_intent_templates: bool = True
    scout_enable_cluster_map: bool = True
    ezra_strict_publish_gate: bool = True
    refresh_age_days: int = 30
    refresh_position_threshold: float = 12.0
    refresh_clicks_threshold: int = 3
    monthly_refresh_cap: int = 8
    quill_article_call_cap: int = 8
    quill_article_cost_cap_usd: float = 0.40
    quill_daily_spend_guard_usd: float = 2.0
    quill_daily_guard_requires_zero_approvals: bool = True


@dataclass
class ScheduleConfig:
    scout: str = "0 */6 * * *"
    quill: str = "0 * * * *"
    sage: str = "30 * * * *"
    ezra: str = "0 */4 * * *"
    herald: str = "0 10,18 * * *"
    lurker: str = "0 */8 * * *"
    morgan: str = "0 7,13,19 * * *"
    atlas: str = "0 6 * * *"
    rival: str = "0 3 * * 1"
    remix: str = "0 12 * * *"


@dataclass
class AtlasConfig:
    enabled: bool = True
    min_articles_for_analysis: int = 10
    min_confidence_score: float = 0.7


@dataclass
class RivalConfig:
    enabled: bool = True
    competitor_domains: list[str] = field(default_factory=list)
    target_keywords: list[str] = field(default_factory=list)
    max_competitor_checks: int = 20


@dataclass
class RemixConfig:
    enabled: bool = True
    remix_types: list[str] = field(
        default_factory=lambda: ["twitter", "linkedin", "email", "youtube"]
    )
    max_remixes_per_run: int = 3


@dataclass
class Config:
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    serp: SerpConfig = field(default_factory=SerpConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    blog: BlogConfig = field(default_factory=BlogConfig)
    copyscape: CopyscapeConfig = field(default_factory=CopyscapeConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    atlas: AtlasConfig = field(default_factory=AtlasConfig)
    rival: RivalConfig = field(default_factory=RivalConfig)
    remix: RemixConfig = field(default_factory=RemixConfig)
    # Which LLM provider to use: "anthropic" or "gemini"
    llm_provider: str = "anthropic"
    # Per-agent provider overrides: e.g. {"sage": "anthropic"} to use a
    # different provider for a specific agent. Empty = all use llm_provider.
    agent_provider_overrides: dict = field(default_factory=dict)
    _base_dir: Path = field(default_factory=lambda: Path.cwd())

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load configuration from YAML file with env var overrides."""
        if config_path is None:
            config_path = Path.cwd() / "config.yaml"
        else:
            config_path = Path(config_path)

        cfg = cls()
        cfg._base_dir = config_path.parent

        if config_path.exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            cfg._apply_dict(raw)

        # Environment variable overrides
        cfg.anthropic.api_key = (
            os.environ.get("ANTHROPIC_API_KEY") or cfg.anthropic.api_key
        )
        cfg.pipeline.database_path = (
            os.environ.get("DATABASE_PATH") or cfg.pipeline.database_path
        )
        cfg.blog.output_dir = (
            os.environ.get("BLOG_OUTPUT_DIR") or cfg.blog.output_dir
        )
        cfg.blog.site_url = (
            os.environ.get("SITE_URL") or cfg.blog.site_url
        )
        cfg.copyscape.api_key = (
            os.environ.get("COPYSCAPE_API_KEY") or cfg.copyscape.api_key
        )
        cfg.reddit.client_id = (
            os.environ.get("REDDIT_CLIENT_ID") or cfg.reddit.client_id
        )
        cfg.reddit.client_secret = (
            os.environ.get("REDDIT_CLIENT_SECRET") or cfg.reddit.client_secret
        )
        cfg.twitter.api_key = (
            os.environ.get("TWITTER_API_KEY") or cfg.twitter.api_key
        )
        cfg.gemini.api_key = (
            os.environ.get("GEMINI_API_KEY") or cfg.gemini.api_key
        )
        cfg.images.unsplash_access_key = (
            os.environ.get("UNSPLASH_ACCESS_KEY") or cfg.images.unsplash_access_key
        )
        cfg.serp.serpapi_key = (
            os.environ.get("SERPAPI_KEY") or cfg.serp.serpapi_key
        )
        cfg.serp.google_cse_key = (
            os.environ.get("GOOGLE_CSE_KEY") or cfg.serp.google_cse_key
        )
        cfg.serp.google_cse_id = (
            os.environ.get("GOOGLE_CSE_ID") or cfg.serp.google_cse_id
        )
        cfg.llm_provider = (
            os.environ.get("LLM_PROVIDER") or cfg.llm_provider
        )

        # Auto-detect provider when not explicitly set: if only one key
        # is configured, use that provider instead of defaulting to Anthropic.
        if not os.environ.get("LLM_PROVIDER"):
            if not cfg.anthropic.api_key and cfg.gemini.api_key:
                cfg.llm_provider = "gemini"
            elif cfg.anthropic.api_key and not cfg.gemini.api_key:
                cfg.llm_provider = "anthropic"

        return cfg

    def _apply_dict(self, raw: dict) -> None:
        """Apply a raw config dict to dataclass fields."""
        section_map = {
            "anthropic": self.anthropic,
            "gemini": self.gemini,
            "serp": self.serp,
            "images": self.images,
            "blog": self.blog,
            "copyscape": self.copyscape,
            "reddit": self.reddit,
            "twitter": self.twitter,
            "pipeline": self.pipeline,
            "schedule": self.schedule,
            "atlas": self.atlas,
            "rival": self.rival,
            "remix": self.remix,
        }
        if "llm_provider" in raw:
            self.llm_provider = raw["llm_provider"]
        if "agent_provider_overrides" in raw and isinstance(
            raw["agent_provider_overrides"], dict
        ):
            self.agent_provider_overrides = raw["agent_provider_overrides"]
        for section_name, section_obj in section_map.items():
            if section_name in raw and isinstance(raw[section_name], dict):
                for key, value in raw[section_name].items():
                    if hasattr(section_obj, key):
                        setattr(section_obj, key, value)

    def resolve_path(self, relative: str) -> Path:
        """Resolve a path relative to the config file's directory."""
        p = Path(relative)
        if p.is_absolute():
            return p
        return self._base_dir / p

    def load_product_context(self, product: str | None = None) -> str:
        """Load PRODUCT_CONTEXT.md content."""
        normalized = (product or "claimcoach").strip().lower()
        path_str = self.pipeline.product_context_path
        if normalized == "medbill":
            path_str = getattr(
                self.pipeline,
                "medbill_product_context_path",
                "reference/MEDBILL_PRODUCT_CONTEXT.md",
            )
        path = self.resolve_path(path_str)
        if normalized == "medbill" and not path.exists():
            sibling = self.resolve_path("../Medbill/reference/PRODUCT_CONTEXT.md")
            if sibling.exists():
                path = sibling
        if path.exists():
            return path.read_text()
        return ""

    def load_state_rules(self) -> str:
        """Load STATE_RULES.md content."""
        path = self.resolve_path(self.pipeline.state_rules_path)
        if path.exists():
            return path.read_text()
        return ""

    def load_seo_template(self) -> str:
        """Load SEO article template guidance."""
        path = self.resolve_path(self.pipeline.seo_template_path)
        if path.exists():
            return path.read_text()
        return ""
