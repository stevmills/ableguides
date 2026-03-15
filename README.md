# ableguides

A Python CLI that generates spoken guide cue audio via [ElevenLabs TTS](https://elevenlabs.io) and assembles them into ready-to-load Ableton Live Instrument Rack presets and MIDI clips.

Each ElevenLabs voice becomes one chain in a Drum Rack. Each cue (Verse, Chorus, Bridge 1–18, etc.) becomes a drum pad within that chain — loaded with the spoken audio for that section. MIDI clips drive the rack from session view: trigger clips call any cue instantly, count-in clips add a 2-3-4 count before the section lands.

Designed for WSL development with Ableton Live on Windows.

---

## Requirements

- Python 3.11+
- WSL (Ubuntu or similar) — if using a Linux/Windows split workflow
- Ableton Live (for loading the generated presets)
- An [ElevenLabs](https://elevenlabs.io) account and API key

---

## Quick Start

```bash
git clone <REPO_URL> && cd ableguides

# Create a virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Set up config and secrets
cp ableguides.toml.example ableguides.toml   # edit with your paths and voices
cp .env.example .env                         # add your ElevenLabs API key

# Generate audio, build the rack, generate MIDI clips
ableguides generate
ableguides build
ableguides midi generate
```

---

## Cue List

`cues.json` defines all cue groups which expand to 186 total spoken files per voice:

| Group | Variants | Notes |
|---|---|---|
| Verse, Chorus, Tag, Refrain, Post Chorus | 1–12 each | |
| Bridge | 1–18 | |
| Pre-Chorus, Instrumental, Interlude, Turn Around | 1–6 each | |
| Numbers | 1–12 | Velocity-mapped on one pad |
| Singles | ~50 | Intro, Outro, Break, Vamp, Build, Acapella, … |

```bash
ableguides cues        # print full list with notes and MIDI assignments
```

To add a cue or extend a variant count, edit `cues.json` and rerun `generate` + `build` + `midi generate`.

---

## Configuration

### `ableguides.toml` (copy from `ableguides.toml.example`)

```toml
[paths]
output_dir   = "/mnt/c/Users/YOU/Music/Ableton/GuidePacks"
midi_dir     = "/mnt/c/Users/YOU/Music/Ableton/GuidePacks/MIDI"

[elevenlabs.tts]
stability        = 1.0
similarity_boost = 0.9
style            = 0.0
use_speaker_boost = false
seed_salt        = 0      # bump to shift all seeds if a cue sounds wrong

[elevenlabs.voices]
guide_male   = "21m00Tcm4TlvDq8ikWAM"
guide_female = "EXAVITQu4vr4xnSDxMaL"

[midi]
time_signatures = ["4/4", "3/4", "6/8"]   # generates subfolders per time sig

[rack]
presets_dir             = "/mnt/c/Users/YOU/.../Instrument Rack/GuidePacks"
guide_pack_windows_root = "C:\\Users\\YOU\\Music\\Ableton\\GuidePacks"
```

### `.env` (copy from `.env.example`)

```
ELEVENLABS_API_KEY=sk_your_key_here
```

Get ElevenLabs voice IDs from the [Voice Library](https://elevenlabs.io/app/voice-library).

---

## CLI Reference

### `generate` — Synthesize WAV audio

```bash
ableguides generate                    # all voices, all cues
ableguides generate --voice guide_male # one voice
ableguides generate --cue verse        # one cue, all voices
ableguides --force generate            # overwrite existing files
ableguides --dry-run generate          # preview without API calls
```

### `build` — Assemble Ableton presets

```bash
ableguides build                       # all voices → AbleGuides.adg
ableguides build --voice guide_male    # single-voice preset only
```

### `midi generate` — Generate MIDI clips

```bash
ableguides midi generate               # all cues, all configured time signatures
ableguides --force midi generate       # regenerate all
ableguides midi generate --cue verse   # one cue only
```

### `midi review` — Review clip

```bash
ableguides midi review                 # one long clip firing every cue in sequence
ableguides --force midi review
```

### `als convert` — Update note assignments in an Ableton Live Set

Remaps MIDI note numbers in a `.als` file when `cues.json` receiving_note values change:

```bash
ableguides als convert session.als --old-cues cues_old.json --new-cues cues.json
ableguides als convert session.als --old-cues cues_old.json --output session_updated.als
```

### `cues` / `voices` / `status`

```bash
ableguides cues     # list all cues with MIDI note assignments
ableguides voices   # list configured voices
ableguides status   # show how many WAVs have been generated per voice
```

### Global flags

| Flag | Description |
|---|---|
| `--config PATH` | Path to `ableguides.toml` (auto-discovered from cwd) |
| `-v / --verbose` | Enable debug logging |
| `--dry-run` | Preview without writing files or calling the API |
| `--force` | Overwrite / regenerate existing files |

---

## Output Structure

### Audio files

```
output_dir/
  guide-male/
    verse.wav
    verse_1.wav … verse_12.wav
    chorus.wav … bridge_18.wav
    intro.wav, outro.wav, …
  guide-female/
    …
```

### Ableton presets

```
presets_dir/
  AbleGuides.adg      # all voices on Chain Selector (load this one)
  guide-male.adg      # single-voice preset
  guide-female.adg
```

### MIDI clips

```
midi_dir/
  Verse/              # trigger clips — call a section without a count-in
    Verse.mid
    Verse 1.mid … Verse 12.mid
  Chorus/, Bridge/, Tag/, …
  Intro.mid, Outro.mid, …   (single-file cues stay flat)
  REVIEW - All Cues.mid     (one long clip for auditioning each voice)

  4-4/                # 4/4 count-in clips
    _Count In.mid     # standalone 2-3-4 count, no section call
    Verse/
      Verse.mid       # "Verse" + 2-3-4 count-in
      Verse 1.mid … Verse 12.mid
    Chorus/, Bridge/, …
    Intro.mid, …

  3-4/                # 3/4 count-in clips (same structure)
    _Count In.mid     # 2-3 count
    …

  6-8/                # 6/8 count-in clips
    _Count In.mid     # 2-3-4-5-6 count (eighth-note beats)
    …
```

---

## TTS Details

- Model: `eleven_turbo_v2_5` (respects `language_code: "en"` — avoids multilingual confusion on short cue words like "Tag")
- Seeds: deterministic per cue ID (MD5 hash), XOR'd with `seed_salt` — same config always produces the same audio
- Pronunciation overrides: use the `spoken` field in `cues.json` (e.g. `"spoken": "seg-way."` for Segue)

---

## Bundled Templates

The `templates/` directory contains XML fragments extracted from an Ableton Live 11 drum rack:

| File | Purpose |
|---|---|
| `rack_header.xml` | Outer Instrument Rack XML prefix |
| `rack_footer.xml` | Outer Instrument Rack XML suffix |
| `voice_chain_template.xml` | Full `InstrumentBranchPreset` (DrumRack with all pads) |
| `single_pad_template.xml` | Template for dynamically generated extra pads |

The patcher replaces sample `<Path>` values in the chain template for each voice and adjusts clip start points based on WAV onset analysis. The assembler wraps all chains into the header/footer to produce the final `.adg`.

---

## WSL / Windows Path Notes

- The CLI runs entirely in WSL (Linux).
- When `output_dir` is under `/mnt/<drive>/`, the Windows path is derived automatically and embedded in the `.adg` XML so Ableton can locate the WAV files.
- If Ableton is open and has files locked, `generate --force` will fail with `Permission denied`. Close Ableton first.

---

## Project Structure

```
ableguides/
  pyproject.toml
  ableguides.toml.example    # copy to ableguides.toml and edit
  .env.example               # copy to .env and add ElevenLabs key
  cues.json                  # master cue list
  README.md
  templates/
    rack_header.xml
    rack_footer.xml
    voice_chain_template.xml
    single_pad_template.xml
  src/ableguides/
    cli.py          # argparse CLI wiring
    models.py       # Cue, Voice, CueList dataclasses
    config.py       # TOML + .env config loading
    paths.py        # WSL/Windows path conversion
    tts.py          # ElevenLabs API client (deterministic seeds, language lock)
    patcher.py      # per-voice .adg patching + onset analysis
    assembler.py    # multi-voice master rack assembly
    midi.py         # MIDI clip generation (multi-time-sig, count-in, review)
    als.py          # .als file note remapper
    audio.py        # WAV onset detection
    report.py       # formatted CLI output
  tests/
```

---

## Running Tests

```bash
pip install pytest
pytest
```

---

## License

MIT
