"""Per-profile tag registry + generic entity tag assignments.

One user-curated vocabulary (issue #100285) shared by every taggable entity
type. Assignments reference entities *by key only* — the entities live in
other stores (projects.db, per-board kanban.db, state.db, cron/jobs.json),
so there is no cross-DB FK to enforce; read paths resolve keys lazily and
``prune`` clears the leftovers.

Scope: **per-profile**, stored at ``$HERMES_HOME/tags.db`` like projects /
sessions / cron. Kanban boards are root-anchored and shared across profiles,
so board/task tags are a per-profile view — an accepted trade-off.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from hermes_constants import get_hermes_home
from hermes_state_common import escape_like

VALID_ENTITY_TYPES: tuple[str, ...] = (
    "project", "board", "task", "session", "cron_job",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT,
    description TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_key  TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (tag_id, entity_type, entity_key)
);

CREATE INDEX IF NOT EXISTS idx_tag_assignments_entity
    ON tag_assignments(entity_type, entity_key);
"""

# First char must be a word char: names starting with '-'/'+' would be
# ambiguous inside the "+add,-remove" assignment spec syntax.
_NAME_RE = re.compile(r"^\w[\w-]{0,63}$", re.UNICODE)
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def tags_db_path() -> Path:
    """The per-profile tags DB path (``$HERMES_HOME/tags.db``)."""
    return get_hermes_home() / "tags.db"


def normalize_tag_name(raw: str) -> str:
    """Trim, casefold, dash inner whitespace; reject anything else."""
    s = re.sub(r"\s+", "-", str(raw or "").strip().casefold())
    if not s:
        raise ValueError("tag name is empty")
    if not _NAME_RE.match(s):
        bad = sorted({c for c in s if not re.match(r"[\w-]", c, re.UNICODE)})
        if bad:
            offenders = " ".join(f"'{c}'" for c in bad)
            raise ValueError(f"invalid characters {offenders} in tag name {raw!r}")
        raise ValueError(
            f"invalid tag name {raw!r}: 1-64 word characters or '-', "
            f"not starting with '-' or '+'"
        )
    return s


def validate_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    c = str(color).strip()
    if not _COLOR_RE.match(c):
        raise ValueError(f"invalid color {color!r}: must be #rgb or #rrggbb")
    return c


def task_key(board_slug: str, task_id: str) -> str:
    """Composite key for tasks — ids are only unique per board."""
    return f"{board_slug}/{task_id}"


def _now() -> int:
    return int(time.time())


