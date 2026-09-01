"""CLI tests for session tagging (part 3 of the tags-registry work).

Drives ``cmd_sessions`` with prebuilt Namespaces, mirroring
``test_sessions_error_exit_codes.py`` — the sessions parser lives inline in
``main.py`` and is not importable standalone.
"""

import sqlite3
from argparse import Namespace

import pytest

import hermes_cli.sessions_cmd as sc
import hermes_cli.tags_db as tdb


def _args(action, **kw):
    base = dict(
        sessions_action=action,
        session_id=None, source=None, limit=20, workspace=None,
        tag=[], spec=None,
    )
    base.update(kw)
    return Namespace(**base)


@pytest.fixture
def state_db_path():
    """Seed two sessions in the store an argless ``SessionDB()`` resolves.

    The autouse ``_hermetic_environment`` fixture re-pins
    ``hermes_state.DEFAULT_DB_PATH`` per test (once ``hermes_state`` is
    imported), so seed through that constant — a hand-picked tmp path would
    be a *different* store than the one ``cmd_sessions`` opens.
    """
    import hermes_state
    from hermes_state import SessionDB

    db = SessionDB(hermes_state.DEFAULT_DB_PATH)
    db.create_session("sess_tagged01", "cli")
    db.create_session("sess_plain001", "cli")
    return hermes_state.DEFAULT_DB_PATH


def _seed_tags(*names):
    with tdb.connect_closing() as conn:
        for n in names:
            tdb.create_tag(conn, n)


def test_tag_and_filter_roundtrip(state_db_path, capsys):
    _seed_tags("client-acme", "urgent")
    rc = sc.cmd_sessions(_args("tag", session_id="sess_tagged01",
                               spec="+client-acme,+urgent"))
    assert rc == 0
    with tdb.connect_closing() as conn:
        assert tdb.tags_for_entity(conn, "session", "sess_tagged01") == [
            "client-acme", "urgent"]
    capsys.readouterr()

    rc = sc.cmd_sessions(_args("list", tag=["client-acme"]))
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "sess_tagged01" in out and "sess_plain001" not in out

    # '-' in the spec detaches
    rc = sc.cmd_sessions(_args("tag", session_id="sess_tagged01",
                               spec="-urgent"))
    assert rc == 0
    with tdb.connect_closing() as conn:
        assert tdb.tags_for_entity(conn, "session", "sess_tagged01") == [
            "client-acme"]


def test_tag_unknown_session_exits_2(state_db_path, capsys):
    _seed_tags("client-acme")
    rc = sc.cmd_sessions(_args("tag", session_id="sess_nope", spec="+client-acme"))
    assert rc == 2
    assert "unknown session" in capsys.readouterr().err


def test_tag_unknown_tag_exits_2_with_create_hint(state_db_path, capsys):
    rc = sc.cmd_sessions(_args("tag", session_id="sess_tagged01", spec="+nope"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown tag" in err and "hermes tag create nope" in err


def test_list_unknown_tag_exits_2(state_db_path, capsys):
    rc = sc.cmd_sessions(_args("list", tag=["nope"]))
    assert rc == 2
    assert "unknown tag" in capsys.readouterr().err


def test_list_tag_filter_reaches_past_limit(state_db_path, capsys):
    """--tag must widen the fetch window: a tagged session older than the
    first --limit rows still has to appear."""
    _seed_tags("client-acme")
    # Backdate the tagged session so it sorts last, then filter with limit=1.
    with sqlite3.connect(state_db_path) as conn:
        conn.execute(
            "UPDATE sessions SET started_at = started_at - 3600 "
            "WHERE id = 'sess_tagged01'")
    assert sc.cmd_sessions(_args("tag", session_id="sess_tagged01",
                                 spec="+client-acme")) == 0
    capsys.readouterr()
    rc = sc.cmd_sessions(_args("list", tag=["client-acme"], limit=1))
    assert rc in (0, None)
    assert "sess_tagged01" in capsys.readouterr().out
