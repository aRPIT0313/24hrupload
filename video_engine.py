import os
import subprocess
import tempfile
import shutil
from pathlib import Path
import requests


# =========================================
# FFMPEG PATH
# =========================================

FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


# =========================================
# RUN FFMPEG
# =========================================

def run_ffmpeg(command):

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


# =========================================
# DOWNLOAD VIDEO
# =========================================

def download_video(url, output_path):

    response = requests.get(
        url,
        stream=True,
        timeout=60
    )

    response.raise_for_status()

    with open(output_path, "wb") as file:

        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):

            if chunk:
                file.write(chunk)


# =========================================
# DOWNLOAD CLIPS
# =========================================

def download_clips(videos, folder):

    clip_paths = []

    for index, video in enumerate(videos):

        if video is None:

            raise ValueError(
                f"No video found for scene {index + 1}"
            )

        path = os.path.join(
            folder,
            f"clip_{index + 1}.mp4"
        )

        download_video(
            video["url"],
            path
        )

        clip_paths.append(path)

    return clip_paths


# =========================================
# PREPARE CLIP
# =========================================

def prepare_clip(
    input_path,
    output_path,
    duration,
):
    """Prepare a clean vertical clip.

    Captions are intentionally NOT rendered here.  We render one continuous
    karaoke-style caption track after all clips are concatenated so the words
    follow the voice across scene cuts instead of appearing as static labels.
    """
    filter_string = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920"
    )

    command = [
        FFMPEG_PATH,
        "-y",
        "-stream_loop", "-1",
        "-i", input_path,
        "-t", str(duration),
        "-vf", filter_string,
        "-r", "30",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        output_path
    ]

    run_ffmpeg(command)


# =========================================
# ASS / KARAOKE CAPTIONS
# =========================================

def _ass_escape(text):
    """Escape text for an ASS subtitle event."""
    text = str(text or "")
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    text = text.replace("\r", " ").replace("\n", " ")
    return text.strip()


def _ass_time(seconds):
    """ASS timestamp: H:MM:SS.cc"""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        whole += 1
        centiseconds = 0
    if whole >= 60:
        minutes += 1
        whole = 0
    if minutes >= 60:
        hours += 1
        minutes = 0
    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"


def _caption_words(text):
    """Return clean spoken words while retaining punctuation for pacing."""
    import re
    return re.findall(r"\S+", str(text or "").replace("\n", " "))


def _build_karaoke_ass(voiceover, duration, output_path):
    """Create modern word-by-word karaoke captions for the full voiceover.

    We do not have forced-alignment timestamps from Gemini TTS, so timing is
    estimated from word length and punctuation.  The total timing is always
    stretched to the exact TTS duration, which keeps the captions synced with
    the finished audio even when the TTS pace changes.
    """
    import re

    words = _caption_words(voiceover)
    if not words:
        return False

    # Approximate natural speech timing. Longer words get slightly more time;
    # punctuation gets a small pause. This is more natural than equal timing.
    weights = []
    for word in words:
        clean = re.sub(r"[^A-Za-z0-9']", "", word)
        weight = max(1.0, len(clean) ** 0.72)
        if re.search(r"[,;:]$", word):
            weight *= 1.18
        elif re.search(r"[.!?]$", word):
            weight *= 1.42
        weights.append(weight)

    total_weight = sum(weights) or 1.0
    raw_durations = [duration * w / total_weight for w in weights]

    # Keep captions readable: normally 3–5 words, but break early when the
    # text would become too wide on a 1080x1920 Short.
    groups = []
    current = []
    current_chars = 0
    for index, word in enumerate(words):
        extra = len(word) + (1 if current else 0)
        if current and (len(current) >= 4 or current_chars + extra > 28):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(index)
        current_chars += extra
    if current:
        groups.append(current)

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,64,&H00FFFFFF,&H00B8B8B8,&H00101010,&H99000000,-1,0,0,0,100,100,0,0,3,5,2,2,90,90,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    cursor = 0.0
    for group in groups:
        group_start = cursor
        group_end = cursor + sum(raw_durations[i] for i in group)
        pieces = []
        for i in group:
            word_duration_cs = max(1, int(round(raw_durations[i] * 100)))
            pieces.append(r"{\kf" + str(word_duration_cs) + "}" + _ass_escape(words[i]))
        text = " ".join(pieces)
        events.append(
            f"Dialogue: 0,{_ass_time(group_start)},{_ass_time(group_end)},Karaoke,,0,0,0,,{text}"
        )
        cursor = group_end

    Path(output_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return True


def burn_karaoke_captions(video_path, voiceover, duration, output_path, temp_dir):
    """Burn the continuous karaoke caption track onto the final video."""
    ass_path = os.path.join(temp_dir, "captions.ass")
    if not _build_karaoke_ass(voiceover, duration, ass_path):
        shutil.copyfile(video_path, output_path)
        return

    # The ASS path must be escaped for FFmpeg's subtitles filter on Windows.
    subtitle_path = ass_path.replace("\\", "/").replace(":", "\\:")
    subtitle_path = subtitle_path.replace("'", "\\'")
    vf = f"subtitles='{subtitle_path}'"

    command = [
        FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    run_ffmpeg(command)


# =========================================
# CONCATENATE
# =========================================

def concatenate_clips(
    clip_paths,
    output_path
):

    concat_file = output_path + "_concat.txt"


    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as file:

        for path in clip_paths:

            safe_path = path.replace(
                "'",
                "'\\''"
            )

            file.write(
                f"file '{safe_path}'\n"
            )


    command = [

        FFMPEG_PATH,

        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        concat_file,

        "-c",
        "copy",

        output_path
    ]


    run_ffmpeg(command)


# =========================================
# GET AUDIO DURATION
# =========================================

def get_audio_duration(audio_path):

    command = [

        FFMPEG_PATH,

        "-i",
        audio_path

    ]


    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )


    output = result.stderr


    import re


    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        output
    )


    if not match:

        raise RuntimeError(
            "Could not determine audio duration."
        )


    hours = int(
        match.group(1)
    )

    minutes = int(
        match.group(2)
    )

    seconds = float(
        match.group(3)
    )


    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# =========================================
