"""ElevenLabs Text-to-Speech client for ableguides.

Calls the ElevenLabs REST API to generate WAV audio for each (voice, cue) pair.
Output files are written to: output_dir/{voice_slug}/{cue_id}.wav

Features:
- Skips files that already exist (idempotent by default).
- --force flag regenerates existing files.
- Exponential backoff retry on HTTP 429 (rate limit) and 5xx errors.
- Dry-run mode logs planned calls without hitting the API.
- All API calls use httpx (stdlib-like, no extra auth helpers needed).

# TODO(future): Support ElevenLabs streaming endpoint for lower latency.
# TODO(future): Allow per-cue voice_settings overrides (stability, similarity).
"""

from __future__ import annotations

import hashlib
import logging
import struct
import time
from pathlib import Path

import httpx

from ableguides.config import TTSSettings
from ableguides.models import Cue, GenerateResult, TTSError, Voice

log = logging.getLogger(__name__)

_API_BASE = "https://api.elevenlabs.io/v1"
_OUTPUT_FORMAT = "pcm_22050"  # raw PCM -- we wrap in WAV manually (22.05kHz, 16-bit, mono)
_MODEL_ID = "eleven_turbo_v2_5"

# Retry settings
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds; doubled each retry


def generate_cue(
    cue: Cue,
    voice: Voice,
    output_dir: Path,
    api_key: str,
    force: bool = False,
    dry_run: bool = False,
    tts_settings: TTSSettings | None = None,
) -> GenerateResult:
    """Generate a single WAV file for one (voice, cue) pair.

    Args:
        cue:        The Cue to speak.
        voice:      The ElevenLabs voice to use.
        output_dir: Root output directory; file written to output_dir/{voice.slug}/{cue.id}.wav.
        api_key:    ElevenLabs API key.
        force:      If True, regenerate even if the file already exists.
        dry_run:    If True, log intent but do not call the API or write files.

    Returns a GenerateResult indicating success, skip, or failure.
    """
    voice_dir = output_dir / voice.slug
    output_path = voice_dir / f"{cue.id}.wav"

    if output_path.exists() and not force:
        log.debug("Skipping (already exists): %s", output_path.name)
        return GenerateResult(
            voice_name=voice.name,
            cue_id=cue.id,
            output_path=output_path,
            success=True,
            skipped=True,
        )

    if dry_run:
        log.info("[dry-run] Would generate: %s / %s.wav", voice.name, cue.id)
        return GenerateResult(
            voice_name=voice.name,
            cue_id=cue.id,
            output_path=output_path,
            success=True,
            skipped=True,
        )

    try:
        salt = tts_settings.seed_salt if tts_settings else 0
        seed = _cue_seed(cue.id) ^ (salt & 0xFFFFFFFF)
        audio_bytes = _call_api(cue.spoken, voice.voice_id, api_key, tts_settings, seed=seed)
        voice_dir.mkdir(parents=True, exist_ok=True)
        _write_wav(audio_bytes, output_path)
        log.info("Generated: %s / %s.wav", voice.name, cue.id)
        return GenerateResult(
            voice_name=voice.name,
            cue_id=cue.id,
            output_path=output_path,
            success=True,
        )
    except TTSError as e:
        log.error("Failed to generate %s / %s: %s", voice.name, cue.id, e.message)
        return GenerateResult(
            voice_name=voice.name,
            cue_id=cue.id,
            output_path=output_path,
            success=False,
            error=e.message,
        )


