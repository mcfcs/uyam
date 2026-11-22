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
    # Evaluators only: "sarc-v2" (the dataset prompt) or "guideline" (the
    # condensed docs/GUIDELINE.md, prompts_guideline.py; stored as guide-v1.1).
    prompt: str = "sarc-v2"


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
    exclude_phatic: bool = True
    phatic_max_tokens: int = 10
    phatic_max_leftover: int = 2


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
    # Gold strata weight for items whose base annotators split on sarcasm
    # (2-1, 1-1): that is where the disagreement lives, so the human sample
    # over-represents them (1.0 = proportional).
    gold_split_oversample: float = 2.0
    # Gold strata weight for items the ensemble shipped as sarcastic: at a ~11%
    # base rate a proportional 300 holds too few positives for a stable kappa
    # on the sarcastic class. Multiplies with the split weight.
    gold_sarcastic_oversample: float = 1.0


@dataclass
class ExportConfig:
    out_dir: Path = Path("data/annotated")


@dataclass
class AggregationConfig:
    """How base-annotator votes become the shipped label.

    v1/v2 (defaults): sarcasm must be unanimous, and any split or 1-1-1 tie
    escalates to the blind adjudicator.
    v3 (equal-size lineup, adviser 2026-11): `sarcasm_rule: majority`
    ships the strict majority of the sarcasm votes, and `tie_break:
    confidence` settles a 1-1-1 tie on a 3-class label with the most
    confident annotator's value (ties on confidence go to config order).
    No item is escalated, so no adjudicator is needed.
    """

    sarcasm_rule: str = "unanimous"  # unanimous | majority
    tie_break: str = "adjudicate"  # adjudicate | confidence


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
    # None when the aggregation rules never escalate (v3: majority + confidence).
    adjudicator: AnnotatorConfig | None
    options: OllamaOptionsConfig
    context: ContextConfig
    candidate_filters: CandidateFiltersConfig
    lid: LidConfig
    tx_sentiment: TxSentimentConfig
    review: ReviewConfig
    export: ExportConfig
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    # Evaluation-only models: same entry shape as annotators, but their votes
    # go to eval_annotations and are only ever compared, never aggregated.
    # Deliberately NOT returned by annotator(): `annotate run` cannot reach them.
    evaluators: list[AnnotatorConfig] = field(default_factory=list)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    # The file this config was loaded from. The pipeline passes it to every
    # child job, so a job never falls back to the default annotation.yaml.
    source_path: Path | None = None

    @property
    def annotator_keys(self) -> list[str]:
        """The base annotators whose votes are aggregated, in config order."""
        return [a.key for a in self.annotators]

    def require_adjudicator(self) -> AnnotatorConfig:
        if self.adjudicator is None:
            raise ValueError(
                "annotation.yaml has no adjudicator: these aggregation rules never "
                "escalate an item (aggregation.sarcasm_rule / tie_break)"
            )
        return self.adjudicator

    def annotator(self, key: str) -> AnnotatorConfig:
        if self.adjudicator is not None and key == self.adjudicator.key:
            return self.adjudicator
        for ann in self.annotators:
            if ann.key == key:
                return ann
        raise KeyError(f"No annotator with key {key!r} in annotation.yaml")

    def evaluator(self, key: str) -> AnnotatorConfig:
        for ev in self.evaluators:
            if ev.key == key:
                return ev
        known = ", ".join(ev.key for ev in self.evaluators) or "none configured"
        raise KeyError(f"No evaluator with key {key!r} in annotation.yaml ({known})")

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
        prompt=str(raw.get("prompt", "sarc-v2")),
    )


def _check_evaluator_keys(
    annotators: list[AnnotatorConfig],
    adjudicator: AnnotatorConfig | None,
    evaluators: list[AnnotatorConfig],
) -> None:
    """Evaluator keys must be unique and disjoint from annotator/adjudicator keys.

    A shared key would let an evaluation-only model's failures and progress be
    mistaken for a dataset annotator's (llm_failures is keyed by model_key).
    """
    taken = {a.key for a in annotators} | ({adjudicator.key} if adjudicator else set())
    seen: set[str] = set()
    for ev in evaluators:
        if ev.key in taken:
            raise ValueError(
                f"annotation.yaml evaluator key {ev.key!r} collides with an annotator/"
                "adjudicator key — evaluators need their own keys"
            )
        if ev.key in seen:
            raise ValueError(f"annotation.yaml evaluator key {ev.key!r} is listed twice")
        seen.add(ev.key)


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
    agg_raw: dict[str, Any] = raw.get("aggregation") or {}
    aggregation = AggregationConfig(
        sarcasm_rule=str(agg_raw.get("sarcasm_rule", "unanimous")),
        tie_break=str(agg_raw.get("tie_break", "adjudicate")),
    )
    if aggregation.sarcasm_rule not in {"unanimous", "majority"}:
        raise ValueError("annotation.yaml aggregation.sarcasm_rule must be unanimous or majority")
    if aggregation.tie_break not in {"adjudicate", "confidence"}:
        raise ValueError("annotation.yaml aggregation.tie_break must be adjudicate or confidence")
    never_escalates = (
        aggregation.sarcasm_rule == "majority" and aggregation.tie_break == "confidence"
    )

    adj_raw = raw.get("adjudicator")
    if not adj_raw and not never_escalates:
        raise ValueError(
            "annotation.yaml must define an adjudicator (only "
            "aggregation.sarcasm_rule: majority with tie_break: confidence needs none)"
        )
    adjudicator = _parse_annotator(adj_raw) if adj_raw else None

    evaluators = [_parse_annotator(e) for e in (raw.get("evaluators") or [])]
    _check_evaluator_keys(annotators, adjudicator, evaluators)
    for ann in [*annotators, *([adjudicator] if adjudicator else [])]:
        if ann.prompt != "sarc-v2":
            raise ValueError(
                f"annotator {ann.key!r}: only evaluators may use another prompt "
                "(the dataset prompt is hash-guarded)"
            )
    for ev in evaluators:
        if ev.prompt not in {"sarc-v2", "guideline"}:
            raise ValueError(f"evaluator {ev.key!r}: prompt must be sarc-v2 or guideline")
    if len({a.key for a in annotators}) != len(annotators):
        raise ValueError("annotation.yaml annotator keys must be unique")

    for ann in [*annotators, *([adjudicator] if adjudicator else []), *evaluators]:
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
        exclude_phatic=bool(filt_raw.get("exclude_phatic", True)),
        phatic_max_tokens=int(filt_raw.get("phatic_max_tokens", 10)),
        phatic_max_leftover=int(filt_raw.get("phatic_max_leftover", 2)),
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
        gold_split_oversample=float(rev_raw.get("gold_split_oversample", 2.0)),
        gold_sarcastic_oversample=float(rev_raw.get("gold_sarcastic_oversample", 1.0)),
    )
    if review.gold_split_oversample <= 0:
        raise ValueError("annotation.yaml review.gold_split_oversample must be > 0")
    if review.gold_sarcastic_oversample <= 0:
        raise ValueError("annotation.yaml review.gold_sarcastic_oversample must be > 0")

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
        evaluators=evaluators,
        aggregation=aggregation,
        source_path=path.resolve(),
    )