_INITIALIZED_PATHS: set[str] = set()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialize if needed) the per-profile tags DB."""
    path = db_path if db_path is not None else tags_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="tags.db")
        conn.execute("PRAGMA foreign_keys=ON")
        if resolved not in _INITIALIZED_PATHS:
            conn.executescript(SCHEMA_SQL)
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Optional[Path] = None):
    """Open a tags DB connection and guarantee close (mirrors projects_db)."""
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------


@dataclass
class Tag:
    id: int
    name: str
    created_at: int
    color: Optional[str] = None
    description: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "description": self.description,
            "created_at": self.created_at,
        }


def _row_to_tag(row: sqlite3.Row) -> Tag:
    return Tag(id=row["id"], name=row["name"], created_at=row["created_at"],
               color=row["color"], description=row["description"])


def create_tag(conn: sqlite3.Connection, name: str, *,
               color: Optional[str] = None,
               description: Optional[str] = None) -> Tag:
    norm = normalize_tag_name(name)
    col = validate_color(color)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO tags (name, color, description, created_at) "
                "VALUES (?, ?, ?, ?)",
                (norm, col, description, _now()),
            )
    except sqlite3.IntegrityError:
        raise ValueError(f"tag {norm!r} already exists")
    tag = get_tag(conn, norm)
    assert tag is not None and tag.id == cur.lastrowid
    return tag


def get_tag(conn: sqlite3.Connection, name: str) -> Optional[Tag]:
    norm = normalize_tag_name(name)
    row = conn.execute("SELECT * FROM tags WHERE name = ?", (norm,)).fetchone()
    return _row_to_tag(row) if row else None


def _require_tag(conn: sqlite3.Connection, name: str) -> Tag:
    tag = get_tag(conn, name)
    if tag is None:
        raise ValueError(f"unknown tag {normalize_tag_name(name)!r}")
    return tag


def list_tags(conn: sqlite3.Connection) -> List[Tag]:
    rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
    return [_row_to_tag(r) for r in rows]


def rename_tag(conn: sqlite3.Connection, old_name: str, new_name: str) -> Tag:
    tag = _require_tag(conn, old_name)
    norm_new = normalize_tag_name(new_name)
    try:
        with conn:
            conn.execute("UPDATE tags SET name = ? WHERE id = ?",
                         (norm_new, tag.id))
    except sqlite3.IntegrityError:
        raise ValueError(f"tag {norm_new!r} already exists")
    return Tag(id=tag.id, name=norm_new, created_at=tag.created_at,
               color=tag.color, description=tag.description)


def assignment_counts(conn: sqlite3.Connection, name: str) -> Dict[str, int]:
    tag = _require_tag(conn, name)
    rows = conn.execute(
        "SELECT entity_type, COUNT(*) AS n FROM tag_assignments "
        "WHERE tag_id = ? GROUP BY entity_type", (tag.id,)).fetchall()
    return {r["entity_type"]: r["n"] for r in rows}


def delete_tag(conn: sqlite3.Connection, name: str) -> Dict[str, int]:
    tag = _require_tag(conn, name)
    counts = assignment_counts(conn, tag.name)
    with conn:
        conn.execute("DELETE FROM tag_assignments WHERE tag_id = ?", (tag.id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag.id,))
    return counts


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


def _check_entity_type(entity_type: str) -> str:
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"invalid entity type {entity_type!r} "
            f"(expected one of {', '.join(VALID_ENTITY_TYPES)})")
    return entity_type


def parse_tag_spec(spec: str) -> Tuple[List[str], List[str]]:
    """Parse ``"+a,-b,c"`` → (adds, removes). Bare names are adds."""
    adds: List[str] = []
    removes: List[str] = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            removes.append(normalize_tag_name(part[1:]))
        else:
            adds.append(normalize_tag_name(part.removeprefix("+")))
    if not adds and not removes:
        raise ValueError("empty tag spec")
    both = set(adds) & set(removes)
    if both:
        raise ValueError(f"tag(s) both added and removed: {', '.join(sorted(both))}")
    return adds, removes


def _resolve_known(conn: sqlite3.Connection, names: Iterable[str]) -> Dict[str, int]:
    """Names → ids, erroring on the first unknown (strict registry)."""
    out: Dict[str, int] = {}
    for name in names:
        tag = get_tag(conn, name)
        if tag is None:
            raise ValueError(
                f"unknown tag {name!r}\n"
                f"  create it first:  hermes tag create {name}")
        out[tag.name] = tag.id
    return out


def apply_spec(conn: sqlite3.Connection, entity_type: str, entity_key: str,
               spec: str) -> Tuple[List[str], List[str]]:
    _check_entity_type(entity_type)
    adds, removes = parse_tag_spec(spec)
    ids = _resolve_known(conn, adds + removes)
    added: List[str] = []
    removed: List[str] = []
    with conn:
        for name in adds:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tag_assignments "
                "(tag_id, entity_type, entity_key, created_at) VALUES (?, ?, ?, ?)",
                (ids[name], entity_type, entity_key, _now()))
            if cur.rowcount:
                added.append(name)
        for name in removes:
            cur = conn.execute(
                "DELETE FROM tag_assignments WHERE tag_id = ? "
                "AND entity_type = ? AND entity_key = ?",
                (ids[name], entity_type, entity_key))
            if cur.rowcount:
                removed.append(name)
    return added, removed


def tags_for_entity(conn: sqlite3.Connection, entity_type: str,
                    entity_key: str) -> List[str]:
    _check_entity_type(entity_type)
    rows = conn.execute(
        "SELECT t.name FROM tag_assignments a JOIN tags t ON t.id = a.tag_id "
        "WHERE a.entity_type = ? AND a.entity_key = ? ORDER BY t.name",
        (entity_type, entity_key)).fetchall()
    return [r["name"] for r in rows]


def tags_for_entities(conn: sqlite3.Connection, entity_type: str,
                      keys: Iterable[str]) -> Dict[str, List[str]]:
    _check_entity_type(entity_type)
    keys = list(keys)
    out: Dict[str, List[str]] = {}
    CHUNK = 500  # SQLite default max host parameters is 999
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT a.entity_key, t.name FROM tag_assignments a "
            f"JOIN tags t ON t.id = a.tag_id "
            f"WHERE a.entity_type = ? AND a.entity_key IN ({marks}) "
            f"ORDER BY t.name",
            (entity_type, *chunk)).fetchall()
        for r in rows:
            out.setdefault(r["entity_key"], []).append(r["name"])
    return out


def entity_keys_for_tags(conn: sqlite3.Connection, entity_type: str,
                         names: Iterable[str]) -> Set[str]:
    _check_entity_type(entity_type)
    ids = _resolve_known(conn, names)
    if not ids:
        raise ValueError("no tag names given")
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT entity_key FROM tag_assignments "
        f"WHERE entity_type = ? AND tag_id IN ({marks}) "
        f"GROUP BY entity_key HAVING COUNT(DISTINCT tag_id) = ?",
        (entity_type, *ids.values(), len(ids))).fetchall()
    return {r["entity_key"] for r in rows}


def entities_for_tag(conn: sqlite3.Connection, name: str) -> Dict[str, List[str]]:
    tag = _require_tag(conn, name)
    rows = conn.execute(
        "SELECT entity_type, entity_key FROM tag_assignments "
        "WHERE tag_id = ? ORDER BY entity_type, entity_key", (tag.id,)).fetchall()
    out: Dict[str, List[str]] = {}
    for r in rows:
        out.setdefault(r["entity_type"], []).append(r["entity_key"])
    return out


def detach_board(conn: sqlite3.Connection, board_slug: str) -> int:
    """Drop all assignments for a board + its tasks (slug-reuse guard).

    Task keys are ``<slug>/<task_id>``, matched with a prefix LIKE. Slugs
    may contain ``_``, which LIKE treats as a single-character wildcard, so
    the slug is escaped — otherwise removing ``my_board`` would also strip
    the tags of ``my-board`` and ``myxboard``.
    """
    with conn:
        cur = conn.execute(
            "DELETE FROM tag_assignments WHERE "
            "(entity_type = 'board' AND entity_key = ?) OR "
            "(entity_type = 'task' AND entity_key LIKE ? ESCAPE '\\')",
            (board_slug, escape_like(board_slug) + "/%"))
    return cur.rowcount


def prune(conn: sqlite3.Connection, resolvers) -> Dict[str, int]:
    """Delete assignments whose entity no longer exists.

    ``resolvers`` maps entity_type → callable(keys) returning the subset of
    keys that still exist in that entity's home store. Types without a
    resolver are left untouched.
    """
    deleted: Dict[str, int] = {}
    for etype, resolve in resolvers.items():
        _check_entity_type(etype)
        rows = conn.execute(
            "SELECT DISTINCT entity_key FROM tag_assignments "
            "WHERE entity_type = ?", (etype,)).fetchall()
        keys = [r["entity_key"] for r in rows]
        if not keys:
            continue
        live = set(resolve(keys))
        dead = [k for k in keys if k not in live]
        if not dead:
            continue
        with conn:
            for key in dead:
                conn.execute(
                    "DELETE FROM tag_assignments "
                    "WHERE entity_type = ? AND entity_key = ?", (etype, key))
        deleted[etype] = len(dead)
    return deleted
