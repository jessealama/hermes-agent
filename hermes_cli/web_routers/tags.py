"""Read-only tag registry endpoints (issue #100285, PR 5).

Serves the active process profile's tags.db (the ``tags_db.connect()``
default path); per-profile query params are a follow-up.
"""

from fastapi import APIRouter, HTTPException

from hermes_cli import tags_db

router = APIRouter()


@router.get("/api/tags")
def list_tags_endpoint():
    with tags_db.connect_closing() as conn:
        out = []
        for tag in tags_db.list_tags(conn):
            d = tag.to_dict()
            d["counts"] = tags_db.assignment_counts(conn, tag.name)
            out.append(d)
    return {"tags": out}


@router.get("/api/tags/{name}")
def show_tag_endpoint(name: str):
    with tags_db.connect_closing() as conn:
        try:
            tag = tags_db.get_tag(conn, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if tag is None:
            raise HTTPException(status_code=404, detail=f"unknown tag {name!r}")
        entities = tags_db.entities_for_tag(conn, tag.name)
    return {"tag": tag.to_dict(), "entities": entities}
