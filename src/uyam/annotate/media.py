"""Media context: whether an item depends on an image, video or link nobody saw.

Neither the LLM annotators nor the model see images: for a comment under an
image post they get only the post's title. A 2026-11-22 audit of the 15,000
v1/v2 labels found 43% of them reply to image or video posts, and those carry
63% of the sarcastic labels. So every exported row says what kind of post it
belongs to and whether media appears in it or its thread. That lets the model
side slice results by it (overall vs text-only), check gold-label agreement
per slice, and optionally test a "context incomplete" tag as an ablation.

The post URL is not in the corpus index, so it is read from the raw JSONL
(submission lines only). Nothing here writes anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Media inside text: Reddit's inline gif/image markup, image/video hosts, files.
_MEDIA_IN_TEXT = re.compile(
    r"!\[(?:gif|img)\]\((?!emote\|)|giphy\.com|i\.redd\.it|preview\.redd\.it|v\.redd\.it|"
    r"imgur\.com|reddit\.com/gallery|youtu\.?be|tiktok\.com|"
    r"\.(?:gif|gifv|jpe?g|png|webp|mp4)\b",
    re.IGNORECASE,
)
_IMAGE_URL = re.compile(
    r"i\.redd\.it|imgur\.com|reddit\.com/gallery|\.(?:jpe?g|png|webp|gif)\b", re.IGNORECASE
)
_VIDEO_URL = re.compile(r"v\.redd\.it|youtu\.?be|tiktok\.com|\.(?:mp4|gifv)\b", re.IGNORECASE)

POST_TYPES = ("text", "image", "video", "link", "unknown")


def has_media(text: str | None) -> bool:
    return bool(text) and bool(_MEDIA_IN_TEXT.search(str(text)))


def post_kind(is_self: Any, url: str | None) -> str:
    """text | image | video | link | unknown, for the post an item belongs to."""
    if is_self is None and not url:
        return "unknown"
    if is_self and not (url and (_IMAGE_URL.search(url) or _VIDEO_URL.search(url))):
        return "text"
    if url and _VIDEO_URL.search(url):
        return "video"
    if url and _IMAGE_URL.search(url):
        return "image"
    if is_self:
        return "text"
    return "link" if url else "unknown"


def load_post_urls(data_dir: Path) -> dict[str, str]:
    """{submission fullname: url} from the raw JSONL (submission lines only)."""
    out: dict[str, str] = {}
    raw = data_dir / "raw"
    if not raw.exists():
        return out
    for path in sorted(raw.glob("**/*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if '"submission"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("record_type") == "submission" and record.get("reddit_fullname"):
                    out.setdefault(str(record["reddit_fullname"]), str(record.get("url") or ""))
    return out


def media_context(
    *,
    is_post: bool,
    target_text: str | None,
    post_is_self: Any,
    post_url: str | None,
    post_body: str | None,
    parent_texts: list[str],
) -> dict[str, Any]:
    """The exported `media_context` block for one item.

    `text_only` is true when nothing the item hangs on is unseen: a text post,
    with no image, video or GIF in the post body, the parents or the target.
    """
    kind = post_kind(post_is_self, post_url)
    flags = {
        "post_type": kind,
        "is_post": bool(is_post),
        "post_body_has_media": has_media(post_body),
        "parent_has_media": any(has_media(t) for t in parent_texts),
        "target_has_media": has_media(target_text),
    }
    flags["text_only"] = kind == "text" and not (
        flags["post_body_has_media"] or flags["parent_has_media"] or flags["target_has_media"]
    )
    return flags


def load_side_files(out_dir: Path) -> dict[str, dict[str, Any]]:
    """{fullname: media_context} from every media_context-*.jsonl (newest file wins)."""
    out: dict[str, dict[str, Any]] = {}
    paths = sorted(out_dir.glob("media_context-*.jsonl"), key=lambda p: p.stat().st_mtime)
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("reddit_fullname"):
                    out[str(row["reddit_fullname"])] = dict(row.get("media_context") or {})
    return out


def describe(flags: dict[str, Any] | None) -> tuple[str, str] | None:
    """(level, message) for the review page, or None when nothing is unseen.

    level is "warning" (the meaning may hinge on something nobody can see) or
    "info" (a text-only thread).
    """
    if not flags:
        return None
    if flags.get("text_only"):
        return "info", "📝 Text-only thread: everything the item hangs on is shown here."
    parts = []
    kind = flags.get("post_type")
    if kind == "image":
        parts.append("🖼️ replies to an **image** post (only its title is shown)")
    elif kind == "video":
        parts.append("🎬 replies to a **video** post (only its title is shown)")
    elif kind == "link":
        parts.append("🔗 replies to a **link** post (the linked page isn't shown)")
    elif kind == "unknown":
        parts.append("❔ the post's type is unknown")
    if flags.get("post_body_has_media"):
        parts.append("the post body has an image or video")
    if flags.get("parent_has_media"):
        parts.append("a parent comment has an image or GIF")
    if flags.get("target_has_media"):
        parts.append("this text has an image or GIF")
    if not parts:
        return None
    return (
        "warning",
        "Unseen media: " + "; ".join(parts) + ". Neither you nor the model sees it. Judge "
        "from the text and title; if the meaning hinges on the picture, label your best "
        "reading and tick **Unsure** (H13).",
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Card block: rows and sarcastic share per post type, plus the text-only slice."""
    by_type: dict[str, dict[str, Any]] = {}
    for kind in POST_TYPES:
        members = [r for r in rows if (r.get("media_context") or {}).get("post_type") == kind]
        if members:
            positives = sum(1 for r in members if (r.get("labels") or {}).get("sarcastic"))
            by_type[kind] = {
                "rows": len(members),
                "sarcastic": positives,
                "sarcastic_rate": round(positives / len(members), 4),
            }
    text_only = [r for r in rows if (r.get("media_context") or {}).get("text_only")]
    positives = sum(1 for r in text_only if (r.get("labels") or {}).get("sarcastic"))
    return {
        "by_post_type": by_type,
        "text_only_rows": len(text_only),
        "text_only_sarcastic": positives,
        "note": "Annotators and model saw only the title of image/video/link posts and "
        "no inline images. Report results overall and on media_context.text_only.",
    }
