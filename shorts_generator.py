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


def _validate_result(result, topic):
    if not isinstance(result, dict):
        raise RuntimeError("Gemini returned an invalid result.")
    if not result.get("voiceover"):
        raise RuntimeError("Gemini did not generate voiceover.")

    voiceover = str(result.get("voiceover", "")).strip()
    result["core_subject"] = str(result.get("core_subject") or topic).strip()
    hook = str(result.get("selected_hook", result.get("hook", ""))).strip()
    cta = str(result.get("cta", "")).strip()
    if not cta:
        cta = "Subscribe for the next mystery we uncover."

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
    return f'''You are the lead writer for a premium, fast-growing YouTube Shorts documentary channel.
Create {count} DIFFERENT 20-26 second YouTube Shorts in ONE response.

STYLE: {style}
LANGUAGE: English

INPUT TOPICS (use each exactly once):
{topic_lines}

CORE GOAL — STOP THE SCROLL:
- Optimize for a COLD viewer who knows nothing about the subject.
- The first 1-2 seconds are the most important part of the Short.
- Start with an immediate, concrete, surprising claim/question/visual idea.
- The viewer should instantly think: "Wait, how is that possible?" or "I need to know."
- Do NOT sound like a school lesson, textbook, Wikipedia summary, or news intro.
- Do NOT begin with "Did you know", "Today we're going to", "In this video", "Scientists have discovered", "According to", or background history.
- Prefer bizarre real-world facts, impossible-sounding phenomena, hidden everyday details, survival stories, strange places, unexplained events, shocking historical incidents, human-scale discoveries, and genuinely current events.
- Technical/scientific subjects are allowed only when explained through a simple, surprising human consequence. Avoid jargon.
- Do not stay in one content zone: across a batch deliberately mix current world events, geopolitics, disasters, history, human stories, science, technology, space, animals, places, crime/mystery, culture, and strange real incidents. Never make every topic an accident/disaster or AI/space topic.
- Avoid generic facts such as percentages, definitions, obvious animal facts, generic invention facts, or broad textbook explanations.

HOOK ENGINE:
- Generate 3 genuinely different first-2-second hook options for every topic.
- Select the strongest one as selected_hook.
- selected_hook MUST be the exact first sentence of voiceover.
- Hook should be 7-14 spoken words, concrete and curiosity-driven.
- Never waste the first sentence introducing the topic.

STORY STRUCTURE:
0-2 sec: selected hook + strongest visual.
2-6 sec: setup that raises the question.
6-12 sec: escalating surprising evidence/fact.
12-18 sec: reveal/payoff.
18-23 sec: satisfying final detail.
23-26 sec: short topic-specific curiosity CTA after the payoff.
Keep the story tight. No filler.

CTA / SUBSCRIBER GOAL:
- End with a short curiosity-driven CTA after the payoff.
- Generate a DIFFERENT CTA for every Short based on its topic.
- Never repeat a generic CTA across the batch.
- Never use "like and subscribe", "please subscribe", "subscribe for more", or "don't forget to subscribe".
- CTA should be 6-10 spoken words and normally begin with "Subscribe for..." or "Subscribe if you want to...".
- The CTA should promise a broad editorial direction the channel can actually deliver.
- It must feel like a natural final thought, not an advertisement.

FACTUAL QUALITY:
- Do not invent facts, dates, statistics, quotes, people, places, discoveries or scientific claims.
- If a claim is disputed or uncertain, say so briefly.
- Prefer topics that can be supported by widely established facts and strong real footage.
- For real/current mode, prioritize genuinely recent stories and YouTube-trending subjects from the supplied trend headlines. Do not turn every headline into an AI-in-space story; preserve the actual event, people, country, object, or consequence.

VISUAL QUALITY:
- Every scene must have a concrete Pexels-friendly search query for REAL footage.
- First scene must be the strongest possible visual representation of the hook and should work even with sound off.
- Prefer people, animals, landscapes, strange locations, machines, objects, buildings, archival-looking real footage, laboratories, oceans, skies and other filmable subjects.
- Avoid generic abstract stock footage and repeated clips.
- On-screen text must NEVER be a long sentence that gets cut off.
- Keep each onscreen_text concise: ideally 2-5 words, and never more than 24 characters per line when possible.
- If a phrase needs more space, structure it naturally into 2 or at most 3 short lines; never cram it into one long line.
- Each scene caption must be readable on a 1080x1920 vertical Short.
- Use 6 scenes so the opening can cut quickly; first scene is exactly 0-2 seconds.

Return ONLY valid JSON. No Markdown.

Return exactly:
{{
  "shorts": [
    {{
      "topic": "exact input topic",
      "core_subject": "canonical underlying event/object/person/place/discovery",
      "title": "Short YouTube title",
      "hook_options": ["hook A", "hook B", "hook C"],
      "selected_hook": "strongest hook; exact first sentence of voiceover",
      "voiceover": "45-55 spoken words, including the final CTA",
      "cta": "6-10 word topic-specific curiosity CTA",
      "scenes": [
        {{"time":"0-2","visual":"Most striking real-world visual for the hook","search_query":"simple Pexels query","onscreen_text":"Selected hook"}},
        {{"time":"2-6","visual":"Concrete setup visual","search_query":"simple Pexels query","onscreen_text":"Short text"}},
        {{"time":"6-12","visual":"Escalating evidence visual","search_query":"simple Pexels query","onscreen_text":"Short text"}},
        {{"time":"12-18","visual":"Reveal/payoff visual","search_query":"simple Pexels query","onscreen_text":"Short text"}},
        {{"time":"18-23","visual":"Final satisfying detail","search_query":"simple Pexels query","onscreen_text":"Short text"}},
        {{"time":"23-26","visual":"Strong closing visual","search_query":"simple Pexels query","onscreen_text":"Short CTA text"}}
      ],
      "caption":"YouTube caption",
      "hashtags":["#shorts","#relevant","#hashtags"]
    }}
  ]
}}
'''
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
