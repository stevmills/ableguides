"""Tests for ableguides.patcher -- sample path substitution."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ableguides.models import CueEntry, CueList, Voice
from ableguides.patcher import (
    _stem_from_filename,
    _stem_to_cue_id,
    _patch_chain,
    _KNOWN_TEMPLATE_BASE_IDS,
)


class TestStemFromFilename:
    def test_verse_base(self):
        assert _stem_from_filename("verse-1-12-1.mp3") == "verse"

    def test_verse_variant(self):
        assert _stem_from_filename("verse_3-12-1.mp3") == "verse_3"

    def test_bridge_variant(self):
        assert _stem_from_filename("bridge_18-12-1.mp3") == "bridge_18"

    def test_post_chorus_base(self):
        assert _stem_from_filename("post-chorus-1-1-1.mp3") == "post-chorus"

    def test_post_chorus_variant(self):
        assert _stem_from_filename("post-chorus_5-1-1-1.mp3") == "post-chorus_5"

    def test_number(self):
        assert _stem_from_filename("7-12-1.mp3") == "7"

    def test_key_change(self):
        assert _stem_from_filename("key_change_down-12-1.mp3") == "key_change_down"


class TestStemToCueId:
    def test_identity_mapping(self):
        assert _stem_to_cue_id("verse") == "verse"
        assert _stem_to_cue_id("chorus") == "chorus"
        assert _stem_to_cue_id("bridge_5") == "bridge_5"

    def test_numbers_mapped(self):
        assert _stem_to_cue_id("1") == "number_1"
        assert _stem_to_cue_id("12") == "number_12"

    def test_key_change_down(self):
        assert _stem_to_cue_id("key_change_down") == "key_down"

    def test_key_change_up(self):
        assert _stem_to_cue_id("key_change_up") == "key_up"

    def test_post_chorus_base(self):
        assert _stem_to_cue_id("post-chorus") == "post_chorus"

    def test_post_chorus_variant(self):
        assert _stem_to_cue_id("post-chorus_7") == "post_chorus_7"

    def test_pre_chorus(self):
        assert _stem_to_cue_id("pre-chorus") == "pre_chorus"
        assert _stem_to_cue_id("pre-chorus_3") == "pre_chorus_3"


class TestPatchChain:
    def _make_minimal_chain(self, filename: str) -> str:
        """Return a minimal XML chain with one SampleRef pointing to filename."""
        return f"""<InstrumentBranchPreset Id="0">
    <Name Value="Voice 1" />
    <DevicePresets>
        <DrumBranchPreset Id="0">
            <Name Value="Verse" />
            <DevicePresets>
                <SampleRef>
                    <FileRef>
                        <RelativePathType Value="6" />
                        <RelativePath Value="Samples/Imported/{filename}" />
                        <Path Value="C:/Users/me/old/{filename}" />
                        <OriginalFileSize Value="12345" />
                        <OriginalCrc Value="99" />
                    </FileRef>
                </SampleRef>
            </DevicePresets>
        </DrumBranchPreset>
    </DevicePresets>
