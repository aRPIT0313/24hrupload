
import os
import re
import json
import math
import shutil
import tempfile
import subprocess
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

from gemini_client import client, call_with_retry
from generator import generate_short
from tts import generate_voice
from pexels import get_best_video

FFMPEG_PATH = r"C:\Users\DELL\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build-shared\bin\ffmpeg.exe"

PLAN_MODEL = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"]


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def make_visual_plan(result):
    """Create a scene-by-scene director plan without changing the production pipeline."""
    scenes = result.get("scenes", [])
    compact = []
    for i, s in enumerate(scenes[:6]):
        compact.append({
            "scene": i + 1,
            "time": s.get("time", ""),
            "voice_visual": s.get("visual", ""),
            "search_query": s.get("search_query", ""),
            "onscreen_text": s.get("onscreen_text", "")
        })

    prompt = f"""
You are the visual director for a premium YouTube Shorts documentary.
We are running an EXPERIMENT only. Design a much more cinematic visual treatment
than generic stock-footage matching.

TOPIC: {result.get("topic", result.get("title", ""))}
VOICEOVER:
{result.get("voiceover", "")}

CURRENT SCENE PLAN:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON:
{{
  "visuals": [
    {{
      "scene": 1,
      "type": "image|video|graphic|map|timeline|comparison",
      "query": "short search query for the exact visual",
      "visual_goal": "what the viewer should see",
      "motion": "slow_zoom_in|slow_zoom_out|pan_left|pan_right|push_in|static",
      "overlay": "short overlay text, max 7 words"
    }}
  ]
}}

RULES:
- Exactly 6 scenes.
- Scene 1 MUST be the strongest visual hook for the first 2 seconds.
- Prefer real evidence/reference imagery for history, archaeology, science, places and unusual objects.
- Use video only where motion genuinely helps.
- Use image/map/timeline/comparison when stock video would look generic.
- Use graphic for facts that benefit from a clean visual explanation.
- Do not use emoji, cartoon stickers, generic gradient cards, or presentation-style boxes.
- Keep overlays short. No paragraphs.
- Queries should be concrete nouns, not abstract concepts.
- A scene can use Wikimedia Commons when archival/reference imagery is more useful than stock footage.
- A map should be used for geographic stories.
- A timeline should be used for historical sequences.
"""
    def request(model):
        return client.models.generate_content(model=model, contents=prompt)

    response = call_with_retry(request, "Visual Lab director plan", PLAN_MODEL)
    data = _parse_json(response.text)
    visuals = data.get("visuals", [])
    if not isinstance(visuals, list) or len(visuals) < 6:
        raise RuntimeError("Visual director did not return six usable scenes.")
    return visuals[:6]


def _download(url, path):
    r = requests.get(url, stream=True, timeout=45, headers={"User-Agent": "VisualLab/1.0"})
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                f.write(chunk)


def search_wikimedia(query):
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1400,
        "format": "json",
    }
    r = requests.get(url, params=params, timeout=30, headers={"User-Agent": "VisualLab/1.0"})
    r.raise_for_status()
    pages = list((r.json().get("query", {}).get("pages", {}) or {}).values())
    candidates = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        u = info.get("thumburl") or info.get("url")
        if not u:
            continue
        meta = info.get("extmetadata") or {}
        title = p.get("title", "").replace("File:", "")
        license_name = ((meta.get("LicenseShortName") or {}).get("value") or "").strip()
        artist = ((meta.get("Artist") or {}).get("value") or "").strip()
        candidates.append({"url": u, "title": title, "license": license_name, "artist": artist})
    return candidates


def _font(size, bold=True):
    paths = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _fit_text(draw, text, max_width, start=74, minimum=34):
    size = start
    while size > minimum and draw.textbbox((0, 0), text, font=_font(size))[2] > max_width:
        size -= 2
    return _font(size)


