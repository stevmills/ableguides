"""Tests for ableguides.models."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ableguides.models import (
    Cue,
    CueEntry,
    CueList,
    Voice,
    load_cues,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        assert slugify("Guide Female") == "guide-female"

    def test_hyphens(self):
        assert slugify("Pre-Chorus") == "pre-chorus"

    def test_underscores_normalized(self):
        assert slugify("turn_around") == "turn-around"

    def test_numbers_preserved(self):
        assert slugify("Verse 3") == "verse-3"

    def test_strips_leading_trailing(self):
        assert slugify("  hello  ") == "hello"


class TestVoice:
    def test_slug(self):
        v = Voice(name="Guide Male", voice_id="abc")
        assert v.slug == "guide-male"

    def test_slug_with_underscore(self):
        v = Voice(name="guide_female", voice_id="abc")
        assert v.slug == "guide-female"


class TestCueEntry:
    def test_expand_no_variants(self):
        entry = CueEntry(id="intro", text="Intro", pad_group="Intro", variants=0)
        cues = entry.expand()
        assert len(cues) == 1
        assert cues[0].id == "intro"
        assert cues[0].text == "Intro"
        assert cues[0].variant is None

    def test_expand_with_variants(self):
        entry = CueEntry(id="verse", text="Verse", pad_group="Verse", variants=3)
        cues = entry.expand()
        assert len(cues) == 4  # base + 3 variants
        assert cues[0].id == "verse"
        assert cues[1].id == "verse_1"
        assert cues[1].text == "Verse 1"
        assert cues[1].variant == 1
        assert cues[3].id == "verse_3"
        assert cues[3].variant == 3

    def test_expand_ids_unique(self):
        entry = CueEntry(id="bridge", text="Bridge", pad_group="Bridges", variants=5)
        cues = entry.expand()
        ids = [c.id for c in cues]
        assert len(ids) == len(set(ids))


class TestCueList:
    def test_expand_total(self, sample_cue_list: CueList):
        expanded = sample_cue_list.expand()
        # verse: 3, chorus: 2, intro: 1 = 6 total
        assert len(expanded) == 6

    def test_expand_order(self, sample_cue_list: CueList):
        expanded = sample_cue_list.expand()
        assert expanded[0].id == "verse"
        assert expanded[1].id == "verse_1"
        assert expanded[2].id == "verse_2"
        assert expanded[3].id == "chorus"

    def test_get_entry_found(self, sample_cue_list: CueList):
        entry = sample_cue_list.get_entry("verse")
        assert entry is not None
        assert entry.text == "Verse"

    def test_get_entry_not_found(self, sample_cue_list: CueList):
        assert sample_cue_list.get_entry("nonexistent") is None


class TestLoadCues:
    def test_load_valid(self):
        data = {
            "schema_version": 1,
            "cues": [
                {"id": "intro", "text": "Intro"},
                {"id": "verse", "text": "Verse", "pad_group": "Verse", "variants": 2},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            path = Path(f.name)

        cue_list = load_cues(path)
        assert cue_list.schema_version == 1
        assert len(cue_list.entries) == 2
        assert cue_list.entries[1].variants == 2
        path.unlink()

    def test_load_missing_file(self):
        from ableguides.models import AblGuidesError
        with pytest.raises(AblGuidesError, match="not found"):
            load_cues(Path("/nonexistent/cues.json"))

    def test_load_invalid_json(self):
        from ableguides.models import AblGuidesError
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{not valid json}")
            path = Path(f.name)
        with pytest.raises(AblGuidesError):
            load_cues(path)
        path.unlink()

    def test_real_cues_json(self):
        """The bundled cues.json should produce 186 expanded cues."""
        cues_path = Path(__file__).parent.parent / "cues.json"
        cue_list = load_cues(cues_path)
        expanded = cue_list.expand()
        assert len(expanded) == 186
