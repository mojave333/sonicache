"""The translation table itself, and how a language is chosen."""

import re
import string

import pytest

from trackhound import i18n

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def russian():
    """Every test starts in Russian and leaves the module as it found it."""
    i18n.set_language("ru")
    yield
    i18n.set_language("ru")


class TestTable:
    def test_both_sides_carry_the_same_placeholders(self):
        """A misspelt placeholder would raise KeyError at the worst moment."""
        for russian, english in i18n.ENGLISH.items():
            assert set(PLACEHOLDER.findall(russian)) == set(PLACEHOLDER.findall(english)), russian

    def test_nothing_is_translated_into_russian_by_accident(self):
        for russian, english in i18n.ENGLISH.items():
            assert not re.search(r"[А-Яа-яЁё]", english), russian

    def test_every_line_says_something(self):
        for russian, english in i18n.ENGLISH.items():
            assert russian.strip() and english.strip()

    def test_placeholders_are_named_not_positional(self):
        """Named ones let the two languages order them differently."""
        for russian in i18n.ENGLISH:
            for _, field, _, _ in string.Formatter().parse(russian):
                assert field is None or field.isidentifier(), russian


class TestTranslate:
    def test_russian_is_returned_as_written(self):
        assert i18n.t("Плейлист пуст или закрыт") == "Плейлист пуст или закрыт"

    def test_english_comes_from_the_table(self):
        i18n.set_language("en")
        assert i18n.t("Плейлист пуст или закрыт") == "The playlist is empty or private"

    def test_an_unknown_line_stays_readable(self):
        i18n.set_language("en")
        assert i18n.t("Такой строки в таблице нет") == "Такой строки в таблице нет"

    def test_placeholders_are_filled_in_either_language(self):
        assert "42" in i18n.t("YouTube не нашёл видео {id}", id=42)
        i18n.set_language("en")
        assert i18n.t("YouTube не нашёл видео {id}", id=42) == "YouTube has no video 42"

    def test_a_line_with_braces_and_no_values_is_left_alone(self):
        assert i18n.t("YouTube не нашёл видео {id}") == "YouTube не нашёл видео {id}"


class TestResolve:
    @pytest.mark.parametrize("setting", ["ru", "en"])
    def test_an_explicit_choice_wins(self, setting, monkeypatch):
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        assert i18n.resolve(setting) == setting

    def test_a_russian_system_gets_russian(self, monkeypatch):
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        assert i18n.resolve("system") == "ru"

    def test_another_system_gets_english(self, monkeypatch):
        monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
        assert i18n.resolve("system") == "en"

    def test_the_language_variable_is_read_too(self, monkeypatch):
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("LANGUAGE", "ru")
        assert i18n.resolve("system") == "ru"

    def test_set_language_answers_with_what_it_chose(self, monkeypatch):
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
        assert i18n.set_language("system") == "ru"
        assert i18n.language() == "ru"
