"""Configuration management for the content pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
    # Per-agent model assignments — all Flash for single-tier usage
    quill_model: str = "gemini-2.5-flash"
    sage_model: str = "gemini-2.5-flash"
    scout_model: str = "gemini-2.5-flash"
    morgan_model: str = "gemini-2.5-flash"
    herald_model: str = "gemini-2.5-flash"
    lurker_model: str = "gemini-2.5-flash"
    # Minimum seconds between API calls (free tier = 5 RPM → 12s)
    rate_limit_delay: float = 12.0


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
    database_path: str = "pipeline.db"
    product_context_path: str = "PRODUCT_CONTEXT.md"
    state_rules_path: str = "STATE_RULES.md"
    blog_output_dir: str = "output/blog"
    min_backlog_topics: int = 15
    articles_per_week_target: int = 7
    max_articles_per_run: int = 3
    max_revision_rounds: int = 5
    approval_score_threshold: int = 90
    dashboard_url: str = ""


@dataclass
class ScheduleConfig:
    scout: str = "0 */6 * * *"
    quill: str = "0 * * * *"
    sage: str = "30 * * * *"
    ezra: str = "0 */4 * * *"
    herald: str = "0 10,18 * * *"
    lurker: str = "0 */8 * * *"
    morgan: str = "0 7,13,19 * * *"


@dataclass
class Config:
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    blog: BlogConfig = field(default_factory=BlogConfig)
    copyscape: CopyscapeConfig = field(default_factory=CopyscapeConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
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
        cfg.llm_provider = (
            os.environ.get("LLM_PROVIDER") or cfg.llm_provider
        )

        return cfg

    def _apply_dict(self, raw: dict) -> None:
        """Apply a raw config dict to dataclass fields."""
        section_map = {
            "anthropic": self.anthropic,
            "gemini": self.gemini,
            "blog": self.blog,
            "copyscape": self.copyscape,
            "reddit": self.reddit,
            "twitter": self.twitter,
            "pipeline": self.pipeline,
            "schedule": self.schedule,
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

    def load_product_context(self) -> str:
        """Load PRODUCT_CONTEXT.md content."""
        path = self.resolve_path(self.pipeline.product_context_path)
        if path.exists():
            return path.read_text()
        return ""

    def load_state_rules(self) -> str:
        """Load STATE_RULES.md content."""
        path = self.resolve_path(self.pipeline.state_rules_path)
        if path.exists():
            return path.read_text()
        return ""
