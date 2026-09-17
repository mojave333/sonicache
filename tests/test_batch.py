"""Lists of links and names read from a file."""

import pytest

from trackhound import batch, sources


class TestText:
    def test_one_entry_per_line_with_comments_and_blanks_ignored(self):
        text = "# weekend\nhttps://open.spotify.com/album/x\n\n  Daft Punk  -  Discovery \n"
        assert batch.parse(text, "list.txt") == (
            ["https://open.spotify.com/album/x", "Daft Punk - Discovery"], 0)

    def test_repeats_are_taken_once_in_the_order_they_came(self):
        text = "b - two\na - one\nB - TWO\n"
        assert batch.parse(text, "list.txt")[0] == ["b - two", "a - one"]

    def test_links_are_pulled_out_of_a_line_of_prose(self):
        text = "listen to https://youtu.be/abc and https://open.spotify.com/track/y.\n"
        assert batch.parse(text, "notes.txt")[0] == ["https://youtu.be/abc", "https://open.spotify.com/track/y"]

    def test_a_txt_file_with_commas_is_still_text(self):
        assert batch.parse("Artist, Title\n", "list.txt")[0] == ["Artist, Title"]

    def test_there_is_a_limit(self):
        text = "".join(f"artist - song {i}\n" for i in range(batch.LIMIT + 20))
        assert len(batch.parse(text, "big.txt")[0]) == batch.LIMIT


class TestCsv:
    def test_exportify_prefers_the_track_uri(self):
        text = ("Track URI,Track Name,Artist Name(s),Album Name\n"
                "spotify:track:4cOdK2wGLETKBW3PvgPWqT,Never Gonna Give You Up,Rick Astley,Whenever\n")
        assert batch.parse(text, "playlist.csv")[0] == ["spotify:track:4cOdK2wGLETKBW3PvgPWqT"]

    def test_without_a_link_a_row_becomes_a_track_search_by_its_first_artist(self):
        text = 'Track Name,Artist Name(s)\nRumspringa,"ear, Someone Else"\n'
        assert batch.parse(text, "playlist.csv")[0] == ["track:ear - Rumspringa"]

    def test_tunemymusic_semicolons_and_lowercase_headers(self):
        text = "Track name;Artist name;Album\nCoil;ear;Rumspringa\n"
        assert batch.parse(text, "tunemymusic.csv")[0] == ["track:ear - Coil"]

    def test_a_csv_is_recognised_without_its_extension(self):
        assert batch.parse("Title,Artist\nCoil,ear\n", "dropped")[0] == ["track:ear - Coil"]

    def test_empty_rows_are_not_counted_as_failures(self):
        text = "Track Name,Artist Name(s)\n,,\nCoil,ear\n"
        assert batch.parse(text, "x.csv") == (["track:ear - Coil"], 0)

    def test_a_row_with_nothing_usable_is_counted(self):
        text = "Track Name,Artist Name(s),Notes\n,,just words\nCoil,ear,\n"
        assert batch.parse(text, "x.csv") == (["track:ear - Coil"], 1)


class TestEncodings:
    @pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1251"])
    def test_cyrillic_survives_whatever_the_file_was_saved_in(self, encoding):
        data = "Кино - Группа крови\n".encode(encoding)
        assert batch.read(data, "ru.txt")[0] == ["Кино - Группа крови"]


class TestTrackPrefix:
    def test_a_marked_entry_searches_for_the_song_not_the_album(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sources, "find_album", lambda *a, **k: pytest.fail("album searched"))
        monkeypatch.setattr(sources, "find_track",
                            lambda artist, title, service, album="": seen.update(artist=artist, title=title))
        sources.resolve("track:ear - Rumspringa")
        assert seen == {"artist": "ear", "title": "Rumspringa"}
