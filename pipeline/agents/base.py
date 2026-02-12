"""Base agent class with common functionality."""

from __future__ import annotations

import logging
import random
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any

from pipeline.config import Config
from pipeline.db import Database


class BaseAgent(ABC):
    """Base class for all pipeline agents."""

    name: str = "base"
    claim_field: str | None = None  # Which claim field this agent uses

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.logger = logging.getLogger(f"pipeline.{self.name}")

    @property
    def default_model(self) -> str:
        """Get this agent's configured model from config."""
        model_attr = f"{self.name}_model"
        return getattr(self.config.anthropic, model_attr, "claude-haiku-4-5-20251001")

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
        """Pick a random unclaimed article from a status and claim it.

        Returns article ID if successful, None otherwise.
        """
        if self.claim_field is None:
            raise ValueError(f"Agent {self.name} has no claim_field defined")

        articles = self.db.query_articles(
            status=from_status,
            writer_claim_empty=(self.claim_field == "writer_claim"),
            limit=50,
        )
        if not articles:
            self.logger.info(f"No articles in '{from_status}' status")
            return None

        # Shuffle to prevent collision when multiple instances run
        random.shuffle(articles)

        for article in articles:
            claim_val = getattr(article, self.claim_field, "")
            if claim_val and claim_val != "":
                continue
            if self.try_claim_article(article.id, to_status):
                self.logger.info(f"Claimed article {article.id}: {article.title}")
                return article.id

        self.logger.info(f"Failed to claim any article from '{from_status}'")
        return None

    def call_claude(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """Call LLM API (Anthropic or Gemini) and return the text response."""
        provider = getattr(self.config, "llm_provider", "anthropic")
        if provider == "gemini":
            return self._call_gemini(prompt, system, model, max_tokens)
        return self._call_anthropic(prompt, system, model, max_tokens)

    def _call_anthropic(
        self, prompt: str, system: str, model: str | None, max_tokens: int
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

        self.logger.debug(f"Calling Claude ({model}), prompt length={len(prompt)}")
        response = client.messages.create(**kwargs)
        text = response.content[0].text
        self.logger.debug(f"Claude response length={len(text)}")
        return text

    def _call_gemini(
        self, prompt: str, system: str, model: str | None, max_tokens: int
    ) -> str:
        import json
        import urllib.error
        import urllib.request

        api_key = self.config.gemini.api_key
        if not api_key:
            raise ValueError("Gemini API key not configured")

        # Ignore Anthropic model names passed from callers; use Gemini config
        is_anthropic_model = model and ("claude" in model or "anthropic" in model)
        model_name = self.config.gemini.default_model if (not model or is_anthropic_model) else model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        self.logger.debug(f"Calling Gemini ({model_name}), prompt length={len(prompt)}")
        payload = json.dumps(body).encode()

        max_retries = 4
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read())
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                self.logger.debug(f"Gemini response length={len(text)}")
                return text
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in (429, 500, 503):
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    self.logger.warning(
                        f"Gemini HTTP {e.code} on attempt {attempt + 1}/{max_retries}, "
                        f"retrying in {wait:.1f}s"
                    )
                    time.sleep(wait)
                    continue
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
