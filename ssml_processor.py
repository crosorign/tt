"""
SSML-based voice naturalisation for edge-tts.

Uses LLM to rewrite scripts into prosody-optimised spoken form,
then generates SSML-aware audio via edge-tts monkey-patch.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import logging
from typing import Callable, List, Optional

logger = logging.getLogger("media.ssml_processor")

# ── Supported edge-tts voices ───────────────────────────────────────
# Multilingual (best natural quality, support prosody + break + emphasis)
VOICE_EN_MALE   = "en-US-AndrewMultilingualNeural"    # TT — warm, confident presenter
VOICE_EN_FEMALE = "en-US-AvaMultilingualNeural"       # TT female — clear, engaging
VOICE_TA_FEMALE = "ta-IN-PallaviNeural"               # AM/NN female Tamil
VOICE_TA_MALE   = "ta-IN-ValluvarNeural"              # AM/NN male Tamil

# ── LLM rewrite prompt ──────────────────────────────────────────────
SSML_REWRITE_PROMPT_EN = """You are a world-class speech writer, linguist, and prosody engineer specializing in Microsoft Azure Neural Text-to-Speech.
Your task is to transform the provided text into highly natural, human-like speech optimized for Microsoft Edge TTS.

Primary Goal:
Make the audio sound as close as possible to a real human speaker in English while preserving the original meaning, intent, tone, and information.

Requirements:
- Preserve meaning exactly; do not add or remove information.
- Rewrite written text into natural spoken language.
- Improve conversational flow and rhythm.
- Break long sentences into shorter, natural speech units.
- Add realistic pauses where humans naturally pause.
- Use punctuation strategically to improve prosody and intonation.
- Avoid robotic, repetitive, overly formal, or book-like phrasing.
- Ensure smooth transitions between ideas.
- Make the speech sound warm, engaging, confident, and natural.
- Optimize for listeners rather than readers.
- Use natural conversational English.
- Expand awkward written constructions into spoken equivalents.
- Make narration sound like a professional presenter or podcast host.

SSML Optimization (edge-tts compatible tags ONLY):
- Use <break time="200ms"/> <break time="400ms"/> <break time="600ms"/> only where beneficial.
- Apply <emphasis level="moderate">...</emphasis> selectively on key facts/numbers.
- Use <prosody rate="-5%" pitch="+5%">...</prosody> for excited moments.
- Use <prosody rate="-10%" pitch="-2%">...</prosody> for serious/important moments.
- Wrap entire output in <prosody rate="-8%" pitch="+1Hz"> for base naturalness.
- DO NOT use mstts:express-as — not supported.
- DO NOT use xml:lang tags in English-only output.

Output Rules:
- Return ONLY the inner content (no <speak> or <voice> wrapper — those are added by the engine).
- No explanations, no markdown, no comments, no notes outside the speech content.
- Valid XML only — escape & as &amp; if it appears in plain text.

Input Text:
{text}"""

SSML_REWRITE_PROMPT_TA = """You are a world-class speech writer, linguist, and prosody engineer specializing in Microsoft Azure Neural Text-to-Speech.
Your task is to transform the provided Tamil text into highly natural, human-like speech.

Primary Goal:
Make the audio sound as close as possible to a real Tamil human speaker while preserving the original meaning, intent, tone, and information.

Requirements:
- Preserve meaning exactly; do not add or remove information.
- Use natural, grammatically correct spoken Tamil.
- Avoid literal or machine-like wording.
- Improve sentence flow for realistic Tamil speech patterns.
- Ensure Tamil sounds comfortable and native when synthesized.
- Break long sentences into shorter natural spoken units.
- Add realistic pauses where Tamil speakers naturally pause.
- Make speech sound warm, engaging, and natural for Tamil listeners.
- Optimize for listeners rather than readers.

SSML Optimization (edge-tts compatible tags ONLY):
- Use <break time="200ms"/> <break time="400ms"/> <break time="600ms"/> only where beneficial.
- Apply <emphasis level="moderate">...</emphasis> selectively on key words/numbers.
- Use <prosody rate="-5%" pitch="+2Hz"> wrapper for natural Tamil cadence.
- DO NOT use mstts:express-as — not supported.
- DO NOT use xml:lang tags.

Output Rules:
- Return ONLY the inner content (no <speak> or <voice> wrapper — those are added by the engine).
- No explanations, no markdown, no comments outside the speech content.
- Valid XML only.

