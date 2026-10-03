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
            "clear, neutral and professional documentary narration"
        ),
        "pace": (
            "steady and natural"
        ),
        "emotion": (
            "neutral, consistent and restrained"
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
        "Speak at a steady natural documentary pace at a consistent rate. "
        "Do not increase the speaking rate later in the narration. "
        "Keep approximately the same words-per-minute throughout this passage, "
        "with clear articulation and brief natural breathing space."
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

    "Neutral": (
        "Use no intentional emotional performance. "
        "Keep the delivery neutral, steady, consistent and clearly spoken "
        "from the first word to the last word. "
        "Do not become mysterious, dramatic, darker, softer, breathier, "
        "whispery or more intense as the story progresses."
    ),

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

CRITICAL LONG-FORM CONSISTENCY RULE:
The opening minute is the reference for the entire narration. Maintain the same
clear, present, natural voice character from the first word to the final word.
Do not let the narration gradually change its vocal character as it gets longer.

Keep these characteristics constant throughout:
- same perceived loudness
- same microphone closeness
- same vocal weight
- same pitch character
- same speaking rate
- same approximate words-per-minute
- same sentence cadence
- same energy
- same articulation
- same clarity

NEVER gradually become:
- quieter
- whispery
- breathy
- thinner
- darker
- more mysterious
- more distant
- slower
- faster
- mumbled
- less articulate

This is a neutral documentary performance, not cinematic acting.

Do NOT add emotional acting to suspense, reveals, shocking facts, sad moments,
or dramatic parts of the story. The narration may describe emotional or
suspenseful events, but the VOICE DELIVERY must remain neutral and consistent.

Do NOT whisper.
Do NOT use ASMR-like delivery.
Do NOT intentionally lower the voice for mystery.
Do NOT create dramatic vocal drops.
Do NOT become more intense during revelations.
Do NOT add theatrical pauses for emotional effect.

Use normal, natural sentence rhythm and brief breathing space where needed.
Treat the speaking rate as a hard constraint: choose one natural conversational
rate and keep it unchanged from the first sentence to the last sentence.
Do not compress later sentences to fit more narration into less time.
Do not gradually speed up near the end or after long paragraphs.
Keep sentence endings as clear and present as sentence beginnings.

Use clear consonants, full vowels and strong articulation.
Every sentence must remain comfortably understandable at normal playback volume.

DOCUMENTARY REFERENCE PERFORMANCE — CRITICAL:
Treat the first minute as the fixed reference performance for both VOICE and
TEMPO. The rest of the narration should sound like the same narrator continuing
the same recording, with the same vocal presence, cadence and speaking rate.
Do not let long paragraphs or later sections change the tempo.

IMPORTANT:
Storytelling can still create curiosity through the WORDS, facts and structure.
Do not create curiosity by changing the voice into a mysterious, dramatic,
whispery or emotional performance.

PACE LOCK:
The narrator must NOT speed up because the narration is long. Keep the same
comfortable cadence throughout. Long sentences should receive the same amount
of speaking time per word as earlier sentences. Never rush the final third.

Do not literally read these instructions.

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