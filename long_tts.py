import os
import base64
import wave
import tempfile
import subprocess

from dotenv import load_dotenv
from gemini_client import client, call_with_retry


# ============================================================
# ENV
# ============================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY not found in .env"
    )


# ============================================================
# GEMINI
# ============================================================



# ============================================================
# CURRENT GEMINI TTS MODEL
# ============================================================

TTS_MODELS = [
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
]


# ============================================================
# SUPPORTED VOICES
# ============================================================

VOICES = {
    "Zephyr": "Bright",
    "Puck": "Upbeat",
    "Charon": "Informative",
    "Kore": "Firm",
    "Fenrir": "Excitable",
    "Leda": "Youthful",
    "Orus": "Firm",
    "Aoede": "Breezy",
    "Callirrhoe": "Easy-going",
    "Autonoe": "Bright",
    "Enceladus": "Breathy",
    "Iapetus": "Clear",
    "Umbriel": "Easy-going",
    "Algieba": "Smooth",
    "Despina": "Smooth",
    "Erinome": "Clear",
    "Algenib": "Gravelly",
    "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat",
    "Achernar": "Soft",
    "Alnilam": "Firm",
    "Schedar": "Even",
    "Gacrux": "Mature",
    "Pulcherrima": "Forward",
    "Achird": "Friendly",
    "Zubenelgenubi": "Casual",
    "Vindemiatrix": "Gentle",
    "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}


# ============================================================
# STYLE PRESETS
# ============================================================

STYLE_PRESETS = {

    "Documentary": {
        "tone": (
            "serious, cinematic and intelligent"
        ),
        "pace": (
            "measured and natural"
        ),
        "emotion": (
            "controlled but emotionally aware"
        ),
    },

    "Cinematic Documentary": {
        "tone": (
            "cinematic, dramatic and immersive"
        ),
        "pace": (
            "deliberate with occasional pauses"
        ),
        "emotion": (
            "strong emotional variation without overacting"
        ),
    },

    "Investigative": {
        "tone": (
            "serious, investigative and authoritative"
        ),
        "pace": (
            "steady and deliberate"
        ),
        "emotion": (
            "restrained tension and curiosity"
        ),
    },

    "Suspenseful": {
        "tone": (
            "dark, mysterious and suspenseful"
        ),
        "pace": (
            "slow during suspense and faster during revelations"
        ),
        "emotion": (
            "high tension with controlled intensity"
        ),
    },

    "Emotional": {
        "tone": (
            "warm, human and emotionally expressive"
        ),
        "pace": (
            "natural with meaningful pauses"
        ),
        "emotion": (
            "emotionally rich but never melodramatic"
        ),
    },

    "Serious News": {
        "tone": (
            "professional, authoritative and serious"
        ),
        "pace": (
            "clear and moderately paced"
        ),
        "emotion": (
            "restrained and factual"
        ),
    },

    "Dramatic": {
        "tone": (
            "powerful, cinematic and dramatic"
        ),
        "pace": (
            "varied, using pauses for emphasis"
        ),
        "emotion": (
            "high but controlled"
        ),
    },

    "Calm & Mysterious": {
        "tone": (
            "calm, mysterious and atmospheric"
        ),
        "pace": (
            "slow and deliberate"
        ),
        "emotion": (
            "subtle tension and curiosity"
        ),
    },
}


# ============================================================
# PACE PRESETS
# ============================================================

PACE_PRESETS = {

    "Slow": (
        "Speak slowly and deliberately. "
        "Leave natural breathing space between important ideas."
    ),

    "Normal": (
        "Speak at a natural conversational documentary pace."
    ),

    "Fast": (
        "Speak slightly faster and maintain energetic momentum, "
        "while remaining clear and understandable."
    ),
}


# ============================================================
# EMOTION PRESETS
# ============================================================

EMOTION_PRESETS = {

    "Low": (
        "Keep emotion subtle and restrained."
    ),

    "Medium": (
        "Use noticeable but natural emotional variation."
    ),

    "High": (
        "Use strong emotional variation, emphasis and intensity "
        "where the story naturally calls for it."
    ),
}


# ============================================================
# BUILD TTS DIRECTOR PROMPT
# ============================================================

def build_tts_prompt(
    text,
    voice,
    style,
    pace="Normal",
    emotion="Medium",
):
    style_info = STYLE_PRESETS.get(
        style,
        STYLE_PRESETS["Documentary"]
    )

    pace_info = PACE_PRESETS.get(
        pace,
        PACE_PRESETS["Normal"]
    )

    emotion_info = EMOTION_PRESETS.get(
        emotion,
        EMOTION_PRESETS["Medium"]
    )

    voice_description = VOICES.get(
        voice,
        "Natural"
    )

    return f"""
You are a professional documentary voice actor.

VOICE:
{voice}

VOICE CHARACTER:
{voice_description}

OVERALL STYLE:
{style}

TONE:
{style_info["tone"]}

BASE PACE:
{style_info["pace"]}

EMOTIONAL PERFORMANCE:
{style_info["emotion"]}

PACE CONTROL:
{pace_info}

EMOTION CONTROL:
{emotion_info}

DIRECTOR'S NOTES:

Do NOT sound like you are reading a textbook.

Do NOT use a flat robotic delivery.

CRITICAL CONSISTENCY RULE:
The first 30 seconds establish the reference voice standard. Maintain that exact
clarity, vocal weight, microphone closeness, intelligibility, pace, and documentary
character until the final word. Never gradually become quieter, breathier, thinner,
more distant, faster, mumbled, or less articulate as the narration continues.
Do not change vocal character midway through the recording. Every sentence must
remain as clear and present as the opening.

Sound like a premium modern documentary narrator speaking directly
to a human audience — the kind of narration used in a high-quality
mystery/history/science documentary.

The target performance is CLEAR DOCUMENTARY, not a movie trailer:
confident, present, intelligent, cinematic and naturally intriguing.
It may carry a subtle sense of mystery and tension, but it must always
remain conversational, articulate and easy to understand.

Naturally emphasize important words.

Use controlled pitch and intensity variation rather than exaggerated acting.

Use short, natural pauses when a sentence creates suspense.

Slow down slightly before important revelations, then return to the normal
documentary pace.

Keep the voice forward and present in the mix. Never let dramatic moments
turn into whispering or distant speech.

Do not overact.

Do not sound like a news-reading robot.

DOCUMENTARY REFERENCE PERFORMANCE — CRITICAL:
From the opening sentence through the final sentence, maintain the same
clear, close, confident vocal character established in the opening minute.
Treat that opening-minute character as the reference for the entire read.
The voice must NOT progressively become softer, breathier, thinner, darker,
more distant, slower, faster, or less articulated as the narration continues.
Do not let long paragraphs or later sections change the delivery.
Every section should sound as though it was recorded in the same take,
with the same microphone distance, vocal weight, energy and clarity.

PACING CONSISTENCY — CRITICAL:
Keep a stable natural documentary speaking rate throughout. Do not gradually
speed up near the end. Pauses should be intentional and brief, not long
breathing gaps. Preserve enough space between words for every sentence to
remain immediately understandable.

VOCAL PRESENCE — CRITICAL:
Use a strong, clean speaking voice with clear consonants and full vowels.
Avoid whispering, breathiness, mumbling, vocal fry, weak endings, swallowed
words, or fading volume. Sentence endings must remain as clear and present
as sentence beginnings.

Do not literally read these instructions.

AUDIO CONSISTENCY — CRITICAL:
Keep the perceived loudness, microphone proximity, vocal weight, pitch character,
and speaking rate consistent from the first word to the last word.
Do NOT gradually become quieter, whisperier, breathier, thinner, distant,
or faster as the narration progresses.
Do NOT switch into a whisper or soft ASMR-like delivery unless the narration
explicitly requires a very brief dramatic moment. Return immediately to the
same clear, present documentary voice afterward.
Maintain strong consonants, clear vowels, and easy-to-understand articulation.
Every sentence must remain comfortably intelligible at normal playback volume.

Only speak the narration below.

NARRATION:

{text}
"""


# ============================================================
# PCM → WAV
# ============================================================

def pcm_to_wav(
    pcm_data,
    sample_rate=24000,
    channels=1,
    sample_width=2
):

    buffer = tempfile.NamedTemporaryFile(
        suffix=".wav",
        delete=False
    )

    buffer.close()

    with wave.open(
        buffer.name,
        "wb"
    ) as wf:

        wf.setnchannels(
            channels
        )

        wf.setsampwidth(
            sample_width
        )

        wf.setframerate(
            sample_rate
        )

        wf.writeframes(
            pcm_data
        )

    with open(
        buffer.name,
        "rb"
    ) as file:

        wav_data = file.read()

    try:
        os.remove(
            buffer.name
        )
    except:
        pass

    return wav_data


# ============================================================
# EXTRACT AUDIO
# ============================================================

def extract_audio_bytes(
    interaction
):

    output_audio = getattr(
        interaction,
        "output_audio",
        None
    )

    if output_audio is None:

        raise RuntimeError(
            "Gemini TTS returned no audio."
        )


    data = getattr(
        output_audio,
        "data",
        None
    )


    if data is None:

        raise RuntimeError(
            "Gemini TTS returned empty audio data."
        )


    if isinstance(
        data,
        str
    ):

        return base64.b64decode(
            data
        )


    if isinstance(
        data,
        bytes
    ):

        return data


    raise RuntimeError(
        "Unknown Gemini audio format."
    )


# ============================================================
# GENERATE VOICE
# ============================================================

def generate_voice(
    text,
    voice="Kore",
    style="Documentary",
    pace="Normal",
    emotion="Medium"
):

    if not text:

        raise ValueError(
            "TTS text cannot be empty."
        )


    if voice not in VOICES:

        raise ValueError(
            f"Unsupported voice: {voice}"
        )


    prompt = build_tts_prompt(
        text=text,
        voice=voice,
        style=style,
        pace=pace,
        emotion=emotion
    )


    def request(model):
        return client.interactions.create(
            model=model,
            input=prompt,
            response_format={"type": "audio"},
            generation_config={
                "speech_config": [{"voice": voice}]
            }
        )

    interaction = call_with_retry(
        request,
        "Gemini TTS",
        TTS_MODELS,
    )

    pcm_data = extract_audio_bytes(
        interaction
    )


    return pcm_to_wav(
        pcm_data
    )


# ============================================================
# PREVIEW VOICE
# ============================================================

def generate_voice_preview(
    voice,
    style="Cinematic Documentary",
    pace="Normal",
    emotion="Medium",
    preview_text=None
):

    if preview_text is None:

        preview_text = (
            "Imagine waking up one morning "
            "and discovering that everything "
            "you thought you knew had changed. "
            "This is the story of what happened next."
        )


    return generate_voice(
        text=preview_text,
        voice=voice,
        style=style,
        pace=pace,
        emotion=emotion
    )