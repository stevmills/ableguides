"""WAV audio analysis utilities for ableguides.

Used during rack assembly to find the true onset of each generated speech
sample so Ableton's Simpler start marker is placed at the first audible frame
rather than at any silence pre-roll added by the TTS engine.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path


# Linear amplitude threshold (out of 32767 for 16-bit PCM).
# ~0.3 % of full scale ≈ −50 dB.  Comfortably above the noise floor of
# ElevenLabs PCM output while still catching soft onset consonants.
_ONSET_THRESHOLD = 100


def analyze_wav_onset(path: Path, threshold: int = _ONSET_THRESHOLD) -> int:
    """Return the sample index of the first frame whose amplitude exceeds *threshold*.

    The returned value is in the same units as Ableton's ``MultiSamplePart``
    ``SampleStart`` attribute (absolute sample count from the start of the file).

    Returns 0 if the file cannot be read, is empty, or is entirely silent.

    Args:
        path:      Path to a 16-bit signed mono WAV file.
        threshold: Minimum |amplitude| (0–32767) to be considered non-silent.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()

            if sampwidth != 2 or n_frames == 0:
                return 0

            raw = wf.readframes(n_frames)
    except Exception:
        return 0

    n_samples = len(raw) // (sampwidth * n_channels)
    if n_samples == 0:
        return 0

    samples = struct.unpack(f"<{n_samples * n_channels}h", raw[: n_samples * sampwidth * n_channels])

    # Advance frame-by-frame; for mono n_channels == 1 so stride == 1.
    for frame_idx in range(n_samples):
        for ch in range(n_channels):
            if abs(samples[frame_idx * n_channels + ch]) >= threshold:
                return frame_idx

    return 0
