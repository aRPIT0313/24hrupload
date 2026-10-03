"""24-Short batch pipeline with trend discovery, semantic no-repeat protection,
controlled Gemini retries/fallbacks and one-call script generation.
"""

import json
import time
import difflib
from datetime import timedelta
from pathlib import Path

from generator import generate_shorts_batch, generate_short
from tts import generate_voice
from pexels import get_best_video
from video_engine import build_short
from trend_discovery import fetch_trending_headlines
from gemini_client import client, call_with_retry
from google.genai import types
from content_learning import remember_content, build_history_subject_context, topic_fingerprint

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output" / "scheduled_shorts"
HISTORY_FILE = BASE_DIR / "topic_titles_history.json"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOPIC_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]


def _history_path():
    if HISTORY_FILE.exists():
        return HISTORY_FILE
    legacy = BASE_DIR.parent / "topic_titles_history.json"
    return legacy if legacy.exists() else HISTORY_FILE


def load_title_history():
    history_path = _history_path()
    existing = []
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            values = data if isinstance(data, list) else data.get("titles", data.get("topics", []))
            if isinstance(values, list):
                existing = [str(x).strip() for x in values if str(x).strip()]
        except Exception:
            pass

    already_published = [
        "Life in the Midnight Zone",
        "World's Most Mysterious Book",
        "Tunguska Explosion",
        "War Against Emu",
        "World's First Computer",
        "Why Vultures Are Important",
    ]

    merged, seen = [], set()
    for title in existing + already_published:
        key = " ".join(title.casefold().split())
        if key and key not in seen:
            seen.add(key)
            merged.append(title)
    if merged and not HISTORY_FILE.exists():
        save_title_history(merged)
    return merged


def _topic_key(text):
    stop={"a","an","the","and","or","of","to","in","on","for","why","how","what","when","where","which","is","are","was","were","did","do","does","can","could","your","you","this","that","these","those","with","from","into","about","behind","really","actually","ever","most","top","secret","secrets","fact","facts","story","history","world","first","important","mysterious","thing","things","video","short","youtube","explained","explain","revealed","reveals","would","might","just","new","true","real","people","person","one","two","three","use","used","using","reason","reasons","happens","hidden"}
    import re
    words=re.findall(r"[a-z0-9]+",str(text).casefold()); out=[]
    for w in words:
        if w in stop: continue
        if len(w)>4 and w.endswith("ies"): w=w[:-3]+"y"
        elif len(w)>4 and w.endswith("s") and not w.endswith("ss"): w=w[:-1]
        out.append(w)
    return set(out)


def _topic_similarity(a,b):
    a_text=" ".join(str(a).casefold().split()); b_text=" ".join(str(b).casefold().split())
    if not a_text or not b_text: return False
    if a_text==b_text: return True
    if difflib.SequenceMatcher(None,a_text,b_text).ratio()>=0.78: return True
    ak=_topic_key(a); bk=_topic_key(b)
    if not ak or not bk: return False
    inter=len(ak&bk); union=len(ak|bk); smaller=min(len(ak),len(bk))
    if smaller<=3: return inter==smaller and smaller>=2
    return inter/union>=0.50 or inter/smaller>=0.67


def _same_underlying_subject(candidate, history_rows):
    parts=[candidate.get(k,"") for k in ("topic","core_subject","specific_angle","primary_entity","knowledge_gap")]
    parts=[str(x).strip() for x in parts if str(x).strip()]
    candidate_fp=topic_fingerprint(*parts)
    for old in history_rows:
        if not isinstance(old,dict): old={"title":str(old)}
        old_parts=[old.get(k,"") for k in ("core_subject","subject_fingerprint","specific_angle","primary_entity","knowledge_gap","topic","title","description")]
        old_parts += [" ".join(old.get("tags") or [])]
        old_parts=[str(x).strip() for x in old_parts if str(x).strip()]
        for cp in parts:
            for op in old_parts:
                if _topic_similarity(cp,op): return True
        old_fp=topic_fingerprint(*old_parts)
        if candidate_fp and old_fp and _topic_similarity(candidate_fp,old_fp): return True
    return False

