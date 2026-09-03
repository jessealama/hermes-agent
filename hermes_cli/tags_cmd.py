"""``hermes tag`` — manage the per-profile tag registry (issue #100285)."""

from __future__ import annotations

import argparse
import functools
import sys

from hermes_cli import tags_db


def build_parser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = parent_subparsers.add_parser(
        "tag", help="Manage tags (shared across projects, boards, tasks, sessions, cron)")
    sub = parser.add_subparsers(dest="tag_action")

    p_create = sub.add_parser("create", help="Create a tag")
    p_create.add_argument("name")
    p_create.add_argument("--color", help="Optional hex color (#rgb or #rrggbb)")
    p_create.add_argument("--description")

    sub.add_parser("list", aliases=["ls"], help="List tags with usage counts")

    p_show = sub.add_parser("show", help="Everything carrying a tag, by entity type")
    p_show.add_argument("name")

    p_rename = sub.add_parser("rename", help="Rename a tag everywhere")
    p_rename.add_argument("old_name")
    p_rename.add_argument("new_name")

    p_delete = sub.add_parser("delete", aliases=["rm"], help="Delete a tag, detaching it everywhere")
    p_delete.add_argument("name")
    p_delete.add_argument("--force", action="store_true", help="Skip confirmation")

    sub.add_parser("prune", help="Drop assignments whose entity no longer exists")

    parser.set_defaults(_tag_parser=parser)
    return parser


def _resolvers() -> dict:
    """entity_type → callable(keys) -> the subset that still exists.

    Assignments reference entities by key across DB boundaries, so nothing
    enforces referential integrity — these liveness probes are how ``prune``
    and ``show`` tell a real entity from a leftover. Every import is local:
    ``hermes tag create`` must not pay for the kanban / state / cron stacks.
    """

    def _keep_all_on_error(fn):
        """Never prune on doubt.

        A store that is momentarily unreadable (locked, permissions, a
        half-written file) must not be read as "none of these exist" —
        that would delete assignments the user never asked to lose.
        Report every key as live and let the next prune do the work.
        """

        @functools.wraps(fn)
        def wrapper(keys):
            try:
                return fn(keys)
            except Exception:
                return set(keys)

        return wrapper

    @_keep_all_on_error
    def projects(keys):
        import hermes_cli.projects_db as pdb

        with pdb.connect_closing() as conn:
            return {k for k in keys if pdb.get_project(conn, k) is not None}

    @_keep_all_on_error
    def boards(keys):
        import hermes_cli.kanban_db as kb

        return {k for k in keys if kb.board_exists(k)}

    @_keep_all_on_error
    def tasks(keys):
        import hermes_cli.kanban_db as kb

        by_board: dict = {}
        for key in keys:
            slug, _, task_id = key.partition("/")
            by_board.setdefault(slug, []).append(task_id)
        live: set = set()
        for slug, ids in by_board.items():
            # A gone board takes its tasks with it — no DB to open.
            if not slug or not kb.board_exists(slug):
                continue
            with kb.connect_closing(board=slug) as conn:
                for task_id in ids:
                    if kb.get_task(conn, task_id) is not None:
                        live.add(f"{slug}/{task_id}")
        return live

    @_keep_all_on_error
    def sessions(keys):
        from hermes_state import SessionDB, _default_db_path

        # Check the path before constructing: SessionDB opens the file in
        # its constructor, and a read-only open of a missing state.db
        # raises rather than returning an empty DB. "No state.db" is a
        # definite answer (no sessions exist), not the unreadable-store
        # case the decorator guards against.
        if not _default_db_path().exists():
            return set()
        db = SessionDB(read_only=True)
        # get_session() is a bare `WHERE id = ?` — archived and hidden
        # sessions still resolve, which is what "still exists" means here.
        return {k for k in keys if db.get_session(k) is not None}

    @_keep_all_on_error
    def cron_jobs(keys):
        from cron.jobs import list_jobs

        ids = {job["id"] for job in list_jobs(include_disabled=True)}
        return {k for k in keys if k in ids}

    return {
        "project": projects,
        "board": boards,
        "task": tasks,
        "session": sessions,
        "cron_job": cron_jobs,
    }


