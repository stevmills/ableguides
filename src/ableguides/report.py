"""Formatted CLI output for ableguides.

All report functions write to stdout. Logging goes through the logging module.
"""

from __future__ import annotations

from pathlib import Path

from ableguides.assembler import BuildResult
from ableguides.models import Cue, CueEntry, Voice
from ableguides.patcher import PatchResult
from ableguides.tts import GenerateResult

_COL = 24


def print_cues(entries: list[CueEntry], expanded: list[Cue]) -> None:
    """Print the cue list (base entries and total expanded count)."""
    print(f"\n{len(entries)} base cue groups, {len(expanded)} total expanded cues:\n")
    header = f"{'ID':<{_COL}} {'TEXT':<{_COL}} {'VARIANTS':>8}  {'TOTAL':>5}"
    print(header)
    print("-" * len(header))
    for entry in entries:
        total = 1 + entry.variants
        var_str = str(entry.variants) if entry.variants else "-"
        print(f"{entry.id:<{_COL}} {entry.text:<{_COL}} {var_str:>8}  {total:>5}")
    print()


def print_voices(voices: list[Voice]) -> None:
    """Print the configured voices."""
    if not voices:
        print("\nNo voices configured. Add [elevenlabs.voices] to ableguides.toml.\n")
        return
    print(f"\n{len(voices)} configured voice(s):\n")
    for v in voices:
        print(f"  {v.name:<{_COL}} {v.voice_id}")
    print()


def print_status(
    voices: list[Voice],
    cues: list[Cue],
    output_dir: Path,
) -> None:
    """Print a matrix of which (voice, cue) WAV files have been generated."""
    print(f"\nStatus: {output_dir}\n")

    if not voices:
        print("No voices configured.\n")
        return

    col_w = 16
    header = f"{'CUE':<{_COL}}" + "".join(f"{v.name[:col_w-1]:<{col_w}}" for v in voices)
    print(header)
    print("-" * len(header))

    for cue in cues:
        row = f"{cue.id:<{_COL}}"
        for voice in voices:
            wav = output_dir / voice.slug / f"{cue.id}.wav"
            mark = "✓" if wav.exists() else "✗"
            row += f"{mark:<{col_w}}"
        print(row)

    # Summary counts
    print()
    for voice in voices:
        voice_dir = output_dir / voice.slug
        existing = sum(1 for c in cues if (voice_dir / f"{c.id}.wav").exists())
        print(f"  {voice.name}: {existing}/{len(cues)} files generated")
    print()


def print_generate(results: list[GenerateResult], dry_run: bool = False) -> None:
    """Print a summary of the generate operation."""
    mode = " [dry-run]" if dry_run else ""
    succeeded = [r for r in results if r.success and not r.skipped]
    skipped = [r for r in results if r.skipped]
    failed = [r for r in results if not r.success]

    skip_label = "Would generate" if dry_run else "Skipped (already exist)"
    print(f"\nGenerate{mode} complete:")
    print(f"  Generated: {len(succeeded)}")
    print(f"  {skip_label}: {len(skipped)}")
    print(f"  Failed:    {len(failed)}")

    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r.voice_name}/{r.cue_id}: {r.error}")
    print()


def print_patch(results: list[PatchResult], dry_run: bool = False) -> None:
    """Print a summary of the patch operation."""
    mode = " [dry-run]" if dry_run else ""
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"\nPatch{mode} complete:")
    print(f"  Generated: {len(succeeded)}")
    print(f"  Failed:    {len(failed)}")

    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r.voice_name}: {r.error}")
    print()


def print_build(result: BuildResult, dry_run: bool = False) -> None:
    """Print a summary of the rack assembly operation."""
    mode = " [dry-run]" if dry_run else ""
    if result.success:
        print(f"\nBuild{mode} complete:")
        print(f"  Voices:  {result.voice_count}")
        print(f"  Output:  {result.output_path}")
    else:
        print(f"\nBuild{mode} failed: {result.error}")
    print()
