"""Tests for the PCO client + resolution logic.

We use `httpx.MockTransport` to intercept requests and return canned
JSON-API responses, so we never touch the real Planning Center API.

The main correctness traps in this code are:
  - Building the right `/open` URL parent path for each attachment based on
    where it's attached (Key vs Arrangement vs Song).
  - Preferring Key-level chord matches over Arrangement-level over Song-level.
  - Handling capo variants (2+ chord-named attachments on the same Key) as
    ambiguous.
  - Pagination via the `links.next` URL.
"""

from __future__ import annotations

import httpx
import pco as pcomod
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_client(handler) -> pcomod.PCOClient:
    """Build a PCOClient backed by a MockTransport. Routes every request
    through `handler(request) -> httpx.Response`."""
    config = pcomod.PCOConfig(application_id="app", secret="secret", default_service_type_id=None)
    client = pcomod.PCOClient(config)
    client.client = httpx.Client(
        base_url=pcomod.PCO_BASE,
        auth=(config.application_id, config.secret),
        headers={"Accept": "application/json"},
        timeout=30.0,
        transport=httpx.MockTransport(handler),
    )
    return client


def attachment(att_id: str, filename: str, attachable_type: str, attachable_id: str) -> dict:
    return {
        "type": "Attachment",
        "id": att_id,
        "attributes": {"filename": filename, "content_type": "application/msword"},
        "relationships": {
            "attachable": {"data": {"type": attachable_type, "id": attachable_id}},
        },
    }


# ---------------------------------------------------------------------------
# is_chord_doc_attachment / is_doc_attachment
# ---------------------------------------------------------------------------


def test_chord_filter_matches_chord_named_doc():
    assert pcomod.is_chord_doc_attachment(
        attachment("1", "Holy Holy Holy - Chord.doc", "Key", "k1")
    )
    assert pcomod.is_chord_doc_attachment(attachment("2", "Song - Chord.docx", "Key", "k1"))


def test_chord_filter_case_insensitive():
    assert pcomod.is_chord_doc_attachment(attachment("1", "SONG - CHORD.DOCX", "Key", "k1"))


def test_chord_filter_rejects_non_doc_filetypes():
    assert not pcomod.is_chord_doc_attachment(attachment("1", "Song - Chord.pdf", "Key", "k1"))
    assert not pcomod.is_chord_doc_attachment(attachment("2", "Song - Chord.txt", "Key", "k1"))


def test_chord_filter_rejects_non_chord_filenames():
    assert not pcomod.is_chord_doc_attachment(
        attachment("1", "Song - Lyric.docx", "Arrangement", "a1")
    )


def test_doc_filter_matches_any_doc_or_docx():
    assert pcomod.is_doc_attachment(attachment("1", "anything.doc", "Song", "s1"))
    assert pcomod.is_doc_attachment(attachment("2", "anything.docx", "Song", "s1"))
    assert not pcomod.is_doc_attachment(attachment("3", "anything.pdf", "Song", "s1"))


# ---------------------------------------------------------------------------
# _attachable_parent_path: the function that builds the URL prefix for the
# /open action, based on where the attachment lives.
# ---------------------------------------------------------------------------


def test_parent_path_for_key_attachment():
    att = attachment("99", "Song - Chord.doc", "Key", "k42")
    path = pcomod._attachable_parent_path(att, song_id="s1", arrangement_id="a1")
    assert path == "/services/v2/songs/s1/arrangements/a1/keys/k42"


def test_parent_path_for_arrangement_attachment():
    att = attachment("99", "Song - Lyric.doc", "Arrangement", "a42")
    path = pcomod._attachable_parent_path(att, song_id="s1", arrangement_id="a1")
    assert path == "/services/v2/songs/s1/arrangements/a42"


def test_parent_path_for_song_attachment():
    att = attachment("99", "Song.doc", "Song", "s42")
    path = pcomod._attachable_parent_path(att, song_id="s1", arrangement_id="a1")
    assert path == "/services/v2/songs/s42"


def test_parent_path_for_key_without_arrangement_returns_none():
    # Defensive: if we don't know the arrangement, we can't construct the
    # /open URL for a Key attachment. Caller treats this as unresolved.
    att = attachment("99", "Song - Chord.doc", "Key", "k42")
    assert pcomod._attachable_parent_path(att, song_id="s1", arrangement_id=None) is None


