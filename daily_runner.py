"""
Headless daily scheduler.

Use this with Windows Task Scheduler after completing YouTube OAuth once
through Streamlit. It generates 24 Shorts and schedules them one hour apart.

Environment variables:
DAILY_NICHE
DAILY_STYLE
DAILY_VOICE
DAILY_FIRST_PUBLISH=08:00
DAILY_TIMEZONE=Asia/Kolkata
DAILY_SYNTHETIC_MEDIA=false
DAILY_COUNT=24
DAILY_INTERVAL_HOURS=1
"""

import logging
import os
from datetime import datetime, timedelta
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from analytics import fetch_shorts_performance, build_performance_insights, recommend_publish_slots

from batch_generator import (
    build_schedule,
    generate_one_short,
    generate_shorts_batch,
    generate_topics,
    generate_topic_candidates,
    load_title_history,
    remember_title,
)
from youtube_uploader import is_connected, schedule_short
from pexels import get_best_video
from tts import generate_voice
from video_engine import build_short
from content_learning import remember_content

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "daily_runner.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

NICHE = os.getenv(
    "DAILY_NICHE",
    "bizarre facts, strange places, real mysteries, shocking history, survival stories, surprising science and hidden everyday facts",
)
STYLE = os.getenv("DAILY_STYLE", "Documentary")
VOICE = os.getenv("DAILY_VOICE", "Kore")
TIMEZONE = os.getenv("DAILY_TIMEZONE", "Asia/Kolkata")
FIRST_PUBLISH = os.getenv("DAILY_FIRST_PUBLISH", "08:00")
SYNTHETIC_MEDIA = os.getenv(
    "DAILY_SYNTHETIC_MEDIA", "false"
).strip().lower() in {"1", "true", "yes"}
COUNT = max(1, min(24, int(os.getenv("DAILY_COUNT", "24"))))
INTERVAL_HOURS = max(1, int(os.getenv("DAILY_INTERVAL_HOURS", "1")))


def get_first_publish():
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    hour, minute = map(int, FIRST_PUBLISH.split(":"))

    target = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    if target <= now:
        target += timedelta(days=1)

    return target


def main():
    logging.info("Starting daily %d-Short adaptive batch.", COUNT)

    if not is_connected():
        raise RuntimeError(
            "YouTube is not connected. Run Streamlit once and complete "
            "the Google OAuth flow first."
        )

    try:
        performance_rows = fetch_shorts_performance(days=21)
        performance_insights = ""
        logging.info("Analytics loaded only for optional publishing-time scheduling; ignored for topic selection.")
    except Exception as exc:
        performance_insights = "Analytics unavailable for this run: " + str(exc)
        logging.warning(performance_insights)

    used_titles = load_title_history()
    trending_count = min(COUNT, int(os.getenv("DAILY_TRENDING_COUNT", "6")))
    candidates = generate_topic_candidates(
        n=COUNT,
        niche=NICHE,
        avoid_topics=used_titles,
        trending_count=trending_count,
        performance_insights=performance_insights,
    )
    topics = [x["topic"] for x in candidates]
    candidate_map = {x["topic"].casefold(): x for x in candidates}
    logging.info("Quality gate selected %d high-curiosity topics; technical/generic candidates were rejected.", len(topics))

    logging.info("Generating all %d scripts in one Gemini request with 3-hook testing per Short.", COUNT)
    scripts = generate_shorts_batch(topics=topics, style=STYLE)
    script_map = {str(x.get("topic", "")).strip().casefold(): x for x in scripts}

    slots = recommend_publish_slots(performance_rows, COUNT, timezone_name=TIMEZONE)
    logging.info("AI learned scheduling plan: %s", ", ".join(s.strftime("%Y-%m-%d %H:%M %Z") for s in slots))
    used_slots = []

    successful_topics = []

    for index, topic in enumerate(topics, start=1):
        try:
            logging.info("Generating Short %d/%d: %s", index, COUNT, topic)

            result = script_map.get(topic.casefold())
            if result is None:
                raise RuntimeError(f"No batch script returned for: {topic}")

            audio_data = generate_voice(
                text=result["voiceover"],
                voice=VOICE,
                style=STYLE,
            )
            scenes = result.get("scenes", [])
            videos = []
            for scene in scenes:
                query = str(scene.get("search_query", "")).strip()
                if not query:
                    videos.append(None)
                    continue
                videos.append(get_best_video(query))
                time.sleep(0.15)
            if any(v is None for v in videos):
                raise RuntimeError("No suitable Pexels footage for one or more scenes.")
            video_bytes = build_short(
                videos=videos,
                audio_data=audio_data,
                scenes=scenes,
                voiceover=result.get("voiceover", ""),
            )

            slot = slots[index - 1]
            now = datetime.now(slot.tzinfo)

            if slot <= now:
                slot = (now.replace(minute=0, second=0, microsecond=0)
                        + timedelta(hours=1))

            if used_slots and slot <= used_slots[-1]:
                slot = used_slots[-1] + timedelta(hours=1)

            used_slots.append(slot)

            hashtags = result.get("hashtags", [])
            tags = [
                str(x).lstrip("#").strip()
                for x in hashtags
                if str(x).strip()
            ]

            final_title = topic

            response = schedule_short(
                video_bytes=video_bytes,
                title=final_title,
                description=result.get(
                    "description",
                    result.get("caption", ""),
                ),
                tags=tags,
                publish_at=slot,
                category_id="27",
                made_for_kids=False,
                contains_synthetic_media=SYNTHETIC_MEDIA,
            )

            # The chosen topic is the canonical title stored for deduplication.
            remember_title(topic)
            candidate_meta = candidate_map.get(topic.casefold(), {})
            remember_content(response["id"], {
                **candidate_meta,
                "topic": topic,
                "selected_hook": result.get("selected_hook", result.get("hook", "")),
                "cta": result.get("cta", ""),
            })

            logging.info(
                "Scheduled %d/%d: %s | %s",
                index,
                COUNT,
                response["id"],
                slot.isoformat(),
            )
            successful_topics.append(topic)

        except Exception:
            logging.exception("Short %d/%d failed: %s", index, COUNT, topic)

    logging.info(
        "Daily run finished. %d/%d successful.",
        len(successful_topics),
        COUNT,
    )


if __name__ == "__main__":
    main()
