"""media_context: which post an item hangs on, and whether unseen media is involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.annotate_fixtures import _comment, _submission, make_config
from tests.test_annotate_runner import FakeClient
from uyam.annotate.aggregate import run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.export import run_export
from uyam.annotate.media import has_media, load_post_urls, media_context, post_kind
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.runner import run_annotator


@pytest.mark.parametrize(
    ("is_self", "url", "kind"),
    [
        (True, "https://www.reddit.com/r/CasualPH/comments/abc/title/", "text"),
        (False, "https://i.redd.it/x1y2.jpeg", "image"),
        (False, "https://www.reddit.com/gallery/abc123", "image"),
        (False, "https://imgur.com/a/xyz", "image"),
        (False, "https://v.redd.it/abc", "video"),
        (False, "https://youtu.be/abc", "video"),
        (False, "https://news.example.com/story", "link"),
        (None, "", "unknown"),
    ],
)
def test_post_kind(is_self: object, url: str, kind: str) -> None:
    assert post_kind(is_self, url) == kind


def test_has_media_ignores_emotes_and_plain_text() -> None:
    assert has_media("![gif](giphy|abc123) haha")
    assert has_media("see https://preview.redd.it/pic.png?width=640")
    assert not has_media("![img](emote|t5_2qh3c|1234) nice")  # a Reddit emote, not an image
    assert not has_media("Wala lang, pagod lang ako today.")
    assert not has_media(None)


def test_text_only_needs_a_text_post_and_no_media_anywhere() -> None:
    base = {
        "is_post": False, "target_text": "ok lang", "post_is_self": True,
        "post_url": "https://www.reddit.com/r/x/comments/a/b/", "post_body": "",
        "parent_texts": [],
    }
    assert media_context(**base)["text_only"] is True
    assert media_context(**base | {"post_url": "https://i.redd.it/a.jpg", "post_is_self": False})[
        "text_only"] is False
    assert media_context(**base | {"parent_texts": ["![gif](giphy|x)"]})["parent_has_media"] is True
    assert media_context(**base | {"target_text": "lol https://imgur.com/x"})["text_only"] is False


def test_load_post_urls_reads_submissions_only(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "CasualPH"
    raw.mkdir(parents=True)
    lines = [
        json.dumps(_submission("img1", is_self=False, url="https://i.redd.it/a.jpg")),
        json.dumps(_comment("c9", "img1", "t3_img1", 0)),
        "{broken",
    ]
    (raw / "2026-08-20.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert load_post_urls(tmp_path) == {"t3_img1": "https://i.redd.it/a.jpg"}


def _pipeline_with_image_post(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = make_config(tmp_path)
    records = [
        _submission("sub1", url="https://www.reddit.com/r/CasualPH/comments/sub1/post/"),
        _comment("c1", "sub1", "t3_sub1", 0),
        _comment("c2", "sub1", "t1_c1", 1, body="Grabe naman yung reply mo ![gif](giphy|abc) lol"),
        _submission("img1", is_self=False, selftext="", url="https://i.redd.it/photo.jpeg"),
        _comment("i1", "img1", "t3_img1", 0, body="Ang ganda naman ng kuha mo dito, ang galing."),
    ]
    out = cfg.data_dir / "raw" / "CasualPH" / "2026-08-20.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
    for key in ("a1", "a2", "a3"):
        run_annotator(cfg, key, client_factory=lambda c, a: FakeClient())
    run_aggregation(cfg, PROMPT_VERSION)
    return cfg


def test_export_ships_media_context_and_card_summary(tmp_path: Path) -> None:
    cfg = _pipeline_with_image_post(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.upsert_human_review({"reddit_fullname": "t1_i1", "sarcastic": 1, "is_gold": 1})
        db.upsert_human_review({"reddit_fullname": "t1_c1", "sarcastic": 0, "is_gold": 1})
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))
    rows = {json.loads(line)["reddit_fullname"]: json.loads(line)
            for line in Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()}
    assert rows["t1_i1"]["media_context"]["post_type"] == "image"
    assert rows["t1_i1"]["media_context"]["text_only"] is False
    assert rows["t1_c1"]["media_context"] == {
        "post_type": "text", "is_post": False, "post_body_has_media": False,
        "parent_has_media": False, "target_has_media": False, "text_only": True,
    }
    assert rows["t1_c2"]["media_context"]["target_has_media"] is True
    assert rows["t3_sub1"]["media_context"]["is_post"] is True

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    media = card["media_context"]
    assert media["by_post_type"]["image"]["rows"] == 1
    text_only = [r for r in rows.values() if r["media_context"]["text_only"]]
    assert media["text_only_rows"] == len(text_only)
    assert media["gold_kappa"]["text_only"]["n"] == 1
    assert media["gold_kappa"]["media_dependent"]["n"] == 1


def test_media_context_command_writes_a_side_file(tmp_path: Path) -> None:
    import yaml
    from typer.testing import CliRunner

    from uyam.annotate.cli import annotate_app

    cfg = _pipeline_with_image_post(tmp_path)
    config = tmp_path / "annotation.yaml"
    config.write_text(yaml.safe_dump({
        "db_path": cfg.db_path.as_posix(), "data_dir": cfg.data_dir.as_posix(),
        "prompt_version": PROMPT_VERSION, "endpoints": {"local": "http://127.0.0.1:11434"},
        "annotators": [{"key": k, "model": "m", "endpoint": "local"} for k in ("a1", "a2", "a3")],
        "adjudicator": {"key": "adj", "model": "m", "endpoint": "local"},
        "export": {"out_dir": (tmp_path / "out").as_posix()},
    }), encoding="utf-8")
    result = CliRunner().invoke(
        annotate_app, ["media-context", "--version", "v2", "--config-path", str(config)]
    )
    assert result.exit_code == 0, result.output
    lines = (tmp_path / "out" / "media_context-v2.jsonl").read_text(encoding="utf-8").splitlines()
    by_id = {json.loads(x)["reddit_fullname"]: json.loads(x)["media_context"] for x in lines}
    assert by_id["t1_i1"]["post_type"] == "image" and by_id["t1_c1"]["text_only"] is True
    assert "text-only slice" in result.output
