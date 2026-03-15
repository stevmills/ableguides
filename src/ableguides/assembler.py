"""Assemble a multi-voice master Instrument Rack .adg from per-voice chain templates.

For each configured voice:
  1. Load the voice_chain_template.xml.
  2. Patch its sample paths via patcher._patch_chain().
  3. Assign a sequential Id and BranchSelectorRange.

Wrap all chains in rack_header.xml + rack_footer.xml and gzip-compress the result.

The assembled rack has one chain per voice, selectable via Ableton's Chain Selector.
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ableguides.models import BuildResult, CueList, Voice
from ableguides.patcher import _patch_chain

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

# The outer rack preset filename written to presets_dir.
_OUTPUT_FILENAME = "AbleGuides.adg"


def assemble_rack(
    voices: list[Voice],
    output_dir: Path,
    guide_pack_windows_root: str,
    presets_dir: Path,
    output_filename: str = _OUTPUT_FILENAME,
    cue_list: CueList | None = None,
    templates_dir: Path | None = None,
    dry_run: bool = False,
) -> BuildResult:
    """Assemble a master Instrument Rack containing one chain per voice.

    Args:
        voices:                  All configured voices (one chain each).
        output_dir:              Root directory where generated WAVs live.
        guide_pack_windows_root: Windows path to the GuidePacks root.
        presets_dir:             Directory to write the assembled .adg into.
        output_filename:         Filename for the assembled rack preset.
        templates_dir:           Override template directory.
        dry_run:                 Log intent but do not write files.
    """
    tdir = templates_dir or _TEMPLATES_DIR
    output_path = presets_dir / output_filename

    try:
        chain_template = (tdir / "voice_chain_template.xml").read_text(encoding="utf-8")
        header_xml = (tdir / "rack_header.xml").read_text(encoding="utf-8")
        footer_xml = (tdir / "rack_footer.xml").read_text(encoding="utf-8")
    except OSError as e:
        return BuildResult(
            output_path=output_path,
            voice_count=0,
            cue_count=0,
            success=False,
            error=str(e),
        )

    voice_max = len(voices) - 1
    header_xml = header_xml.replace("__VOICE_MAX__", str(voice_max))

    chain_blocks: list[str] = []

    for idx, voice in enumerate(voices):
        log.info("Assembling chain %d/%d: %s", idx + 1, len(voices), voice.name)

        patched_chain, _, _ = _patch_chain(
            chain_template, voice, output_dir, guide_pack_windows_root, cue_list, tdir
        )

        patched_chain = _set_chain_id(patched_chain, idx)
        patched_chain = _set_branch_selector_range(patched_chain, idx)

        chain_blocks.append(patched_chain)

    full_xml = header_xml + "\n".join(chain_blocks) + "\n" + footer_xml

    if dry_run:
        log.info(
            "[dry-run] Would write master rack: %s (%d voices)",
            output_path.name, len(voices),
        )
        return BuildResult(
            output_path=output_path,
            voice_count=len(voices),
            cue_count=0,
            success=True,
        )

    try:
        presets_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(output_path, "wb") as f:
            f.write(full_xml.encode("utf-8"))
        log.info("Wrote master rack: %s (%d voices)", output_path.name, len(voices))
        return BuildResult(
            output_path=output_path,
            voice_count=len(voices),
            cue_count=0,
            success=True,
        )
    except OSError as e:
        return BuildResult(
            output_path=output_path,
            voice_count=0,
            cue_count=0,
            success=False,
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _set_chain_id(chain_xml: str, index: int) -> str:
    """Replace the Id attribute of the outer InstrumentBranchPreset."""
    return re.sub(
        r'^(\s*<InstrumentBranchPreset )Id="\d+"',
        lambda m: f'{m.group(1)}Id="{index}"',
        chain_xml,
        count=1,
        flags=re.MULTILINE,
    )


def _set_branch_selector_range(chain_xml: str, index: int) -> str:
    """Set the outer chain's BranchSelectorRange to [index, index].

    The outer chain's BranchSelectorRange is the LAST one in the template —
    all inner nested racks (drum pads, velocity chains) have their own
    BranchSelectorRange blocks earlier in the XML.
    """
    pattern = (
        r'(<BranchSelectorRange>\s*\n'
        r'\s*<Min Value=)"[^"]*"(\s*/>\s*\n'
        r'\s*<Max Value=)"[^"]*"(\s*/>\s*\n'
        r'\s*<CrossfadeMin Value=)"[^"]*"(\s*/>\s*\n'
        r'\s*<CrossfadeMax Value=)"[^"]*"(\s*/>)'
    )

    replacement = rf'\g<1>"{index}"\2"{index}"\3"0"\4"0"\5'

    # Find all matches and replace only the last one (outer chain's range).
    matches = list(re.finditer(pattern, chain_xml, flags=re.DOTALL))
    if not matches:
        return chain_xml

    last = matches[-1]
    patched_block = re.sub(pattern, replacement, last.group(0), count=1, flags=re.DOTALL)
    return chain_xml[: last.start()] + patched_block + chain_xml[last.end() :]
