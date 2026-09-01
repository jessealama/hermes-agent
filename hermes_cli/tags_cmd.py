"""``hermes tag`` — manage the per-profile tag registry (issue #100285)."""

from __future__ import annotations

import argparse
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
    """entity_type → callable(keys) -> existing-key subset. Filled in PR 2."""
    return {}


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
    for etype, keys in grouped.items():
        print(f"  {etype} ({len(keys)}):")
        for key in keys:
            print(f"    {key}")
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
