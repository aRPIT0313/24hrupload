import os
import re
import json
import math
import shutil
import tempfile
import subprocess
import wave
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import types

from gemini_client import client, call_with_retry
from tts import generate_voice
from content_learning import get_history_topics, content_similarity
from pexels import get_best_landscape_video

REAL_FOOTAGE_TIMEOUT = 15


# ============================================================
# CONFIG
# ============================================================

# Tried in order. On a 503 / rate limit the request is retried with backoff
# on each model, then falls back to the next one.
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

BASE_DIR = r"G:\AI_GEN"

FFMPEG_PATH = (
    r"C:\Users\DELL\AppData\Local\Microsoft\WinGet"
    r"\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0-full_build-shared\bin"
    r"\ffmpeg.exe"
)

# Number of simultaneous Pexels operations.
# 4 is a good balance between speed and API/network pressure.
PEXELS_WORKERS = 4

# Desired average visual duration.
# Actual number of visuals is calculated from TTS duration.
MIN_CLIP_SECONDS = 10
MAX_CLIP_SECONDS = 15
TARGET_CLIP_SECONDS = 12

# Gemini generates enough visual candidates.
# We later select only the number actually needed.
MAX_VISUAL_CANDIDATES = 28


# ============================================================
# TEMP DIRECTORY CLEANUP
# ============================================================

def cleanup_temp_directory(temp_dir):
    """Safely remove a temporary long-video working directory."""
    if not temp_dir:
        return

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as exc:
        # Cleanup must never hide the original generation error.
        print(f"Warning: could not fully clean temp directory {temp_dir}: {exc}")



# ============================================================
# FFMPEG CHECK
# ============================================================

def check_ffmpeg():

    if not os.path.exists(FFMPEG_PATH):

        raise FileNotFoundError(
            "FFmpeg not found at:\n\n"
            + FFMPEG_PATH
        )


# ============================================================
# RUN FFMPEG
# ============================================================

def run_ffmpeg(command):

    check_ffmpeg()

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            "FFmpeg failed:\n\n"
            + result.stderr
        )


# ============================================================
# GET MEDIA DURATION
# ============================================================

def get_media_duration(media_path):

    command = [
        FFMPEG_PATH,
        "-i",
        media_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    output = result.stderr

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        output
    )

    if not match:

        raise RuntimeError(
            "Could not determine media duration:\n\n"
            + output
        )

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# ============================================================
# CALCULATE VISUAL COUNT
# ============================================================

def calculate_visual_count(narration_duration):
    """Choose a reasonable number of visual clips for the narration length."""
    count = math.ceil(narration_duration / TARGET_CLIP_SECONDS)
    minimum_count = math.ceil(narration_duration / MAX_CLIP_SECONDS)
    maximum_count = math.ceil(narration_duration / MIN_CLIP_SECONDS)

    count = max(count, minimum_count)
    count = min(count, maximum_count)

    return max(count, 1)


# ============================================================
# SELECT VISUALS
# ============================================================