def test_parent_path_for_unknown_attachable_type():
    att = attachment("99", "Whatever.doc", "Mystery", "m42")
    assert pcomod._attachable_parent_path(att, song_id="s1", arrangement_id="a1") is None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_get_all_follows_next_links():
    # Two pages of two items each, then exhausted.
    pages = {
        "https://api.planningcenteronline.com/services/v2/things": {
            "data": [{"id": "1"}, {"id": "2"}],
            "links": {"next": "https://api.planningcenteronline.com/services/v2/things?offset=2"},
        },
        "https://api.planningcenteronline.com/services/v2/things?offset=2": {
            "data": [{"id": "3"}, {"id": "4"}],
            "links": {},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[str(request.url)])

    client = make_client(handler)
    try:
        records = [rec for rec, _payload in client.get_all("/services/v2/things")]
    finally:
        client.close()
    assert [r["id"] for r in records] == ["1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# Full resolve_plan_songs: simulate a small plan and assert the right
# attachments are picked, with the right parent paths.
# ---------------------------------------------------------------------------


def _build_resolve_handler():
    """Build a handler that simulates a 3-song plan:

    - Song "Auto" has exactly one Key-level chord attachment (auto-resolved)
    - Song "Ambiguous" has two Key-level chord attachments (capo case)
    - Song "Lyric Only" has a Lyric attachment on its Arrangement but no chord
    """

    items_payload = {
        "data": [
            {
                "id": "i1",
                "type": "Item",
                "attributes": {"item_type": "song", "sequence": 1, "title": "Auto"},
                "relationships": {
                    "song": {"data": {"type": "Song", "id": "s1"}},
                    "arrangement": {"data": {"type": "Arrangement", "id": "a1"}},
                    "key": {"data": {"type": "Key", "id": "k1"}},
                },
            },
            {
                "id": "i2",
                "type": "Item",
                "attributes": {"item_type": "song", "sequence": 2, "title": "Ambiguous"},
                "relationships": {
                    "song": {"data": {"type": "Song", "id": "s2"}},
                    "arrangement": {"data": {"type": "Arrangement", "id": "a2"}},
                    "key": {"data": {"type": "Key", "id": "k2"}},
                },
            },
            {
                "id": "i3",
                "type": "Item",
                "attributes": {"item_type": "song", "sequence": 3, "title": "Lyric Only"},
                "relationships": {
                    "song": {"data": {"type": "Song", "id": "s3"}},
                    "arrangement": {"data": {"type": "Arrangement", "id": "a3"}},
                    "key": {"data": {"type": "Key", "id": "k3"}},
                },
            },
            # Non-song items should be skipped.
            {
                "id": "i4",
                "type": "Item",
                "attributes": {"item_type": "header", "sequence": 4, "title": "Communion"},
                "relationships": {},
            },
        ],
        "included": [
            {"type": "Song", "id": "s1", "attributes": {"title": "Auto"}},
            {"type": "Song", "id": "s2", "attributes": {"title": "Ambiguous"}},
            {"type": "Song", "id": "s3", "attributes": {"title": "Lyric Only"}},
        ],
        "links": {},
    }

    all_attachments_payload = {
        "data": [
            attachment("att-1", "Auto - Chord.doc", "Key", "k1"),
            attachment("att-2", "Ambiguous - Chord.docx", "Key", "k2"),
            attachment("att-3", "Ambiguous - Chord Capo.docx", "Key", "k2"),
            attachment("att-4", "Lyric Only - Lyric.docx", "Arrangement", "a3"),
        ],
        "links": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/items"):
            return httpx.Response(200, json=items_payload)
        if path.endswith("/all_attachments"):
            return httpx.Response(200, json=all_attachments_payload)
        return httpx.Response(404, json={"errors": [{"detail": f"unexpected {path}"}]})

    return handler


def test_resolve_plan_songs_auto_picks_single_chord():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={}
        )
    finally:
        client.close()

    by_title = {r.song_title: r for r in resolutions}
    assert set(by_title) == {"Auto", "Ambiguous", "Lyric Only"}

    auto = by_title["Auto"]
    assert auto.picked is not None
    assert auto.picked["id"] == "att-1"
    assert auto.pick_source == "auto"
    assert auto.chord_source == "Key"
    assert auto.picked_parent_path == "/services/v2/songs/s1/arrangements/a1/keys/k1"


def test_resolve_plan_songs_capo_case_is_ambiguous():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={}
        )
    finally:
        client.close()

    amb = next(r for r in resolutions if r.song_title == "Ambiguous")
    assert amb.picked is None
    assert len(amb.chord_candidates) == 2
    assert {a["id"] for a in amb.chord_candidates} == {"att-2", "att-3"}


def test_pick_resolves_capo_ambiguity():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={"s2": "att-3"}
        )
    finally:
        client.close()

    amb = next(r for r in resolutions if r.song_title == "Ambiguous")
    assert amb.picked is not None
    assert amb.picked["id"] == "att-3"
    assert amb.pick_source == "pick"
    # Parent path uses the Key the user picked on.
    assert amb.picked_parent_path == "/services/v2/songs/s2/arrangements/a2/keys/k2"


def test_lyric_only_song_remains_unresolved_but_lists_doc_candidates():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={}
        )
    finally:
        client.close()

    lyric = next(r for r in resolutions if r.song_title == "Lyric Only")
    assert lyric.picked is None
    assert lyric.chord_candidates == []  # no chord-named files
    # The lyric file should still surface in the fallback doc list (so the
    # agent can show it to the user as a possible --pick override).
    assert any(a["id"] == "att-4" for a in lyric.doc_candidates)


def test_non_song_items_are_skipped():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={}
        )
    finally:
        client.close()

    # The "Communion" header item must not appear in resolutions.
    assert "Communion" not in {r.song_title for r in resolutions}
    assert all(r.song_id in {"s1", "s2", "s3"} for r in resolutions)


def test_resolution_order_matches_plan_sequence():
    client = make_client(_build_resolve_handler())
    try:
        resolutions = pcomod.resolve_plan_songs(
            client, service_type_id="st1", plan_id="p1", picks={}
        )
    finally:
        client.close()

    assert [r.song_title for r in resolutions] == ["Auto", "Ambiguous", "Lyric Only"]
    assert [r.sequence for r in resolutions] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_http_error_raises_pco_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"detail": "unauthorized"}]})

    client = make_client(handler)
    try:
        with pytest.raises(pcomod.PCOError) as exc:
            client.get("/services/v2/service_types")
        assert "401" in str(exc.value)
    finally:
        client.close()
