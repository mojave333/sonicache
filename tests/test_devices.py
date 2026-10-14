"""Syncing the library to a player or a phone that shows up as a drive (sonicache.engine.devices)."""

import json
import threading
import types

import pytest

from sonicache.engine import devices
from sonicache.engine.devices import PlaylistSpec, Source


def manifest_path(root):
    return root / devices.SYNC_DIR / devices.MANIFEST


def make(path, data=b"music"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def record(source, mode="copy"):
    """A manifest entry for a source as it is now."""
    stat = source.stat()
    return {"source": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "mode": mode,
            "bytes": stat.st_size}


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"


@pytest.fixture
def dev(tmp_path):
    device = tmp_path / "dev"
    device.mkdir()
    return device


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


class TestPlan:
    def test_a_new_source_is_written(self, lib, dev):
        source = make(lib / "R" / "A" / "a.flac")
        plan = devices.plan(dev, [Source(source, "R/A/a.flac")], [])
        assert [(item.dest, item.mode, item.audio) for item in plan.write] == [("Sonicache/R/A/a.flac", "copy", True)]
        assert plan.keep == 0 and plan.delete == [] and plan.conflicts == []

    def test_an_unchanged_source_is_kept(self, lib, dev):
        source = make(lib / "a.flac")
        make(dev / "Sonicache" / "a.flac")
        devices.save_manifest(dev, {"Sonicache/a.flac": record(source)})
        plan = devices.plan(dev, [Source(source, "a.flac")], [])
        assert plan.write == [] and plan.keep == 1

    def test_a_changed_source_is_written_again(self, lib, dev):
        source = make(lib / "a.flac")
        make(dev / "Sonicache" / "a.flac")
        devices.save_manifest(dev, {"Sonicache/a.flac": {**record(source), "mtime_ns": 1}})
        plan = devices.plan(dev, [Source(source, "a.flac")], [])
        assert [item.dest for item in plan.write] == ["Sonicache/a.flac"]

    def test_a_copy_gone_from_the_device_is_written_again(self, lib, dev):
        source = make(lib / "a.flac")
        devices.save_manifest(dev, {"Sonicache/a.flac": record(source)})
        plan = devices.plan(dev, [Source(source, "a.flac")], [])
        assert [item.dest for item in plan.write] == ["Sonicache/a.flac"]

    def test_a_change_of_mode_writes_it_again(self, lib, dev):
        source = make(lib / "a.flac")
        make(dev / "Sonicache" / "a.flac")
        devices.save_manifest(dev, {"Sonicache/a.flac": record(source)})
        plan = devices.plan(dev, [Source(source, "a.flac", 60)], [], transcode=True)
        assert [(item.dest, item.mode) for item in plan.write] == [("Sonicache/a.mp3", "mp3:256")]
        assert plan.delete == ["Sonicache/a.flac"]

    def test_what_is_no_longer_chosen_is_deleted(self, lib, dev):
        make(dev / "Sonicache" / "x.mp3")
        devices.save_manifest(dev, {"Sonicache/x.mp3": record(make(lib / "x.mp3"))})
        assert devices.plan(dev, [], []).delete == ["Sonicache/x.mp3"]

    def test_a_moved_source_is_deleted_and_copied_anew(self, lib, dev):
        source = make(lib / "new" / "a.flac")
        make(dev / "Sonicache" / "old" / "a.flac")
        devices.save_manifest(dev, {"Sonicache/old/a.flac": {**record(source), "source": str(lib / "old" / "a.flac")}})
        plan = devices.plan(dev, [Source(source, "new/a.flac")], [])
        assert [item.dest for item in plan.write] == ["Sonicache/new/a.flac"]
        assert plan.delete == ["Sonicache/old/a.flac"]

    def test_a_file_of_someone_else_is_a_conflict(self, lib, dev):
        source = make(lib / "R" / "A" / "a.flac")
        make(dev / "Sonicache" / "R" / "A" / "a.flac", b"theirs")
        plan = devices.plan(dev, [Source(source, "R/A/a.flac")], [])
        assert plan.conflicts == ["Sonicache/R/A/a.flac"]
        assert plan.write == [] and plan.delete == []

    def test_mp3_is_copied_even_when_transcoding(self, lib, dev):
        source = make(lib / "a.mp3")
        plan = devices.plan(dev, [Source(source, "a.mp3", 60)], [], transcode=True)
        assert [(item.dest, item.mode) for item in plan.write] == [("Sonicache/a.mp3", "copy")]

    def test_without_ffmpeg_nothing_is_transcoded(self, lib, dev):
        source = make(lib / "a.flac")
        plan = devices.plan(dev, [Source(source, "a.flac", 60)], [], transcode=True, can_transcode=False)
        assert [(item.dest, item.mode) for item in plan.write] == [("Sonicache/a.flac", "copy")]

    def test_two_sources_on_one_name_get_a_number(self, lib, dev):
        sources = [Source(make(lib / "a.flac"), "a.flac", 60), Source(make(lib / "a.mp3"), "a.mp3", 60)]
        plan = devices.plan(dev, sources, [], transcode=True)
        assert [item.dest for item in plan.write] == ["Sonicache/a.mp3", "Sonicache/a (2).mp3"]

    def test_the_cover_and_the_lyrics_go_along(self, lib, dev):
        source = make(lib / "A" / "01 a.flac")
        make(lib / "A" / "01 a.lrc", b"[00:01.00]hi")
        make(lib / "A" / "cover.jpg", b"\xff\xd8\xff")
        plan = devices.plan(dev, [Source(source, "A/01 a.flac", 60)], [], transcode=True)
        assert {(item.dest, item.mode, item.audio) for item in plan.write} == {
            ("Sonicache/A/01 a.mp3", "mp3:256", True), ("Sonicache/A/01 a.lrc", "copy", False),
            ("Sonicache/A/cover.jpg", "copy", False)}

    def test_the_size_after_transcoding_is_estimated(self, lib, dev):
        source = make(lib / "a.flac")
        [item] = devices.plan(dev, [Source(source, "a.flac", 100)], [], transcode=True, bitrate=256).write
        assert item.size == 100 * 256 * 1000 // 8 + 65536

    def test_need_counts_what_is_freed_and_fits_compares_with_free(self, lib, dev, monkeypatch):
        monkeypatch.setattr(devices.shutil, "disk_usage", lambda root: types.SimpleNamespace(total=10_000, used=0, free=1000))
        make(dev / "Sonicache" / "old.mp3", b"x" * 300)
        devices.save_manifest(dev, {"Sonicache/old.mp3": {**record(make(lib / "old.mp3")), "bytes": 300}})
        source = make(lib / "a.flac", b"x" * 1200)
        plan = devices.plan(dev, [Source(source, "a.flac")], [])
        assert (plan.free, plan.need, plan.fits) == (1000, 900, True)
        bigger = devices.plan(dev, [Source(make(lib / "b.flac", b"x" * 1400), "b.flac")], [])
        assert (bigger.need, bigger.fits) == (1100, False)

    def test_a_playlist_lists_its_tracks_relative_to_its_file(self, lib, dev):
        source = make(lib / "R" / "A" / "a.flac")
        stray = lib / "not chosen.flac"
        plan = devices.plan(dev, [Source(source, "R/A/a.flac")], [PlaylistSpec("Mix: best", [source, stray])])
        assert list(plan.playlists) == ["Sonicache/Playlists/Mix_ best.m3u8"]
        text = plan.playlists["Sonicache/Playlists/Mix_ best.m3u8"]
        assert text.splitlines() == ["#EXTM3U", "#PLAYLIST:Mix: best", "../R/A/a.flac"]


class TestRun:
    def test_run_copies_and_records(self, lib, dev):
        source = make(lib / "R" / "a.flac", b"abc")
        report = devices.run(dev, devices.plan(dev, [Source(source, "R/a.flac")], []))
        assert (dev / "Sonicache" / "R" / "a.flac").read_bytes() == b"abc"
        entry = devices.load_manifest(dev)["Sonicache/R/a.flac"]
        assert (entry["source"], entry["mode"], entry["bytes"]) == (str(source), "copy", 3)
        assert report.copied == 1 and report.error == "" and not report.stopped

    def test_run_deletes_only_what_the_manifest_lists(self, lib, dev):
        mine = make(dev / "Sonicache" / "old" / "x.mp3")
        theirs = make(dev / "Sonicache" / "theirs.mp3")
        devices.save_manifest(dev, {"Sonicache/old/x.mp3": record(make(lib / "x.mp3"))})
        report = devices.run(dev, devices.plan(dev, [], []))
        assert not mine.exists() and not mine.parent.exists()
        assert theirs.exists() and report.deleted == 1
        assert devices.load_manifest(dev) == {}

    def test_a_second_run_writes_nothing(self, lib, dev):
        sources = [Source(make(lib / "a.flac"), "a.flac"), Source(make(lib / "b.flac"), "b.flac")]
        devices.run(dev, devices.plan(dev, sources, []))
        again = devices.plan(dev, sources, [])
        assert again.write == [] and again.keep == 2

    def test_stop_keeps_what_was_done(self, lib, dev):
        sources = [Source(make(lib / f"{n}.flac"), f"{n}.flac") for n in "abc"]
        stop = threading.Event()

        def progress(done, total, title):
            if done == 1:
                stop.set()
        report = devices.run(dev, devices.plan(dev, sources, []), progress=progress, stop=stop)
        assert report.stopped and report.copied == 1
        assert list(devices.load_manifest(dev)) == ["Sonicache/a.flac"]
        assert [item.dest for item in devices.plan(dev, sources, []).write] == ["Sonicache/b.flac", "Sonicache/c.flac"]

    def test_device_gone_mid_run_keeps_what_was_done(self, lib, dev, monkeypatch):
        sources = [Source(make(lib / f"{n}.flac"), f"{n}.flac") for n in "abc"]
        work = devices.plan(dev, sources, [])
        copy = devices.shutil.copyfile
        calls = []

        def flaky(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError(5, "the device is gone")
            return copy(source, target)
        monkeypatch.setattr(devices.shutil, "copyfile", flaky)
        report = devices.run(dev, work)
        assert report.error and report.copied == 1
        assert list(devices.load_manifest(dev)) == ["Sonicache/a.flac"]

    def test_leftover_part_files_are_removed(self, lib, dev):
        part = make(dev / "Sonicache" / "R" / "x.mp3.part")
        devices.run(dev, devices.plan(dev, [], []))
        assert not part.exists()

    def test_an_unreadable_source_is_reported_and_the_rest_go_on(self, lib, dev):
        gone = make(lib / "a.flac")
        sources = [Source(gone, "a.flac"), Source(make(lib / "b.flac"), "b.flac")]
        work = devices.plan(dev, sources, [])
        gone.unlink()
        report = devices.run(dev, work)
        assert report.failed == ["a.flac"] and report.copied == 1

    def test_playlists_are_written_and_dropped(self, lib, dev):
        source = make(lib / "a.flac")
        devices.run(dev, devices.plan(dev, [Source(source, "a.flac")], [PlaylistSpec("Mix", [source])]))
        playlist = dev / "Sonicache" / "Playlists" / "Mix.m3u8"
        assert playlist.read_text(encoding="utf-8").splitlines()[-1] == "../a.flac"
        assert devices.load_manifest(dev)["Sonicache/Playlists/Mix.m3u8"]["mode"] == "playlist"
        devices.run(dev, devices.plan(dev, [Source(source, "a.flac")], []))
        assert not playlist.exists()
