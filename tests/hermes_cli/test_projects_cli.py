"""Tests for the `hermes project` CLI dispatch (hermes_cli/projects_cmd)."""

from __future__ import annotations

import argparse

import pytest

from hermes_cli import projects_cmd
from hermes_cli import projects_db as pdb


def _run(argv):
    """Build the project subparser, parse argv, and dispatch. Returns rc."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = projects_cmd.build_parser(sub)
    p.set_defaults(func=projects_cmd.projects_command)
    args = parser.parse_args(["project", *argv])
    return projects_cmd.projects_command(args)


def test_create_list_show(capsys, tmp_path):
    assert _run(["create", "My App", str(tmp_path), "--use"]) == 0
    out = capsys.readouterr().out
    assert "Created project" in out

    with pdb.connect_closing() as conn:
        projects = pdb.list_projects(conn)
        assert len(projects) == 1
        assert projects[0].name == "My App"
        # --use set it active.
        assert pdb.get_active_id(conn) == projects[0].id

    assert _run(["list"]) == 0
    assert "my-app" in capsys.readouterr().out

    assert _run(["show", "my-app"]) == 0
    assert "My App" in capsys.readouterr().out




def test_rename_and_archive(tmp_path):
    _run(["create", "Old Name", str(tmp_path)])
    assert _run(["rename", "old-name", "New Name"]) == 0
    with pdb.connect_closing() as conn:
        assert pdb.get_project(conn, "old-name").name == "New Name"

    assert _run(["archive", "old-name"]) == 0
    with pdb.connect_closing() as conn:
        assert pdb.list_projects(conn) == []
        assert len(pdb.list_projects(conn, include_archived=True)) == 1

    assert _run(["restore", "old-name"]) == 0
    with pdb.connect_closing() as conn:
        assert len(pdb.list_projects(conn)) == 1


def test_project_tagging_roundtrip(capsys):
    import hermes_cli.tags_db as tdb

    with tdb.connect_closing() as conn:
        tdb.create_tag(conn, "acme")
        tdb.create_tag(conn, "urgent")
    _run(["create", "Foo", "--tags", "acme"])
    _run(["create", "Bar"])
    capsys.readouterr()
    assert _run(["list", "--tag", "acme"]) == 0
    out = capsys.readouterr().out
    assert "Foo" in out and "Bar" not in out
    # +/- spec via the tag subcommand
    assert _run(["tag", "foo", "+urgent,-acme"]) == 0
    assert _run(["list", "--tag", "urgent"]) == 0
    assert "Foo" in capsys.readouterr().out


def test_project_create_unknown_tag_fails(capsys):
    assert _run(["create", "Baz", "--tags", "nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown tag" in err and "hermes tag create nope" in err