def select_visuals(visual_shots, required_count):
    """
    Select exactly the number of visual shots needed for the narration.

    Gemini may return fewer/more candidate shots than the final video needs.
    We preserve the original story order, prefer unique search queries, and
    only cycle through candidates if Gemini supplied too few unique shots.
    This function performs no API calls.
    """
    if not isinstance(visual_shots, list) or not visual_shots:
        raise RuntimeError("No visual shots were provided by Gemini.")

    try:
        required_count = int(required_count)
    except (TypeError, ValueError):
        required_count = 1

    required_count = max(1, required_count)

    cleaned = []
    seen_queries = set()

    # First pass: unique visual queries in Gemini's story order.
    for shot in visual_shots:
        if not isinstance(shot, dict):
            continue

        query = str(shot.get("search_query", "")).strip()
        if not query:
            continue

        key = re.sub(r"\s+", " ", query.lower())
        if key in seen_queries:
            continue

        seen_queries.add(key)
        cleaned.append(dict(shot))

        if len(cleaned) >= required_count:
            break

    # If Gemini supplied duplicate/insufficient queries, use the remaining
    # valid candidates rather than failing generation.
    if len(cleaned) < required_count:
        for shot in visual_shots:
            if not isinstance(shot, dict):
                continue
            query = str(shot.get("search_query", "")).strip()
            if not query:
                continue
            if any(existing is shot for existing in cleaned):
                continue
            cleaned.append(dict(shot))
            if len(cleaned) >= required_count:
                break

    # If the AI generated fewer candidates than the narration requires, cycle
    # them deterministically. Reusing a shot is preferable to silently making
    # the final video shorter than the narration.
    if not cleaned:
        raise RuntimeError("Gemini did not generate any usable visual shots.")

    result = []
    for index in range(required_count):
        shot = dict(cleaned[index % len(cleaned)])
        shot["shot_number"] = index + 1
        result.append(shot)

    return result


# ============================================================
# SAFE JSON EXTRACTION
# ============================================================

def extract_json(text):

    if not text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    text = text.strip()

    # Remove markdown fences if Gemini adds them.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    text = text.strip()

    # First attempt: complete JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: locate first { and last }.
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:

        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)

        except json.JSONDecodeError as error:

            raise RuntimeError(
                "Gemini returned invalid JSON.\n\n"
                + candidate[:12000]
            ) from error

    raise RuntimeError(
        "Gemini returned invalid JSON.\n\n"
        + text[:12000]
    )


# ============================================================
# GENERATE LONG VIDEO PLAN
# ============================================================

