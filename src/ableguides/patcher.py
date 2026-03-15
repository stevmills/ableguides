"""Ableton .adg preset patcher for ableguides.

Reads the voice_chain_template.xml,
replaces the sample <Path> values for each cue slot with paths from the
generated voice output directory, and produces a new single-voice .adg preset.

Patch strategy:
  1. Walk lines tracking state (inside SampleRef, current filename).
  2. For each <Path Value="...mp3"> inside a SampleRef:
     a. Extract the original filename stem.
     b. Look up the matching cue ID via STEM_TO_CUE_MAP.
     c. Replace with the Windows absolute path to the new .wav.
  3. Clear RelativePath, set RelativePathType to 0, zero out OriginalFileSize/Crc.
  4. Replace outer chain <Name Value="Voice 1"> with the new voice name.
  5. Wrap in rack_header.xml + rack_footer.xml to form the final .adg.

# TODO(future): When M4L can set SampleRef paths via Live API, replace this
#               file-based approach with direct API calls.
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ableguides.audio import analyze_wav_onset
from ableguides.models import CueEntry, CueList, Voice
from ableguides.paths import windows_path_to_posix

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

# Mapping from original filename stem (with numeric suffix stripped) to our cue ID.
# Stems that map 1:1 (same name) are NOT listed here; the fallback is identity.
_STEM_TO_CUE_ID: dict[str, str] = {
    # Numbers 1-12 (filename stem is just the digit string)
    "1": "number_1",
    "2": "number_2",
    "3": "number_3",
    "4": "number_4",
    "5": "number_5",
    "6": "number_6",
    "7": "number_7",
    "8": "number_8",
    "9": "number_9",
    "10": "number_10",
    "11": "number_11",
    "12": "number_12",
    # Key cues (original used "key_change_*")
    "key_change_down": "key_down",
    "key_change_up": "key_up",
    # Post/Pre-Chorus (original used hyphens instead of underscores)
    "post-chorus": "post_chorus",
    **{f"post-chorus_{n}": f"post_chorus_{n}" for n in range(1, 13)},
    "pre-chorus": "pre_chorus",
    **{f"pre-chorus_{n}": f"pre_chorus_{n}" for n in range(1, 7)},
    # breakdown spelled differently
    "breakdown": "breakdown",
}

_PLACEHOLDER_PADS: set[str] = set()

# Original template chain name (Voice 1)
_TEMPLATE_CHAIN_NAME = "Voice 1"

# Base cue IDs that already have drum pads in voice_chain_template.xml.
# Any CueEntry.id NOT in this set will get a dynamically generated pad appended.
_KNOWN_TEMPLATE_BASE_IDS: frozenset[str] = frozenset({
    # Numbers 1-12 share one "Numbers" pad (velocity-mapped chains inside)
    "number_1", "number_2", "number_3", "number_4", "number_5", "number_6",
    "number_7", "number_8", "number_9", "number_10", "number_11", "number_12",
    # Variant groups (each has its own pad with velocity-mapped InstrumentBranchPresets)
    "verse", "chorus", "bridge", "tag", "refrain", "post_chorus", "pre_chorus",
    "instrumental", "interlude", "turnaround",
    # Single-cue pads
    "acapella", "acoustic", "ad_lib", "all_in", "bass", "big_ending",
    "breakdown", "break", "bring_it_down", "build_it_up", "build",
    "double_time", "down_bridge", "down_chorus", "drums_in", "drums_out", "drums",
    "end", "ending", "full_band", "half_time", "hits", "hold", "intro",
    "key_down", "key_up", "keys", "kick_it_in", "last_time", "outro",
    "slowly_build", "softly", "solo", "swell", "trashcan", "vamp",
    "worship_leader",
    # Extended single-cue pads added to template (IDs 48–72)
    "coda", "again", "one_more_time", "segue", "button", "stop",
    "lift", "drop",
    "choir", "horns", "pad", "strings",
    "channel", "modulation", "pre_bridge", "feel", "shout_chorus", "take_it_home",
    "altar", "ministry", "prayer", "read", "response", "scripture", "wait",
})

# New pads start at this DrumBranchPreset Id (template uses 0-48).
_NEW_PAD_START_ID = 49

# New pad ReceivingNotes count down from here (template uses 81-128).
# 80 is the first available note below the "Rack Template" pad's 81.
_NEW_PAD_START_NOTE = 80


@dataclass
class PatchResult:
    """Result of patching a single voice chain."""

    voice_name: str
    output_path: Path
    success: bool
    cues_patched: int = 0
    cues_skipped: int = 0
    error: str | None = None


def patch_voice(
    voice: Voice,
    output_dir: Path,
    guide_pack_windows_root: str,
    presets_dir: Path,
    cue_list: CueList | None = None,
    templates_dir: Path | None = None,
    dry_run: bool = False,
) -> PatchResult:
    """Generate a patched single-voice .adg preset.

    Args:
        voice:                   The Voice to patch in.
        output_dir:              Root directory where generated WAVs live.
        guide_pack_windows_root: Windows path to the GuidePacks root (e.g.
                                 ``C:\\Users\\me\\Music\\Ableton\\GuidePacks``).
        presets_dir:             Directory to write the output .adg into.
        cue_list:                Full CueList; new cues not in the template get
                                 dynamically generated pads appended.
        templates_dir:           Override template directory (default: repo templates/).
        dry_run:                 Log intent but do not write files.
    """
    tdir = templates_dir or _TEMPLATES_DIR
    output_path = presets_dir / f"{voice.slug}.adg"

    try:
        chain_xml = (tdir / "voice_chain_template.xml").read_text(encoding="utf-8")
        header_xml = (tdir / "rack_header.xml").read_text(encoding="utf-8")
        footer_xml = (tdir / "rack_footer.xml").read_text(encoding="utf-8")
    except OSError as e:
        return PatchResult(
            voice_name=voice.name, output_path=output_path, success=False, error=str(e)
        )

    patched, cues_patched, cues_skipped = _patch_chain(
        chain_xml, voice, output_dir, guide_pack_windows_root, cue_list, tdir
    )

    full_xml = header_xml + patched + footer_xml

    if dry_run:
        log.info(
            "[dry-run] Would write preset: %s (%d cues patched, %d skipped)",
            output_path.name, cues_patched, cues_skipped,
        )
        return PatchResult(
            voice_name=voice.name,
            output_path=output_path,
            success=True,
            cues_patched=cues_patched,
            cues_skipped=cues_skipped,
        )

    try:
        presets_dir.mkdir(parents=True, exist_ok=True)
        _write_adg(full_xml, output_path)
        log.info(
            "Wrote preset: %s (%d cues patched, %d skipped)",
            output_path.name, cues_patched, cues_skipped,
        )
        return PatchResult(
            voice_name=voice.name,
            output_path=output_path,
            success=True,
            cues_patched=cues_patched,
            cues_skipped=cues_skipped,
        )
    except OSError as e:
        return PatchResult(
            voice_name=voice.name, output_path=output_path, success=False, error=str(e)
        )


def patch_all(
    voices: list[Voice],
    output_dir: Path,
    guide_pack_windows_root: str,
    presets_dir: Path,
    cue_list: CueList | None = None,
    templates_dir: Path | None = None,
    dry_run: bool = False,
) -> list[PatchResult]:
    """Patch all configured voices."""
    results = []
    for voice in voices:
        result = patch_voice(
            voice=voice,
            output_dir=output_dir,
            guide_pack_windows_root=guide_pack_windows_root,
            presets_dir=presets_dir,
            cue_list=cue_list,
            templates_dir=templates_dir,
            dry_run=dry_run,
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_pad_xml(
    entry: CueEntry,
    voice: Voice,
    windows_root: str,
    pad_id: int,
    receiving_note: int,
    templates_dir: Path,
) -> str:
    """Render a single DrumBranchPreset XML block for a cue with no template pad.

    Uses single_pad_template.xml with five string substitutions:
      __PAD_ID__        → sequential DrumBranchPreset id integer
      __PAD_NAME__      → human-readable pad label (CueEntry.text)
      __SAMPLE_NAME__   → MultiSamplePart name (CueEntry.id slug)
      __WAV_PATH__      → absolute Windows path to the generated WAV
      __RECEIVING_NOTE__→ MIDI note the drum pad listens on
    """
    tpl = (templates_dir / "single_pad_template.xml").read_text(encoding="utf-8")
    win_path = f"{windows_root}\\{voice.slug}\\{entry.id}.wav"
    return (
        tpl
        .replace("__PAD_ID__", str(pad_id))
        .replace("__PAD_NAME__", entry.text)
        .replace("__SAMPLE_NAME__", entry.id)
        .replace("__WAV_PATH__", win_path)
        .replace("__RECEIVING_NOTE__", str(receiving_note))
    )


def _update_sample_starts(xml: str) -> str:
    """Analyze each patched WAV and set its MultiSamplePart SampleStart to the
    actual audio onset, eliminating TTS pre-roll silence.

    Strategy:
      1. Collect positions of all simple ``<SampleStart Value="N" />`` tags.
      2. Collect positions of all ``<Path Value="*.wav">`` tags inside SampleRef.
      3. For each WAV path, pair it with the nearest SampleStart that appears
         *before* it in the XML (guaranteed to be in the same MultiSamplePart).
      4. Convert the Windows path to a WSL path, analyze the file, and
         substitute the onset sample index.
    Replacements are applied in reverse position order so earlier offsets
    remain valid throughout.
    """
    # Simple-form SampleStart tags (MultiSamplePart level, not LoopModulators block).
    ss_pattern = re.compile(r'<SampleStart Value="\d+" />')
    # WAV paths written into SampleRef blocks by the main patcher.
    path_pattern = re.compile(r'<Path Value="([^"]+\.wav)"')

    ss_positions: list[tuple[int, int, str]] = []  # (start, end, full_match)
    for m in ss_pattern.finditer(xml):
        ss_positions.append((m.start(), m.end(), m.group(0)))

    replacements: list[tuple[int, int, str]] = []  # (start, end, new_text)

    for pm in path_pattern.finditer(xml):
        win_path = pm.group(1)
        posix_path = windows_path_to_posix(win_path)
        if posix_path is None or not posix_path.exists():
            continue

        onset = analyze_wav_onset(posix_path)

        # Find the closest SampleStart that precedes this Path in the XML.
        paired: tuple[int, int, str] | None = None
        for ss_start, ss_end, ss_text in ss_positions:
            if ss_start < pm.start():
                paired = (ss_start, ss_end, ss_text)
            else:
                break  # ss_positions is in document order

        if paired is None:
            continue
        ss_start, ss_end, _ = paired
        new_tag = f'<SampleStart Value="{onset}" />'
        # Only queue a replacement if the value is actually changing.
        if xml[ss_start:ss_end] != new_tag:
            replacements.append((ss_start, ss_end, new_tag))

    # Apply in reverse order so earlier offsets stay valid.
    for start, end, new_text in sorted(replacements, key=lambda r: r[0], reverse=True):
        xml = xml[:start] + new_text + xml[end:]

    return xml


def _write_adg(xml: str, path: Path) -> None:
    """Gzip-compress and write an XML string as an Ableton .adg file."""
    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))


def _stem_from_filename(filename: str) -> str:
    """Extract the cue stem from an original sample filename.

    Strips trailing numeric suffixes like -12-1, -1-12-1, -1-1-1.

    Example:
        >>> _stem_from_filename("verse-1-12-1.mp3")
        'verse'
        >>> _stem_from_filename("bridge_3-12-1.mp3")
        'bridge_3'
        >>> _stem_from_filename("post-chorus_2-1-1-1.mp3")
        'post-chorus_2'
    """
    name = filename
    if name.endswith(".mp3"):
        name = name[:-4]
    elif name.endswith(".wav"):
        name = name[:-4]
    # Strip trailing -N-N or -N-N-N patterns (numeric suffix)
    name = re.sub(r"-\d+-\d+(-\d+)?$", "", name)
    return name


def _stem_to_cue_id(stem: str) -> str | None:
    """Map a filename stem to our cue ID. Returns None for unknown stems."""
    if stem in _STEM_TO_CUE_ID:
        return _STEM_TO_CUE_ID[stem]
    # Identity mapping: most stems are already the cue ID
    return stem


def _patch_chain(
    chain_xml: str,
    voice: Voice,
    output_dir: Path,
    guide_pack_windows_root: str,
    cue_list: CueList | None = None,
    templates_dir: Path | None = None,
) -> tuple[str, int, int]:
    """Patch all sample paths in a voice chain XML block.

    Returns (patched_xml, cues_patched_count, cues_skipped_count).

    If cue_list is provided, any CueEntry whose base id is not in
    _KNOWN_TEMPLATE_BASE_IDS gets a dynamically generated DrumBranchPreset
    block inserted before the "Rack Template" placeholder pad.

    Uses lambda replacements in re.sub to prevent Python from interpreting
    backslashes in Windows paths as regex escape sequences.
    """
    # Build pad_group → receiving_note mapping from cue_list.
    pad_note_map: dict[str, int] = {}
    if cue_list is not None:
        for entry in cue_list.entries:
            if entry.pad_group and entry.receiving_note:
                pad_note_map[entry.pad_group] = entry.receiving_note

    lines = chain_xml.splitlines()
    result: list[str] = []

    in_sample_ref = False
    in_placeholder_pad = False
    current_pad_name: str | None = None
    drum_branch_pad_name: str | None = None
    drum_branch_depth = 0
    cues_patched = 0
    cues_skipped = 0

    # Track the last seen filename stem when we enter a SampleRef
    current_stem: str | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track DrumBranchPreset nesting to correctly pair ReceivingNote
        # with the outermost drum pad name (not inner velocity chains).
        if "<DrumBranchPreset " in stripped:
            drum_branch_depth += 1
            if i + 1 < len(lines):
                m = re.search(r'<Name Value="([^"]+)"', lines[i + 1])
                if m:
                    name = m.group(1)
                    current_pad_name = name
                    in_placeholder_pad = name in _PLACEHOLDER_PADS
                    if drum_branch_depth == 1:
                        drum_branch_pad_name = name
        elif "</DrumBranchPreset>" in stripped:
            if drum_branch_depth == 1:
                drum_branch_pad_name = None
            drum_branch_depth = max(0, drum_branch_depth - 1)
        elif "<InstrumentBranchPreset " in stripped:
            if i + 1 < len(lines):
                m = re.search(r'<Name Value="([^"]+)"', lines[i + 1])
                if m:
                    current_pad_name = m.group(1)
                    in_placeholder_pad = current_pad_name in _PLACEHOLDER_PADS

        # Patch ReceivingNote for the outermost DrumBranchPreset pad.
        if (
            drum_branch_depth == 1
            and "<ReceivingNote Value=" in stripped
            and drum_branch_pad_name
            and drum_branch_pad_name in pad_note_map
        ):
            new_note = pad_note_map[drum_branch_pad_name]
            line = re.sub(
                r'<ReceivingNote Value="[^"]*"',
                lambda _: f'<ReceivingNote Value="{new_note}"',
                line,
            )

        # Track SampleRef scope
        if "<SampleRef>" in stripped:
            in_sample_ref = True
            current_stem = None
        elif "</SampleRef>" in stripped:
            in_sample_ref = False
            current_stem = None

        # Patch sample path lines inside SampleRef
        if in_sample_ref and not in_placeholder_pad:
            if "<Path Value=" in stripped and (
                ".mp3" in stripped or ".wav" in stripped
            ):
                # Extract filename and determine cue ID
                m = re.search(r'<Path Value="([^"]*)"', line)
                if m and m.group(1):
                    filename = m.group(1).split("/")[-1].split("\\")[-1]
                    stem = _stem_from_filename(filename)
                    cue_id = _stem_to_cue_id(stem)
                    current_stem = stem

                    if cue_id:
                        new_path = (
                            f"{guide_pack_windows_root}\\{voice.slug}\\{cue_id}.wav"
                        )
                        repl = f'<Path Value="{new_path}"'
                        line = re.sub(r'<Path Value="[^"]*"', lambda _: repl, line)
                        cues_patched += 1
                    else:
                        cues_skipped += 1
                        log.debug("No cue mapping for stem: %s", stem)

            elif "<RelativePath Value=" in stripped:
                line = re.sub(
                    r'<RelativePath Value="[^"]*"',
                    lambda _: '<RelativePath Value=""',
                    line,
                )

            elif "<RelativePathType Value=" in stripped:
                line = re.sub(
                    r'<RelativePathType Value="[^"]*"',
                    lambda _: '<RelativePathType Value="0"',
                    line,
                )

            elif "<OriginalFileSize Value=" in stripped:
                line = re.sub(
                    r'<OriginalFileSize Value="[^"]*"',
                    lambda _: '<OriginalFileSize Value="0"',
                    line,
                )

            elif "<OriginalCrc Value=" in stripped:
                line = re.sub(
                    r'<OriginalCrc Value="[^"]*"',
                    lambda _: '<OriginalCrc Value="0"',
                    line,
                )

        result.append(line)
        i += 1

    patched = "\n".join(result)

    # Replace outer chain name "Voice 1" -> voice display name
    patched = patched.replace(
        f'<Name Value="{_TEMPLATE_CHAIN_NAME}" />',
        f'<Name Value="{voice.name}" />',
        1,  # only the first occurrence (the outer chain name)
    )

    # Append dynamically generated pads for cues not in the fixed template.
    if cue_list is not None:
        tdir = templates_dir or _TEMPLATES_DIR
        new_entries = [
            e for e in cue_list.entries
            if e.id not in _KNOWN_TEMPLATE_BASE_IDS
        ]
        if new_entries:
            new_pad_blocks: list[str] = []
            pad_id = _NEW_PAD_START_ID
            for entry in new_entries:
                note = entry.receiving_note or (_NEW_PAD_START_NOTE - (pad_id - _NEW_PAD_START_ID))
                pad_xml = _generate_pad_xml(
                    entry, voice, guide_pack_windows_root, pad_id, note, tdir
                )
                new_pad_blocks.append(pad_xml)
                log.debug(
                    "Generated pad for '%s' (Id=%d, note=%d)", entry.id, pad_id, note
                )
                pad_id += 1

            # Insert new pads after the last existing DrumBranchPreset.
            last_close = patched.rfind("</DrumBranchPreset>")
            if last_close != -1:
                insert_at = last_close + len("</DrumBranchPreset>")
                insertion = "\n" + "\n".join(new_pad_blocks)
                patched = patched[:insert_at] + insertion + patched[insert_at:]
            else:
                patched = patched + "\n" + "\n".join(new_pad_blocks)
            cues_patched += len(new_entries)
            log.info(
                "Inserted %d new pad(s) for voice '%s'",
                len(new_entries), voice.name,
            )

    patched = _update_sample_starts(patched)

    return patched, cues_patched, cues_skipped
