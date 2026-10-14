"""Syncing the library to a player or a phone that shows up as a drive (sonicache.engine.devices)."""

import json

from sonicache.engine import devices


def manifest_path(root):
    return root / devices.SYNC_DIR / devices.MANIFEST


class TestNames:
    def test_safe_rel_cleans_each_part_for_fat(self):
        assert devices.safe_rel('AC/DC: Live?/01 "Hi" .flac.') == "AC/DC_ Live_/01 _Hi_ .flac"

    def test_safe_rel_limits_a_part_and_the_path(self):
        rel = devices.safe_rel("/".join(["x" * 200] * 3))
        assert all(len(part) <= 120 for part in rel.split("/"))
        assert len(rel) <= 240


class TestManifest:
    def test_manifest_round_trips(self, tmp_path):
        files = {"Sonicache/a.mp3": {"source": "s", "size": 1, "mtime_ns": 2, "mode": "copy", "bytes": 1}}
        devices.save_manifest(tmp_path, files)
        assert devices.load_manifest(tmp_path) == files

    def test_a_missing_manifest_is_empty(self, tmp_path):
        assert devices.load_manifest(tmp_path) == {}

    def test_a_broken_manifest_is_empty(self, tmp_path):
        manifest_path(tmp_path).parent.mkdir()
        manifest_path(tmp_path).write_text("{nope", encoding="utf-8")
        assert devices.load_manifest(tmp_path) == {}

    def test_a_manifest_of_another_version_is_empty(self, tmp_path):
        manifest_path(tmp_path).parent.mkdir()
        manifest_path(tmp_path).write_text(json.dumps({"version": 2, "files": {"Sonicache/a": {}}}), encoding="utf-8")
        assert devices.load_manifest(tmp_path) == {}
