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


class TestRegistryCrud:
    def test_create_normalizes_and_roundtrips(self, conn):
        tag = tdb.create_tag(conn, "Client Acme", color="#e11d48",
                             description="Acme Corp work")
        assert tag.name == "client-acme"
        got = tdb.get_tag(conn, "CLIENT ACME")
        assert got is not None and got.id == tag.id
        assert got.color == "#e11d48" and got.description == "Acme Corp work"

    def test_create_duplicate_errors(self, conn):
        tdb.create_tag(conn, "acme")
        with pytest.raises(ValueError, match="already exists"):
            tdb.create_tag(conn, "ACME")

    def test_list_sorted_by_name(self, conn):
        tdb.create_tag(conn, "zeta")
        tdb.create_tag(conn, "alpha")
        assert [t.name for t in tdb.list_tags(conn)] == ["alpha", "zeta"]

    def test_rename_keeps_id_and_rejects_collision(self, conn):
        tag = tdb.create_tag(conn, "client-acme")
        tdb.create_tag(conn, "urgent")
        renamed = tdb.rename_tag(conn, "client-acme", "acme")
        assert renamed.id == tag.id and renamed.name == "acme"
        with pytest.raises(ValueError, match="already exists"):
            tdb.rename_tag(conn, "acme", "urgent")

    def test_rename_unknown_errors(self, conn):
        with pytest.raises(ValueError, match="unknown tag"):
            tdb.rename_tag(conn, "nope", "new")

    def test_delete_returns_counts_and_frees_name(self, conn):
        tdb.create_tag(conn, "acme")
        counts = tdb.delete_tag(conn, "acme")
        assert counts == {}
        tdb.create_tag(conn, "acme")  # name reusable immediately


class TestAssignments:
    @pytest.fixture(autouse=True)
    def seed(self, conn):
        tdb.create_tag(conn, "acme")
        tdb.create_tag(conn, "urgent")

    def test_parse_tag_spec(self):
        assert tdb.parse_tag_spec("+acme,-urgent,night") == (
            ["acme", "night"], ["urgent"])
        with pytest.raises(ValueError):
            tdb.parse_tag_spec("+a,-a")
        with pytest.raises(ValueError):
            tdb.parse_tag_spec("  ")

    def test_apply_spec_add_remove_idempotent(self, conn):
        added, removed = tdb.apply_spec(conn, "task", "b/t_1", "acme,urgent")
        assert set(added) == {"acme", "urgent"} and removed == []
        added, removed = tdb.apply_spec(conn, "task", "b/t_1", "+acme,-urgent")
        assert added == [] and removed == ["urgent"]
        assert tdb.tags_for_entity(conn, "task", "b/t_1") == ["acme"]

    def test_apply_spec_unknown_tag_hints_create(self, conn):
        with pytest.raises(ValueError, match="hermes tag create urgnet"):
            tdb.apply_spec(conn, "task", "b/t_1", "+urgnet")

    def test_apply_spec_rejects_bad_entity_type(self, conn):
        with pytest.raises(ValueError, match="entity type"):
            tdb.apply_spec(conn, "widget", "w_1", "+acme")

    def test_entity_keys_for_tags_is_intersection(self, conn):
        tdb.apply_spec(conn, "project", "p_1", "acme,urgent")
        tdb.apply_spec(conn, "project", "p_2", "acme")
        assert tdb.entity_keys_for_tags(conn, "project", ["acme"]) == {"p_1", "p_2"}
        assert tdb.entity_keys_for_tags(conn, "project", ["acme", "urgent"]) == {"p_1"}
        with pytest.raises(ValueError, match="unknown tag"):
            tdb.entity_keys_for_tags(conn, "project", ["nope"])

    def test_entities_for_tag_groups_by_type(self, conn):
        tdb.apply_spec(conn, "project", "p_1", "+acme")
        tdb.apply_spec(conn, "board", "b", "+acme")
        assert tdb.entities_for_tag(conn, "acme") == {
            "board": ["b"], "project": ["p_1"]}

    def test_tags_for_entities_bulk(self, conn):
        tdb.apply_spec(conn, "session", "s1", "+acme")
        out = tdb.tags_for_entities(conn, "session", ["s1", "s2"])
        assert out == {"s1": ["acme"]}

    def test_detach_board_drops_board_and_its_tasks_only(self, conn):
        tdb.apply_spec(conn, "board", "b", "+acme")
        tdb.apply_spec(conn, "task", "b/t_1", "+acme")
        tdb.apply_spec(conn, "task", "b2/t_1", "+acme")
        assert tdb.detach_board(conn, "b") == 2
        assert tdb.entities_for_tag(conn, "acme") == {"task": ["b2/t_1"]}

    def test_prune_deletes_only_unresolvable(self, conn):
        tdb.apply_spec(conn, "project", "p_live", "+acme")
        tdb.apply_spec(conn, "project", "p_dead", "+acme")
        deleted = tdb.prune(conn, {"project": lambda keys: {"p_live"}})
        assert deleted == {"project": 1}
        assert tdb.entities_for_tag(conn, "acme") == {"project": ["p_live"]}
