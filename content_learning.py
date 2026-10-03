"""Persistent channel-content history and lightweight semantic no-repeat helpers."""

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
METADATA_FILE = BASE_DIR / "short_content_metadata.json"
HISTORY_FILE = BASE_DIR / "content_history.json"


def load_content_metadata():
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_content_metadata(data):
    METADATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _topic_key(text):
    """Normalize a topic into useful subject words for duplicate detection."""
    stop = {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "why",
        "how", "what", "when", "where", "which", "is", "are", "was", "were",
        "did", "do", "does", "can", "could", "your", "you", "this", "that",
        "these", "those", "with", "from", "into", "about", "behind", "really",
        "actually", "ever", "most", "top", "secret", "secrets", "fact", "facts",
        "story", "history", "world", "first", "important", "mysterious", "mystery",
        "thing", "things", "video", "short", "youtube", "explained", "explain",
        "revealed", "reveals", "could", "would", "might", "just", "new", "true",
        "true", "real", "people", "person", "one", "two", "three",
    }
    words = re.findall(r"[a-z0-9]+", _clean_text(text).casefold())
    normalized = []
    for word in words:
        if word in stop:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        normalized.append(word)
    return set(normalized)


def content_similarity(a, b):
    """Return True when two titles/topics likely describe the same subject.

    This is deliberately conservative: exact/fuzzy matches and strong keyword
    overlap are blocked locally, while the Gemini prompts also receive the full
    history so differently-worded titles can be rejected semantically.
    """
    a_text = _clean_text(a).casefold()
    b_text = _clean_text(b).casefold()
    if not a_text or not b_text:
        return False
    if a_text == b_text:
        return True

    from difflib import SequenceMatcher
    if SequenceMatcher(None, a_text, b_text).ratio() >= 0.80:
        return True

    a_key = _topic_key(a_text)
    b_key = _topic_key(b_text)
    if not a_key or not b_key:
        return False

    intersection = len(a_key & b_key)
    union = len(a_key | b_key)
    return (
        intersection / union >= 0.55
        or intersection / min(len(a_key), len(b_key)) >= 0.75
    )



def topic_fingerprint(*parts):
    """Compact canonical subject fingerprint used by Shorts no-repeat checks."""
    words = set()
    for part in parts:
        words.update(_topic_key(part))
    return " ".join(sorted(words))


def build_history_subject_context(content_type="shorts", limit=500):
    """Build a compact history prompt from the unified local history."""
    rows = load_content_history().get(content_type, [])
    lines = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        subject = _clean_text(row.get("core_subject") or row.get("topic") or row.get("title"))
        angle = _clean_text(row.get("specific_angle"))
        entity = _clean_text(row.get("primary_entity"))
        gap = _clean_text(row.get("knowledge_gap"))
        if subject:
            extra = "; ".join(x for x in (angle, entity, gap) if x)
            lines.append(f"- {subject}" + (f" | {extra}" if extra else ""))
    return "\n".join(lines) or "- none"

def _empty_history():
    return {"shorts": [], "long": [], "last_sync": ""}


def load_content_history():
    if not HISTORY_FILE.exists():
        return _empty_history()
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_history()
        return {
            "shorts": data.get("shorts", []) if isinstance(data.get("shorts", []), list) else [],
            "long": data.get("long", []) if isinstance(data.get("long", []), list) else [],
            "last_sync": str(data.get("last_sync", "")),
        }
    except Exception:
        return _empty_history()


