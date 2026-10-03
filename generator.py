import json

from gemini_client import client, call_with_retry

SCRIPT_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini returned invalid JSON.\n\n" + text) from exc


def _creative_subscribe_cta(result, topic):
    """Return a short, topic-specific spoken subscription CTA.
    Avoids generic 'subscribe for more' filler and ties the CTA to the actual fact.
    """
    subject = str(result.get("core_subject", "") or topic).strip()
    entity = str(result.get("primary_entity", "") or "").strip()
    angle = str(result.get("specific_angle", "") or "").strip()
    gap = str(result.get("knowledge_gap", "") or "").strip()

    # Keep the CTA short enough to fit the final 2-3 seconds.
    candidates = [
        f"Subscribe—there's more weird science behind {subject}.",
        f"Subscribe for more facts hiding in plain sight.",
        f"Subscribe—your next strange fact is already waiting.",
    ]

    # More specific constructions when the generated metadata is useful.
    if entity and subject:
        candidates.insert(0, f"Subscribe—{entity} has more surprises like this.")
    elif subject:
        candidates.insert(0, f"Subscribe—there's another side of {subject} you haven't seen.")

    # Pick the most topic-specific candidate, while avoiding generic CTA wording.
    return candidates[0]


def _is_generic_cta(cta):
    import re
    normalized = re.sub(r"[^a-z0-9 ]+", " ", str(cta or "").casefold())
    normalized = " ".join(normalized.split())
    generic_patterns = (
        "subscribe for more",
        "subscribe for more facts",
        "subscribe for more amazing facts",
        "subscribe for more interesting facts",
        "subscribe for more videos",
        "like and subscribe",
        "follow for more",
        "stay tuned for more",
    )
    return any(pattern in normalized for pattern in generic_patterns)


def _validate_result(result, topic):
    if not isinstance(result, dict):
        raise RuntimeError("Gemini returned an invalid result.")
    if not result.get("voiceover"):
        raise RuntimeError("Gemini did not generate voiceover.")

    voiceover = str(result.get("voiceover", "")).strip()
    hook = str(result.get("selected_hook", result.get("hook", ""))).strip()
    cta = str(result.get("cta", "")).strip()
    if not cta or _is_generic_cta(cta):
        # Never fall back to generic subscription filler.
        cta = _creative_subscribe_cta(result, topic)

    def _norm(text):
        return " ".join(str(text).lower().strip().rstrip(".?! ").split())

    # The hook must be the actual beginning of the spoken audio.
    if hook and not _norm(voiceover).startswith(_norm(hook)):
        voiceover = hook.rstrip(" .?!") + ". " + voiceover
    if not hook:
        hook = voiceover.split(".")[0].strip()[:140]

    # The CTA must be spoken, not merely returned as metadata.
    if _norm(cta) not in _norm(voiceover):
        voiceover = voiceover.rstrip(" .?!") + ". " + cta.rstrip(" .?!") + "."

    result["voiceover"] = voiceover
    result["selected_hook"] = hook
    result["hook"] = hook

    hook_options = result.get("hook_options", [])
    if isinstance(hook_options, list):
        result["hook_options"] = [str(x).strip() for x in hook_options if str(x).strip()][:3]
    else:
        result["hook_options"] = [hook]

    result["cta"] = cta
    result["core_subject"] = str(result.get("core_subject", topic)).strip() or topic
    result["specific_angle"] = str(result.get("specific_angle", "")).strip()
    result["primary_entity"] = str(result.get("primary_entity", "")).strip()
    result["knowledge_gap"] = str(result.get("knowledge_gap", "")).strip()
    scenes = []
    for scene in result.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        query = str(scene.get("search_query", "")).strip()
        if not query:
            continue
        scenes.append({
            "time": str(scene.get("time", "")).strip(),
            "visual": str(scene.get("visual", "")).strip(),
            "search_query": query,
            "onscreen_text": str(scene.get("onscreen_text", "")).strip(),
        })
    if not scenes:
        raise RuntimeError("No usable scenes were generated.")

    # The first two seconds are the highest-priority visual beat.
    scenes[0]["time"] = "0-2"
    scenes[0]["onscreen_text"] = hook
    if not scenes[0]["visual"]:
        scenes[0]["visual"] = "The most visually striking real-world representation of the hook."

    result["scenes"] = scenes
    result["title"] = str(result.get("title", topic)).strip() or topic
    result["caption"] = str(result.get("caption", "")).strip()
    tags = result.get("hashtags", [])
    result["hashtags"] = [str(x).strip() for x in tags if str(x).strip()] if isinstance(tags, list) else []
    return result


