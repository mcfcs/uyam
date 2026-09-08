"""Configuration loading for the annotation pipeline (config/annotation.yaml).

Mirrors uyam.config: plain dataclasses, validated at load time so CLI
commands fail fast. Relative paths resolve against the repository root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "annotation.yaml"


@dataclass
class AnnotatorConfig:
    key: str
    model: str
    endpoint: str  # name into AnnotationConfig.endpoints
    think: bool | None = None  # None = do not send the think parameter
    merge_system_prompt: bool = False  # Gemma2-arch models have no system role


@dataclass
class OllamaOptionsConfig:
    temperature: float = 0.0
    seed: int = 7
    num_ctx: int = 8192
    num_predict: int = 700
    keep_alive: str = "30m"
    timeout_seconds: float = 300.0
    max_retries: int = 3


@dataclass
class ContextConfig:
    max_parent_chain: int = 6
    max_replies: int = 3
    max_context_chars: int = 6000


@dataclass
class CandidateFiltersConfig:
    min_chars: int = 12
    min_tokens: int = 3
    exclude_authors: list[str] = field(default_factory=lambda: ["AutoModerator"])
    exclude_distinguished_moderator: bool = True
    include_link_posts: bool = False


@dataclass
class LidConfig:
    model_path: Path = Path("data/models/lid.176.bin")
    mono_threshold: float = 0.9


@dataclass
class TxSentimentConfig:
    model: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
    device: str = "auto"  # auto | cpu | cuda
    batch_size: int = 32


@dataclass
class ReviewConfig:
    gold_size: int = 300
    adjudicator_confidence_floor: float = 0.6


@dataclass
class ExportConfig:
    out_dir: Path = Path("data/annotated")


@dataclass
class PipelineConfig:
    """Defaults for the one-click chain (`annotate pipeline` / Streamlit Start ALL)."""

    target_items: int = 12000  # every annotator covers the same N-item target set


@dataclass
class AnnotationConfig:
    db_path: Path
    data_dir: Path
    prompt_version: str
    endpoints: dict[str, str]
    annotators: list[AnnotatorConfig]
    adjudicator: AnnotatorConfig
    options: OllamaOptionsConfig
    context: ContextConfig
    candidate_filters: CandidateFiltersConfig
    lid: LidConfig
    tx_sentiment: TxSentimentConfig
    review: ReviewConfig
    export: ExportConfig
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    def annotator(self, key: str) -> AnnotatorConfig:
        if key == self.adjudicator.key:
            return self.adjudicator
        for ann in self.annotators:
            if ann.key == key:
                return ann
        raise KeyError(f"No annotator with key {key!r} in annotation.yaml")

    def endpoint_url(self, name: str) -> str:
        try:
            return self.endpoints[name]
        except KeyError as exc:
            raise KeyError(f"No endpoint named {name!r} in annotation.yaml") from exc

    def is_local_endpoint(self, name: str) -> bool:
        """The endpoint that shares this machine's GPU with the transformer passes."""
        if name == "local":
            return True
        host = urlparse(self.endpoint_url(name)).hostname or ""
        return host in {"127.0.0.1", "localhost", "::1"}


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else _REPO_ROOT / path


def _parse_annotator(raw: dict[str, Any]) -> AnnotatorConfig:
    if "key" not in raw or "model" not in raw or "endpoint" not in raw:
        raise ValueError(f"Annotator entry needs key/model/endpoint: {raw!r}")
    return AnnotatorConfig(
        key=str(raw["key"]),
        model=str(raw["model"]),
        endpoint=str(raw["endpoint"]),
        think=raw.get("think"),
        merge_system_prompt=bool(raw.get("merge_system_prompt", False)),
    )


