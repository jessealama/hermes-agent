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