def save_content_history(data):
    clean = _empty_history()
    for content_type in ("shorts", "long"):
        rows = data.get(content_type, []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            rows = []
        seen = set()
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            video_id = _clean_text(row.get("video_id"))
            title = _clean_text(row.get("title"))
            core_subject = _clean_text(row.get("core_subject") or row.get("topic") or title)
            key = video_id or f"{content_type}:{title.casefold()}:{core_subject.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            item = dict(row)
            item["video_id"] = video_id
            item["title"] = title
            item["core_subject"] = core_subject
            item["content_type"] = content_type
            out.append(item)
        clean[content_type] = out[-10000:]
    clean["last_sync"] = str(data.get("last_sync", "")) if isinstance(data, dict) else ""
    HISTORY_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def _legacy_history_values():
    """Read the old topic_titles_history.json without assuming its exact shape."""
    legacy_file = BASE_DIR / "topic_titles_history.json"
    if not legacy_file.exists():
        parent = BASE_DIR.parent / "topic_titles_history.json"
        if parent.exists():
            legacy_file = parent
    if not legacy_file.exists():
        return []
    try:
        data = json.loads(legacy_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    values = data if isinstance(data, list) else data.get("titles", data.get("topics", []))
    result = []
    if not isinstance(values, list):
        return result
    for value in values:
        if isinstance(value, dict):
            title = value.get("title") or value.get("core_subject") or value.get("topic")
            if title:
                result.append(_clean_text(title))
            continue
        text = _clean_text(value)
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, dict):
                    title = parsed.get("title") or parsed.get("core_subject") or parsed.get("topic")
                    if title:
                        result.append(_clean_text(title))
                        continue
            except Exception:
                pass
        if text:
            result.append(text)
    return result


def _duration_seconds(duration):
    match = re.fullmatch(r"P(?:0D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration or ""))
    if not match:
        return 0
    hours, minutes, seconds = (int(x or 0) for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _looks_like_short(item):
    """Classify current YouTube Shorts: square/vertical videos up to 3 minutes."""
    duration = _duration_seconds(item.get("contentDetails", {}).get("duration", ""))
    if duration <= 0 or duration > 180:
        return False

    streams = item.get("fileDetails", {}).get("videoStreams", []) or []
    known_ratios = []
    for stream in streams:
        ratio = stream.get("aspectRatio")
        if ratio:
            try:
                w, h = (float(x) for x in str(ratio).split(":", 1))
                known_ratios.append((w, h))
            except Exception:
                pass
    if known_ratios:
        return any(h >= w for w, h in known_ratios)
    return True


def _youtube_record(item, content_type):
    snippet = item.get("snippet", {}) or {}
    details = item.get("contentDetails", {}) or {}
    stats = item.get("statistics", {}) or {}
    title = _clean_text(snippet.get("title"))
    description = _clean_text(snippet.get("description"))
    tags = [str(x).strip() for x in (snippet.get("tags") or []) if str(x).strip()]
    # Prefer the title as the stable subject anchor. Description/tags are retained
    # so future Gemini prompts have more context than title-only history.
    core_subject = title
    return {
        "video_id": str(item.get("id", "")),
        "content_type": content_type,
        "title": title,
        "core_subject": core_subject,
        "topic": core_subject,
        "description": description[:4000],
        "tags": tags[:30],
        "publishedAt": str(snippet.get("publishedAt", "")),
        "duration": str(details.get("duration", "")),
        "views": int(stats.get("viewCount", 0) or 0),
        "likes": int(stats.get("likeCount", 0) or 0),
        "comments": int(stats.get("commentCount", 0) or 0),
        "source": "youtube_sync",
    }


def sync_channel_history():
    """Read the complete channel upload playlist and rebuild local history.

    The YouTube upload playlist is used instead of the 21-day Analytics window,
    so an old upload is still part of no-repeat protection after local files are
    lost/reset. Shorts and long-form videos are stored separately.
    """
    from youtube_uploader import get_youtube_service

    service = get_youtube_service()
    channel = service.channels().list(
        part="contentDetails",
        mine=True,
        maxResults=1,
    ).execute()
    items = channel.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel was found for the connected account.")

    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    video_ids = []
    page_token = None

    while True:
        page = service.playlistItems().list(
            part="contentDetails,snippet",
            playlistId=uploads_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for row in page.get("items", []):
            video_id = row.get("contentDetails", {}).get("videoId")
            if video_id:
                video_ids.append(video_id)
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    video_ids = list(dict.fromkeys(video_ids))
    history = _empty_history()

    # Fetch complete metadata in API-sized batches.
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]
        data = service.videos().list(
            part="snippet,contentDetails,statistics,fileDetails",
            id=",".join(batch),
            maxResults=50,
        ).execute()
        for item in data.get("items", []):
            content_type = "shorts" if _looks_like_short(item) else "long"
            history[content_type].append(_youtube_record(item, content_type))

    # Keep old locally generated topics as Shorts history if they were not on
    # YouTube anymore / were created before metadata tracking existed. This
    # prevents a reset from silently re-enabling known subjects.
    existing = load_content_history()
    youtube_ids = {row.get("video_id") for group in ("shorts", "long") for row in history[group]}
    for row in existing.get("shorts", []):
        if row.get("video_id") and row.get("video_id") in youtube_ids:
            continue
        if row.get("title") or row.get("core_subject"):
            history["shorts"].append(row)
    for title in _legacy_history_values():
        if not any(content_similarity(title, row.get("core_subject") or row.get("title")) for row in history["shorts"]):
            history["shorts"].append({
                "video_id": "",
                "content_type": "shorts",
                "title": title,
                "core_subject": title,
                "topic": title,
                "source": "legacy_local_history",
            })

    history["last_sync"] = datetime.now(timezone.utc).isoformat()
    history = save_content_history(history)

    # Backfill the existing Shorts metadata file too, so analytics keeps using
    # the same metadata path it already knows about.
    metadata = load_content_metadata()
    for row in history["shorts"]:
        video_id = _clean_text(row.get("video_id"))
        if not video_id:
            continue
        metadata.setdefault(video_id, {})
        metadata[video_id].setdefault("topic", row.get("core_subject", row.get("title", "")))
        metadata[video_id].setdefault("core_subject", row.get("core_subject", row.get("title", "")))
        metadata[video_id].setdefault("content_type", "shorts")
    save_content_metadata(metadata)

    # Keep the old Shorts history file compatible with the existing generator.
    shorts_titles = [row.get("core_subject") or row.get("title") for row in history["shorts"]]
    shorts_titles = [_clean_text(x) for x in shorts_titles if _clean_text(x)]
    (BASE_DIR / "topic_titles_history.json").write_text(
        json.dumps(shorts_titles[-10000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return history


def ensure_history_bootstrapped():
    """Bootstrap from YouTube when local history is empty/missing."""
    history = load_content_history()
    if history["shorts"] or history["long"]:
        return history
    try:
        return sync_channel_history()
    except Exception:
        # History should never make video generation unusable when OAuth is not
        # connected yet. The caller can explicitly sync once YouTube is connected.
        return history


def get_history_topics(content_type, bootstrap=True):
    if bootstrap:
        history = ensure_history_bootstrapped()
    else:
        history = load_content_history()
    rows = history.get(content_type, [])
    result = []
    for row in rows:
        if isinstance(row, dict):
            for value in (row.get("core_subject"), row.get("topic"), row.get("title")):
                value = _clean_text(value)
                if value and value not in result:
                    result.append(value)
                    break
    return result


def remember_content(video_id, metadata):
    if not video_id:
        return

    data = load_content_metadata()
    item = {
        "topic": _clean_text(metadata.get("topic")),
        "core_subject": _clean_text(metadata.get("core_subject") or metadata.get("topic")),
        "category": _clean_text(metadata.get("category")),
        "hook_style": _clean_text(metadata.get("hook_style")),
        "quality_score": float(metadata.get("quality_score", 0) or 0),
        "selected_hook": _clean_text(metadata.get("selected_hook", metadata.get("hook", ""))),
        "cta": _clean_text(metadata.get("cta")),
        "content_type": _clean_text(metadata.get("content_type") or "shorts"),
    }
    data[str(video_id)] = item
    if len(data) > 10000:
        keys = list(data)[-10000:]
        data = {k: data[k] for k in keys}
    save_content_metadata(data)

    # Also update the unified history immediately so app-generated uploads are
    # protected without waiting for the next full YouTube sync.
    history = load_content_history()
    content_type = item["content_type"] if item["content_type"] in {"shorts", "long"} else "shorts"
    row = {
        "video_id": str(video_id),
        "content_type": content_type,
        "title": _clean_text(metadata.get("title") or metadata.get("topic")),
        "core_subject": item["core_subject"] or _clean_text(metadata.get("title")),
        "topic": item["topic"],
        "category": item["category"],
        "hook_style": item["hook_style"],
        "quality_score": item["quality_score"],
        "selected_hook": item["selected_hook"],
        "cta": item["cta"],
        "specific_angle": _clean_text(metadata.get("specific_angle")),
        "primary_entity": _clean_text(metadata.get("primary_entity")),
        "knowledge_gap": _clean_text(metadata.get("knowledge_gap")),
        "subject_fingerprint": topic_fingerprint(
            item["core_subject"], item["topic"],
            metadata.get("specific_angle"), metadata.get("primary_entity"),
            metadata.get("knowledge_gap")
        ),
        "source": "app_upload",
    }
    history[content_type] = [x for x in history[content_type] if x.get("video_id") != str(video_id)]
    history[content_type].append(row)
    history["last_sync"] = history.get("last_sync", "")
    save_content_history(history)


def metadata_for_video(video_id):
    return load_content_metadata().get(str(video_id), {})