def generate_long_video_plan(
    title,
    style,
    target_minutes=None,
    performance_insights="",
    avoid_topics=None,
):

    # Generate a compact visual pool. For a strict 3–4 minute video, 28
    # candidates are enough and avoid wasting Gemini output tokens on shots
    # that can never be used.
    visual_count = MAX_VISUAL_CANDIDATES

    avoid_topics = [str(x).strip() for x in (avoid_topics or []) if str(x).strip()]
    avoid_text = "\n".join(f"- {x}" for x in avoid_topics[-800:]) or "- No previous long-form topics are available."

    prompt = f"""
You are an expert YouTube documentary writer and video producer.

Create a complete long-form YouTube video plan.

If TOPIC is blank, you MUST choose one strong long-form topic yourself using the
CHANNEL PERFORMANCE DATA. Favor subjects adjacent to proven audience interests,
strong curiosity, broad appeal and good visual storytelling potential. Do not simply
copy an existing Short title. Choose a genuinely new underlying subject/angle.

TOPIC:
{title if title else "Choose the topic yourself from the channel-performance data below."}

CHANNEL PERFORMANCE DATA (use this only when topic selection is AI mode):
{performance_insights or "No channel data available; choose a broad, high-curiosity topic suited to the channel."}

PREVIOUS LONG-FORM CONTENT — DO NOT REPEAT THE UNDERLYING SUBJECT
{avoid_text}

DUPLICATE RULE:
- The list above is the channel's existing LONG-FORM history, not just exact titles.
- Do not make another video about the same event, person, place, invention, discovery, object,
  incident or core subject even if you change the title, wording, hook, or angle.
- A different title is still a duplicate if the viewer would recognize it as the same underlying story.
- Prefer a genuinely new subject.

STYLE:
{style}

TARGET LENGTH:
Exactly approximately {target_minutes} minutes.

IMPORTANT:
The user has selected a strict 3-4 minute long-form format.
Do not write for 5+ minutes.
Aim for roughly 135-145 spoken words per minute and approximately
{int(target_minutes * 140) if target_minutes else 490} words, allowing a small natural variation so the final TTS remains close to the requested length.

Write a natural, engaging narration suitable for voice synthesis.

The narration MUST NOT sound like a textbook or Wikipedia article.

Use:
- strong opening hook
- curiosity
- storytelling
- varied sentence lengths
- emotional moments where appropriate
- suspense where appropriate
- natural transitions
- occasional rhetorical questions
- emphasis-friendly wording
- conversational documentary narration

STRUCTURE:

1. HOOK
Immediately create curiosity.
Do not simply begin with:
"Today we are going to talk about..."

2. INTRODUCTION
Give context and explain why the viewer should care.

3. MAIN STORY
Develop the topic chronologically or logically.

4. KEY DETAILS
Explain important facts clearly.

5. HUMAN / EMOTIONAL DIMENSION
Where appropriate, show the human consequences.

6. BIGGER MEANING
Explain why this topic still matters.

7. ENDING
Do NOT suddenly stop after the last fact.

End with:
- a memorable conclusion
- reflection
- emotional payoff
- and a natural YouTube-style closing thought.

Do NOT write stage directions such as:
[dramatic pause]
[slow voice]
[emotional]
etc.

The voice system will handle delivery.

------------------------------------------------------------

VISUALS

Create {visual_count} visual-shot candidates.

Each visual MUST match the narration beat it represents. Do NOT choose a visually interesting clip that is unrelated to the story.

Each shot must contain:

"shot_number"
"search_query"
"caption"
"visual_role"

VISUAL ACCURACY RULES — VERY IMPORTANT:
- Treat the TOPIC and NARRATION as the source of truth.
- Every search_query must describe something that is actually being discussed in that part of the narration.
- Include the main subject, country, organization, person, mission, place or object when relevant.
- Keep important topic anchors in the query. Example: for an Indian space mission, use queries such as "India ISRO rocket launch", "Indian space mission satellite", "ISRO mission control", "India rocket launch pad" — NOT generic "riot crowd", "British protest", "European city" or unrelated historical footage.
- NEVER use generic stock footage just because it looks cinematic.
- NEVER introduce another country, conflict, protest, riot, city, industry or historical event unless the narration explicitly discusses it.
- If an exact event is difficult to find, use a visually equivalent shot that is still from the same subject domain. For example, an Indian space story may use an Indian rocket, ISRO facility, launch pad, satellite, mission control or Earth-from-space visual — not unrelated protests.

Pexels searches work best with short, concrete queries.
Use 3-7 meaningful words.
Avoid abstract queries and generic filler.

Do NOT burn captions or explanatory text into the video. The caption field is metadata only.
The video should rely on real footage, photographs, archival visuals, and cinematic editing.

------------------------------------------------------------

YOUTUBE METADATA

Generate:

title
description
hashtags
tags
chapters
recommended_duration_minutes
thumbnail_text
thumbnail_search_query

The title should be compelling but NOT clickbait.
The description should be useful and naturally written for YouTube.
Tags should be comma-separated concepts as an array.
Chapters should be a list of timestamp/title pairs starting at 00:00.
Set recommended_duration_minutes to exactly the requested duration (3 or 4).
thumbnail_text should be 2-5 powerful words maximum.
thumbnail_search_query should describe the strongest real visual for the thumbnail.

------------------------------------------------------------

RETURN ONLY VALID JSON.

Do not use markdown.

Required JSON structure:

{{
    "title": "string",
    "description": "string",
    "hashtags": ["#example"],
    "tags": ["example"],
    "chapters": [{{"time":"00:00","title":"Hook"}}],
    "recommended_duration_minutes": 5,
    "thumbnail_text": "string",
    "thumbnail_search_query": "string",
    "narration": "string",
    "visual_shots": [
        {{
            "shot_number": 1,
            "search_query": "string",
            "caption": "string",
            "visual_role": "hook"
        }}
    ]
}}

Do not add any other fields.
"""

    def request(model):
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9,
                max_output_tokens=30000
            )
        )

    try:

        response = call_with_retry(
            request,
            "Gemini long-video planning",
            GEMINI_MODELS
        )

    except Exception as error:

        raise RuntimeError(
            "Gemini long-video planning failed:\n\n"
            + str(error)
        ) from error

    data = extract_json(
        response.text
    )

    # ========================================================
    # VALIDATE
    # ========================================================

    narration = data.get(
        "narration",
        ""
    )

    if not isinstance(
        narration,
        str
    ) or not narration.strip():

        raise RuntimeError(
            "Gemini returned no narration."
        )

    visual_shots = data.get(
        "visual_shots",
        []
    )

    if not isinstance(
        visual_shots,
        list
    ):

        raise RuntimeError(
            "Gemini returned invalid visual_shots."
        )

    # Clean visuals.
    cleaned_visuals = []

    for index, shot in enumerate(
        visual_shots
    ):

        if not isinstance(
            shot,
            dict
        ):
            continue

        query = str(
            shot.get(
                "search_query",
                ""
            )
        ).strip()

        caption = str(
            shot.get(
                "caption",
                ""
            )
        ).strip()

        visual_role = str(
            shot.get(
                "visual_role",
                "story"
            )
        ).strip() or "story"

        if not query:
            continue

        cleaned_visuals.append(
            {
                "shot_number": index + 1,
                "search_query": query,
                "caption": caption,
                "visual_role": visual_role
            }
        )

    if not cleaned_visuals:

        raise RuntimeError(
            "Gemini did not generate usable visual shots."
        )

    data["visual_shots"] = cleaned_visuals

    # Ensure metadata exists.
    data["title"] = str(
        data.get(
            "title",
            title
        )
    ).strip()

    data["description"] = str(
        data.get(
            "description",
            ""
        )
    ).strip()

    hashtags = data.get(
        "hashtags",
        []
    )

    if not isinstance(
        hashtags,
        list
    ):
        hashtags = []

    data["hashtags"] = [
        str(tag).strip()
        for tag in hashtags
        if str(tag).strip()
    ]

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    data["tags"] = [str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()][:30]

    chapters = data.get("chapters", [])
    if not isinstance(chapters, list):
        chapters = []
    data["chapters"] = chapters

    try:
        recommended = int(data.get("recommended_duration_minutes", target_minutes or 5))
    except Exception:
        recommended = target_minutes or 5
    data["recommended_duration_minutes"] = max(3, min(4, recommended))
    data["thumbnail_text"] = str(data.get("thumbnail_text", data["title"])).strip()[:80]
    data["thumbnail_search_query"] = str(data.get("thumbnail_search_query", data["title"])).strip()

    return data


