"""Shared fixtures for ableguides tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ableguides.models import Cue, CueEntry, CueList, Voice


@pytest.fixture
def voice_a() -> Voice:
    return Voice(name="guide_male", voice_id="abc123")


@pytest.fixture
def voice_b() -> Voice:
    return Voice(name="guide_female", voice_id="def456")


@pytest.fixture
def cue_verse() -> Cue:
    return Cue(id="verse", text="Verse", pad_name="Verse", base_id="verse", variant=None)


@pytest.fixture
def cue_verse_3() -> Cue:
    return Cue(id="verse_3", text="Verse 3", pad_name="Verse 3", base_id="verse", variant=3)


@pytest.fixture
def sample_cue_list() -> CueList:
    return CueList(
        schema_version=1,
        entries=[
            CueEntry(id="verse", text="Verse", pad_group="Verse", variants=2),
            CueEntry(id="chorus", text="Chorus", pad_group="Chorus", variants=1),
            CueEntry(id="intro", text="Intro", pad_group="Intro", variants=0),
        ],
    )