def save_title_history(titles):
    cleaned, seen = [], set()
    for title in titles:
        title = str(title).strip()
        if not title:
            continue
        key = " ".join(title.casefold().split())
        if key not in seen:
            seen.add(key)
            cleaned.append(title)
    HISTORY_FILE.write_text(
        json.dumps(cleaned[-5000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remember_title(title):
    if title and str(title).strip():
        save_title_history(load_title_history() + [str(title).strip()])


def delete_local_file(path):
    if not path:
        return
    try:
        p = Path(path)
        p.unlink(missing_ok=True)
        if p.parent != BASE_DIR and p.parent.exists() and not any(p.parent.iterdir()):
            p.parent.rmdir()
    except OSError:
        pass


def _filter_unique_topics(candidates, banned):
    unique = []
    for candidate in candidates:
        candidate = str(candidate).strip()
        if not candidate:
            continue
        if any(_topic_similarity(candidate, old) for old in banned):
            continue
        if any(_topic_similarity(candidate, current) for current in unique):
            continue
        unique.append(candidate)
    return unique


def _topic_prompt(n, niche, banned, trend_headlines, trending_count, performance_insights="", history_context=""):
    candidate_count=max(n+24,50)
    avoid_text="\n".join(f"- {x}" for x in banned[-600:]) or "- none"
    trends="\n".join(f"- {x}" for x in trend_headlines[:60]) or "- No fresh trend feed available."
    return f"""Generate {candidate_count} DISTINCT candidate topics for a curiosity-first YouTube Shorts channel.

CHANNEL IDENTITY:
Science. History. The human body. Animals and nature. Space. Amazing facts.
The viewer should learn something they were never taught in school.

THIS IS A 1-MONTH CHANNEL-IDENTITY PHASE:
Ignore channel analytics, view counts, swipe-away rate, subscriber conversion and past winners.
Do NOT optimize for whatever previously got views. Build a broad recognizable knowledge identity first.

FRESH HEADLINE FEED (optional inspiration only):
{trends}

ALREADY-COVERED SHORTS — DO NOT REPEAT THE UNDERLYING SUBJECT:
{history_context}

ADDITIONAL BANNED TOPICS:
{avoid_text}

CONTENT MISSION:
Prioritize fresh, evergreen curiosity questions and concrete explanations: surprising everyday science; animal abilities and adaptations; human body and psychology; hidden explanations behind ordinary things; history and archaeology with a specific fascinating mechanism/object; nature and ecosystems; space only when the question itself is genuinely interesting; technology/inventions only when the underlying idea is surprising.

GOOD DIRECTION:
Why do we use thermal energy?
How can an elephant see in extremely low light?
Why is the one-horned rhino endangered?
Why do fingers wrinkle in water?
Why is Venus hotter than Mercury?

BAD DIRECTION:
accident stories, survival stories, tragedy bait, random mysteries, generic AI use cases, AI-in-space, generic space mission news, man-survives stories, random shocking deaths, viral-news recycling, generic listicles, broad textbook lessons.

UNDERLYING-SUBJECT RULE — CRITICAL:
A different title or question wording does NOT make a new topic. If an old Short is about elephant night vision, then Why elephants see at night, How elephants see in darkness, Elephant night vision explained, and How elephant eyes work at night are ALL duplicates. Choose a DIFFERENT underlying fact, mechanism, organism, object, event or discovery.

ANGLE RULE:
Same broad category is fine, but the subject and knowledge gap must change. Elephant night vision and elephant infrasound communication are different. Roman concrete self-healing and Roman roads are different. But why Roman concrete lasts and how Roman concrete repairs itself are the same subject.

For every candidate return:
- topic: viewer-facing topic
- core_subject: canonical subject in 3-8 words; NOT the title
- specific_angle: exact question/mechanism/fact the Short explains
- primary_entity: main animal/object/person/place/body part
- knowledge_gap: one thing the viewer learns
- category, hook_style, scores and why_clickable

DUPLICATE RULE:
Reject a candidate if its core_subject, primary_entity + mechanism, or knowledge_gap substantially matches ANY covered Short above. Do not merely change wording to evade this rule.

QUALITY:
quality_score >= 75, hook_strength >= 8, broad_appeal >= 7, technicality <= 4. The hook must make a cold viewer think Wait... what? without sounding like a classroom lesson. At least 70% of candidates should be evergreen. AI topics should normally be absent. Space should be a minority.

Return ONLY valid JSON. No Markdown.
Return exactly:
{{
  "candidates": [
    {{
      "topic":"specific viewer-facing topic",
      "core_subject":"canonical underlying subject",
      "specific_angle":"specific mechanism/question/fact",
      "primary_entity":"main entity",
      "knowledge_gap":"what the viewer learns",
      "category":"surprising_science",
      "hook_style":"curiosity_question",
      "is_trending":false,
      "quality_score":88, "hook_strength":9, "broad_appeal":9, "visual_strength":9,
      "surprise":9, "shareability":8, "subscriber_potential":8, "technicality":2,
      "why_clickable":"one short sentence"
    }}
  ]
}}
"""

def _parse_json_response(text):
    """Parse Gemini JSON robustly, including fenced responses and surrounding prose."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty topic response.")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Recover when Gemini adds a sentence before/after the JSON object.
        decoder = json.JSONDecoder()
        for marker in ("{", "["):
            start = text.find(marker)
            if start >= 0:
                try:
                    value, _ = decoder.raw_decode(text[start:])
                    return value
                except json.JSONDecodeError:
                    continue
        raise RuntimeError("Gemini returned malformed JSON for topic generation. Please retry the batch.")


def generate_topic_candidates(n=24, niche="bizarre facts, strange places, real mysteries, shocking history, survival stories, surprising science and hidden everyday facts", avoid_topics=None, trending_count=6, performance_insights=""):
    avoid_topics = [str(x).strip() for x in (avoid_topics or []) if str(x).strip()]
    try:
        from content_learning import ensure_history_bootstrapped, load_content_history
        history_rows = ensure_history_bootstrapped().get("shorts", [])
    except Exception:
        history_rows = []
    history_context = build_history_subject_context("shorts", limit=500)
    trend_headlines = fetch_trending_headlines(limit=60)
    prompt = _topic_prompt(n, niche, avoid_topics, trend_headlines, trending_count, performance_insights, history_context)

    def request(model):
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

    response = call_with_retry(request, "Gemini topic generation", TOPIC_MODELS)
    text = response.text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    data = _parse_json_response(text)
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini did not return a candidates list.")

    clean = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        if not topic:
            continue
        item["core_subject"] = str(item.get("core_subject", "")).strip()
        item["specific_angle"] = str(item.get("specific_angle", "")).strip()
        item["primary_entity"] = str(item.get("primary_entity", "")).strip()
        item["knowledge_gap"] = str(item.get("knowledge_gap", "")).strip()
        if not item["core_subject"] or not item["specific_angle"]:
            continue
        try:
            score = float(item.get("quality_score", 0) or 0)
            hook_strength = float(item.get("hook_strength", 0) or 0)
            broad_appeal = float(item.get("broad_appeal", 0) or 0)
            technicality = float(item.get("technicality", 10) or 10)
        except (TypeError, ValueError):
            continue
        item["topic"] = topic
        item["quality_score"] = score
        item["hook_strength"] = hook_strength
        item["broad_appeal"] = broad_appeal
        item["technicality"] = technicality
        item["is_trending"] = bool(item.get("is_trending", False))
        if score < 75 or hook_strength < 8 or broad_appeal < 7 or technicality > 4:
            continue
        if any(_topic_similarity(topic, old) for old in avoid_topics):
            continue
        if any(_topic_similarity(topic, old["topic"]) for old in clean):
            continue
        if _same_underlying_subject(item, history_rows):
            continue
        if any(_same_underlying_subject(item, [old]) for old in clean):
            continue
        clean.append(item)

    # Select a genuinely mixed batch. Trending items are capped so one domain cannot take over.
    clean.sort(key=lambda x: (x["quality_score"], x["hook_strength"]), reverse=True)
    trending = [x for x in clean if x["is_trending"]]
    evergreen = [x for x in clean if not x["is_trending"]]
    selected = []
    category_counts = {}
    domain_counts = {}
    def domain_key(item):
        text = str(item.get("topic", "")).casefold()
        for key, words in {
            "ai": ("ai", "artificial intelligence", "chatgpt", "machine learning"),
            "space": ("space", "galaxy", "hubble", "planet", "star", "cosmic", "astronomy"),
            "science": ("science", "physics", "chemistry", "experiment"),
            "nature": ("animal", "ocean", "forest", "nature", "plant"),
            "history": ("history", "ancient", "war", "empire", "archaeology"),
        }.items():
            if any(word in text for word in words):
                return key
        return str(item.get("category", "other")).casefold() or "other"
    max_domain = max(2, int(n * 0.25))
    # First guarantee trend variety, then fill with the best non-trending topics.
    pool = trending[:max(trending_count, 0)] + evergreen + trending[max(trending_count, 0):]
    for item in pool:
        domain = domain_key(item)
        if domain_counts.get(domain, 0) >= max_domain:
            continue
        selected.append(item)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        category = str(item.get("category", "other"))
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= n:
            break
    if len(selected) < n:
        # Relax only the quality threshold slightly; never relax duplicate protection.
        relaxed = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            topic = str(item.get("topic", "")).strip()
            if not topic or any(_topic_similarity(topic, old) for old in avoid_topics) or any(_topic_similarity(topic, old["topic"]) for old in relaxed):
                continue
            if _same_underlying_subject(item, history_rows):
                continue
            score = float(item.get("quality_score", 0) or 0)
            if score < 70:
                continue
            item["topic"] = topic
            item["quality_score"] = score
            relaxed.append(item)
            if len(relaxed) >= n:
                break
        if len(relaxed) >= n:
            selected = relaxed[:n]
    if len(selected) < n:
        raise RuntimeError(f"Only {len(selected)} high-quality, genuinely new topics were generated. Run again to refresh the trend feed.")
    return selected[:n]


def generate_topics(n=24, niche="science, everyday phenomena, animals, human body, history, nature, space and amazing facts", avoid_topics=None, trending_count=6, performance_insights=""):
    return [x["topic"] for x in generate_topic_candidates(n, niche, avoid_topics, trending_count, performance_insights)]


def generate_one_short(topic, style, voice):
    result = generate_short(topic=topic, style=style)
    audio_data = generate_voice(text=result["voiceover"], voice=voice, style=style)
    scenes = result.get("scenes", [])
    if not scenes:
        raise RuntimeError("No scenes were generated.")
    videos = []
    for scene in scenes:
        query = str(scene.get("search_query", "")).strip()
        if not query:
            videos.append(None)
            continue
        videos.append(get_best_video(query))
        time.sleep(0.15)
    if any(v is None for v in videos):
        missing = [str(i + 1) for i, v in enumerate(videos) if v is None]
        raise RuntimeError("No suitable Pexels footage for scene(s): " + ", ".join(missing))
    return result, build_short(
                            videos=videos,
                            audio_data=audio_data,
                            scenes=scenes,
                            voiceover=result.get("voiceover", "")
                        )


def _safe_filename(title, max_length=80):
    """Create a Windows-safe filename from a YouTube title."""
    import re
    text = str(title or "").replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1,10)} | {f"LPT{i}" for i in range(1,10)}
    if text.upper() in reserved:
        text = "_" + text
    return (text[:max_length].rstrip(" .") or "short").strip()


def save_short(video_bytes, index, title):
    """Optional local save; always use a Windows-safe filename."""
    safe = _safe_filename(title, 80)
    path = OUTPUT_DIR / f"{index:02d}_{safe}.mp4"
    path.write_bytes(video_bytes)
    return path

def next_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def build_schedule(start_at, count=24):
    if start_at.tzinfo is None:
        raise ValueError("start_at must be timezone-aware.")
    return [start_at + timedelta(hours=i) for i in range(count)]