# ============================================================
# DOWNLOAD FILE
# ============================================================

def download_file(
    url,
    output_path
):

    response = requests.get(
        url,
        stream=True,
        timeout=120
    )

    response.raise_for_status()

    with open(
        output_path,
        "wb"
    ) as file:

        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):

            if chunk:
                file.write(chunk)


# ============================================================
# GENERATE COMPLETE VOICE
# ============================================================

def _normalize_narration_loudness(input_path, output_path):
    """Normalize the complete narration to stable spoken-word loudness."""
    run_ffmpeg([
        FFMPEG_PATH,
        "-y",
        "-i", input_path,
        "-af", (
            "highpass=f=70,"
            "acompressor=threshold=-24dB:ratio=2.0:attack=18:release=250:makeup=2:knee=6,"
            "loudnorm=I=-16:TP=-1.5:LRA=6"
        ),
        "-ar", "24000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_path,
    ])


def _split_narration_for_tts(narration, target_words=170):
    """
    Split long narration into sentence-safe chunks of roughly 170 words.

    Gemini can gradually increase speaking rate when asked to synthesize a very
    long narration in one request. Keeping each request around one minute gives
    the model a much smaller context while preserving the same voice settings.
    """
    text = re.sub(r"\s+", " ", (narration or "").strip())
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []
    current_words = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        words = sentence.split()
        count = len(words)

        if current and current_words + count > target_words:
            chunks.append(" ".join(current).strip())
            current = []
            current_words = 0

        # Protect against an unusually long single sentence.
        if count > target_words:
            for i in range(0, count, target_words):
                piece = " ".join(words[i:i + target_words]).strip()
                if piece:
                    chunks.append(piece)
            continue

        current.append(sentence)
        current_words += count

    if current:
        chunks.append(" ".join(current).strip())

    return chunks


