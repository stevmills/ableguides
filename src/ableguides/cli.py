"""CLI entry point for ableguides.

Subcommands:
  cues      -- List all guide cues (expanded with variants).
  voices    -- List configured ElevenLabs voices.
  generate  -- Generate WAV audio for each (voice, cue) pair via ElevenLabs TTS.
  status    -- Show which (voice, cue) files have been generated.
  build     -- Assemble Ableton .adg rack preset(s) from generated WAVs.
  als       -- Convert Ableton .als sessions when cues.json note assignments change.

Global flags:
  --config PATH    Path to ableguides.toml (default: auto-discovered from cwd)
  -v / --verbose   Enable debug logging
  --dry-run        Log operations without writing files
  --force          Overwrite / regenerate existing files
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ableguides import __version__
from ableguides.assembler import assemble_rack
from ableguides.config import (
    AblGuidesConfig,
    bundled_guide_template_path,
    load_dotenv,
    elevenlabs_api_key,
)
from ableguides.models import load_cues
from ableguides.patcher import patch_all
from ableguides.paths import to_windows_path, resolve_output_dir, resolve_path
from ableguides.report import (
    print_build,
    print_cues,
    print_generate,
    print_patch,
    print_status,
    print_voices,
)
from ableguides.als import convert_als
from ableguides.midi import generate_all as midi_generate_all, generate_review_clip, parse_time_sigs
from ableguides.tts import generate_all

log = logging.getLogger("ableguides")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ableguides",
        description="Generate spoken guide cue WAVs and assemble Ableton Drum Rack presets.",
    )
    parser.add_argument("--version", action="version", version=f"ableguides {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to ableguides.toml (default: auto-discovered from cwd upward)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log operations without writing files or calling the API",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite / regenerate existing files",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # cues
    sub.add_parser("cues", help="List all guide cues (expanded with variants)")

    # voices
    sub.add_parser("voices", help="List configured ElevenLabs voices")

    # generate
    gen = sub.add_parser(
        "generate",
        help="Generate WAV audio via ElevenLabs TTS",
    )
    gen.add_argument(
        "--voice",
        metavar="NAME",
        help="Generate only for this voice name (default: all)",
    )
    gen.add_argument(
        "--cue",
        metavar="ID",
        help="Generate only for this cue ID (default: all)",
    )
    gen.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Root directory for generated WAVs (overrides config)",
    )

    # status
    st = sub.add_parser("status", help="Show generation status matrix")
    st.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Root directory for generated WAVs (overrides config)",
    )

    # build
    bld = sub.add_parser(
        "build",
        help="Assemble Ableton .adg rack preset(s) from generated WAVs",
    )
    bld.add_argument(
        "--voice",
        metavar="NAME",
        help="Build preset for a single voice only (default: assemble all into master rack)",
    )
    bld.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Root directory for generated WAVs (overrides config)",
    )
    bld.add_argument(
        "--presets-dir",
        metavar="PATH",
        help="Directory to write .adg preset(s) into (overrides config)",
    )
    bld.add_argument(
        "--template",
        metavar="PATH",
        help="Override path to voice_chain_template.xml parent directory",
    )
    bld.add_argument(
        "--output-name",
        metavar="FILENAME",
        default="GUIDE-MASTER.adg",
        help="Filename for the assembled master rack (default: GUIDE-MASTER.adg)",
    )

    # midi
    midi_parser = sub.add_parser("midi", help="Generate MIDI clips for guide cues")
    midi_sub = midi_parser.add_subparsers(dest="midi_command", metavar="ACTION")
    midi_sub.required = True

    midi_gen = midi_sub.add_parser("generate", help="Generate MIDI clip files")
    midi_gen.add_argument(
        "--cue",
        metavar="ID",
        help="Generate only for this cue ID (default: all)",
    )
    midi_gen.add_argument(
        "--midi-dir",
        metavar="PATH",
        help="Output directory for MIDI files (overrides config)",
    )

    midi_rev = midi_sub.add_parser(
        "review",
        help="Generate a single long MIDI clip with all cues in sequence (for auditioning voices)",
    )
    midi_rev.add_argument(
        "--gap-beats",
        metavar="N",
        type=int,
        default=4,
        help="Beats allocated per cue (note + silence). Default: 4",
    )
    midi_rev.add_argument(
        "--midi-dir",
        metavar="PATH",
        help="Output directory for MIDI files (overrides config)",
    )

    # als
    als_parser = sub.add_parser(
        "als",
        help="Convert Ableton .als sessions when cues.json note assignments change",
    )
    als_sub = als_parser.add_subparsers(dest="als_command", metavar="ACTION")
    als_sub.required = True

    als_conv = als_sub.add_parser(
        "convert",
        help=(
            "Remap MIDI note assignments in an .als file to match a new cues.json. "
            "Backs up the original in place."
        ),
    )
    als_conv.add_argument(
        "als_file",
        metavar="SESSION.als",
        help="Path to the Ableton Live Set to convert",
    )
    als_conv.add_argument(
        "--old-cues",
        metavar="PATH",
        required=True,
        help="Path to the old cues.json snapshot (before the note assignments changed)",
    )
    als_conv.add_argument(
        "--new-cues",
        metavar="PATH",
        help="Path to the new cues.json (default: cues_file from config)",
    )
    als_conv.add_argument(
        "--output",
        metavar="PATH",
        help="Write converted .als here instead of overwriting in place",
    )

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_cues(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    cue_list = load_cues(cues_path)
    expanded = cue_list.expand()
    print_cues(cue_list.entries, expanded)
    return 0


def _cmd_voices(args: argparse.Namespace, cfg: AblGuidesConfig) -> int:
    voices = cfg.elevenlabs.as_voice_list()
    print_voices(voices)
    return 0


def _cmd_generate(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    load_dotenv()
    api_key = elevenlabs_api_key()
    if not api_key and not args.dry_run:
        print(
            "Error: ELEVENLABS_API_KEY not set.\n"
            "Add it to a .env file or set the environment variable.",
            file=sys.stderr,
        )
        return 2

    voices = cfg.elevenlabs.as_voice_list()
    if not voices:
        print("Error: no voices configured. Add [elevenlabs.voices] to ableguides.toml.", file=sys.stderr)
        return 2

    raw_output = getattr(args, "output_dir", None) or cfg.paths.output_dir
    if not raw_output:
        print("Error: output_dir is not set. Configure it in ableguides.toml.", file=sys.stderr)
        return 2
    output_dir = resolve_output_dir(raw_output)

    cue_list = load_cues(cues_path)
    expanded = cue_list.expand()

    results = generate_all(
        cues=expanded,
        voices=voices,
        output_dir=output_dir,
        api_key=api_key or "",
        force=args.force,
        dry_run=args.dry_run,
        voice_filter=getattr(args, "voice", None),
        cue_filter=getattr(args, "cue", None),
        tts_settings=cfg.elevenlabs.tts,
    )

    print_generate(results, dry_run=args.dry_run)
    failed = [r for r in results if not r.success]
    return 1 if failed else 0


def _cmd_status(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    voices = cfg.elevenlabs.as_voice_list()
    raw_output = getattr(args, "output_dir", None) or cfg.paths.output_dir
    if not raw_output:
        print("Error: output_dir is not set. Configure it in ableguides.toml.", file=sys.stderr)
        return 2
    output_dir = resolve_output_dir(raw_output)

    cue_list = load_cues(cues_path)
    expanded = cue_list.expand()

    print_status(voices, expanded, output_dir)
    return 0


def _cmd_midi(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    raw_midi = getattr(args, "midi_dir", None) or cfg.paths.midi_dir
    if not raw_midi:
        print(
            "Error: midi_dir is not set.\n"
            "Set paths.midi_dir in ableguides.toml or pass --midi-dir.",
            file=sys.stderr,
        )
        return 2
    midi_dir = resolve_path(raw_midi)

    cue_list = load_cues(cues_path)

    if args.midi_command == "review":
        gap_beats = getattr(args, "gap_beats", 4)
        result = generate_review_clip(
            cue_list=cue_list,
            midi_dir=midi_dir,
            gap_beats=gap_beats,
            force=args.force,
        )
        if result:
            print(f"\nReview clip generated: {result}")
        else:
            print("\nReview clip already exists (use --force to regenerate).")
        print()
        return 0

    cue_filter = getattr(args, "cue", None)
    time_sigs = parse_time_sigs(cfg.midi.time_signatures)
    generated, skipped, failed = midi_generate_all(
        cue_list=cue_list,
        midi_dir=midi_dir,
        force=args.force,
        cue_filter=cue_filter,
        time_sigs=time_sigs,
    )

    print(f"\nMIDI generate complete:")
    print(f"  Generated: {generated}")
    if skipped:
        print(f"  Skipped (already exist): {skipped}")
    if failed:
        print(f"  Failed:    {failed}")
    print(f"  Output:    {midi_dir}")
    print()

    return 1 if failed else 0


def _cmd_build(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    voices = cfg.elevenlabs.as_voice_list()
    if not voices:
        print("Error: no voices configured. Add [elevenlabs.voices] to ableguides.toml.", file=sys.stderr)
        return 2

    raw_output = getattr(args, "output_dir", None) or cfg.paths.output_dir
    if not raw_output:
        print("Error: output_dir is not set. Configure it in ableguides.toml.", file=sys.stderr)
        return 2
    output_dir = resolve_output_dir(raw_output)

    # Determine Windows root for embedding in .adg XML
    win_root = cfg.rack.guide_pack_windows_root
    if not win_root:
        win_root = to_windows_path(output_dir) or ""
    if not win_root and not args.dry_run:
        print(
            "Error: could not determine Windows path for output_dir.\n"
            "Set rack.guide_pack_windows_root in ableguides.toml.",
            file=sys.stderr,
        )
        return 2

    # Presets dir
    raw_presets = getattr(args, "presets_dir", None) or cfg.rack.presets_dir
    if not raw_presets:
        print(
            "Error: presets_dir is not set.\n"
            "Set rack.presets_dir in ableguides.toml or pass --presets-dir.",
            file=sys.stderr,
        )
        return 2
    presets_dir = resolve_path(raw_presets)

    # Templates dir override
    templates_dir: Path | None = None
    if getattr(args, "template", None):
        templates_dir = resolve_path(args.template)

    cue_list = load_cues(cues_path)

    voice_filter = getattr(args, "voice", None)
    if voice_filter:
        # Build a single-voice preset via patcher
        target_voices = [v for v in voices if v.name == voice_filter]
        if not target_voices:
            print(f"Error: voice '{voice_filter}' not found in config.", file=sys.stderr)
            return 2
        results = patch_all(
            voices=target_voices,
            output_dir=output_dir,
            guide_pack_windows_root=win_root,
            presets_dir=presets_dir,
            cue_list=cue_list,
            templates_dir=templates_dir,
            dry_run=args.dry_run,
        )
        print_patch(results, dry_run=args.dry_run)
        failed = [r for r in results if not r.success]
        return 1 if failed else 0
    else:
        # Assemble the full master rack
        result = assemble_rack(
            voices=voices,
            output_dir=output_dir,
            guide_pack_windows_root=win_root,
            presets_dir=presets_dir,
            output_filename=args.output_name,
            cue_list=cue_list,
            templates_dir=templates_dir,
            dry_run=args.dry_run,
        )
        print_build(result, dry_run=args.dry_run)
        return 0 if result.success else 1


def _cmd_als(args: argparse.Namespace, cfg: AblGuidesConfig, cues_path: Path) -> int:
    als_path = resolve_path(args.als_file)
    if not als_path.exists():
        print(f"Error: .als file not found: {als_path}", file=sys.stderr)
        return 2

    old_cues_path = resolve_path(args.old_cues)
    if not old_cues_path.exists():
        print(f"Error: old cues.json not found: {old_cues_path}", file=sys.stderr)
        return 2

    new_cues_path = resolve_path(args.new_cues) if getattr(args, "new_cues", None) else cues_path

    old_cues = load_cues(old_cues_path)
    new_cues = load_cues(new_cues_path)

    output_path = resolve_path(args.output) if getattr(args, "output", None) else None

    try:
        files_written, keys_remapped = convert_als(
            als_path=als_path,
            old_cues=old_cues,
            new_cues=new_cues,
            output_path=output_path,
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    dest = output_path or als_path
    if keys_remapped == 0:
        print("\nNo changes needed — note assignments are identical.")
    elif args.dry_run:
        print(f"\n[dry-run] Would remap {keys_remapped} MidiKey occurrence(s) in {als_path.name}.")
    else:
        print(f"\nConverted: {dest.name}")
        print(f"  MidiKey occurrences remapped: {keys_remapped}")
    print()
    return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Load config
    if args.config:
        cfg = AblGuidesConfig.load(Path(args.config))
    else:
        cfg = AblGuidesConfig.find_and_load()

    # Resolve cues.json path
    cues_file = cfg.paths.cues_file or "cues.json"
    cues_path = resolve_path(cues_file)

    # Dispatch
    try:
        if args.command == "cues":
            sys.exit(_cmd_cues(args, cfg, cues_path))
        elif args.command == "voices":
            sys.exit(_cmd_voices(args, cfg))
        elif args.command == "generate":
            sys.exit(_cmd_generate(args, cfg, cues_path))
        elif args.command == "status":
            sys.exit(_cmd_status(args, cfg, cues_path))
        elif args.command == "build":
            sys.exit(_cmd_build(args, cfg, cues_path))
        elif args.command == "midi":
            sys.exit(_cmd_midi(args, cfg, cues_path))
        elif args.command == "als":
            sys.exit(_cmd_als(args, cfg, cues_path))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        if args.verbose:
            raise
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
