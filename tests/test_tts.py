"""Tests for ableguides.tts -- TTS generation with mocked HTTP."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ableguides.models import Cue, Voice
from ableguides.tts import generate_cue, generate_all


class TestGenerateCue:
    def test_skips_existing_without_force(self, tmp_path: Path):
        voice = Voice(name="test_voice", voice_id="v123")
        cue = Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")
        voice_dir = tmp_path / voice.slug
        voice_dir.mkdir()
        wav = voice_dir / "intro.wav"
        wav.write_bytes(b"existing")

        result = generate_cue(cue, voice, tmp_path, api_key="key", force=False)
        assert result.skipped is True
        assert result.success is True

    def test_force_regenerates(self, tmp_path: Path):
        voice = Voice(name="test_voice", voice_id="v123")
        cue = Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")
        voice_dir = tmp_path / voice.slug
        voice_dir.mkdir()
        wav = voice_dir / "intro.wav"
        wav.write_bytes(b"existing")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"new-audio-data"

        with patch("ableguides.tts.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_response
            result = generate_cue(cue, voice, tmp_path, api_key="key", force=True)

        assert result.success is True
        assert result.skipped is False
        assert wav.read_bytes().startswith(b"RIFF")

    def test_dry_run_skips_api(self, tmp_path: Path):
        voice = Voice(name="test_voice", voice_id="v123")
        cue = Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")

        with patch("ableguides.tts.httpx.Client") as MockClient:
            result = generate_cue(cue, voice, tmp_path, api_key="key", dry_run=True)
            MockClient.assert_not_called()

        assert result.skipped is True

    def test_api_error_returns_failure(self, tmp_path: Path):
        voice = Voice(name="test_voice", voice_id="v123")
        cue = Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"detail": {"message": "Unauthorized"}}

        with patch("ableguides.tts.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_response
            result = generate_cue(cue, voice, tmp_path, api_key="bad_key")

        assert result.success is False
        assert result.error is not None

    def test_success_writes_file(self, tmp_path: Path):
        voice = Voice(name="test_voice", voice_id="v123")
        cue = Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"audio-content"

        with patch("ableguides.tts.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_response
            result = generate_cue(cue, voice, tmp_path, api_key="key")

        assert result.success is True
        wav_bytes = (tmp_path / voice.slug / "intro.wav").read_bytes()
        assert wav_bytes.startswith(b"RIFF")
        assert b"WAVE" in wav_bytes[:12]
        assert b"audio-content" in wav_bytes


class TestGenerateAll:
    def test_voice_filter(self, tmp_path: Path):
        voices = [
            Voice(name="voice_a", voice_id="va"),
            Voice(name="voice_b", voice_id="vb"),
        ]
        cues = [Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro")]

        results = generate_all(
            cues=cues,
            voices=voices,
            output_dir=tmp_path,
            api_key="key",
            dry_run=True,
            voice_filter="voice_a",
        )
        assert len(results) == 1
        assert results[0].voice_name == "voice_a"

    def test_cue_filter(self, tmp_path: Path):
        voices = [Voice(name="voice_a", voice_id="va")]
        cues = [
            Cue(id="intro", text="Intro", spoken="Intro.", pad_name="Intro", base_id="intro"),
            Cue(id="outro", text="Outro", spoken="Outro.", pad_name="Outro", base_id="outro"),
        ]

        results = generate_all(
            cues=cues,
            voices=voices,
            output_dir=tmp_path,
            api_key="key",
            dry_run=True,
            cue_filter="intro",
        )
        assert len(results) == 1
        assert results[0].cue_id == "intro"
