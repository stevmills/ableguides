"""Core data models for ableguides.

Cue        -- a single guide cue (e.g. "Verse", "Chorus 3")
CueEntry   -- one row in cues.json (base cue with optional variant count)
CueList    -- the full loaded cues.json
Voice      -- an ElevenLabs TTS voice with name + voice_id
GenerateResult -- result from a TTS generation call
BuildResult    -- result from assembling a rack preset
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Cue models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    """A single expanded guide cue -- one WAV file per voice.

    Attributes:
        id:        Machine-safe identifier (e.g. 'verse', 'bridge_3').
        text:      The display text / pad label (e.g. 'Verse', 'Bridge 3').
        spoken:    Text sent to the TTS API. Defaults to ``text`` when not
                   overridden. Supports trailing punctuation (. ! ?) to shape
                   prosody, or full SSML wrapped in ``<speak>...</speak>``.
        pad_name:  The Ableton drum pad label (same as text by default).
        base_id:   The base cue id without variant suffix (e.g. 'bridge').
        variant:   Variant number, or None for base cues.
    """

    id: str
    text: str
    spoken: str
    pad_name: str
    base_id: str
    receiving_note: int = 0
    count_in: str = "none"
    variant: int | None = None


@dataclass
class CueEntry:
    """One row in cues.json.

    Attributes:
        id:        Slug identifier for the cue (e.g. 'verse').
        text:      Display / pad label (e.g. 'Verse').
        pad_group: Drum pad group label in Ableton (may differ from text).
        variants:  Number of numbered variants (e.g. 12 -> Verse 1 ... Verse 12).
                   If 0 or not set, only the base cue is produced.
        spoken:    Optional override for the TTS text. Defaults to ``text`` when
                   absent. Trailing punctuation (. ! ?) shapes prosody; the
                   variant number is inserted before the trailing punctuation so
                   e.g. ``spoken="Verse."`` produces ``"Verse 3."`` for variant 3.
                   Full SSML (``<speak>...</speak>``) is also accepted.
    """

    id: str
    text: str
    pad_group: str = ""
    variants: int = 0
    spoken: str = ""
    receiving_note: int = 0
    count_in: str = "none"

    def _spoken_base(self) -> str:
        return self.spoken or self.text

    def expand(self) -> list[Cue]:
        """Return the base cue plus any numbered variants as Cue objects."""
        spoken_base = self._spoken_base()

        # Split trailing punctuation so variant numbers slot in before it.
        # e.g. "Verse." → stem="Verse", suffix="."
        # SSML strings are left intact (number appended before </speak>).
        if spoken_base.rstrip().endswith("</speak>"):
            stem = spoken_base.rstrip()[: spoken_base.rstrip().rfind("</speak>")]
            suffix = "</speak>"
        else:
            stem = spoken_base.rstrip(".!?,;")
            suffix = spoken_base[len(stem):]

        cues: list[Cue] = [
            Cue(
                id=self.id,
                text=self.text,
                spoken=spoken_base,
                pad_name=self.text,
                base_id=self.id,
                receiving_note=self.receiving_note,
                count_in=self.count_in,
                variant=None,
            )
        ]
        for n in range(1, self.variants + 1):
            variant_id = f"{self.id}_{n}"
            variant_text = f"{self.text} {n}"
            variant_spoken = f"{stem} {n}{suffix}"
            cues.append(
                Cue(
                    id=variant_id,
                    text=variant_text,
                    spoken=variant_spoken,
                    pad_name=variant_text,
                    base_id=self.id,
                    receiving_note=self.receiving_note,
                    count_in=self.count_in,
                    variant=n,
                )
            )
        return cues


@dataclass
class CueList:
    """The full set of cues loaded from cues.json."""

    schema_version: int
    entries: list[CueEntry]

    def expand(self) -> list[Cue]:
        """Return every cue (base + all variants) in definition order."""
        result: list[Cue] = []
        for entry in self.entries:
            result.extend(entry.expand())
        return result

    def get_entry(self, base_id: str) -> CueEntry | None:
        """Look up a CueEntry by base id."""
        for entry in self.entries:
            if entry.id == base_id:
                return entry
        return None


# ---------------------------------------------------------------------------
# Voice models
# ---------------------------------------------------------------------------


@dataclass
class Voice:
    """An ElevenLabs TTS voice.

    Attributes:
        name:     Short slug used for folder naming and Ableton chain labels.
        voice_id: ElevenLabs voice ID string.
    """

    name: str
    voice_id: str

    @property
    def slug(self) -> str:
        """Filesystem-safe slug from the voice name."""
        return slugify(self.name)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class GenerateResult:
    """Outcome of a single TTS generation call."""

    voice_name: str
    cue_id: str
    output_path: Path
    success: bool
    skipped: bool = False
    error: str | None = None


@dataclass
class BuildResult:
    """Outcome of assembling one or more rack presets."""

    output_path: Path
    voice_count: int
    cue_count: int
    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AblGuidesError(Exception):
    """Base exception for ableguides errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(AblGuidesError):
    """Raised when configuration is invalid or missing required values."""


class TTSError(AblGuidesError):
    """Raised when the ElevenLabs API call fails."""


class BuildError(AblGuidesError):
    """Raised when rack assembly fails."""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def load_cues(cues_path: Path) -> CueList:
    """Load and parse cues.json, returning a CueList.

    Raises AblGuidesError if the file is missing or malformed.
    """
    if not cues_path.exists():
        raise AblGuidesError(f"Cues file not found: {cues_path}")
    try:
        data = json.loads(cues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AblGuidesError(f"Failed to parse {cues_path}: {e}") from e

    entries = []
    for raw in data.get("cues", []):
        entries.append(
            CueEntry(
                id=raw["id"],
                text=raw["text"],
                pad_group=raw.get("pad_group", raw["text"]),
                variants=raw.get("variants", 0),
                spoken=raw.get("spoken", ""),
                receiving_note=raw.get("receiving_note", 0),
                count_in=raw.get("count_in", "none"),
            )
        )
    return CueList(
        schema_version=data.get("schema_version", 1),
        entries=entries,
    )


def slugify(text: str, separator: str = "-") -> str:
    """Convert text to a filesystem-safe slug.

    Example:
        >>> slugify("Guide Female")
        'guide-female'
        >>> slugify("Pre-Chorus")
        'pre-chorus'
    """
    lowered = text.lower()
    slug = re.sub(r"[^a-z0-9]+", separator, lowered)
    return slug.strip(separator)
