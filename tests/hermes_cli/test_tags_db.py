import pytest

import hermes_cli.tags_db as tdb


@pytest.fixture
def conn(tmp_path):
    c = tdb.connect(db_path=tmp_path / "tags.db")
    try:
        yield c
    finally:
        c.close()


class TestNormalizeTagName:
    def test_trims_and_casefolds(self):
        assert tdb.normalize_tag_name("  Client Acme  ") == "client-acme"

    def test_collapses_inner_whitespace_runs_to_one_dash(self):
        assert tdb.normalize_tag_name("RUN AT \t NIGHT") == "run-at-night"

    def test_unicode_letters_allowed(self):
        assert tdb.normalize_tag_name("日本語") == "日本語"

    def test_rejects_punctuation_with_offender_named(self):
        with pytest.raises(ValueError, match="':'"):
            tdb.normalize_tag_name("client: acme")

    def test_rejects_comma(self):
        with pytest.raises(ValueError):
            tdb.normalize_tag_name("a,b")

    def test_rejects_leading_dash_and_plus(self):
        with pytest.raises(ValueError):
            tdb.normalize_tag_name("-urgent")
        with pytest.raises(ValueError):
            tdb.normalize_tag_name("+urgent")

    def test_rejects_empty_and_too_long(self):
        with pytest.raises(ValueError):
            tdb.normalize_tag_name("   ")
        with pytest.raises(ValueError):
            tdb.normalize_tag_name("x" * 65)


class TestValidateColor:
    def test_none_passes_through(self):
        assert tdb.validate_color(None) is None

    def test_hex6_and_hex3_ok(self):
        assert tdb.validate_color("#e11d48") == "#e11d48"
        assert tdb.validate_color("#abc") == "#abc"

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="#rgb or #rrggbb"):
            tdb.validate_color("reddish")


def test_task_key():
    assert tdb.task_key("acme-board", "t_0042") == "acme-board/t_0042"


def test_connect_creates_schema(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tags", "tag_assignments"} <= names
