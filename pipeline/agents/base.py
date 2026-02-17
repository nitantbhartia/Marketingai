"""Base agent class with common functionality."""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pipeline.config import Config
from pipeline.db import Database

# Module-level rate limiter shared across all agents in the same process.
_gemini_lock = threading.Lock()
_gemini_last_call: float = 0.0

# --------------------------------------------------------------------------
# Daily budget tracker — prevents burning through rate-limited Pro tier
# --------------------------------------------------------------------------
_budget_lock = threading.Lock()
_budget_counters: dict[str, int] = {}  # "pro:2026-02-12" → call count
_TIER_LIMITS: dict[str, str] = {
    "gemini-2.5-pro": "pro_daily_budget",
    "gemini-2.5-flash": "flash_daily_budget",
    "gemini-2.5-flash-lite": "flash_lite_daily_budget",
}

# --------------------------------------------------------------------------
# Token-per-minute (TPM) tracker — prevents 429s on the 250k TPM limit
# --------------------------------------------------------------------------
_token_lock = threading.Lock()
_token_window: list[tuple[float, int]] = []  # (timestamp, token_count) entries
_TPM_LIMIT = 4_000_000  # Gemini paid tier TPM limit
_TPM_WINDOW = 60.0      # 60-second sliding window
_TPM_PARK_SECONDS = 5.0  # How long to park when approaching limit


