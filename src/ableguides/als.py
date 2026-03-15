"""Ableton Live Set (.als) converter for ableguides.

Converts a Live Set to update MIDI note assignments when cues.json
receiving_note values change between versions.

How it works
------------
.als files are gzip-compressed XML.  MIDI clip note data is stored inline as
<KeyTrack MidiKey="N"> elements, where N = 128 - receiving_note (the same
formula used by ableguides when building MIDI clips).

Converter strategy:
  1. Build old_midi_key → new_midi_key mapping by comparing old and new
     cues.json, matching cue entries by id.
  2. Gunzip the .als, read as UTF-8 text.
  3. Replace every MidiKey="old" with MidiKey="new" via regex.
  4. Re-gzip and write output (backing up the original when writing in place).

The clip *name* (Name="Verse") is the human-readable identifier that survives
the .als import — ableguides does not need to parse it for conversion since the
remapping is applied globally to all key tracks in the file.  Any clip that
uses a remapped note will be updated automatically.
"""

from __future__ import annotations

import gzip
import logging
import re
import shutil
from pathlib import Path

from ableguides.models import CueList

log = logging.getLogger(__name__)

# Matches MidiKey="N" anywhere in the XML — scoped enough since MidiKey is
# specific to <KeyTrack> elements in the Ableton .als schema.
_MIDI_KEY_RE = re.compile(r'MidiKey="(\d+)"')


def build_note_remap(old_cues: CueList, new_cues: CueList) -> dict[int, int]:
    """Build a MIDI note remapping dict from old → new receiving_note assignments.

    Matches entries by cue id.  Only includes pairs where the receiving_note
    actually changed.  Keys and values are MidiKey numbers (128 - receiving_note).

    Args:
        old_cues: CueList loaded from the old cues.json snapshot.
        new_cues: CueList loaded from the current (new) cues.json.

    Returns a dict mapping old MidiKey → new MidiKey.
    """
    old_notes = {e.id: e.receiving_note for e in old_cues.entries}
    new_notes = {e.id: e.receiving_note for e in new_cues.entries}

    remap: dict[int, int] = {}
    for cue_id, old_recv in old_notes.items():
        new_recv = new_notes.get(cue_id)
        if new_recv is None:
            log.warning("Cue '%s' exists in old cues.json but not in new — skipping.", cue_id)
            continue
        if old_recv != new_recv:
            old_key = 128 - old_recv
            new_key = 128 - new_recv
            remap[old_key] = new_key
            log.debug(
                "  %s: receiving_note %d→%d  (MidiKey %d→%d)",
                cue_id, old_recv, new_recv, old_key, new_key,
            )

    return remap


def convert_als(
    als_path: Path,
    old_cues: CueList,
    new_cues: CueList,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Convert an .als file to use updated receiving_note assignments.

    Args:
        als_path:    Path to the source .als file.
        old_cues:    CueList from the old cues.json snapshot.
        new_cues:    CueList from the current cues.json.
        output_path: Where to write the result.  Defaults to als_path (in-place,
                     with an automatic .als.bak backup created first).
        dry_run:     Log planned changes without writing any files.

    Returns:
        (files_written, keys_remapped) — files_written is 0 for dry-run or
        no-op, 1 if the file was written.

    Raises:
        RuntimeError if the .als cannot be read or written.
    """
    remap = build_note_remap(old_cues, new_cues)

    if not remap:
        log.info("No receiving_note changes between the two cues.json files — nothing to do.")
        return 0, 0

    log.info(
        "%d note assignment(s) changed — scanning %s …",
        len(remap), als_path.name,
    )
    for old_key, new_key in sorted(remap.items()):
        log.info("  MidiKey %d → %d", old_key, new_key)

    if output_path is None:
        output_path = als_path

    # --- Read and decompress ---
    try:
        with gzip.open(als_path, "rb") as f:
            xml_bytes = f.read()
    except Exception as exc:
        raise RuntimeError(f"Cannot read {als_path}: {exc}") from exc

    xml_text = xml_bytes.decode("utf-8")

    # --- Apply remapping ---
    keys_remapped = 0

    def _replace(m: re.Match) -> str:
        nonlocal keys_remapped
        key = int(m.group(1))
        if key in remap:
            keys_remapped += 1
            return f'MidiKey="{remap[key]}"'
        return m.group(0)

    new_xml = _MIDI_KEY_RE.sub(_replace, xml_text)

    if keys_remapped == 0:
        log.info("No MidiKey values in %s matched the remap — file unchanged.", als_path.name)
        return 0, 0

    log.info("%d MidiKey occurrence(s) would be updated.", keys_remapped)

    if dry_run:
        log.info("[dry-run] Skipping write.")
        return 0, keys_remapped

    # --- Back up original when writing in place ---
    if output_path.resolve() == als_path.resolve():
        backup = als_path.with_suffix(".als.bak")
        shutil.copy2(als_path, backup)
        log.info("Backed up original → %s", backup.name)

    # --- Re-compress and write ---
    try:
        with gzip.open(output_path, "wb") as f:
            f.write(new_xml.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot write {output_path}: {exc}") from exc

    log.info("Written: %s", output_path.name)
    return 1, keys_remapped