def create_graphic(text, subtitle, path, style="fact"):
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (18, 20, 24))
    d = ImageDraw.Draw(img)
    # Editorial layout: no gradients/emoji cards.
    d.rectangle((70, 150, 1010, 154), fill=(220, 220, 220))
    d.text((70, 210), "ZACKDFLIMS / VISUAL LAB", font=_font(30), fill=(180, 180, 180))
    f = _fit_text(d, text, 880, 92, 38)
    bbox = d.multiline_textbbox((0, 0), text, font=f, spacing=14)
    y = 650 - (bbox[3] - bbox[1]) / 2
    d.multiline_text((70, y), text, font=f, fill=(245, 245, 245), spacing=14)
    if subtitle:
        sf = _fit_text(d, subtitle, 880, 44, 28)
        d.multiline_text((70, y + (bbox[3]-bbox[1]) + 90), subtitle, font=sf, fill=(185, 185, 185), spacing=10)
    d.text((70, 1690), "A visual explanation", font=_font(34), fill=(170, 170, 170))
    img.save(path, quality=95)


def create_timeline(text, subtitle, path):
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (20, 21, 24))
    d = ImageDraw.Draw(img)
    d.text((70, 160), "TIMELINE", font=_font(42), fill=(210,210,210))
    d.line((130, 420, 130, 1450), fill=(210,210,210), width=6)
    parts = [x.strip() for x in re.split(r"\s*;\s*|\s*\|\s*", text) if x.strip()]
    if not parts:
        parts = [text]
    step = 1000 / max(1, len(parts)-1)
    for i, part in enumerate(parts):
        y = 420 + int(i*step)
        d.ellipse((110, y-20, 150, y+20), fill=(245,245,245))
        f = _fit_text(d, part, 790, 52, 30)
        d.multiline_text((190, y-35), part, font=f, fill=(245,245,245), spacing=8)
    if subtitle:
        d.text((70, 1660), subtitle, font=_font(34), fill=(170,170,170))
    img.save(path, quality=95)


def create_comparison(text, path):
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (19,20,23))
    d = ImageDraw.Draw(img)
    d.text((70, 160), "THE DIFFERENCE", font=_font(42), fill=(210,210,210))
    parts = [x.strip() for x in re.split(r"\s*\|\s*|\s*;\s*", text) if x.strip()]
    if len(parts) < 2:
        parts = [text, "What this changes"]
    mid = 540
    d.line((mid, 360, mid, 1510), fill=(120,120,120), width=3)
    left = _fit_text(d, parts[0], 400, 58, 30)
    right = _fit_text(d, parts[1], 400, 58, 30)
    d.multiline_text((70, 760), parts[0], font=left, fill=(245,245,245), spacing=10)
    d.multiline_text((580, 760), parts[1], font=right, fill=(245,245,245), spacing=10)
    img.save(path, quality=95)


def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError("FFmpeg failed:\n\n" + p.stderr[-5000:])


def _duration(scene):
    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", str(scene.get("time", "")))
    if not m:
        return 4.0
    return max(0.8, float(m.group(2)) - float(m.group(1)))


