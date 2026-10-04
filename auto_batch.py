"""Cloud runner: generate 8 Shorts, schedule the next 8 two-hour slots, then exit.

This file is additive: the existing Streamlit/manual workflow can continue using
batch_generator.py exactly as before.
"""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from batch_generator import generate_topic_candidates
from generator import generate_shorts_batch
from tts import generate_voice
from pexels import get_best_video
from video_engine import build_short
from youtube_uploader import schedule_short
from content_learning import remember_content, sync_channel_history, load_content_history

BASE_DIR = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")
STYLE = os.getenv("SHORT_STYLE", "Documentary")
VOICE = os.getenv("SHORT_VOICE", "Kore")
BATCH_SIZE = 8


def _git(*args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    ).stdout.strip()


def sync_repo_before_run():
    """Pull the latest history before generation so cloud never uses stale state."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    branch = os.getenv("GITHUB_REF_NAME", "main")

    _git("config", "user.name", "shorts-bot")
    _git("config", "user.email", "shorts-bot@users.noreply.github.com")
    _git("fetch", "origin", branch)
    _git("reset", "--hard", f"origin/{branch}")


def persist_history_to_repo(message):
    """Persist history after each successful upload; MP4s are never committed."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    branch = os.getenv("GITHUB_REF_NAME", "main")

    files = [
        "content_history.json",
        "short_content_metadata.json",
        "topic_titles_history.json",
    ]

    _git("add", *[x for x in files if (BASE_DIR / x).exists()])

    status = _git("status", "--porcelain")

    if not status:
        return

    _git("commit", "-m", message)

    # Rebase any tiny concurrent history update before pushing.
    _git("pull", "--rebase", "origin", branch)
    _git("push", "origin", f"HEAD:{branch}")


def publish_slots_for_batch(now_ist):
    """Return 8 publishing slots, two hours apart.

    The GitHub Action starts around 4 PM IST.
    The first Short is scheduled two hours later, giving the
    generator/render/upload process enough time to complete.
    """
    start = (now_ist + timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    return [start + timedelta(hours=2 * i) for i in range(BATCH_SIZE)]


def main():
    sync_repo_before_run()

    # YouTube is the final source of truth for anything already uploaded.
    # This also merges existing local/generated history.
    sync_channel_history()

    history = load_content_history()

    print(
        f"History loaded: {len(history.get('shorts', []))} Shorts, "
        f"{len(history.get('long', []))} long videos"
    )

    candidates = generate_topic_candidates(
        n=BATCH_SIZE,
        niche="science, everyday phenomena, animals, human body, history, nature, space and amazing facts",
        avoid_topics=[],
        trending_count=0,
        performance_insights="",
    )

    topics = [x["topic"] for x in candidates]

    print("Selected topics:")

    for i, topic in enumerate(topics, 1):
        print(f"  {i}. {topic}")

    scripts = generate_shorts_batch(
        topics=topics,
        style=STYLE,
    )

    script_map = {
        str(x.get("topic", "")).strip().casefold(): x
        for x in scripts
    }

    slots = publish_slots_for_batch(datetime.now(IST))

    print("Publishing schedule:")

    for i, slot in enumerate(slots, 1):
        print(f"  Short {i}: {slot.isoformat()}")

    for index, topic in enumerate(topics):

        result = script_map.get(topic.casefold())

        if not result:
            raise RuntimeError(f"Missing Gemini script for: {topic}")

        print(f"[{index + 1}/{BATCH_SIZE}] Rendering: {topic}")

        audio_data = generate_voice(
            text=result["voiceover"],
            voice=VOICE,
            style=STYLE,
        )

        scenes = result.get("scenes", [])

        videos = []

        for scene in scenes:
            query = str(scene.get("search_query", "")).strip()
            videos.append(
                get_best_video(query) if query else None
            )

        if any(v is None for v in videos):
            raise RuntimeError(
                f"Missing Pexels footage for: {topic}"
            )

        video_bytes = build_short(
            videos=videos,
            audio_data=audio_data,
            scenes=scenes,
            voiceover=result.get("voiceover", ""),
        )

        publish_at = slots[index]

        response = schedule_short(
            video_bytes=video_bytes,
            title=result.get("title") or topic,
            publish_at=publish_at,
            description=result.get("caption", ""),
            tags=result.get("hashtags", []),
            category_id="27",
            made_for_kids=False,
            contains_synthetic_media=True,
        )

        video_id = response["id"]

        print(
            f"  Uploaded {video_id}; "
            f"scheduled for {publish_at.isoformat()}"
        )

        remember_content(
            video_id,
            {
                **result,
                "title": result.get("title") or topic,
                "topic": result.get("topic") or topic,
                "content_type": "shorts",
                "category": result.get("category", ""),
                "hook_style": result.get("hook_style", ""),
                "quality_score": result.get("quality_score", 0),
                "specific_angle": result.get("specific_angle", ""),
                "primary_entity": result.get("primary_entity", ""),
                "knowledge_gap": result.get("knowledge_gap", ""),
            },
        )

        persist_history_to_repo(
            f"history: add scheduled Short {video_id}"
        )

    print(
        "Batch complete. 8 Shorts scheduled; runner exits now."
    )


if __name__ == "__main__":
    main()