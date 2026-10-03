import os
import re
import tempfile
import subprocess
import requests

from tts import generate_voice
from pexels import get_best_landscape_video


# =========================================
# FFMPEG
# =========================================

FFMPEG_PATH = (
    r"C:\Users\DELL\AppData\Local\Microsoft\WinGet"
    r"\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0-full_build-shared\bin\ffmpeg.exe"
)


# =========================================
# G DRIVE WORKSPACE
# =========================================

BASE_DIR = r"G:\AI_GEN"

os.makedirs(
    BASE_DIR,
    exist_ok=True
)


# =========================================
# CHECK FFMPEG
# =========================================

def check_ffmpeg():

    if not os.path.isfile(FFMPEG_PATH):

        raise FileNotFoundError(
            "FFmpeg not found at:\n"
            + FFMPEG_PATH
        )


# =========================================
# RUN FFMPEG
# =========================================

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


# =========================================
# DOWNLOAD FILE
# =========================================

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


# =========================================
# GET MEDIA DURATION
# =========================================

def get_media_duration(
    media_path
):

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
            "Could not determine media duration:\n"
            + output
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
# ESCAPE DRAW TEXT
# =========================================

def escape_drawtext(
    text
):

    text = str(text)

    text = text.replace(
        "\\",
        "\\\\"
    )

    text = text.replace(
        ":",
        "\\:"
    )

    text = text.replace(
        "'",
        "\\'"
    )

    text = text.replace(
        "%",
        "\\%"
    )

    text = text.replace(
        "\n",
        " "
    )

    return text


# =========================================
# GENERATE ONE CONTINUOUS VOICE
# =========================================

def generate_complete_voice(
    narration,
    voice,
    style,
    output_path
):

    print(
        "Generating ONE complete narration..."
    )

    audio_data = generate_voice(
        text=narration,
        voice=voice,
        style=style
    )

    if not audio_data:

        raise RuntimeError(
            "TTS returned empty audio."
        )

    with open(
        output_path,
        "wb"
    ) as file:

        file.write(
            audio_data
        )

    duration = get_media_duration(
        output_path
    )

    print(
        f"Voice generated: "
        f"{duration:.2f} seconds"
    )

    return duration


# =========================================
# DOWNLOAD 40 PEXELS VIDEOS
# =========================================

def download_visuals(
    visual_shots,
    temp_dir,
    progress_callback=None
):

    downloaded = []

    total = len(
        visual_shots
    )

    for index, shot in enumerate(
        visual_shots
    ):

        query = shot[
            "search_query"
        ]

        print(
            f"Pexels {index + 1}/{total}: "
            f"{query}"
        )

        video = get_best_landscape_video(
            query
        )

        if video is None:

            raise RuntimeError(
                "No Pexels video found for:\n"
                + query
            )

        output_path = os.path.join(
            temp_dir,
            f"source_{index + 1:02d}.mp4"
        )

        download_file(
            video["url"],
            output_path
        )

        downloaded.append(
            {
                "path": output_path,
                "caption": shot.get(
                    "caption",
                    ""
                ),
                "query": query
            }
        )

        if progress_callback:

            progress_callback(
                index + 1,
                total,
                f"Downloading footage "
                f"{index + 1}/{total}"
            )

    return downloaded


# =========================================
# PREPARE CLIP
# =========================================

def prepare_clip(
    input_path,
    output_path,
    duration,
    caption
):

    caption = escape_drawtext(
        caption
    )

    # -----------------------------------------
    # VIDEO + CAPTION
    # -----------------------------------------

    filter_string = (
        "scale=1920:1080:"
        "force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        "drawtext="
        "fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
        f"text='{caption}':"
        "fontcolor=white:"
        "fontsize=48:"
        "borderw=3:"
        "bordercolor=black:"
        "x=(w-text_w)/2:"
        "y=h-120"
    )

    command = [

        FFMPEG_PATH,

        "-y",

        # Loop source if shorter than required
        "-stream_loop",
        "-1",

        "-i",
        input_path,

        "-t",
        str(duration),

        "-vf",
        filter_string,

        "-r",
        "30",

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "fast",

        "-pix_fmt",
        "yuv420p",

        output_path
    ]

    run_ffmpeg(
        command
    )


# =========================================
# CONCAT VIDEO CLIPS
# =========================================