def load_annotation_config(config_path: Path | None = None) -> AnnotationConfig:
    """Load and parse annotation.yaml."""
    path = config_path or _DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    endpoints = {str(k): str(v) for k, v in (raw.get("endpoints") or {}).items()}
    if not endpoints:
        raise ValueError("annotation.yaml must define at least one endpoint")

    annotators = [_parse_annotator(a) for a in (raw.get("annotators") or [])]
    if not annotators:
        raise ValueError("annotation.yaml must define at least one annotator")
    adj_raw = raw.get("adjudicator")
    if not adj_raw:
        raise ValueError("annotation.yaml must define an adjudicator")
    adjudicator = _parse_annotator(adj_raw)

    for ann in [*annotators, adjudicator]:
        if ann.endpoint not in endpoints:
            raise ValueError(f"Annotator {ann.key!r} references unknown endpoint {ann.endpoint!r}")

    opt_raw: dict[str, Any] = raw.get("options") or {}
    options = OllamaOptionsConfig(
        temperature=float(opt_raw.get("temperature", 0.0)),
        seed=int(opt_raw.get("seed", 7)),
        num_ctx=int(opt_raw.get("num_ctx", 8192)),
        num_predict=int(opt_raw.get("num_predict", 700)),
        keep_alive=str(opt_raw.get("keep_alive", "30m")),
        timeout_seconds=float(opt_raw.get("timeout_seconds", 300.0)),
        max_retries=int(opt_raw.get("max_retries", 3)),
    )

    ctx_raw: dict[str, Any] = raw.get("context") or {}
    context = ContextConfig(
        max_parent_chain=int(ctx_raw.get("max_parent_chain", 6)),
        max_replies=int(ctx_raw.get("max_replies", 3)),
        max_context_chars=int(ctx_raw.get("max_context_chars", 6000)),
    )

    filt_raw: dict[str, Any] = raw.get("candidate_filters") or {}
    candidate_filters = CandidateFiltersConfig(
        min_chars=int(filt_raw.get("min_chars", 12)),
        min_tokens=int(filt_raw.get("min_tokens", 3)),
        exclude_authors=list(filt_raw.get("exclude_authors") or ["AutoModerator"]),
        exclude_distinguished_moderator=bool(
            filt_raw.get("exclude_distinguished_moderator", True)
        ),
        include_link_posts=bool(filt_raw.get("include_link_posts", False)),
    )

    lid_raw: dict[str, Any] = raw.get("lid") or {}
    lid = LidConfig(
        model_path=_resolve(Path(lid_raw.get("model_path", "data/models/lid.176.bin"))),
        mono_threshold=float(lid_raw.get("mono_threshold", 0.9)),
    )

    tx_raw: dict[str, Any] = raw.get("tx_sentiment") or {}
    tx_sentiment = TxSentimentConfig(
        model=str(tx_raw.get("model", "cardiffnlp/twitter-xlm-roberta-base-sentiment")),
        device=str(tx_raw.get("device", "auto")),
        batch_size=int(tx_raw.get("batch_size", 32)),
    )

    rev_raw: dict[str, Any] = raw.get("review") or {}
    review = ReviewConfig(
        gold_size=int(rev_raw.get("gold_size", 300)),
        adjudicator_confidence_floor=float(rev_raw.get("adjudicator_confidence_floor", 0.6)),
    )

    exp_raw: dict[str, Any] = raw.get("export") or {}
    export = ExportConfig(out_dir=_resolve(Path(exp_raw.get("out_dir", "data/annotated"))))

    pipe_raw: dict[str, Any] = raw.get("pipeline") or {}
    pipeline = PipelineConfig(target_items=int(pipe_raw.get("target_items", 12000)))
    if pipeline.target_items <= 0:
        raise ValueError("annotation.yaml pipeline.target_items must be a positive integer")

    return AnnotationConfig(
        db_path=_resolve(Path(raw.get("db_path", "data/db/annotation.sqlite3"))),
        data_dir=_resolve(Path(raw.get("data_dir", "data"))),
        prompt_version=str(raw.get("prompt_version", "sarc-v1")),
        endpoints=endpoints,
        annotators=annotators,
        adjudicator=adjudicator,
        options=options,
        context=context,
        candidate_filters=candidate_filters,
        lid=lid,
        tx_sentiment=tx_sentiment,
        review=review,
        export=export,
        pipeline=pipeline,
    )