def _concat_wav_files(wav_paths, output_path):
    """Concatenate WAV files with identical audio parameters."""
    if not wav_paths:
        raise ValueError("No WAV files supplied for concatenation.")

    first_params = None

    with wave.open(wav_paths[0], "rb") as first:
        first_params = first.getparams()
        frames = [first.readframes(first.getnframes())]

    for path in wav_paths[1:]:
        with wave.open(path, "rb") as current:
            params = current.getparams()
            if (
                params.nchannels != first_params.nchannels
                or params.sampwidth != first_params.sampwidth
                or params.framerate != first_params.framerate
                or params.comptype != first_params.comptype
            ):
                raise RuntimeError(
                    f"TTS chunk audio format mismatch: {path}"
                )
            frames.append(current.readframes(current.getnframes()))

    with wave.open(output_path, "wb") as output:
        output.setnchannels(first_params.nchannels)
        output.setsampwidth(first_params.sampwidth)
        output.setframerate(first_params.framerate)
        output.setcomptype(first_params.comptype, first_params.compname)
        for data in frames:
            output.writeframes(data)


def generate_complete_voice(narration, voice, style, output_path):
    """
    Generate long-form narration in short, sentence-safe TTS chunks.

    This is intentional: very long single TTS requests can start speaking
    faster as they approach the end of the narration. Each chunk gets the exact
    same neutral documentary instructions, then the WAV chunks are concatenated
    before the normal loudness processing.
    """
    chunks = _split_narration_for_tts(narration, target_words=170)
    if not chunks:
        raise RuntimeError("Narration is empty.")

    working_dir = os.path.dirname(output_path)
    raw_paths = []

    print(
        f"Generating long-form narration in {len(chunks)} pace-locked TTS chunk(s)..."
    )

    for index, chunk in enumerate(chunks, start=1):
        print(
            f"Generating TTS chunk {index}/{len(chunks)} "
            f"({len(chunk.split())} words)..."
        )

        audio_data = generate_voice(
            text=chunk,
            voice=voice,
            style="Documentary",
            pace="Normal",
            emotion="Neutral"
        )

        if not audio_data:
            raise RuntimeError(
                f"TTS returned empty audio for chunk {index}."
            )

        chunk_path = os.path.join(
            working_dir,
            f"narration_chunk_{index:02d}.wav"
        )
        with open(chunk_path, "wb") as file:
            file.write(audio_data)
        raw_paths.append(chunk_path)

    raw_path = os.path.join(
        working_dir,
        "narration_raw.wav"
    )
    normalized_path = os.path.join(
        working_dir,
        "narration_normalized.wav"
    )

    _concat_wav_files(raw_paths, raw_path)
    _normalize_narration_loudness(raw_path, normalized_path)
    shutil.copyfile(normalized_path, output_path)

    duration = get_media_duration(output_path)
    print(f"Final normalized narration duration: {duration:.2f}s")
    return duration



# ============================================================
# VISUAL DOWNLOAD / PREPARATION / CONCAT / AUDIO
# ============================================================

