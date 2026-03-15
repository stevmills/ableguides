"""Configuration loading for ableguides.

Loads settings from ableguides.toml (TOML, Python 3.11+ tomllib) and the
ElevenLabs API key from a .env file (python-dotenv).

Precedence: CLI flag > ableguides.toml > built-in default.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ableguides.models import ConfigError, Voice

_DEFAULT_CONFIG_FILENAME = "ableguides.toml"
_DEFAULT_CUES_FILENAME = "cues.json"
_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def load_dotenv(env_file: Path | None = None) -> None:
    """Load a .env file into os.environ using python-dotenv.

    Searches for .env in the current directory and upward if env_file is None.
    Silently does nothing if the file does not exist.
    """
    try:
        from dotenv import load_dotenv as _load  # type: ignore[import]

        if env_file:
            _load(env_file, override=False)
        else:
            # Walk up from cwd looking for .env
            directory = Path.cwd().resolve()
            for candidate in [directory, *directory.parents]:
                dotenv_path = candidate / ".env"
                if dotenv_path.exists():
                    _load(dotenv_path, override=False)
                    break
    except ImportError:
        pass  # python-dotenv not installed; rely on env being set externally


def elevenlabs_api_key() -> str | None:
    """Return the ElevenLabs API key from the environment."""
    return os.environ.get("ELEVENLABS_API_KEY")


def bundled_guide_template_path() -> Path:
    """Return the path to the bundled single-voice guide rack template."""
    return _TEMPLATES_DIR / "guide-template.adg"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PathsConfig:
    output_dir: str = ""
    cues_file: str = _DEFAULT_CUES_FILENAME
    midi_dir: str = ""


@dataclass
class TTSSettings:
    """ElevenLabs voice_settings payload.

    Tuned for uniform, declarative cue pronunciation by default:
      - stability 0.9  → minimal take-to-take variation
      - style 0.0      → no style exaggeration (critical for consistency)
      - similarity_boost 0.75 → preserves voice character
      - use_speaker_boost False → not needed for batch generation
    """

    stability: float = 0.9
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = False
    # XOR'd with every per-cue seed before sending to the API.  Bump this when
    # a specific seed causes language drift or other bad output across all cues.
    seed_salt: int = 0

    def as_dict(self) -> dict:
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "use_speaker_boost": self.use_speaker_boost,
        }


@dataclass
class ElevenLabsConfig:
    voices: dict[str, str] = field(default_factory=dict)
    tts: TTSSettings = field(default_factory=TTSSettings)

    def as_voice_list(self) -> list[Voice]:
        """Return configured voices as a list of Voice objects."""
        return [Voice(name=name, voice_id=vid) for name, vid in self.voices.items()]


@dataclass
class MidiSettings:
    """Settings for MIDI clip generation."""

    # Time signatures to generate count-in clips for.
    # 4/4 clips go in midi_dir root; others in subdirectories (e.g. midi_dir/3-4/).
    # No-count clips are always generated once in midi_dir root, time-sig independent.
    time_signatures: list[str] = field(default_factory=lambda: ["4/4"])


@dataclass
class RackConfig:
    presets_dir: str = ""
    guide_pack_windows_root: str = ""
    template: str = ""


@dataclass
class AblGuidesConfig:
    """Full runtime configuration.

    All fields have defaults so the tool works with zero config.
    """

    paths: PathsConfig = field(default_factory=PathsConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)
    rack: RackConfig = field(default_factory=RackConfig)
    midi: MidiSettings = field(default_factory=MidiSettings)

    @classmethod
    def load(cls, config_path: Path) -> "AblGuidesConfig":
        """Load configuration from a TOML file.

        Raises ConfigError if the file exists but cannot be parsed.
        Returns defaults if the file does not exist.
        """
        if not config_path.exists():
            return cls()

        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Failed to parse {config_path}: {e}") from e
        except OSError as e:
            raise ConfigError(f"Cannot read {config_path}: {e}") from e

        paths_data = data.get("paths", {})
        el_data = data.get("elevenlabs", {})
        rack_data = data.get("rack", {})
        midi_data = data.get("midi", {})
        tts_data = el_data.get("tts", {})

        return cls(
            paths=PathsConfig(
                output_dir=paths_data.get("output_dir", ""),
                cues_file=paths_data.get("cues_file", _DEFAULT_CUES_FILENAME),
                midi_dir=paths_data.get("midi_dir", ""),
            ),
            elevenlabs=ElevenLabsConfig(
                voices=el_data.get("voices", {}),
                tts=TTSSettings(
                    stability=tts_data.get("stability", 0.9),
                    similarity_boost=tts_data.get("similarity_boost", 0.75),
                    style=tts_data.get("style", 0.0),
                    use_speaker_boost=tts_data.get("use_speaker_boost", False),
                    seed_salt=tts_data.get("seed_salt", 0),
                ),
            ),
            rack=RackConfig(
                presets_dir=rack_data.get("presets_dir", ""),
                guide_pack_windows_root=rack_data.get("guide_pack_windows_root", ""),
                template=rack_data.get("template", ""),
            ),
            midi=MidiSettings(
                time_signatures=midi_data.get("time_signatures", ["4/4"]),
            ),
        )

    @classmethod
    def find_and_load(cls, start_dir: Path | None = None) -> "AblGuidesConfig":
        """Search for ableguides.toml starting from start_dir (default: cwd).

        Walks up the directory tree until found or filesystem root reached.
        Returns defaults if no config file is found.
        """
        directory = (start_dir or Path.cwd()).resolve()
        for candidate in [directory, *directory.parents]:
            config_file = candidate / _DEFAULT_CONFIG_FILENAME
            if config_file.exists():
                return cls.load(config_file)
        return cls()
