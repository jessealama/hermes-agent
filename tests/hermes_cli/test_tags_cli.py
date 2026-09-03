import argparse

import hermes_cli.tags_cmd as tags_cmd
import hermes_cli.tags_db as tdb


def _run(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = tags_cmd.build_parser(sub)
    p.set_defaults(func=tags_cmd.tags_command)
    args = parser.parse_args(["tag", *argv])
    return tags_cmd.tags_command(args)


def test_create_list_roundtrip(capsys):
    assert _run(["create", "Client Acme", "--color", "#e11d48",
                 "--description", "Acme work"]) == 0
    assert _run(["list"]) == 0
    out = capsys.readouterr().out
    assert "client-acme" in out and "#e11d48" in out


def test_create_invalid_name_exits_2(capsys):
    assert _run(["create", "client: acme"]) == 2
    assert "invalid" in capsys.readouterr().err


def test_rename_and_collision(capsys):
    _run(["create", "client-acme"])
    _run(["create", "urgent"])
    assert _run(["rename", "client-acme", "acme"]) == 0
    assert _run(["rename", "acme", "urgent"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_delete_requires_force_when_assigned(capsys, monkeypatch):
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "project", "p_1", "+acme")
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert _run(["delete", "acme"]) == 1          # declined
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert _run(["delete", "acme"]) == 0          # confirmed
    with tdb.connect_closing() as conn:
        assert tdb.get_tag(conn, "acme") is None


def test_delete_force_skips_prompt():
    _run(["create", "acme"])
    assert _run(["delete", "acme", "--force"]) == 0


def test_show_groups_by_type(capsys):
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "project", "p_1", "+acme")
        tdb.apply_spec(conn, "board", "b", "+acme")
    assert _run(["show", "acme"]) == 0
    out = capsys.readouterr().out
    assert "project" in out and "p_1" in out and "board" in out


def test_prune_drops_dead_projects(capsys):
    import hermes_cli.projects_db as pdb

    _run(["create", "acme"])
    with pdb.connect_closing() as conn:
        live_id = pdb.create_project(conn, name="Live")
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "project", live_id, "+acme")
        tdb.apply_spec(conn, "project", "p_deadbeef", "+acme")
    capsys.readouterr()
    assert _run(["prune"]) == 0
    assert "1 project" in capsys.readouterr().out
    with tdb.connect_closing() as conn:
        assert tdb.entities_for_tag(conn, "acme") == {"project": [live_id]}


def test_prune_drops_dead_boards_tasks_sessions_and_cron(capsys):
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "board", "ghost-board", "+acme")
        tdb.apply_spec(conn, "task", "ghost-board/t_1", "+acme")
        tdb.apply_spec(conn, "session", "sess_nope", "+acme")
        tdb.apply_spec(conn, "cron_job", "job_nope", "+acme")
    capsys.readouterr()
    assert _run(["prune"]) == 0
    with tdb.connect_closing() as conn:
        assert tdb.entities_for_tag(conn, "acme") == {}


def test_prune_keeps_live_boards_and_tasks(capsys):
    """The board/task resolvers must recognise real rows, not just absence."""
    import hermes_cli.kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.create_board("live-board")
    with kb.connect_closing(board="live-board") as conn:
        task_id = kb.create_task(conn, title="Live task", created_by="test")
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "board", "live-board", "+acme")
        tdb.apply_spec(conn, "task", tdb.task_key("live-board", task_id), "+acme")
        tdb.apply_spec(conn, "task", tdb.task_key("live-board", "t_gone"), "+acme")
    capsys.readouterr()
    assert _run(["prune"]) == 0
    with tdb.connect_closing() as conn:
        assert tdb.entities_for_tag(conn, "acme") == {
            "board": ["live-board"],
            "task": [tdb.task_key("live-board", task_id)],
        }


def test_prune_keeps_live_sessions(capsys):
    """The session resolver must recognise a real row, not just a missing DB."""
    from hermes_state import SessionDB

    db = SessionDB()
    db.create_session("sess_live", source="cli")
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "session", "sess_live", "+acme")
        tdb.apply_spec(conn, "session", "sess_gone", "+acme")
    capsys.readouterr()
    assert _run(["prune"]) == 0
    with tdb.connect_closing() as conn:
        assert tdb.entities_for_tag(conn, "acme") == {"session": ["sess_live"]}


def test_prune_keeps_live_cron_jobs(capsys):
    """The cron resolver must recognise a real job, not just an empty store."""
    from cron.jobs import create_job

    job = create_job(prompt="x", schedule="0 3 * * *")
    _run(["create", "acme"])
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "cron_job", job["id"], "+acme")
        tdb.apply_spec(conn, "cron_job", "job_gone", "+acme")
    capsys.readouterr()
    assert _run(["prune"]) == 0
    with tdb.connect_closing() as conn:
        assert tdb.entities_for_tag(conn, "acme") == {"cron_job": [job["id"]]}


def test_prune_reports_nothing_to_do(capsys):
    _run(["create", "acme"])
    capsys.readouterr()
    assert _run(["prune"]) == 0
    assert "Nothing to prune" in capsys.readouterr().out


def test_show_marks_missing_entities(capsys):
    import hermes_cli.projects_db as pdb

    _run(["create", "acme"])
    with pdb.connect_closing() as conn:
        live_id = pdb.create_project(conn, name="Live")
    with tdb.connect_closing() as conn:
        tdb.apply_spec(conn, "project", live_id, "+acme")
        tdb.apply_spec(conn, "project", "p_deadbeef", "+acme")
    capsys.readouterr()
    assert _run(["show", "acme"]) == 0
    lines = {
        line.strip().split("  ")[0]: line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("    ")
    }
    assert "missing" not in lines[live_id]
    assert "hermes tag prune" in lines["p_deadbeef"]