def _try_real_footage(query, output_path):
    """Best-effort Wikimedia Commons video lookup. Returns True on success; otherwise Pexels is used."""
    try:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": query + " filetype:video", "gsrnamespace": 6,
            "gsrlimit": 5, "prop": "imageinfo", "iiprop": "url|mime|size"
        }
        data = requests.get("https://commons.wikimedia.org/w/api.php", params=params,
                            timeout=REAL_FOOTAGE_TIMEOUT, headers={"User-Agent": "AI-Documentary-Generator/1.0"}).json()
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            url, mime = info.get("url"), info.get("mime", "")
            if url and (mime.startswith("video/") or url.lower().endswith((".mp4", ".webm", ".ogv"))):
                r = requests.get(url, timeout=REAL_FOOTAGE_TIMEOUT, stream=True,
                                 headers={"User-Agent": "AI-Documentary-Generator/1.0"})
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(1024 * 256):
                        if chunk: f.write(chunk)
                return True
    except Exception as exc:
        print(f"Real-footage lookup skipped for '{query}': {exc}")
    return False


def _topic_anchor(topic_context):
    """Create a compact subject anchor that keeps stock-footage searches on-topic."""
    text = re.sub(r"[^a-zA-Z0-9&' -]", " ", str(topic_context or ""))
    words = [w for w in re.sub(r"\s+", " ", text).strip().split() if len(w) > 1]
    return " ".join(words[:8]).strip()