def concatenate_video(
    video_paths,
    output_path
):

    concat_file = os.path.join(
        os.path.dirname(output_path),
        "video_concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as file:

        for path in video_paths:

            safe_path = (
                os.path.abspath(path)
                .replace(
                    "\\",
                    "/"
                )
                .replace(
                    "'",
                    "'\\''"
                )
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

    run_ffmpeg(
        command
    )


# =========================================
# ADD AUDIO
# =========================================

def add_audio(
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

        # Audio controls final duration
        "-shortest",

        output_path
    ]

    run_ffmpeg(
        command
    )


# =========================================
# BUILD COMPLETE LONG VIDEO
# =========================================

def build_long_video(
    narration,
    visual_shots,
    voice,
    style,
    progress_callback=None
):

    # =====================================
    # CREATE G DRIVE TEMP FOLDER
    # =====================================

    temp_dir = tempfile.mkdtemp(
        prefix="ai_long_",
        dir=BASE_DIR
    )

    print(
        "\n========================================"
    )

    print(
        "LONG VIDEO WORKSPACE:"
    )

    print(
        temp_dir
    )

    print(
        "========================================\n"
    )

    # =====================================
    # STEP 1
    # ONE TTS REQUEST ONLY
    # =====================================

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

    # =====================================
    # STEP 2
    # DOWNLOAD VISUALS
    # =====================================

    visuals = download_visuals(
        visual_shots=visual_shots,
        temp_dir=temp_dir,
        progress_callback=progress_callback
    )

    # =====================================
    # STEP 3
    # VALIDATE VISUALS
    # =====================================

    number_of_visuals = len(
        visuals
    )

    if number_of_visuals == 0:

        raise RuntimeError(
            "No visual clips were downloaded."
        )

    # =====================================
    # STEP 4
    # DISTRIBUTE AUDIO TIME
    # =====================================

    average_duration = (
        narration_duration
        / number_of_visuals
    )

    print(
        f"\nNarration duration: "
        f"{narration_duration:.2f} seconds"
    )

    print(
        f"Visual clips: "
        f"{number_of_visuals}"
    )

    print(
        f"Average clip duration: "
        f"{average_duration:.2f} seconds\n"
    )

    # =====================================
    # STEP 5
    # PREPARE CLIPS
    # =====================================

    prepared_clips = []

    total = len(
        visuals
    )

    for index, visual in enumerate(
        visuals
    ):

        output_path = os.path.join(
            temp_dir,
            f"prepared_{index + 1:02d}.mp4"
        )

        prepare_clip(
            input_path=visual["path"],
            output_path=output_path,
            duration=average_duration,
            caption=visual["caption"]
        )

        prepared_clips.append(
            output_path
        )

        if progress_callback:

            progress_callback(
                index + 1,
                total,
                f"Preparing visual "
                f"{index + 1}/{total}"
            )

    # =====================================
    # STEP 6
    # CONCAT ALL VISUALS
    # =====================================

    visual_video = os.path.join(
        temp_dir,
        "visual_video.mp4"
    )

    concatenate_video(
        video_paths=prepared_clips,
        output_path=visual_video
    )

    # =====================================
    # STEP 7
    # ADD CONTINUOUS AUDIO
    # =====================================

    final_video = os.path.join(
        temp_dir,
        "final_long_video.mp4"
    )

    add_audio(
        video_path=visual_video,
        audio_path=narration_audio,
        output_path=final_video
    )

    # =====================================
    # STEP 8
    # VERIFY FINAL VIDEO
    # =====================================

    if not os.path.isfile(
        final_video
    ):

        raise RuntimeError(
            "Final video was not created."
        )

    final_size = os.path.getsize(
        final_video
    )

    if final_size == 0:

        raise RuntimeError(
            "Final video is empty."
        )

    # =====================================
    # STEP 9
    # READ VIDEO
    # =====================================

       # =====================================
    # STEP 9
    # READ FINAL VIDEO
    # =====================================

    with open(
        final_video,
        "rb"
    ) as file:

        video_data = file.read()

    final_size = os.path.getsize(
        final_video
    )

    if final_size == 0:

        raise RuntimeError(
            "Final video is empty."
        )

    print(
        "\n========================================"
    )

    print(
        "FINAL VIDEO CREATED"
    )

    print(
        final_video
    )

    print(
        f"Size: "
        f"{final_size / (1024 * 1024):.2f} MB"
    )

    print(
        "========================================\n"
    )

    # =====================================
    # STEP 10
    # DELETE TEMP FILES
    # =====================================

    try:

        import shutil

        shutil.rmtree(
            temp_dir
        )

        print(
            "Temporary clips deleted successfully."
        )

    except Exception as e:

        print(
            "Warning: Could not delete "
            "temporary files:"
        )

        print(e)

    # =====================================
    # RETURN
    # =====================================

    return {
        "video": video_data,
        "duration": narration_duration,
        "temp_dir": temp_dir,
        "final_path": final_video
    }