def _cmd_create(args) -> int:
    with tags_db.connect_closing() as conn:
        tag = tags_db.create_tag(conn, args.name, color=args.color,
                                 description=args.description)
    print(f"Created tag {tag.name}")
    return 0


def _cmd_list(args) -> int:
    with tags_db.connect_closing() as conn:
        tags = tags_db.list_tags(conn)
        if not tags:
            print("No tags. Create one with: hermes tag create <name>")
            return 0
        for tag in tags:
            counts = tags_db.assignment_counts(conn, tag.name)
            total = sum(counts.values())
            bits = [tag.name]
            if tag.color:
                bits.append(tag.color)
            bits.append(f"{total} assignment{'s' if total != 1 else ''}")
            if tag.description:
                bits.append(tag.description)
            print("  ".join(bits))
    return 0


def _cmd_show(args) -> int:
    with tags_db.connect_closing() as conn:
        tag = tags_db.get_tag(conn, args.name)
        if tag is None:
            print(f"unknown tag {args.name!r}", file=sys.stderr)
            return 2
        grouped = tags_db.entities_for_tag(conn, tag.name)
    header = tag.name + (f"  {tag.color}" if tag.color else "")
    print(header)
    if tag.description:
        print(f"  {tag.description}")
    if not grouped:
        print("  (not assigned to anything)")
    resolvers = _resolvers()
    for etype, keys in grouped.items():
        # Mark leftovers rather than hiding them: a key that no longer
        # resolves is exactly what the user needs to see to know why a
        # count looks wrong, and what to run about it.
        try:
            live = resolvers[etype](keys) if etype in resolvers else set(keys)
        except Exception:
            live = set(keys)
        print(f"  {etype} ({len(keys)}):")
        for key in keys:
            suffix = "" if key in live else "  (missing — run: hermes tag prune)"
            print(f"    {key}{suffix}")
    return 0


def _cmd_rename(args) -> int:
    with tags_db.connect_closing() as conn:
        tag = tags_db.rename_tag(conn, args.old_name, args.new_name)
    print(f"Renamed to {tag.name} (propagates everywhere)")
    return 0


def _cmd_delete(args) -> int:
    with tags_db.connect_closing() as conn:
        counts = tags_db.assignment_counts(conn, args.name)
        if counts and not args.force:
            summary = ", ".join(f"{n} {t}{'s' if n != 1 else ''}"
                                for t, n in sorted(counts.items()))
            print(f"'{tags_db.normalize_tag_name(args.name)}' is attached to: {summary}")
            answer = input("Delete and detach everywhere? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 1
        tags_db.delete_tag(conn, args.name)
    print("Deleted.")
    return 0


def _cmd_prune(args) -> int:
    with tags_db.connect_closing() as conn:
        deleted = tags_db.prune(conn, _resolvers())
    if not deleted:
        print("Nothing to prune.")
        return 0
    summary = ", ".join(f"{n} {t}{'s' if n != 1 else ''}"
                        for t, n in sorted(deleted.items()))
    print(f"Removed stale assignments: {summary}")
    return 0


def tags_command(args: argparse.Namespace) -> int:
    action = getattr(args, "tag_action", None)
    if action is None:
        parser = getattr(args, "_tag_parser", None)
        if parser is not None:
            parser.print_help()
        return 2
    handlers = {
        "create": _cmd_create,
        "list": _cmd_list,
        "ls": _cmd_list,
        "show": _cmd_show,
        "rename": _cmd_rename,
        "delete": _cmd_delete,
        "rm": _cmd_delete,
        "prune": _cmd_prune,
    }
    handler = handlers.get(action)
    if handler is None:
        print(f"Unknown tag action: {action}", file=sys.stderr)
        return 2
    try:
        return handler(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