Input Text:
{text}"""

# ── Monkey-patch: bypass XML escaping for SSML content ──────────────
def _patch_edge_tts_for_ssml():
    """Monkey-patch edge_tts.communicate.escape to pass SSML through unescaped."""
    try:
        import edge_tts.communicate as _comm
        _orig = _comm.escape

        def _ssml_passthrough(text: str) -> str:
            stripped = text.strip()
            # Detect pre-built SSML inner content
            if any(tag in stripped for tag in ('<break', '<prosody', '<emphasis', '<lang ')):
                return text  # Pass through as-is
            return _orig(text)

        _comm.escape = _ssml_passthrough
        logger.debug("edge-tts SSML passthrough patch applied")
    except Exception as e:
        logger.warning("Could not patch edge-tts for SSML: %s", e)


# ── LLM rewrite ─────────────────────────────────────────────────────
def rewrite_script_as_ssml(
    script: str,
    language: str = "en",
    call_llm_fn: Optional[Callable] = None,
) -> str:
    """
    Use LLM to rewrite script into prosody-optimised SSML inner content.
    Falls back to smart punctuation-based conversion if LLM unavailable.
    """
    if call_llm_fn is None:
        logger.warning("No LLM function provided — using punctuation fallback")
        return _punctuation_to_ssml(script, language)

    prompt_template = SSML_REWRITE_PROMPT_TA if language == "ta" else SSML_REWRITE_PROMPT_EN
    prompt = prompt_template.format(text=script[:4000])

    try:
        raw = call_llm_fn(prompt, max_tokens=6000)
        # Strip any accidental wrapper tags
        cleaned = _strip_speak_wrapper(raw.strip())
        if cleaned and len(cleaned) > 50:
            logger.info("SSML rewrite: %d chars → %d chars", len(script), len(cleaned))
            return cleaned
    except Exception as e:
        logger.warning("LLM SSML rewrite failed: %s — using fallback", e)

    return _punctuation_to_ssml(script, language)


def _strip_speak_wrapper(text: str) -> str:
    """Remove <speak>/<voice>/<prosody> wrappers if LLM added them."""
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = re.sub(r"<speak[^>]*>", "", text)
    text = re.sub(r"</speak>", "", text)
    text = re.sub(r"<voice[^>]*>", "", text)
    text = re.sub(r"</voice>", "", text)
    # Remove mstts tags (not supported by edge-tts)
    text = re.sub(r"<mstts:[^>]*>", "", text)
    text = re.sub(r"</mstts:[^>]+>", "", text)
    # Remove lang tags (edge-tts handles this via voice selection)
    text = re.sub(r'<lang[^>]*>', "", text)
    text = re.sub(r'</lang>', "", text)
    return text.strip()


def _punctuation_to_ssml(script: str, language: str = "en") -> str:
    """
    Smart fallback: convert pause markers and punctuation to SSML breaks.
    No LLM needed — rule-based prosody improvement.
    """
    # Convert our custom pause markers
    text = script
    text = text.replace("[PAUSE_LONG]",  '<break time="700ms"/>')
    text = text.replace("[PAUSE_MED]",   '<break time="400ms"/>')
    text = text.replace("[PAUSE_SHORT]", '<break time="200ms"/>')

    # Sentence-ending pauses
    text = re.sub(r'([.!?])\s+', r'\1<break time="300ms"/> ', text)
    # Em-dash pauses
    text = re.sub(r'—', '<break time="200ms"/>—<break time="200ms"/>', text)
    # Ellipsis
    text = re.sub(r'\.{3,}', '<break time="500ms"/>', text)

    # Numbers/prices get emphasis in English
    if language == "en":
        text = re.sub(
            r'(₹[\d,]+(?:\s*(?:lakh|crore|thousand))?)',
            r'<emphasis level="moderate">\1</emphasis>',
            text
        )
        text = re.sub(
            r'\b(\d+(?:\.\d+)?(?:%|x|km|kmpl|bhp|Nm))\b',
            r'<emphasis level="moderate">\1</emphasis>',
            text
        )

    # Wrap in natural base prosody
    rate = "-8%" if language == "en" else "-10%"
    pitch = "+1Hz" if language == "en" else "+2Hz"
    return f'<prosody rate="{rate}" pitch="{pitch}">{text}</prosody>'


# ── SSML → audio ────────────────────────────────────────────────────
async def _synthesize_ssml_chunk(
    ssml_inner: str,
    voice: str,
    output_path: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bool:
    """Generate audio from SSML inner content via edge-tts."""
    import edge_tts

    _patch_edge_tts_for_ssml()

    communicate = edge_tts.Communicate(
        ssml_inner,
        voice,
        rate=rate,
        pitch=pitch,
    )
    try:
        await communicate.save(output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        logger.error("SSML synthesis failed: %s", e)
        return False


def generate_ssml_audio(
    script: str,
    output_path: str,
    voice: str,
    language: str = "en",
    call_llm_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
    base_rate: str = "+0%",
    base_pitch: str = "+0Hz",
) -> bool:
    """
    Full pipeline: script → SSML rewrite → edge-tts → EQ → output.
    Returns True on success.
    """
    runner = run_fn or _default_run

    # Step 1: Rewrite to SSML
    ssml_inner = rewrite_script_as_ssml(script, language, call_llm_fn)

    # Step 2: Split into chunks (edge-tts has 4096 byte limit)
    chunks = _split_ssml_chunks(ssml_inner, max_bytes=3800)
    logger.info("SSML audio: %d chunks for %d chars", len(chunks), len(script))

    temp_dir = os.path.dirname(output_path) or "/tmp"
    chunk_paths: List[str] = []

    # Step 3: Synthesize each chunk
    async def _synth_all():
        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(temp_dir, f"ssml_chunk_{i}.mp3")
            ok = await _synthesize_ssml_chunk(chunk, voice, chunk_path, base_rate, base_pitch)
            if ok:
                chunk_paths.append(chunk_path)
            else:
                # Fallback: strip SSML tags and use plain text
                plain = re.sub(r"<[^>]+>", " ", chunk)
                plain = re.sub(r"\s+", " ", plain).strip()
                fallback_path = os.path.join(temp_dir, f"ssml_chunk_{i}_fb.mp3")
                import edge_tts
                c = edge_tts.Communicate(plain, voice, rate=base_rate, pitch=base_pitch)
                try:
                    await c.save(fallback_path)
                    if os.path.exists(fallback_path):
                        chunk_paths.append(fallback_path)
                except Exception as e2:
                    logger.error("Fallback TTS also failed chunk %d: %s", i, e2)

    asyncio.run(_synth_all())

    if not chunk_paths:
        logger.error("All SSML chunks failed")
        return False

    # Step 4: Concatenate chunks
    raw_path = output_path.replace(".mp3", "_ssml_raw.mp3")
    if len(chunk_paths) == 1:
        os.rename(chunk_paths[0], raw_path)
    else:
        concat_file = os.path.join(temp_dir, "ssml_concat.txt")
        with open(concat_file, "w") as f:
            for p in chunk_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        r = runner(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
             "-c", "copy", raw_path],
            timeout=300
        )
        if r.returncode != 0 or not os.path.exists(raw_path):
            logger.error("Concat failed")
            return False

    # Cleanup chunks
    for p in chunk_paths:
        try:
            os.remove(p)
        except OSError:
            pass

    # Step 5: EQ — natural, not over-processed
    eq = _get_eq_filter(language)
    r = runner(
        ["ffmpeg", "-y", "-i", raw_path, "-af", eq, output_path],
        timeout=600
    )
    try:
        os.remove(raw_path)
    except OSError:
        pass

    if r.returncode != 0:
        # EQ failed — use raw
        os.rename(raw_path, output_path)

    return os.path.exists(output_path) and os.path.getsize(output_path) > 5000


def _get_eq_filter(language: str) -> str:
    """Minimal, natural-sounding EQ. Let SSML prosody do the heavy lifting."""
    if language == "ta":
        return (
            "highpass=f=80,"
            "equalizer=f=200:t=q:w=0.9:g=1.5,"
            "equalizer=f=800:t=q:w=0.8:g=2,"
            "equalizer=f=3000:t=q:w=1:g=1,"
            "equalizer=f=6000:t=q:w=1:g=-1.5,"
            "aecho=0.75:0.62:26:0.05,"
            "acompressor=threshold=-20dB:ratio=1.6:attack=10:release=300:makeup=1.5,"
            "loudnorm=I=-14:TP=-1.5:LRA=12"
        )
    else:  # English
        return (
            "highpass=f=85,"
            "equalizer=f=180:t=q:w=0.9:g=1.5,"
            "equalizer=f=1000:t=q:w=0.8:g=1.5,"
            "equalizer=f=3500:t=q:w=1:g=1,"
            "equalizer=f=7000:t=q:w=1:g=-1.5,"
            "aecho=0.72:0.58:22|40:0.06|0.03,"
            "acompressor=threshold=-20dB:ratio=1.6:attack=10:release=300:makeup=1.5,"
            "loudnorm=I=-14:TP=-1.5:LRA=12"
        )


def _split_ssml_chunks(ssml: str, max_bytes: int = 3800) -> List[str]:
    """Split SSML at sentence/break boundaries without cutting tags."""
    if len(ssml.encode("utf-8")) <= max_bytes:
        return [ssml]

    # Split at sentence boundaries
    parts = re.split(r'(?<=[.!?।])\s+', ssml)
    chunks: List[str] = []
    current = ""
    for part in parts:
        test = f"{current} {part}".strip() if current else part
        if len(test.encode("utf-8")) <= max_bytes:
            current = test
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks or [ssml[:max_bytes]]


def _default_run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