# ADD VOICEOVER
# =========================================

def add_voiceover(
    video_path,
    audio_path,
    output_path
):

    command = [

        FFMPEG_PATH,

        "-y",

        "-i",
        video_path,

        "-i",
        audio_path,

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        # Video should follow audio duration
        "-tune",
        "zerolatency",

        output_path
    ]


    run_ffmpeg(command)


# =========================================
# BUILD SHORT
# =========================================

def build_short(
    videos,
    audio_data,
    scenes,
    voiceover=None
):

    temp_dir = tempfile.mkdtemp(
        prefix="ai_short_"
    )


    # =====================================
    # SAVE AUDIO
    # =====================================

    audio_path = os.path.join(
        temp_dir,
        "voice.wav"
    )


    with open(
        audio_path,
        "wb"
    ) as file:

        file.write(audio_data)


    # =====================================
    # GET ACTUAL VOICE DURATION
    # =====================================

    audio_duration = get_audio_duration(
        audio_path
    )


    # =====================================
    # DOWNLOAD CLIPS
    # =====================================

    clip_folder = os.path.join(
        temp_dir,
        "downloads"
    )

    os.makedirs(
        clip_folder,
        exist_ok=True
    )


    downloaded = download_clips(
        videos,
        clip_folder
    )


    # =====================================
    # CALCULATE SCENE DURATIONS
    # =====================================
    # Respect Gemini's timing plan so the first scene is genuinely ~2 seconds
    # instead of being stretched to an equal share of the narration.
    number_of_scenes = len(downloaded)

    def _planned_duration(scene):
        import re
        text = str(scene.get("time", ""))
        match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        start = float(match.group(1))
        end = float(match.group(2))
        value = end - start
        return value if value > 0 else None

    planned = [_planned_duration(scene) for scene in scenes[:number_of_scenes]]
    if len(planned) != number_of_scenes or any(x is None for x in planned):
        base_duration = audio_duration / number_of_scenes
        durations = [base_duration for _ in downloaded]
    else:
        total_planned = sum(planned)
        scale = audio_duration / total_planned if total_planned > 0 else 1.0
        durations = [max(0.8, value * scale) for value in planned]
        correction = audio_duration / sum(durations)
        durations = [value * correction for value in durations]


    # =====================================
    # PREPARE CLIPS
    # =====================================

    prepared_clips = []


    for index, input_path in enumerate(
        downloaded
    ):

        output_path = os.path.join(
            temp_dir,
            f"prepared_{index + 1}.mp4"
        )


        prepare_clip(
            input_path,
            output_path,
            durations[index]
        )


        prepared_clips.append(
            output_path
        )


    # =====================================
    # CONCATENATE
    # =====================================

    combined_path = os.path.join(
        temp_dir,
        "combined.mp4"
    )


    concatenate_clips(
        prepared_clips,
        combined_path
    )


    # =====================================
    # ADD VOICE
    # =====================================

    final_path = os.path.join(
        temp_dir,
        "final_short.mp4"
    )


    add_voiceover(
        combined_path,
        audio_path,
        final_path
    )

    # =====================================
    # BURN WORD-BY-WORD CAPTIONS
    # =====================================

    captioned_path = os.path.join(
        temp_dir,
        "final_short_captioned.mp4"
    )

    # Backward compatible fallback: if an older caller does not pass the
    # voiceover, use the scene labels rather than failing the render.
    if not voiceover:
        voiceover = " ".join(
            str(scene.get("onscreen_text", "")).strip()
            for scene in scenes
            if str(scene.get("onscreen_text", "")).strip()
        )

    burn_karaoke_captions(
        final_path,
        voiceover,
        audio_duration,
        captioned_path,
        temp_dir
    )

    final_path = captioned_path


    # =====================================
    # RETURN VIDEO
    # =====================================

    try:
        with open(
            final_path,
            "rb"
        ) as file:
            result = file.read()
        return result
    finally:
        # The final MP4 is returned in memory. Remove all source clips,
        # audio and FFmpeg intermediates immediately.
        shutil.rmtree(temp_dir, ignore_errors=True)