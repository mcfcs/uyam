"""Ollama chat client for structured annotation calls.

Wraps the official `ollama` package (lazy import). One client per endpoint;
schema-constrained decoding via format=<json schema>; retries with backoff for
transport errors and a temperature bump on the final attempt for parse errors.

Known model quirks handled here:
- Qwen3: thinking mode + format can return empty content -> send think=False
  (only for models that declare `think` in annotation.yaml; sending the
  parameter to non-thinking models like gemma3 is a server error).
- Gemma2-arch (SEA-LION v3 9B): no system role -> merge_system_prompt merges
  the system prompt into the first user turn.
- Any model may still leak a <think> block; it is stripped before parsing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from uyam.annotate.config import AnnotatorConfig, OllamaOptionsConfig
from uyam.annotate.schemas import LLM_OUTPUT_SCHEMA, LLMAnnotationOut

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_RETRY_TEMPERATURE = 0.2


class AnnotationCallError(Exception):
    """All retries exhausted for one annotation call."""


@dataclass
class AnnotationResult:
    parsed: LLMAnnotationOut
    raw_content: str
    options_used: dict[str, Any]
    attempts: int
    duration_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None


def _response_meta(response: Any, attr: str) -> int | None:
    value = getattr(response, attr, None)
    if value is None and isinstance(response, dict):
        value = response.get(attr)
    return int(value) if value is not None else None


def extract_json(content: str) -> str:
    """Strip <think> blocks and any prose around the outermost JSON object."""
    content = _THINK_RE.sub("", content).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end > start:
        return content[start : end + 1]
    return content


class OllamaClient:
    def __init__(self, endpoint_url: str, timeout_seconds: float) -> None:
        import ollama

        self.endpoint_url = endpoint_url.rstrip("/")
        self._client = ollama.Client(host=self.endpoint_url, timeout=timeout_seconds)

    # -- provenance helpers -------------------------------------------------

    def server_version(self) -> str | None:
        try:
            import requests

            resp = requests.get(f"{self.endpoint_url}/api/version", timeout=10)
            resp.raise_for_status()
            return str(resp.json().get("version"))
        except Exception:  # noqa: BLE001 - provenance is best-effort
            return None

    def model_digest(self, model: str) -> str | None:
        try:
            listing = self._client.list()
            models = getattr(listing, "models", None) or listing.get("models", [])
            for entry in models:
                name = getattr(entry, "model", None) or entry.get("model", "")
                if name in (model, f"{model}:latest") or model in (name, f"{name}:latest"):
                    digest = getattr(entry, "digest", None) or entry.get("digest")
                    return str(digest) if digest else None
        except Exception:  # noqa: BLE001 - provenance is best-effort
            return None
        return None

    def model_template(self, model: str) -> str | None:
        try:
            info = self._client.show(model)
            template = getattr(info, "template", None) or info.get("template")
            return str(template) if template else None
        except Exception:  # noqa: BLE001
            return None

    def list_model_names(self) -> list[str]:
        listing = self._client.list()
        models = getattr(listing, "models", None) or listing.get("models", [])
        names = []
        for entry in models:
            name = getattr(entry, "model", None) or entry.get("model", "")
            if name:
                names.append(str(name))
        return names

    # -- the annotation call ------------------------------------------------

    def annotate(
        self,
        annotator: AnnotatorConfig,
        system_prompt: str,
        user_prompt: str,
        options_cfg: OllamaOptionsConfig,
    ) -> AnnotationResult:
        if annotator.merge_system_prompt:
            messages = [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        last_error: Exception | None = None
        for attempt in range(1, options_cfg.max_retries + 1):
            temperature = (
                _RETRY_TEMPERATURE
                if attempt == options_cfg.max_retries and options_cfg.max_retries > 1
                else options_cfg.temperature
            )
            options = {
                "temperature": temperature,
                "seed": options_cfg.seed,
                "num_ctx": options_cfg.num_ctx,
                "num_predict": options_cfg.num_predict,
            }
            kwargs: dict[str, Any] = {
                "model": annotator.model,
                "messages": messages,
                "format": LLM_OUTPUT_SCHEMA,
                "options": options,
                "keep_alive": options_cfg.keep_alive,
            }
            if annotator.think is not None:
                kwargs["think"] = annotator.think

            try:
                response = self._client.chat(**kwargs)
            except Exception as exc:  # transport / server error
                last_error = exc
                logger.warning(
                    "ollama_transport_error",
                    extra={"model": annotator.model, "attempt": attempt, "error": str(exc)},
                )
                time.sleep(min(30.0, 2.0**attempt))
                continue

            message = getattr(response, "message", None) or response.get("message", {})
            content = getattr(message, "content", None) or (
                message.get("content") if isinstance(message, dict) else ""
            )
            content = content or ""

            try:
                parsed = LLMAnnotationOut.model_validate(json.loads(extract_json(content)))
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "ollama_parse_error",
                    extra={
                        "model": annotator.model,
                        "attempt": attempt,
                        "error": str(exc),
                        "content_head": content[:200],
                    },
                )
                continue

            total_ns = _response_meta(response, "total_duration")
            options_used = dict(options)
            if annotator.think is not None:
                options_used["think"] = annotator.think
            return AnnotationResult(
                parsed=parsed,
                raw_content=content,
                options_used=options_used,
                attempts=attempt,
                duration_ms=None if total_ns is None else total_ns // 1_000_000,
                prompt_tokens=_response_meta(response, "prompt_eval_count"),
                completion_tokens=_response_meta(response, "eval_count"),
            )

        raise AnnotationCallError(
            f"{annotator.model} failed after {options_cfg.max_retries} attempts: {last_error}"
        )
