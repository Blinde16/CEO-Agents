from __future__ import annotations


def handle_voice_transcript(transcript: str) -> dict:
    return {"source": "twilio_voice", "transcript": transcript}