</InstrumentBranchPreset>"""

    def test_path_replaced(self):
        voice = Voice(name="Guide Male", voice_id="v1")
        chain = self._make_minimal_chain("verse-1-12-1.mp3")
        patched, patched_count, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\GuidePacks"
        )
        assert 'C:\\GuidePacks\\guide-male\\verse.wav' in patched
        assert patched_count >= 1

    def test_relative_path_cleared(self):
        voice = Voice(name="Guide Male", voice_id="v1")
        chain = self._make_minimal_chain("verse-1-12-1.mp3")
        patched, _, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\GuidePacks"
        )
        assert '<RelativePath Value=""' in patched

    def test_relative_path_type_set_to_zero(self):
        voice = Voice(name="Guide Male", voice_id="v1")
        chain = self._make_minimal_chain("verse-1-12-1.mp3")
        patched, _, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\GuidePacks"
        )
        assert '<RelativePathType Value="0"' in patched

    def test_original_file_size_zeroed(self):
        voice = Voice(name="Guide Male", voice_id="v1")
        chain = self._make_minimal_chain("verse-1-12-1.mp3")
        patched, _, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\GuidePacks"
        )
        assert '<OriginalFileSize Value="0"' in patched

    def test_chain_name_replaced(self):
        voice = Voice(name="Guide Female", voice_id="v2")
        chain = self._make_minimal_chain("verse-1-12-1.mp3")
        patched, _, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\GuidePacks"
        )
        assert '<Name Value="Guide Female"' in patched
        assert '<Name Value="Voice 1"' not in patched

    def test_windows_backslash_not_escaped(self):
        """Ensure Windows paths with backslashes are embedded literally."""
        voice = Voice(name="Voice A", voice_id="va")
        chain = self._make_minimal_chain("chorus-1-12-1.mp3")
        patched, _, _ = _patch_chain(
            chain, voice, Path("/mnt/c/GuidePacks"), "C:\\Users\\me\\GuidePacks"
        )
        assert "C:\\Users\\me\\GuidePacks\\voice-a\\chorus.wav" in patched


class TestKnownTemplateBaseIds:
    def test_original_singles_present(self):
        for cue_id in ("acapella", "acoustic", "vamp", "worship_leader", "softly"):
            assert cue_id in _KNOWN_TEMPLATE_BASE_IDS

    def test_variant_groups_present(self):
        for cue_id in ("verse", "chorus", "bridge", "tag", "refrain", "post_chorus"):
            assert cue_id in _KNOWN_TEMPLATE_BASE_IDS

    def test_numbers_present(self):
        for n in range(1, 13):
            assert f"number_{n}" in _KNOWN_TEMPLATE_BASE_IDS

    def test_new_cues_absent(self):
        """New cues added after the original template should not be in the set."""
        for cue_id in ("again", "drop", "lift", "altar", "ministry", "choir", "pad"):
            assert cue_id not in _KNOWN_TEMPLATE_BASE_IDS


class TestDynamicPadGeneration:
    def _make_chain_with_rack_template(self) -> str:
        """Minimal chain XML that includes a Rack Template placeholder."""
        return (
            '<InstrumentBranchPreset Id="0">\n'
            '    <Name Value="Voice 1" />\n'
            '    <DrumBranchPreset Id="0">\n'
            '        <Name Value="Rack Template" />\n'
            '    </DrumBranchPreset>\n'
            '</InstrumentBranchPreset>'
        )

    def test_new_cue_pad_inserted(self, tmp_path):
        """A cue not in _KNOWN_TEMPLATE_BASE_IDS gets a pad block inserted."""
        # Write a minimal single_pad_template.xml to tmp_path
        pad_tpl = (
            '\t\t\t\t\t\t\t\t\t<DrumBranchPreset Id="__PAD_ID__">\n'
            '\t\t\t\t\t\t\t\t\t\t<Name Value="__PAD_NAME__" />\n'
            '\t\t\t\t\t\t\t\t\t\t<SampleRef>\n'
            '\t\t\t\t\t\t\t\t\t\t\t<FileRef>\n'
            '\t\t\t\t\t\t\t\t\t\t\t\t<Path Value="__WAV_PATH__" />\n'
            '\t\t\t\t\t\t\t\t\t\t\t</FileRef>\n'
            '\t\t\t\t\t\t\t\t\t\t</SampleRef>\n'
            '\t\t\t\t\t\t\t\t\t\t<ReceivingNote Value="__RECEIVING_NOTE__" />\n'
            '\t\t\t\t\t\t\t\t\t</DrumBranchPreset>\n'
        )
        (tmp_path / "single_pad_template.xml").write_text(pad_tpl)

        cue_list = CueList(
            schema_version=1,
            entries=[CueEntry(id="again", text="Again", pad_group="Again")],
        )
        voice = Voice(name="Ben", voice_id="abc")
        chain = self._make_chain_with_rack_template()

        patched, count, _ = _patch_chain(
            chain, voice, Path("/tmp/out"), "C:\\GuidePacks",
            cue_list=cue_list, templates_dir=tmp_path,
        )

        assert '__PAD_ID__' not in patched
        assert '__WAV_PATH__' not in patched
        assert "Again" in patched
        assert "C:\\GuidePacks\\ben\\again.wav" in patched
        assert count >= 1
        # New pad appears BEFORE Rack Template
        new_pad_pos = patched.find("Again")
        rack_template_pos = patched.find("Rack Template")
        assert new_pad_pos < rack_template_pos

    def test_known_cues_not_duplicated(self, tmp_path):
        """Cues in _KNOWN_TEMPLATE_BASE_IDS do NOT get an extra pad generated."""
        (tmp_path / "single_pad_template.xml").write_text(
            '<DrumBranchPreset Id="__PAD_ID__"><Name Value="__PAD_NAME__" /></DrumBranchPreset>\n'
        )
        cue_list = CueList(
            schema_version=1,
            entries=[CueEntry(id="verse", text="Verse", pad_group="Verse", variants=2)],
        )
        voice = Voice(name="Test", voice_id="t1")
        chain = self._make_chain_with_rack_template()

        patched, _, _ = _patch_chain(
            chain, voice, Path("/tmp"), "C:\\Root",
            cue_list=cue_list, templates_dir=tmp_path,
        )
        # "Verse" should only appear once (the Rack Template marker, not a new pad)
        assert patched.count('<Name Value="Verse"') == 0