def _prompt_for_topics(topics, style):
    topic_lines = "\n".join(f"{i + 1}. {topic}" for i, topic in enumerate(topics))
    count = len(topics)
    return f"""You are the lead writer for a curiosity-first YouTube Shorts channel.
Create {count} DIFFERENT 20-26 second Shorts in ONE response.

CHANNEL IDENTITY:
Science. History. The human body. Animals and nature. Space. Amazing facts.
The viewer should learn something genuinely interesting that they were never taught in school.

STYLE: {style}
LANGUAGE: English

INPUT TOPICS (use each exactly once):
{topic_lines}

EDITORIAL RULE:
These are NOT random viral-news Shorts. They are evergreen or durable curiosity stories that build a recognizable educational channel identity.
Prefer WHY, HOW, WHAT HAPPENS, or hidden-fact topics.
Desired examples: why we use thermal energy; how an elephant sees at night; why the one-horned rhino is endangered; why fingers wrinkle in water; why Venus is hotter than Mercury.

STRICTLY AVOID:
- accidents, disasters, survival-bait, tragedy/death shock stories
- generic AI use cases, AI-in-space stories, generic AI news
- generic space mission news
- random viral news
- celebrity, politics, sports or finance
- listicles such as 5 facts
- broad textbook definitions
- generic "what is X" explanations
- topics selected only because they are viral

COLD-VIEWER GOAL:
Start with the most interesting claim/question. Do not use "Did you know?", "Today we're going to", "In this video", or school-lesson introductions.
The first sentence should create a specific curiosity gap and be understandable without context.

HOOK ENGINE:
Generate 3 different hooks. Select the strongest as selected_hook. selected_hook MUST be the exact first sentence of voiceover. Hook = 7-14 spoken words.

STORY STRUCTURE:
0-2 sec: immediate question/claim + strongest visual.
2-6 sec: establish the familiar thing and puzzle.
6-12 sec: surprising mechanism/evidence.
12-18 sec: reveal the unexpected part.
18-23 sec: consequence/memorable takeaway.
23-26 sec: short topic-specific curiosity CTA.
Every sentence must add new information. No filler.

VISUAL STORYTELLING:
Make ONE mini-documentary, not six unrelated stock clips.
Create a stable visual world/subject. Each scene must show what is happening in the narration beat.
Prefer 2-3 visual sources reused across connected beats with different crops/sections over six unrelated clips.
Keep location, time of day, main subject and visual logic consistent whenever possible.
The first frame must show the actual subject/problem, not a generic skyline, sunset, map or stock person.
Never use random silhouettes, generic city shots, generic night skies, or unrelated landscapes as filler.

FACTUAL QUALITY:
Use widely established facts. Do not invent statistics, dates, mechanisms or scientific claims.

CTA:
6-10 spoken words, and it MUST contain a natural subscription instruction.
Make it creatively connected to THIS Short's actual subject or reveal.
It should feel like the next line of the story, not an ad break.
Never use "like and subscribe", "subscribe for more", "follow for more", or generic filler.
BAD: "Subscribe for more amazing facts."
GOOD: "Subscribe—there's more hiding inside your own body."
GOOD: "Subscribe—elephants have even stranger abilities than this."
GOOD: "Subscribe before your next ordinary habit gets weird."

Return ONLY valid JSON. No Markdown.
Return exactly:
{{
  "shorts": [{{
    "topic": "exact input topic", "title": "curiosity-driven YouTube title",
    "core_subject": "canonical underlying subject, 3-8 words",
    "specific_angle": "exact mechanism/question/fact being explained",
    "primary_entity": "main animal/object/person/place/body part",
    "knowledge_gap": "one thing the viewer learns",
    "hook_options": ["hook A","hook B","hook C"], "selected_hook": "strongest hook; exact first sentence of voiceover",
    "voiceover": "45-55 spoken words including CTA", "cta": "6-10 word creative subscription CTA tied to this exact topic; must sound natural when spoken",
    "visual_world": "stable visual world/subject", "story_arc": "visual progression",
    "scenes": [
      {{"time":"0-2","story_beat":"hook","visual":"exact visual event","search_query":"specific real footage query","onscreen_text":"short hook"}},
      {{"time":"2-6","story_beat":"setup","visual":"same world progressing","search_query":"specific related footage query","onscreen_text":"short text"}},
      {{"time":"6-12","story_beat":"explanation","visual":"visual evidence of mechanism","search_query":"specific related footage query","onscreen_text":"short text"}},
      {{"time":"12-18","story_beat":"reveal","visual":"most surprising consequence","search_query":"specific related footage query","onscreen_text":"short text"}},
      {{"time":"18-23","story_beat":"payoff","visual":"return to subject or consequence","search_query":"specific related footage query","onscreen_text":"short text"}},
      {{"time":"23-26","story_beat":"close","visual":"closing image connected to opening","search_query":"specific related footage query","onscreen_text":"short CTA"}}
    ],
    "caption": "YouTube caption", "hashtags": ["#shorts","#science","#facts"]
  }}]
}}
"""


def generate_short(topic, style):
    if not topic or not topic.strip():
        raise ValueError("Short title/topic cannot be empty.")
    if not style or not style.strip():
        raise ValueError("Short type cannot be empty.")

    prompt = _prompt_for_topics([topic.strip()], style.strip())

    def request(model):
        return client.models.generate_content(model=model, contents=prompt)

    response = call_with_retry(request, "Gemini script generation", SCRIPT_MODELS)
    data = _parse_json(response.text)
    items = data.get("shorts", []) if isinstance(data, dict) else []
    if items and isinstance(items[0], dict):
        return _validate_result(items[0], topic)
    return _validate_result(data, topic)


def generate_shorts_batch(topics, style):
    """Generate all scripts in one request, reducing 24 script calls to one."""
    topics = [str(x).strip() for x in topics if str(x).strip()]
    if not topics:
        return []

    prompt = _prompt_for_topics(topics, style)

    def request(model):
        return client.models.generate_content(model=model, contents=prompt)

    response = call_with_retry(request, "Gemini batch script generation", SCRIPT_MODELS)
    data = _parse_json(response.text)
    items = data.get("shorts", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise RuntimeError("Gemini batch response did not contain a shorts list.")

    by_topic = {
        str(item.get("topic", "")).strip().casefold(): item
        for item in items if isinstance(item, dict)
    }
    results = []
    missing = []
    for topic in topics:
        item = by_topic.get(topic.casefold())
        if item is None:
            for key, value in by_topic.items():
                if key.replace(" ", "") == topic.casefold().replace(" ", ""):
                    item = value
                    break
        if item is None:
            missing.append(topic)
        else:
            results.append(_validate_result(item, topic))

    if missing:
        raise RuntimeError("Gemini batch omitted topic(s): " + ", ".join(missing))
    return results