def generate_all(
    cues: list[Cue],
    voices: list[Voice],
    output_dir: Path,
    api_key: str,
    force: bool = False,
    dry_run: bool = False,
    voice_filter: str | None = None,
    cue_filter: str | None = None,
    tts_settings: TTSSettings | None = None,
) -> list[GenerateResult]:
    """Generate WAV files for all (voice, cue) combinations.

    Args:
        cues:         Full expanded cue list.
        voices:       All configured voices.
        output_dir:   Root output directory.
        api_key:      ElevenLabs API key.
        force:        Regenerate existing files.
        dry_run:      Log without calling the API.
        voice_filter: If set, only generate for this voice name.
        cue_filter:   If set, only generate for this cue id.

    Returns a flat list of GenerateResults.
    """
    results: list[GenerateResult] = []

    target_voices = [v for v in voices if voice_filter is None or v.name == voice_filter]
    target_cues = [c for c in cues if cue_filter is None or c.id == cue_filter]

    if not target_voices:
        log.warning("No matching voices for filter: %s", voice_filter)
    if not target_cues:
        log.warning("No matching cues for filter: %s", cue_filter)

    for voice in target_voices:
        for cue in target_cues:
            result = generate_cue(
                cue=cue,
                voice=voice,
                output_dir=output_dir,
                api_key=api_key,
                force=force,
                dry_run=dry_run,
                tts_settings=tts_settings,
            )
            results.append(result)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cue_seed(cue_id: str) -> int:
    """Derive a stable 32-bit seed from the cue ID.

    Uses MD5 (not for security — just for a fast, stable, portable hash).
    The same cue_id always maps to the same seed across runs and machines,
    making generation fully deterministic when combined with ElevenLabs' seed param.
    """
    digest = hashlib.md5(cue_id.encode()).digest()
    return int.from_bytes(digest[:4], "little")


def _call_api(
    text: str,
    voice_id: str,
    api_key: str,
    tts_settings: TTSSettings | None = None,
    seed: int | None = None,
) -> bytes:
    """Call the ElevenLabs TTS endpoint and return raw PCM bytes.

    Uses exponential backoff on rate limit (429) and server errors (5xx).
    Raises TTSError on unrecoverable failure.
    """
    settings = tts_settings or TTSSettings()
    url = f"{_API_BASE}/text-to-speech/{voice_id}?output_format={_OUTPUT_FORMAT}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
    }
    payload = {
        "text": text,
        "model_id": _MODEL_ID,
        "voice_settings": settings.as_dict(),
        # Pin to English so eleven_multilingual_v2 never infers another language
        # from a seed or short/ambiguous cue text.
        "language_code": "en",
    }
    if seed is not None:
        payload["seed"] = seed

    delay = _RETRY_BASE_DELAY
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            if attempt == _MAX_RETRIES:
                raise TTSError(f"Network error calling ElevenLabs API: {e}") from e
            log.warning("Network error (attempt %d/%d): %s", attempt, _MAX_RETRIES, e)
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code == 200:
            return response.content

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == _MAX_RETRIES:
                raise TTSError(
                    f"ElevenLabs API error {response.status_code} after {_MAX_RETRIES} retries"
                )
            log.warning(
                "API returned %d (attempt %d/%d), retrying in %.0fs...",
                response.status_code, attempt, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
            delay *= 2
            continue

        # Non-retryable error
        try:
            detail = response.json().get("detail", {})
            msg = detail.get("message", response.text[:200])
        except Exception:
            msg = response.text[:200]
        raise TTSError(
            f"ElevenLabs API error {response.status_code}: {msg}"
        )

    raise TTSError("ElevenLabs API failed after all retries")  # unreachable


def _write_wav(pcm_bytes: bytes, path: Path) -> None:
    """Wrap raw PCM bytes in a standard RIFF/WAV header and write to disk.

    ElevenLabs pcm_44100 returns 16-bit signed, mono, 44100 Hz PCM.
    Ableton requires a proper WAV container (RIFF header), not bare PCM.
    """
    sample_rate = 22050
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    riff_size = 36 + data_size  # total file size minus the 8-byte RIFF+size fields

    header = struct.pack(
        "<4sI4s"   # RIFF, file_size, WAVE
        "4sIHHIIHH"  # fmt chunk
        "4sI",       # data chunk header
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    path.write_bytes(header + pcm_bytes)
