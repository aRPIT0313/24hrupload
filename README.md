# AI Long-Form Video Generator

## Current workflow

1. Choose **I choose the topic** or **AI chooses from my YouTube data**.
2. Select **3 minutes** or **4 minutes**.
3. Select style and voice.
4. Generate the documentary.
5. Review the video and thumbnail.
6. Publish immediately or choose an exact date/time for YouTube scheduling.

## Gemini usage / free-tier efficiency

- Main planning is **one Gemini generation request**.
- AI topic selection happens inside that same planning request; it does not use a second Gemini request.
- Long-form narration is **one Gemini TTS request per video**. Narration is never split into many TTS sections.
- Voice previews are cached per voice/style in the current Streamlit session, so repeated previews reuse the existing preview audio.
- Visual candidates are limited to 28 because 3–4 minutes does not need 60 candidates. This reduces Gemini output-token usage without making the visual plan sparse.

## Voice quality

The TTS director prompt explicitly requires stable loudness, vocal presence, pitch character, speaking rate and articulation from beginning to end and prohibits gradual whispering/breathiness.

The final narration is also processed with gentle compression followed by loudness normalization. This is intentionally conservative: the goal is to improve intelligibility and consistency without destroying the natural premium voice character.