def _build_visual_query(topic_context, query):
    """Keep Gemini's shot idea, but force the search to retain the documentary topic."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    anchor = _topic_anchor(topic_context)
    if not query:
        return anchor
    if not anchor:
        return query

    # Do not replace the shot idea. Add the topic anchor so stock search has
    # enough context to avoid drifting into unrelated stock footage.
    return f"{anchor} {query}".strip()[:180]


def download_visuals(visual_shots, temp_dir, topic_context="", progress_callback=None):
    downloaded = []
    total = len(visual_shots)
    for index, shot in enumerate(visual_shots):
        raw_query = str(shot.get("search_query", "")).strip()
        if not raw_query:
            continue
        query = _build_visual_query(topic_context, raw_query)
        print(f"Pexels {index + 1}/{total}: {query}")
        output_path = os.path.join(temp_dir, f"source_{index + 1:02d}.mp4")
        # Prefer real/archival Commons footage when available; never fail the video only
        # because a Commons result is unavailable. Pexels remains the reliable fallback.
        if not _try_real_footage(query, output_path):
            video = get_best_landscape_video(query)
            if not video or not video.get("url"):
                raise RuntimeError(f"No real footage or Pexels video found for:\n{query}")
            download_file(video["url"], output_path)
        downloaded.append({"path": output_path, "caption": shot.get("caption", ""), "query": query})
        if progress_callback:
            progress_callback(index + 1, total, f"Downloading footage {index + 1}/{total}")
    if not downloaded:
        raise RuntimeError("No visual footage could be downloaded from Pexels.")
    return downloaded


def prepare_clip(input_path, output_path, duration, caption=""):
    # Clean cinematic footage only: intentionally no burned-in captions.
    filter_string = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080"
    )
    run_ffmpeg([
        FFMPEG_PATH, "-y", "-stream_loop", "-1", "-i", input_path,
        "-t", str(duration), "-vf", filter_string, "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path
    ])


def prepare_visual_clips(visuals, temp_dir, narration_duration, progress_callback=None):
    count = len(visuals)
    clip_duration = narration_duration / max(count, 1)
    clip_duration = max(MIN_CLIP_SECONDS, min(MAX_CLIP_SECONDS, clip_duration))
    # Ensure the total exactly covers the narration; the final clip absorbs rounding.
    durations = [clip_duration] * count
    if count > 1:
        durations[-1] = max(MIN_CLIP_SECONDS, narration_duration - sum(durations[:-1]))
    else:
        durations[0] = narration_duration
    prepared = []
    for i, visual in enumerate(visuals):
        out = os.path.join(temp_dir, f"prepared_{i + 1:02d}.mp4")
        prepare_clip(visual["path"], out, durations[i], visual.get("caption", ""))
        prepared.append(out)
        if progress_callback:
            progress_callback(i + 1, count, f"Preparing footage {i + 1}/{count}")
    return prepared


def concatenate_video(video_paths, output_path):
    concat_file = os.path.join(os.path.dirname(output_path), "video_concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for path in video_paths:
            safe = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    run_ffmpeg([FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
                "-c", "copy", output_path])


def add_audio(video_path, audio_path, output_path):
    run_ffmpeg([FFMPEG_PATH, "-y", "-i", video_path, "-i", audio_path,
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest", output_path])


def _download_thumbnail_source(query, output_path):
    """Download a topic-specific Pexels video for the thumbnail frame."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query:
        return False

    try:
        video = get_best_landscape_video(query)
        if not video or not video.get("url"):
            return False
        download_file(video["url"], output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as exc:
        print(f"Topic-specific thumbnail footage unavailable for '{query}': {exc}")
        return False


def generate_thumbnail(source_video, output_path, text="", thumbnail_search_query="", topic_context=""):
    """Create a thumbnail from a topic-matched visual, with video-frame fallback."""
    work_dir = os.path.dirname(output_path)
    thumbnail_source = os.path.join(work_dir, "thumbnail_source.mp4")
    frame_path = os.path.join(work_dir, "thumbnail_frame.jpg")

    # Prefer the AI-generated thumbnail query over an arbitrary frame from the
    # finished video. This makes the thumbnail image itself match the topic.
    query = _build_visual_query(topic_context, thumbnail_search_query or text)
    used_topic_source = _download_thumbnail_source(query, thumbnail_source)

    try:
        source_for_frame = thumbnail_source if used_topic_source else source_video
        run_ffmpeg([FFMPEG_PATH, "-y", "-ss", "2", "-i", source_for_frame,
                    "-frames:v", "1", "-q:v", "2", frame_path])

        from PIL import Image, ImageDraw, ImageFont
        image = Image.open(frame_path).convert("RGB")
        image = image.resize((1280, 720), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle((0, 590, 1280, 720), fill=(0, 0, 0, 120))
        label = str(text or "").strip()[:55]
        if label:
            try:
                font = ImageFont.truetype("arialbd.ttf", 58)
            except Exception:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(((1280 - tw) / 2, 625), label, fill=(255,255,255,255), font=font,
                      stroke_width=2, stroke_fill=(0,0,0,220))
        image.save(output_path, "JPEG", quality=92, optimize=True)
    finally:
        for path in (frame_path, thumbnail_source):
            try:
                os.remove(path)
            except OSError:
                pass

# ============================================================
# BUILD COMPLETE LONG VIDEO
# ============================================================

def build_long_video(
    narration,
    visual_shots,
    voice,
    style,
    thumbnail_text="",
    thumbnail_search_query="",
    topic_context="",
    progress_callback=None
):

    os.makedirs(
        BASE_DIR,
        exist_ok=True
    )

    temp_dir = tempfile.mkdtemp(
        prefix="ai_long_",
        dir=BASE_DIR
    )

    final_video = None

    try:

        # ====================================================
        # STEP 1 — ONE TTS REQUEST + FINAL LOUDNESS NORMALIZATION
        # ====================================================

        if progress_callback:

            progress_callback(
                0,
                1,
                "Generating narration and normalizing final loudness..."
            )

        narration_audio = os.path.join(
            temp_dir,
            "narration.wav"
        )

        narration_duration = (
            generate_complete_voice(
                narration=narration,
                voice=voice,
                style=style,
                output_path=narration_audio
            )
        )

        print(
            f"Actual narration duration: "
            f"{narration_duration:.2f}s"
        )

        # ====================================================
        # STEP 2 — CALCULATE REQUIRED VISUAL COUNT
        # ====================================================

        required_visuals = calculate_visual_count(
            narration_duration
        )

        print(
            f"Required visuals: "
            f"{required_visuals}"
        )

        # ====================================================
        # STEP 3 — SELECT ONLY REQUIRED VISUALS
        # ====================================================

        selected_visuals = select_visuals(
            visual_shots,
            required_visuals
        )

        print(
            f"Using {len(selected_visuals)} visuals "
            f"for {narration_duration / 60:.2f} minutes."
        )

        # ====================================================
        # STEP 4 — PARALLEL PEXELS SEARCH + DOWNLOAD
        # ====================================================

        visuals = download_visuals(
            visual_shots=selected_visuals,
            temp_dir=temp_dir,
            topic_context=topic_context,
            progress_callback=progress_callback
        )

        # ====================================================
        # STEP 5 — PREPARE VISUAL CLIPS
        # ====================================================

        prepared_clips = prepare_visual_clips(
            visuals=visuals,
            temp_dir=temp_dir,
            narration_duration=narration_duration,
            progress_callback=progress_callback
        )

        # ====================================================
        # STEP 6 — CONCAT VISUALS
        # ====================================================

        visual_video = os.path.join(
            temp_dir,
            "visual_video.mp4"
        )

        concatenate_video(
            video_paths=prepared_clips,
            output_path=visual_video
        )

        # ====================================================
        # STEP 7 — ADD CONTINUOUS AUDIO
        # ====================================================

        temp_final_video = os.path.join(
            temp_dir,
            "final_long_video.mp4"
        )

        add_audio(
            video_path=visual_video,
            audio_path=narration_audio,
            output_path=temp_final_video
        )

        # ====================================================
        # STEP 8 — COPY FINAL VIDEO OUTSIDE TEMP DIRECTORY
        # ====================================================

        final_output_dir = os.path.join(
            BASE_DIR,
            "final_videos"
        )

        os.makedirs(
            final_output_dir,
            exist_ok=True
        )

        safe_title = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            "long_video"
        )

        final_video = os.path.join(
            final_output_dir,
            safe_title + ".mp4"
        )

        # Avoid overwriting an existing final video.
        counter = 1

        original_final = final_video

        while os.path.exists(
            final_video
        ):

            final_video = os.path.join(
                final_output_dir,
                f"{safe_title}_{counter}.mp4"
            )

            counter += 1

        shutil.copy2(
            temp_final_video,
            final_video
        )

        # Verify final file exists.
        if not os.path.exists(
            final_video
        ):

            raise RuntimeError(
                "Final video was not created."
            )

        final_size = os.path.getsize(
            final_video
        )

        thumbnail_output = os.path.join(
            final_output_dir,
            os.path.splitext(os.path.basename(final_video))[0] + "_thumbnail.jpg"
        )
        generate_thumbnail(
            source_video=final_video,
            output_path=thumbnail_output,
            text=thumbnail_text or "Documentary",
            thumbnail_search_query=thumbnail_search_query,
            topic_context=topic_context
        )

        if final_size == 0:

            raise RuntimeError(
                "Final video file is empty."
            )

        print(
            f"Final video created:\n"
            f"{final_video}"
        )

        # ====================================================
        # STEP 9 — CLEAN ALL 40/30/etc TEMP CLIPS
        # ====================================================

        # IMPORTANT:
        # The temporary directory contains:
        #
        # source_XXX.mp4
        # prepared_XXX.mp4
        # narration.wav
        # visual_video.mp4
        # final_long_video.mp4
        #
        # Everything is deleted AFTER the final video has
        # successfully been copied to final_videos.
        # ====================================================

        cleanup_temp_directory(
            temp_dir
        )

        temp_dir = None

        # ====================================================
        # STEP 10 — RETURN FINAL VIDEO
        # ====================================================

        with open(
            final_video,
            "rb"
        ) as file:

            video_data = file.read()

        return {
            "video": video_data,
            "duration": narration_duration,
            "temp_dir": None,
            "output_path": final_video,
            "thumbnail_path": thumbnail_output,
            "visual_count": required_visuals
        }

    except Exception:

        # If something fails before final creation,
        # clean the temporary files too.
        cleanup_temp_directory(
            temp_dir
        )

        raise