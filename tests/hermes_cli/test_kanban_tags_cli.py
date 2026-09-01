"""Tests for kanban task/board tagging (``hermes kanban tag``, ``boards tag``).

Part 2 of the tag registry (#100285): tasks and boards become taggable, the
``list`` commands gain a repeatable ``--tag`` AND-filter, and removing a board
detaches its assignments so a slug reused later starts clean.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

# Ensure the worktree (not a stale global clone) is first on sys.path.
_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

import hermes_cli.kanban as kanban
import hermes_cli.kanban_db as kb
import hermes_cli.tags_db as tdb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME holding both kanban.db and tags.db."""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in (
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_HOME",
        "HERMES_KANBAN_BOARD",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        import hermes_constants

        hermes_constants._cached_default_hermes_root = None  # type: ignore[attr-defined]
    except Exception:
        pass
    kb._INITIALIZED_PATHS.clear()
    tdb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _run(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = kanban.build_parser(sub)
    p.set_defaults(func=kanban.kanban_command)
    args = parser.parse_args(["kanban", *argv])
    return kanban.kanban_command(args)


def _seed_tags(*names):
    with tdb.connect_closing() as conn:
        for n in names:
            tdb.create_tag(conn, n)


# ---------------------------------------------------------------------------
# Task tagging
# ---------------------------------------------------------------------------

def test_create_with_tags_then_filter(kanban_home, capsys):
    _seed_tags("urgent", "night")
    _run(["create", "Tagged task", "--triage", "--tags", "urgent"])
    _run(["create", "Plain task", "--triage"])
    capsys.readouterr()
    assert _run(["list", "--tag", "urgent", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    titles = [r["title"] for r in rows]
    assert titles == ["Tagged task"]
    assert rows[0]["tags"] == ["urgent"]


def test_list_json_carries_empty_tags_for_untagged(kanban_home, capsys):
    _run(["create", "Plain task", "--triage"])
    capsys.readouterr()
    assert _run(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["tags"] == []


def test_kanban_tag_subcommand_spec(kanban_home, capsys):
    _seed_tags("urgent", "night")
    _run(["create", "T", "--triage", "--tags", "urgent"])
    capsys.readouterr()
    with kb.connect_closing() as conn:
        task_id = kb.list_tasks(conn)[0].id
    assert _run(["tag", task_id, "+night,-urgent"]) == 0
    board = kb.get_current_board()
    with tdb.connect_closing() as conn:
        assert tdb.tags_for_entity(conn, "task", tdb.task_key(board, task_id)) == [
            "night"
        ]


def test_kanban_tag_unknown_task_errors(kanban_home, capsys):
    _seed_tags("urgent")
    assert _run(["tag", "t_nope", "+urgent"]) == 2
    assert "unknown task" in capsys.readouterr().err


def test_filter_unknown_tag_errors(kanban_home, capsys):
    assert _run(["list", "--tag", "nope"]) == 2
    assert "unknown tag" in capsys.readouterr().err


def test_create_with_unknown_tag_errors(kanban_home, capsys):
    assert _run(["create", "T", "--triage", "--tags", "nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown tag" in err and "hermes tag create nope" in err


def test_task_tags_are_board_scoped(kanban_home, capsys):
    """Same task id on another board must not inherit the tag."""
    _seed_tags("urgent")
    _run(["create", "T", "--triage", "--tags", "urgent"])
    capsys.readouterr()
    with kb.connect_closing() as conn:
        task_id = kb.list_tasks(conn)[0].id
    with tdb.connect_closing() as conn:
        assert tdb.tags_for_entity(conn, "task", tdb.task_key("other", task_id)) == []