def _escape(text):
    return str(text or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def render_media_clip(media_path, output_path, duration, overlay, motion="slow_zoom_in", is_image=False):
    ov = _escape(overlay)
    vf = [
        "scale=1080:1920:force_original_aspect_ratio=increase",
        "crop=1080:1920",
    ]
    if is_image:
        zoom = "zoompan=z='min(zoom+0.0008,1.10)'" if motion in ("slow_zoom_in","push_in") else "zoompan=z='if(lte(zoom,1.0),1.0,max(1.0,zoom-0.0006))'"
        vf = [f"scale=1280:2280:force_original_aspect_ratio=increase,crop=1080:1920,{zoom}:d=1:s=1080x1920:fps=30"]
    vf.append(
        "drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
        f"text='{ov}':fontcolor=white:fontsize=58:borderw=4:bordercolor=black:"
        "x=(w-text_w)/2:y=h-280"
    )
    cmd = [FFMPEG_PATH, "-y"]
    if is_image:
        cmd += ["-loop", "1", "-i", media_path]
    else:
        cmd += ["-stream_loop", "-1", "-i", media_path]
    cmd += ["-t", str(duration), "-vf", ",".join(vf), "-r", "30", "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path]
    _run(cmd)


def build_visual_lab(result, plan, voice='Kore', style='Documentary', progress=None):
    """Render a complete experimental Short and delete all source/intermediate files."""
    temp = tempfile.mkdtemp(prefix="visual_lab_")
    try:
        audio = generate_voice(text=result["voiceover"], voice=voice, style=style)
        audio_path = os.path.join(temp, "voice.wav")
        with open(audio_path, "wb") as f:
            f.write(audio)

        clips = []
        credits = []
        for idx, scene in enumerate(plan):
            kind = str(scene.get("type", "video")).lower()
            query = str(scene.get("query", "")).strip()
            overlay = str(scene.get("overlay", "")).strip() or str(result.get("scenes", [])[idx].get("onscreen_text", ""))
            dur = _duration(result.get("scenes", [])[idx])
            raw = os.path.join(temp, f"raw_{idx}.bin")
            clip = os.path.join(temp, f"clip_{idx}.mp4")

            if progress:
                progress(idx, f"Building visual {idx+1}/6: {kind}")

            if kind in ("graphic", "comparison", "timeline"):
                if kind == "timeline":
                    create_timeline(overlay, query, raw + ".png")
                elif kind == "comparison":
                    create_comparison(overlay, raw + ".png")
                else:
                    create_graphic(overlay, query, raw + ".png")
                render_media_clip(raw + ".png", clip, dur, overlay, scene.get("motion", "static"), True)
            elif kind in ("map",):
                items = search_wikimedia(query or result.get("topic", "map"))
                if not items:
                    raise RuntimeError(f"No Wikimedia map/reference image found for scene {idx+1}: {query}")
                _download(items[0]["url"], raw + ".jpg")
                credits.append(items[0])
                render_media_clip(raw + ".jpg", clip, dur, overlay, scene.get("motion", "slow_zoom_in"), True)
            else:
                # Try real Pexels video first. If it fails, use Wikimedia reference image.
                video = None
                try:
                    video = get_best_video(query)
                except Exception:
                    video = None
                if video:
                    _download(video["url"], raw + ".mp4")
                    render_media_clip(raw + ".mp4", clip, dur, overlay, scene.get("motion", "slow_zoom_in"), False)
                else:
                    items = search_wikimedia(query)
                    if not items:
                        raise RuntimeError(f"No free visual found for scene {idx+1}: {query}")
                    _download(items[0]["url"], raw + ".jpg")
                    credits.append(items[0])
                    render_media_clip(raw + ".jpg", clip, dur, overlay, scene.get("motion", "slow_zoom_in"), True)
            clips.append(clip)

        concat = os.path.join(temp, "concat.txt")
        with open(concat, "w", encoding="utf-8") as f:
            for c in clips:
                f.write("file '" + c.replace("'", "'\\''") + "'\n")
        combined = os.path.join(temp, "combined.mp4")
        _run([FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", combined])

        final = os.path.join(temp, "final.mp4")
        _run([FFMPEG_PATH, "-y", "-i", combined, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final])
        with open(final, "rb") as f:
            data = f.read()
        return data, credits
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_visual_experiment(topic, style="Documentary", voice="Kore"):
    result = generate_short(topic=topic, style=style)
    plan = make_visual_plan(result)
    # Re-render voice using the selected experimental voice.
    # build_visual_lab uses Kore internally by design for a stable first experiment.
    # The visual experiment is intentionally separate from production.
    data, credits = build_visual_lab(result, plan, voice=voice, style=style)
    return result, plan, data, credits
