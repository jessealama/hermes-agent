"""CLI tests for cron job tagging (``hermes cron tag``, ``cron list --tag``).

Part 4 of the tag registry (#100285): cron jobs become taggable via
``hermes cron tag <job_id> <spec>`` and ``cron list`` gains a repeatable
``--tag`` AND-filter. jobs.json is never modified — assignments live only
in tags.db.
"""

import argparse

import pytest

from cron.jobs import create_job
from hermes_cli.cron import cron_command
from hermes_cli.main import cmd_cron
from hermes_cli.subcommands.cron import build_cron_parser
from hermes_cli import tags_db as tdb


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


def _seed_tags(*names):
    with tdb.connect_closing() as conn:
        for n in names:
            tdb.create_tag(conn, n)


def _run(argv):
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_cron_parser(subparsers, cmd_cron=cron_command)
    args = parser.parse_args(["cron", *argv])
    return cron_command(args)


def _run_via_cmd_cron(argv):
    """Drive through ``hermes_cli.main.cmd_cron`` — the real wrapper the CLI
    dispatcher calls (``args.func(args)`` in ``main()``). Regression guard
    for the wrapper dropping ``cron_command``'s return value, which used to
    make exit-2 errors (unknown tag/job) look like success (exit 0) at the
    process level even though the right message hit stderr.
    """
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_cron_parser(subparsers, cmd_cron=cmd_cron)
    args = parser.parse_args(["cron", *argv])
    return cmd_cron(args)


class TestCronTag:
    def test_tag_applies_to_job_id(self, tmp_cron_dir, capsys):
        job = create_job(prompt="x", schedule="0 3 * * *")
        _seed_tags("client-acme", "urgent")

        rc = _run(["tag", job["id"], "+client-acme,+urgent"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "+ client-acme" in out
        assert "+ urgent" in out
        with tdb.connect_closing() as conn:
            assert tdb.tags_for_entity(conn, "cron_job", job["id"]) == [
                "client-acme", "urgent"]

    def test_tag_removes_via_minus_spec(self, tmp_cron_dir, capsys):
        job = create_job(prompt="x", schedule="0 3 * * *")
        _seed_tags("client-acme", "urgent")
        _run(["tag", job["id"], "+client-acme"])
        capsys.readouterr()

        # Combine the add and remove into one spec (a bare "-name" as the
        # sole CLI token would look like an option flag to argparse).
        rc = _run(["tag", job["id"], "+urgent,-client-acme"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "- client-acme" in out
        with tdb.connect_closing() as conn:
            assert tdb.tags_for_entity(conn, "cron_job", job["id"]) == ["urgent"]

    def test_tag_no_changes_prints_message(self, tmp_cron_dir, capsys):
        job = create_job(prompt="x", schedule="0 3 * * *")
        _seed_tags("client-acme")
        _run(["tag", job["id"], "+client-acme"])
        capsys.readouterr()

        rc = _run(["tag", job["id"], "+client-acme"])

        assert rc == 0
        assert "No changes." in capsys.readouterr().out

    def test_tag_unknown_job_id_exits_2(self, tmp_cron_dir, capsys):
        _seed_tags("client-acme")

        rc = _run(["tag", "nope-not-a-job", "+client-acme"])

        assert rc == 2
        assert "unknown cron job" in capsys.readouterr().err

    def test_tag_unknown_tag_exits_2(self, tmp_cron_dir, capsys):
        job = create_job(prompt="x", schedule="0 3 * * *")

        rc = _run(["tag", job["id"], "+does-not-exist"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown tag" in err
        assert "hermes tag create" in err

    def test_jobs_json_untouched_by_tagging(self, tmp_cron_dir):
        job = create_job(prompt="x", schedule="0 3 * * *")
        _seed_tags("client-acme")

        from cron.jobs import load_jobs

        before = load_jobs()
        _run(["tag", job["id"], "+client-acme"])
        after = load_jobs()

        assert before == after


class TestCronListTagFilter:
    def test_list_tag_filter_shows_only_tagged_job(self, tmp_cron_dir, capsys):
        tagged = create_job(prompt="tagged job", schedule="0 3 * * *", name="tagged")
        untagged = create_job(prompt="plain job", schedule="0 4 * * *", name="plain")
        _seed_tags("client-acme")
        _run(["tag", tagged["id"], "+client-acme"])
        capsys.readouterr()

        rc = _run(["list", "--tag", "client-acme"])

        assert rc == 0
        out = capsys.readouterr().out
        assert tagged["id"] in out
        assert untagged["id"] not in out

    def test_list_tag_filter_is_and_across_repeats(self, tmp_cron_dir, capsys):
        both = create_job(prompt="both", schedule="0 3 * * *", name="both")
        one_only = create_job(prompt="one", schedule="0 4 * * *", name="one")
        _seed_tags("client-acme", "urgent")
        _run(["tag", both["id"], "+client-acme,+urgent"])
        _run(["tag", one_only["id"], "+client-acme"])
        capsys.readouterr()

        rc = _run(["list", "--tag", "client-acme", "--tag", "urgent"])

        assert rc == 0
        out = capsys.readouterr().out
        assert both["id"] in out
        assert one_only["id"] not in out

    def test_list_unknown_tag_exits_2(self, tmp_cron_dir, capsys):
        create_job(prompt="x", schedule="0 3 * * *")

        rc = _run(["list", "--tag", "no-such-tag"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown tag" in err

    def test_list_without_tag_still_shows_all_jobs(self, tmp_cron_dir, capsys):
        a = create_job(prompt="a", schedule="0 3 * * *", name="a")
        b = create_job(prompt="b", schedule="0 4 * * *", name="b")

        rc = _run(["list"])

        assert rc == 0
        out = capsys.readouterr().out
        assert a["id"] in out
        assert b["id"] in out


class TestCmdCronWrapperPropagatesExitCode:
    """Regression test for the wrapper boundary: ``hermes_cli.main.cmd_cron``
    must propagate ``cron_command``'s return value, not just call it and
    drop the result. ``main()``'s dispatcher (``args.func(args)``) turns a
    non-zero int return into ``sys.exit(rc)`` — if ``cmd_cron`` discards the
    return value, `hermes cron tag nope +x` (and `list --tag <unknown>`)
    print the right error to stderr but the process still exits 0.
    """

    def test_list_unknown_tag_exits_2_through_cmd_cron(self, tmp_cron_dir, capsys):
        create_job(prompt="x", schedule="0 3 * * *")

        rc = _run_via_cmd_cron(["list", "--tag", "no-such-tag"])

        assert rc == 2
        assert "unknown tag" in capsys.readouterr().err

    def test_tag_unknown_job_id_exits_2_through_cmd_cron(self, tmp_cron_dir, capsys):
        _seed_tags("client-acme")

        rc = _run_via_cmd_cron(["tag", "nope-not-a-job", "+client-acme"])

        assert rc == 2
        assert "unknown cron job" in capsys.readouterr().err
