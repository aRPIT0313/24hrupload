"""Cloud runner: generate 8 Shorts, schedule them one hour apart, then exit.

The existing Streamlit/manual workflow remains unchanged.
Cloud-specific optimization:
- Generate 8 scripts in one Gemini request.
- Schedule Shorts one hour apart.
- Persist history only once after the full batch.
- Push history to GitHub only once.
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
from content_learning import (
    remember_content,
    sync_channel_history,
    load_content_history,
)


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
    """Pull the latest repository/history before generation."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    branch = os.getenv("GITHUB_REF_NAME", "main")

    _git("config", "user.name", "shorts-bot")
    _git("config", "user.email", "shorts-bot@users.noreply.github.com")

    _git("fetch", "origin", branch)
    _git("reset", "--hard", f"origin/{branch}")


def persist_history_to_repo():
    """Commit and push all history changes once after the batch finishes."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    branch = os.getenv("GITHUB_REF_NAME", "main")

    files = [
        "content_history.json",
        "short_content_metadata.json",
        "topic_titles_history.json",
    ]

    existing_files = [
        filename
        for filename in files
        if (BASE_DIR / filename).exists()
    ]

    if not existing_files:
        print("No history files found to commit.")
        return

    _git("add", *existing_files)

    status = _git("status", "--porcelain")

    if not status:
        print("No history changes to commit.")
        return

    print("Persisting batch history to GitHub...")

    _git(
        "commit",
        "-m",
        f"history: add {BATCH_SIZE} scheduled Shorts",
    )

    # Rebase any small concurrent repository update before pushing.
    _git("pull", "--rebase", "origin", branch)

    _git("push", "origin", f"HEAD:{branch}")

    print("History pushed successfully.")


def publish_slots_for_batch(now_ist):
    """Return 8 publishing slots, one hour apart.

    GitHub Action starts around 4 PM IST.
    The first Short is scheduled one hour later to give the
    generator/render/upload process some buffer.
    """

    start = (now_ist + timedelta(hours=1)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    return [
        start + timedelta(hours=i)
        for i in range(BATCH_SIZE)
    ]


def main():

    # =========================================================
    # SYNC REPOSITORY
    # =========================================================

    sync_repo_before_run()

    # =========================================================
    # SYNC YOUTUBE HISTORY
    # =========================================================

    # YouTube remains the final source of truth for uploaded Shorts.
    sync_channel_history()

    history = load_content_history()

    print(
        f"History loaded: "
        f"{len(history.get('shorts', []))} Shorts, "
        f"{len(history.get('long', []))} long videos"
    )

    # =========================================================
    # GENERATE TOPICS
    # =========================================================

    candidates = generate_topic_candidates(
        n=BATCH_SIZE,
        niche=(
            "science, everyday phenomena, animals, human body, "
            "history, nature, space and amazing facts"
        ),
        avoid_topics=[],
        trending_count=0,
        performance_insights="",
    )

    topics = [
        x["topic"]
        for x in candidates
    ]

    print("Selected topics:")

    for i, topic in enumerate(topics, 1):
        print(f"  {i}. {topic}")

    # =========================================================
    # GENERATE ALL SCRIPTS IN ONE REQUEST
    # =========================================================

    scripts = generate_shorts_batch(
        topics=topics,
        style=STYLE,
    )

    script_map = {
        str(x.get("topic", "")).strip().casefold(): x
        for x in scripts
    }

    # =========================================================
    # PUBLISHING SCHEDULE
    # =========================================================

    slots = publish_slots_for_batch(
        datetime.now(IST)
    )

    print("Publishing schedule:")

    for i, slot in enumerate(slots, 1):
        print(
            f"  Short {i}: "
            f"{slot.isoformat()}"
        )

    # =========================================================
    # GENERATE + UPLOAD EACH SHORT
    # =========================================================

    successful_uploads = 0

    for index, topic in enumerate(topics):

        result = script_map.get(
            topic.casefold()
        )

        if not result:
            raise RuntimeError(
                f"Missing Gemini script for: {topic}"
            )

        print(
            f"[{index + 1}/{BATCH_SIZE}] "
            f"Rendering: {topic}"
        )

        # -----------------------------------------------------
        # TTS
        # -----------------------------------------------------

        audio_data = generate_voice(
            text=result["voiceover"],
            voice=VOICE,
            style=STYLE,
        )

        # -----------------------------------------------------
        # PEXELS
        # -----------------------------------------------------

        scenes = result.get(
            "scenes",
            []
        )

        videos = []

        for scene in scenes:

            query = str(
                scene.get(
                    "search_query",
                    ""
                )
            ).strip()

            videos.append(
                get_best_video(query)
                if query
                else None
            )

        if any(
            video is None
            for video in videos
        ):
            raise RuntimeError(
                f"Missing Pexels footage for: {topic}"
            )

        # -----------------------------------------------------
        # VIDEO RENDER
        # -----------------------------------------------------

        video_bytes = build_short(
            videos=videos,
            audio_data=audio_data,
            scenes=scenes,
            voiceover=result.get(
                "voiceover",
                ""
            ),
        )

        # -----------------------------------------------------
        # YOUTUBE SCHEDULE
        # -----------------------------------------------------

        publish_at = slots[index]

        response = schedule_short(
            video_bytes=video_bytes,
            title=result.get(
                "title"
            ) or topic,
            publish_at=publish_at,
            description=result.get(
                "caption",
                ""
            ),
            tags=result.get(
                "hashtags",
                []
            ),
            category_id="27",
            made_for_kids=False,
            contains_synthetic_media=True,
        )

        video_id = response["id"]

        print(
            f"  Uploaded {video_id}; "
            f"scheduled for "
            f"{publish_at.isoformat()}"
        )

        # -----------------------------------------------------
        # UPDATE LOCAL HISTORY
        # -----------------------------------------------------

        remember_content(
            video_id,
            {
                **result,
                "title": (
                    result.get("title")
                    or topic
                ),
                "topic": (
                    result.get("topic")
                    or topic
                ),
                "content_type": "shorts",
                "category": result.get(
                    "category",
                    ""
                ),
                "hook_style": result.get(
                    "hook_style",
                    ""
                ),
                "quality_score": result.get(
                    "quality_score",
                    0
                ),
                "specific_angle": result.get(
                    "specific_angle",
                    ""
                ),
                "primary_entity": result.get(
                    "primary_entity",
                    ""
                ),
                "knowledge_gap": result.get(
                    "knowledge_gap",
                    ""
                ),
            },
        )

        successful_uploads += 1

    # =========================================================
    # ONE GIT PUSH AFTER THE ENTIRE BATCH
    # =========================================================

    if successful_uploads == BATCH_SIZE:
        persist_history_to_repo()
    else:
        print(
            f"Only {successful_uploads}/{BATCH_SIZE} Shorts "
            f"uploaded. Skipping Git history push."
        )

    print(
        f"Batch complete. "
        f"{successful_uploads}/{BATCH_SIZE} Shorts scheduled; "
        f"runner exits now."
    )


if __name__ == "__main__":
    main()