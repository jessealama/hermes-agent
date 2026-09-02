"""Tests for the read-only tag registry dashboard API (issue #100285, PR 5)."""

import pytest
from fastapi import HTTPException

import hermes_cli.tags_db as tdb
from hermes_cli.web_routers import tags as tags_api


def test_list_tags_endpoint():
    with tdb.connect_closing() as conn:
        tdb.create_tag(conn, "acme", color="#e11d48")
        tdb.apply_spec(conn, "project", "p_1", "+acme")
    body = tags_api.list_tags_endpoint()
    [tag] = body["tags"]
    assert tag["name"] == "acme"
    assert tag["color"] == "#e11d48"
    assert tag["counts"] == {"project": 1}


def test_list_tags_endpoint_empty():
    assert tags_api.list_tags_endpoint() == {"tags": []}


def test_show_tag_endpoint():
    with tdb.connect_closing() as conn:
        tdb.create_tag(conn, "acme")
        tdb.apply_spec(conn, "project", "p_1", "+acme")
        tdb.apply_spec(conn, "session", "sess_1", "+acme")
    body = tags_api.show_tag_endpoint("acme")
    assert body["tag"]["name"] == "acme"
    assert body["entities"] == {"project": ["p_1"], "session": ["sess_1"]}


def test_show_tag_endpoint_404():
    with pytest.raises(HTTPException) as exc:
        tags_api.show_tag_endpoint("nope")
    assert exc.value.status_code == 404


def test_show_tag_endpoint_invalid_name_400():
    with pytest.raises(HTTPException) as exc:
        tags_api.show_tag_endpoint("not a valid, name!")
    assert exc.value.status_code == 400


class TestSessionsListTags:
    """GET /api/sessions rows carry a ``tags`` list (empty when untagged)."""

    @pytest.fixture(autouse=True)
    def _setup_test_client(self, monkeypatch, _isolate_hermes_home):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import hermes_state
        from hermes_constants import get_hermes_home
        from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(
            hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db"
        )
        self.client = TestClient(app)
        self.client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    def _seed_sessions(self):
        from hermes_constants import get_hermes_home
        from hermes_state import SessionDB

        seed = SessionDB(db_path=get_hermes_home() / "state.db")
        try:
            seed.create_session("tagged", source="cli")
            seed.create_session("untagged", source="cli")
        finally:
            seed.close()

    def test_rows_carry_tags(self):
        self._seed_sessions()
        with tdb.connect_closing() as conn:
            tdb.create_tag(conn, "acme")
            tdb.apply_spec(conn, "session", "tagged", "+acme")

        response = self.client.get("/api/sessions?limit=50&offset=0")
        assert response.status_code == 200
        rows = {row["id"]: row for row in response.json()["sessions"]}
        assert rows["tagged"]["tags"] == ["acme"]
        assert rows["untagged"]["tags"] == []

    def test_tags_db_failure_never_breaks_listing(self, monkeypatch):
        self._seed_sessions()

        def boom(*_args, **_kwargs):
            raise RuntimeError("tags.db unavailable")

        monkeypatch.setattr(tdb, "connect_closing", boom)
        response = self.client.get("/api/sessions?limit=50&offset=0")
        assert response.status_code == 200
        assert all(row["tags"] == [] for row in response.json()["sessions"])
