"""Tests for the read-only tag registry dashboard API (issue #100285, PR 5)."""

import pytest
from fastapi import HTTPException

import hermes_cli.tags_db as tdb
from hermes_cli.web_routers import tags as tags_api


def test_list_tags_endpoint():
    with tdb.connect_closing() as conn:
        tdb.create_tag(conn, "acme", color="#e11d48")
        tdb.apply_spec(conn, "project", "p_1", "+acme")
    body = tags_api.list_tags_endpoint()
    [tag] = body["tags"]
    assert tag["name"] == "acme"
    assert tag["color"] == "#e11d48"
    assert tag["counts"] == {"project": 1}


def test_list_tags_endpoint_empty():
    assert tags_api.list_tags_endpoint() == {"tags": []}


def test_show_tag_endpoint():
    with tdb.connect_closing() as conn:
        tdb.create_tag(conn, "acme")
        tdb.apply_spec(conn, "project", "p_1", "+acme")
        tdb.apply_spec(conn, "session", "sess_1", "+acme")
    body = tags_api.show_tag_endpoint("acme")
    assert body["tag"]["name"] == "acme"
    assert body["entities"] == {"project": ["p_1"], "session": ["sess_1"]}


def test_show_tag_endpoint_404():
    with pytest.raises(HTTPException) as exc:
        tags_api.show_tag_endpoint("nope")
    assert exc.value.status_code == 404


def test_show_tag_endpoint_invalid_name_400():
    with pytest.raises(HTTPException) as exc:
        tags_api.show_tag_endpoint("not a valid, name!")
    assert exc.value.status_code == 400
