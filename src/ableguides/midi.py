"""MIDI clip generator for ableguides.

Generates standard MIDI files (.mid) for each expanded cue, matching the
drum rack pad layout defined in cues.json (receiving_note).

Directory layout
----------------
  midi_dir/          ← trigger-only clips (no count-in, time-sig independent)
                       + REVIEW - All Cues.mid
  midi_dir/4-4/      ← 4/4 count-in section clips + Count In.mid
  midi_dir/3-4/      ← 3/4 count-in section clips + Count In.mid
  midi_dir/6-8/      ← 6/8 count-in section clips + Count In.mid
  …

Clip types
----------
  Trigger (root): 1 beat-unit, cue note only.  Used to call any section without
    a count-in.  Generated once per cue regardless of time signatures.

  Count-in section (subfolder): plays the cue on beat 1 then counts 2…N.
    One file per cue per configured time signature.
    - Non-variant: one full bar (beats_per_bar beat-units)
    - Variant:     bar + 1 beat (beat 1 = cue, beat 2 = silent, 3…N+1 = count)

  Count In (subfolder): standalone "2-3-4" (or 2-3, 2-3-4-5-6 etc.) with no
    section cue on beat 1.  One file per configured time signature.

count_in field on CueEntry:
  "none" → trigger clip only
  "yes"  → count-in section clip only (per time sig)
  "both" → trigger clip (root) + count-in section clip (per time sig)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import mido

from ableguides.models import Cue, CueList

log = logging.getLogger(__name__)

_TICKS_PER_BEAT = 96   # ticks per quarter note
_DEFAULT_VELOCITY = 64
_NUMBERS_RECV_NOTE = 117


# ---------------------------------------------------------------------------
# TimeSignature
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeSignature:
    """A MIDI time signature with derived tick geometry.

    Attributes:
        numerator:   Beats per bar (top number).
        denominator: Note value (bottom number): 4 = quarter, 8 = eighth.
    """

    numerator: int
    denominator: int

    @property
    def label(self) -> str:
        """Filesystem-safe label, e.g. '4-4', '3-4', '6-8'."""
        return f"{self.numerator}-{self.denominator}"

    @property
    def beat_ticks(self) -> int:
        """Ticks per single beat unit (one denominator note)."""
        return _TICKS_PER_BEAT * 4 // self.denominator

    @property
    def beats_per_bar(self) -> int:
        return self.numerator

    @property
    def bar_ticks(self) -> int:
        return self.beat_ticks * self.numerator


# Commonly-used time signatures, keyed by the string used in ableguides.toml.
KNOWN_TIME_SIGS: dict[str, TimeSignature] = {
    "4/4":  TimeSignature(4, 4),
    "3/4":  TimeSignature(3, 4),
    "2/4":  TimeSignature(2, 4),
    "5/4":  TimeSignature(5, 4),
    "6/8":  TimeSignature(6, 8),
    "12/8": TimeSignature(12, 8),
}

_DEFAULT_TIME_SIG = KNOWN_TIME_SIGS["4/4"]


def parse_time_sigs(strings: list[str]) -> list[TimeSignature]:
    """Convert a list of 'N/D' strings to TimeSignature objects.

    Unknown values are warned and skipped.  Falls back to [4/4] if all fail.
    """
    result: list[TimeSignature] = []
    for s in strings:
        ts = KNOWN_TIME_SIGS.get(s)
        if ts is None:
            log.warning("Unknown time signature '%s' in config — skipping.", s)
        else:
            result.append(ts)
    return result or [_DEFAULT_TIME_SIG]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_clip(
    cue: Cue,
    cue_list: CueList,
    midi_dir: Path,
    with_count_in: bool,
    force: bool = False,
    time_sig: TimeSignature | None = None,
) -> Path | None:
    """Generate a single MIDI clip for a cue.

    Trigger clips (with_count_in=False) go to midi_dir root.
    Count-in clips (with_count_in=True) go to midi_dir/{ts.label}/.
    Cues that belong to a variant group (variants > 0) or are numbers get a
    further subfolder within: midi_dir[/{ts.label}]/{group}/

    Returns the output path on success, None if skipped.
    """
    ts = time_sig or _DEFAULT_TIME_SIG

    base_dir = midi_dir if not with_count_in else midi_dir / ts.label
    subdir = _variant_subdir(cue, cue_list)
    clip_dir = base_dir / subdir if subdir else base_dir

    filename = _clip_filename(cue)
    output_path = clip_dir / filename

    if output_path.exists() and not force:
        log.debug("Skipping (already exists): %s", output_path.relative_to(midi_dir))
        return None

    mid = _build_clip(cue, cue_list, with_count_in, schema_version=cue_list.schema_version, time_sig=ts)
    clip_dir.mkdir(parents=True, exist_ok=True)
    mid.save(str(output_path))
    log.info("Generated: %s", output_path.relative_to(midi_dir))
    return output_path


def generate_count_in_clip(
    midi_dir: Path,
    time_sig: TimeSignature,
    force: bool = False,
) -> Path | None:
    """Generate a standalone count-in clip for the given time signature.

    Contains only the count-in beats (2 through N) with no section cue on
    beat 1.  Written to midi_dir/{ts.label}/Count In.mid.

    Returns the output path on success, None if skipped.
    """
    clip_dir = midi_dir / time_sig.label
    output_path = clip_dir / "_Count In.mid"

    if output_path.exists() and not force:
        log.debug("Skipping (already exists): %s", output_path.relative_to(midi_dir))
        return None

    beat = time_sig.beat_ticks
    numbers_note = _recv_to_midi(_NUMBERS_RECV_NOTE)
    count_beats = list(range(2, time_sig.beats_per_bar + 1))
    total_ticks = len(count_beats) * beat

    mid = mido.MidiFile(type=0, ticks_per_beat=_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    track.append(mido.MetaMessage("track_name", name="_Count In", time=0))
    for _ in range(2):  # duplicate time sig event matches Ableton export format
        track.append(mido.MetaMessage(
            "time_signature",
            numerator=time_sig.numerator,
            denominator=time_sig.denominator,
            clocks_per_click=36,
            notated_32nd_notes_per_beat=8,
            time=0,
        ))

    for i, beat_num in enumerate(count_beats):
        # All notes are back-to-back: note_on delta=0 follows immediately after
        # the previous note_off.  First note starts at tick 0.
        _add_note(track, numbers_note, velocity=beat_num, time=0, duration=beat)

    _add_end_of_track(track, total_ticks=total_ticks)

    clip_dir.mkdir(parents=True, exist_ok=True)
    mid.save(str(output_path))
    log.info("Generated: %s", output_path.relative_to(midi_dir))
    return output_path


def generate_all(
    cue_list: CueList,
    midi_dir: Path,
    force: bool = False,
    cue_filter: str | None = None,
    time_sigs: list[TimeSignature] | None = None,
) -> tuple[int, int, int]:
    """Generate MIDI clips for all expanded cues plus standalone count-in clips.

    Trigger clips (no count-in) → midi_dir root.
    Count-in section clips      → midi_dir/{ts.label}/ per time signature.
    Count-in only clips         → midi_dir/{ts.label}/Count In.mid per time sig.

    Returns (generated_count, skipped_count, failed_count).
    """
    ts_list = time_sigs or [_DEFAULT_TIME_SIG]

    expanded = cue_list.expand()
    if cue_filter:
        expanded = [c for c in expanded if c.id == cue_filter]

    generated = 0
    skipped = 0
    failed = 0

    for cue in expanded:
        for with_count_in in _clips_for_cue(cue):
            if not with_count_in:
                # Trigger clips: time-sig independent, generated once in root.
                try:
                    result = generate_clip(cue, cue_list, midi_dir, False, force)
                    if result:
                        generated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    log.error("Failed to generate MIDI for %s: %s", cue.id, e)
                    failed += 1
            else:
                # Count-in section clips: one per configured time signature.
                for ts in ts_list:
                    try:
                        result = generate_clip(cue, cue_list, midi_dir, True, force, time_sig=ts)
                        if result:
                            generated += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        log.error(
                            "Failed to generate MIDI for %s (%s): %s",
                            cue.id, ts.label, e,
                        )
                        failed += 1

    # Standalone count-in clips: one per time signature (no cue filter applied).
    if not cue_filter:
        for ts in ts_list:
            try:
                result = generate_count_in_clip(midi_dir, ts, force)
                if result:
                    generated += 1
                else:
                    skipped += 1
            except Exception as e:
                log.error("Failed to generate count-in clip (%s): %s", ts.label, e)
                failed += 1

    return generated, skipped, failed


def generate_review_clip(
    cue_list: CueList,
    midi_dir: Path,
    gap_beats: int = 4,
    force: bool = False,
) -> Path | None:
    """Generate a single long MIDI clip that fires every expanded cue in sequence.

    Each cue gets one beat on its pad note (at its correct velocity) followed by
    ``gap_beats - 1`` beats of silence.  Written to midi_dir root.

    Returns the output path, or None if skipped.
    """
    output_path = midi_dir / "REVIEW - All Cues.mid"
    if output_path.exists() and not force:
        log.debug("Skipping review clip (already exists)")
        return None

    expanded = cue_list.expand()

    mid = mido.MidiFile(type=0, ticks_per_beat=_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    track.append(mido.MetaMessage("track_name", name="REVIEW - All Cues", time=0))
    for _ in range(2):
        track.append(mido.MetaMessage(
            "time_signature", numerator=4, denominator=4,
            clocks_per_click=36, notated_32nd_notes_per_beat=8, time=0,
        ))
    track.append(mido.MetaMessage(
        "text",
        text=f"ableguides:review:schema_v{cue_list.schema_version}",
        time=0,
    ))

    beat = _TICKS_PER_BEAT
    gap_ticks = gap_beats * beat
    first = True

    for cue in expanded:
        note = _recv_to_midi(cue.receiving_note)

        if cue.base_id.startswith("number_"):
            velocity = int(cue.base_id.split("_")[1])
        elif cue.variant is not None:
            velocity = cue.variant + 1
        else:
            entry = cue_list.get_entry(cue.base_id)
            velocity = 1 if (entry and entry.variants > 0) else _DEFAULT_VELOCITY

        onset = 0 if first else gap_ticks - beat
        first = False
        _add_note(track, note, velocity, time=onset, duration=beat)

    _add_end_of_track(track, total_ticks=len(expanded) * gap_ticks)

    midi_dir.mkdir(parents=True, exist_ok=True)
    mid.save(str(output_path))
    log.info(
        "Generated review clip: %s (%d cues, %d beats each)",
        output_path.name, len(expanded), gap_beats,
    )
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _recv_to_midi(receiving_note: int) -> int:
    """Convert an Ableton drum rack ReceivingNote to a MIDI note number."""
    return 128 - receiving_note


def _clips_for_cue(cue: Cue) -> list[bool]:
    """Return list of (with_count_in) booleans for clips to generate."""
    if cue.count_in == "both":
        return [True, False]
    elif cue.count_in == "yes":
        return [True]
    else:
        return [False]


def _clip_filename(cue: Cue) -> str:
    """Generate the .mid filename for a cue.

    Trigger and count-in clips share the same name because they live in
    different parent directories (root vs ts-subfolder).
    """
    return f"{cue.text}.mid"


def _variant_subdir(cue: Cue, cue_list: CueList) -> str | None:
    """Return a grouping subfolder name for variant groups and numbers, or None.

    - Number cues (number_1 … number_12) → 'Numbers'
    - Cues whose CueEntry has variants > 0 → the base cue's display text
      (e.g. 'Verse', 'Chorus', 'Bridge')
    - Single-file cues → None (stay flat)
    """
    if cue.base_id.startswith("number_"):
        return "Numbers"
    entry = cue_list.get_entry(cue.base_id)
    if entry and entry.variants > 0:
        return entry.text  # e.g. "Verse", "Post Chorus", "Turn Around"
    return None


def _build_clip(
    cue: Cue,
    cue_list: CueList,
    with_count_in: bool,
    schema_version: int = 2,
    time_sig: TimeSignature | None = None,
) -> mido.MidiFile:
    """Build a Type 0 MIDI file for the given cue and time signature."""
    ts = time_sig or _DEFAULT_TIME_SIG
    beat = ts.beat_ticks

    mid = mido.MidiFile(type=0, ticks_per_beat=_TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    track.append(mido.MetaMessage("track_name", name=cue.text, time=0))
    for _ in range(2):  # duplicate matches Ableton-exported clip format
        track.append(mido.MetaMessage(
            "time_signature",
            numerator=ts.numerator,
            denominator=ts.denominator,
            clocks_per_click=36,
            notated_32nd_notes_per_beat=8,
            time=0,
        ))
    track.append(mido.MetaMessage(
        "text",
        text=f"ableguides:{cue.base_id}:note={cue.receiving_note}:schema_v{schema_version}",
        time=0,
    ))

    cue_note = _recv_to_midi(cue.receiving_note)

    if cue.base_id.startswith("number_"):
        cue_velocity = int(cue.base_id.split("_")[1])
    elif cue.variant is not None:
        cue_velocity = cue.variant + 1
    else:
        entry = cue_list.get_entry(cue.base_id)
        cue_velocity = 1 if (entry and entry.variants > 0) else _DEFAULT_VELOCITY

    if with_count_in and cue.variant is not None:
        # Variant + count-in: bar + 1 beat
        # Beat 1: section cue  |  Beat 2: silent  |  Beats 3…N+1: count-in
        _add_note(track, cue_note, cue_velocity, time=0, duration=beat)
        _add_count_in(track, start_after=beat * 2, time_sig=ts)
        _add_end_of_track(track, total_ticks=(ts.beats_per_bar + 1) * beat)

    elif with_count_in:
        # Non-variant + count-in: exactly one bar
        # Beat 1: section cue  |  Beats 2…N: count-in
        _add_note(track, cue_note, cue_velocity, time=0, duration=beat)
        _add_count_in(track, start_after=beat, time_sig=ts)
        _add_end_of_track(track, total_ticks=ts.bar_ticks)

    else:
        # Trigger only: 1 beat-unit
        _add_note(track, cue_note, cue_velocity, time=0, duration=beat)
        _add_end_of_track(track, total_ticks=beat)

    return mid


def _add_note(
    track: mido.MidiTrack,
    note: int,
    velocity: int,
    time: int = 0,
    duration: int = _TICKS_PER_BEAT,
) -> None:
    """Append a note_on / note_off pair to the track."""
    track.append(mido.Message("note_on", note=note, velocity=velocity, time=time))
    track.append(mido.Message("note_off", note=note, velocity=64, time=duration))


def _add_count_in(
    track: mido.MidiTrack,
    start_after: int,
    time_sig: TimeSignature | None = None,
) -> None:
    """Append the count-in beats (2 … N) using the Numbers pad.

    Beat numbers map directly to velocities, selecting the correct spoken
    number sample in the velocity-mapped Numbers drum pad.

    Args:
        start_after: Ticks from the cue note's onset to the first count-in beat.
        time_sig:    Time signature driving count-in length and beat duration.
    """
    ts = time_sig or _DEFAULT_TIME_SIG
    numbers_note = _recv_to_midi(_NUMBERS_RECV_NOTE)
    beat = ts.beat_ticks

    for i, beat_num in enumerate(range(2, ts.beats_per_bar + 1)):
        # First note delta: start_after minus the beat already consumed by the
        # previous note_off.  Subsequent notes follow immediately (delta=0).
        delta = (start_after - beat) if i == 0 else 0
        _add_note(track, numbers_note, velocity=beat_num, time=delta, duration=beat)


def _add_end_of_track(track: mido.MidiTrack, total_ticks: int) -> None:
    """Append end_of_track after padding to the correct total tick length."""
    elapsed = sum(msg.time for msg in track)
    remaining = max(0, total_ticks - elapsed)
    track.append(mido.MetaMessage("end_of_track", time=remaining))