def _budget_key(model: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{model}:{today}"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


def _record_tokens(count: int) -> None:
    """Record token usage in the sliding window."""
    now = time.time()
    with _token_lock:
        _token_window.append((now, count))
        # Prune entries older than the window
        cutoff = now - _TPM_WINDOW
        while _token_window and _token_window[0][0] < cutoff:
            _token_window.pop(0)


def _tokens_in_window() -> int:
    """Get total tokens used in the current sliding window."""
    now = time.time()
    cutoff = now - _TPM_WINDOW
    with _token_lock:
        # Prune stale entries
        while _token_window and _token_window[0][0] < cutoff:
            _token_window.pop(0)
        return sum(count for _, count in _token_window)


class RateLimitError(Exception):
    """Raised when LLM API returns 429 after exhausting retries.

    Callers should catch this to distinguish transient rate limits from
    real failures — articles should NOT be bounced to revision on rate
    limits because the content hasn't changed.
    """
    pass


class BaseAgent(ABC):
    """Base class for all pipeline agents."""

    name: str = "base"
    claim_field: str | None = None  # Which claim field this agent uses

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.logger = logging.getLogger(f"pipeline.{self.name}")

    @property
    def provider(self) -> str:
        """Get this agent's LLM provider, respecting per-agent overrides.

        Priority:
          1. Per-agent override (config.agent_provider_overrides)
          2. Global llm_provider setting
          3. Auto-detect from available API keys — if the configured
             provider has no key but the other does, fall back.
        """
        overrides = getattr(self.config, "agent_provider_overrides", {})
        chosen = overrides.get(
            self.name, getattr(self.config, "llm_provider", "anthropic")
        )

        # Auto-detect: if chosen provider has no key, try the other
        if chosen == "anthropic" and not self.config.anthropic.api_key:
            if self.config.gemini.api_key:
                return "gemini"
        elif chosen == "gemini" and not self.config.gemini.api_key:
            if self.config.anthropic.api_key:
                return "anthropic"

        return chosen

    @property
    def default_model(self) -> str:
        """Get this agent's configured model, provider-aware.

        When using Gemini, returns the Gemini model for this agent
        (e.g. gemini-2.5-pro for Quill). When using Anthropic, returns
        the Anthropic model (e.g. claude-sonnet for Sage).
        """
        model_attr = f"{self.name}_model"
        if self.provider == "gemini":
            return getattr(
                self.config.gemini, model_attr, self.config.gemini.default_model
            )
        return getattr(self.config.anthropic, model_attr, "claude-haiku-4-5-20251001")

    @property
    def fast_model(self) -> str:
        """Cheaper/faster model for bulk drafting and lightweight tasks.

        Always returns the fast tier: Gemini Flash or Claude Haiku.
        """
        if self.provider == "gemini":
            return self.config.gemini.default_model  # flash
        return "claude-haiku-4-5-20251001"

    @property
    def strategy_model(self) -> str:
        """Stronger model for high-trust tasks: outlines, briefs, fact checks.

        Returns Gemini Pro or Claude Sonnet — used where E-E-A-T quality
        matters more than speed.
        """
        if self.provider == "gemini":
            return getattr(
                self.config.gemini, "strategy_model", "gemini-2.5-pro"
            )
        # Use each Anthropic agent's configured model (e.g. sage_model for Sage)
        return self.default_model

    @property
    def utility_model(self) -> str:
        """Cheapest model for repetitive utility tasks: meta descriptions, alt-text.

        Returns Gemini Flash-Lite (1000 RPD) or Claude Haiku.
        """
        if self.provider == "gemini":
            return getattr(
                self.config.gemini, "utility_model", "gemini-2.5-flash-lite"
            )
        return "claude-haiku-4-5-20251001"

    @property
    def has_llm(self) -> bool:
        """True if any LLM API key is configured (Anthropic or Gemini)."""
        return bool(self.config.anthropic.api_key or self.config.gemini.api_key)

    def _check_budget(self, model: str) -> bool:
        """Check if we have remaining budget for this model tier today.

        Returns True if the call is allowed, False if budget exhausted.
        When Pro budget is exhausted, callers should fall back to Flash.
        """
        budget_attr = _TIER_LIMITS.get(model)
        if not budget_attr:
            return True  # Unknown model — allow

        limit = getattr(self.config.gemini, budget_attr, 9999)
        key = _budget_key(model)

        with _budget_lock:
            return _budget_counters.get(key, 0) < limit

    def _record_budget_usage(self, model: str) -> None:
        """Record one API call against the daily budget for this model."""
        key = _budget_key(model)
        with _budget_lock:
            _budget_counters[key] = _budget_counters.get(key, 0) + 1

    @abstractmethod
    def run(self) -> dict[str, Any]:
        """Execute the agent's main task. Returns a summary dict."""
        ...

    def generate_claim_id(self) -> str:
        """Generate a unique claim ID for claim locking."""
        ts = int(time.time())
        rand = uuid.uuid4().hex[:6]
        return f"{self.name}-{ts}-{rand}"

    def try_claim_article(self, article_id: int, new_status: str) -> bool:
        """Attempt to claim an article using this agent's claim field."""
        if self.claim_field is None:
            raise ValueError(f"Agent {self.name} has no claim_field defined")
        claim_id = self.generate_claim_id()
        return self.db.try_claim(article_id, self.claim_field, claim_id, new_status)

    def pick_and_claim(self, from_status: str, to_status: str) -> int | None:
        """Pick the highest-priority unclaimed article from a status and claim it.

        Returns article ID if successful, None otherwise.
        """
        if self.claim_field is None:
            raise ValueError(f"Agent {self.name} has no claim_field defined")

        order_by = "created_at ASC"
        if from_status == "todo":
            # Highest ROI first: priority refreshes, high intent, high volume,
            # then easier keywords.
            order_by = (
                "CASE refresh_priority "
                "WHEN 'HIGH' THEN 0 "
                "WHEN 'MEDIUM' THEN 1 "
                "WHEN 'LOW' THEN 2 "
                "ELSE 3 END, "
                "commercial_intent DESC, "
                "search_volume DESC, "
                "keyword_difficulty ASC, "
                "created_at ASC"
            )
        elif from_status == "revision":
            # Finish articles already close to done.
            order_by = "revision_count DESC, updated_at ASC"

        articles = self.db.query_articles(
            status=from_status,
            writer_claim_empty=(self.claim_field == "writer_claim"),
            limit=50,
            order_by=order_by,
        )
        if not articles:
            self.logger.info(f"No articles in '{from_status}' status")
            return None

        for article in articles:
            claim_val = getattr(article, self.claim_field, "")
            if claim_val and claim_val != "":
                continue
            if self.try_claim_article(article.id, to_status):
                self.logger.info(f"Claimed article {article.id}: {article.title}")
                return article.id

        self.logger.info(f"Failed to claim any article from '{from_status}'")
        return None

    def record_lesson(self, target_agent: str, category: str, lesson: str) -> None:
        """Store a lesson for another agent to learn from."""
        self.db.upsert_lesson(self.name, target_agent, category, lesson)

    def get_lessons_for_me(
        self, category: str | None = None, min_confidence: float = 0.0
    ) -> list:
        """Retrieve lessons targeted at this agent."""
        return self.db.get_lessons(self.name, category, min_confidence)

    def call_claude(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> str:
        """Call LLM API (Anthropic or Gemini) and return the text response.

        Respects per-agent provider overrides — e.g. Sage uses Anthropic
        Sonnet for reviews even when the global provider is Gemini.

        Args:
            temperature: Sampling temperature (0.0-2.0). Higher values make
                output more creative/varied. For SEO writing that feels human,
                use 0.8-0.9. For structured/analytical tasks, use 0.2-0.4.
                None uses the provider's default.
        """
        if self.provider == "gemini":
            return self._call_gemini(prompt, system, model, max_tokens, temperature)
        return self._call_anthropic(prompt, system, model, max_tokens, temperature)

    def _call_anthropic(
        self, prompt: str, system: str, model: str | None, max_tokens: int,
        temperature: float | None = None,
    ) -> str:
        import anthropic

        api_key = self.config.anthropic.api_key or None
        client = anthropic.Anthropic(api_key=api_key)
        model = model or self.default_model

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature

        self.logger.debug(f"Calling Claude ({model}), prompt length={len(prompt)}")

        max_retries = 3
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = client.messages.create(**kwargs)
                text = response.content[0].text
                self.logger.debug(f"Claude response length={len(text)}")
                return text
            except anthropic.RateLimitError as e:
                last_error = e
                wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                self.logger.warning(
                    f"Anthropic 429 on attempt {attempt + 1}/{max_retries}, "
                    f"retrying in {wait:.1f}s — {e}"
                )
                time.sleep(wait)
                continue
            except anthropic.APIStatusError as e:
                if e.status_code in (500, 503, 529):
                    last_error = e
                    wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                    self.logger.warning(
                        f"Anthropic {e.status_code} on attempt {attempt + 1}/{max_retries}, "
                        f"retrying in {wait:.1f}s — {e}"
                    )
                    time.sleep(wait)
                    continue
                raise

        # Exhausted retries — only raise RateLimitError if the actual
        # error was a rate limit; server errors (500/503/529) should
        # propagate as-is so callers don't confuse them with rate limits.
        if isinstance(last_error, anthropic.RateLimitError):
            self.logger.error(
                f"Anthropic rate limit exhausted after {max_retries} attempts"
            )
            raise RateLimitError(
                f"Anthropic API rate limited after {max_retries} retries: {last_error}"
            ) from last_error
        raise last_error  # type: ignore[misc]

    def _call_gemini(
        self, prompt: str, system: str, model: str | None, max_tokens: int,
        temperature: float | None = None,
    ) -> str:
        global _gemini_last_call
        import json
        import urllib.error
        import urllib.request

        api_key = self.config.gemini.api_key
        if not api_key:
            raise ValueError("Gemini API key not configured")

        # ── TPM gate: park if approaching the 250k token-per-minute limit ──
        estimated_prompt_tokens = _estimate_tokens(prompt + (system or ""))
        current_tpm = _tokens_in_window()
        if current_tpm + estimated_prompt_tokens > _TPM_LIMIT * 0.85:
            self.logger.info(
                f"TPM gate: {current_tpm:,} tokens in window "
                f"(+{estimated_prompt_tokens:,} pending), "
                f"parking {_TPM_PARK_SECONDS:.0f}s"
            )
            time.sleep(_TPM_PARK_SECONDS)

        # ── Rate limiter: enforce minimum delay between Gemini calls ──
        min_delay = getattr(self.config.gemini, "rate_limit_delay", 0.5)
        with _gemini_lock:
            elapsed = time.time() - _gemini_last_call
            if elapsed < min_delay:
                wait_for = min_delay - elapsed
                self.logger.info(f"Rate limit: waiting {wait_for:.1f}s before Gemini call")
                time.sleep(wait_for)
            _gemini_last_call = time.time()

        # Ignore Anthropic model names passed from callers; use Gemini config
        is_anthropic_model = model and ("claude" in model or "anthropic" in model)
        model_name = self.config.gemini.default_model if (not model or is_anthropic_model) else model

        # ── Budget gate: auto-downgrade Pro → Flash when daily limit hit ──
        if not self._check_budget(model_name):
            fallback = self.config.gemini.default_model
            self.logger.warning(
                f"Budget exhausted for {model_name}, falling back to {fallback}"
            )
            model_name = fallback

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )

        gen_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            gen_config["temperature"] = temperature
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        # ── Search Grounding: enable for Pro calls (insurance law freshness) ──
        use_grounding = (
            "pro" in model_name
            and getattr(self.config.gemini, "search_grounding", False)
        )
        if use_grounding:
            body["tools"] = [{"google_search": {}}]

        self.logger.debug(f"Calling Gemini ({model_name}), prompt length={len(prompt)}")
        payload = json.dumps(body).encode()

        max_retries = 3
        request_timeout = 60  # seconds per request
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                    result = json.loads(resp.read())

                candidates = result.get("candidates", [])
                if not candidates:
                    # Gemini returned no candidates (safety filter, empty response, etc.)
                    block_reason = result.get("promptFeedback", {}).get("blockReason", "unknown")
                    raise ValueError(
                        f"Gemini returned no candidates (blockReason={block_reason})"
                    )

                text = candidates[0]["content"]["parts"][0]["text"]
                self._record_budget_usage(model_name)
                # Track tokens for TPM sliding window
                total_tokens = estimated_prompt_tokens + _estimate_tokens(text)
                _record_tokens(total_tokens)
                self.logger.debug(
                    f"Gemini response length={len(text)} "
                    f"[{model_name} budget: {_budget_counters.get(_budget_key(model_name), 0)}, "
                    f"TPM window: {_tokens_in_window():,}]"
                )
                return text
            except urllib.error.HTTPError as e:
                last_error = e
                body_text = ""
                try:
                    body_text = e.read().decode()[:300]
                except Exception:
                    pass
                if e.code in (429, 500, 503):
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    self.logger.warning(
                        f"Gemini HTTP {e.code} on attempt {attempt + 1}/{max_retries}, "
                        f"retrying in {wait:.1f}s — {body_text}"
                    )
                    time.sleep(wait)
                    continue
                self.logger.error(f"Gemini HTTP {e.code}: {body_text}")
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                wait = (2 ** attempt) + random.uniform(0, 1)
                self.logger.warning(
                    f"Gemini network error on attempt {attempt + 1}/{max_retries}: {e}, "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue

        # Distinguish rate limits from other failures so callers don't
        # bounce articles to REVISION on transient 429s.
        if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429:
            self.logger.error(
                f"Gemini rate limit exhausted after {max_retries} attempts"
            )
            raise RateLimitError(
                f"Gemini API rate limited after {max_retries} retries"
            ) from last_error
        raise last_error  # type: ignore[misc]


class Agent:
    """
    Simple agent base class for agents that use direct database access.
    Alternative to BaseAgent for simpler use cases.
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.logger = logging.getLogger(f"pipeline.{name}")

    def log(self, message: str, level: str = "info"):
        """Log a message."""
        if level == "info":
            self.logger.info(message)
        elif level == "error":
            self.logger.error(message)
        elif level == "warning":
            self.logger.warning(message)
        else:
            self.logger.debug(message)

    def _generate_claim_id(self) -> str:
        """Generate unique claim ID."""
        ts = int(time.time())
        rand = uuid.uuid4().hex[:6]
        return f"{self.name}-{ts}-{rand}"

    def run(self) -> dict:
        """Execute agent's main task. Override in subclass."""
        raise NotImplementedError()
