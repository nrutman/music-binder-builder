"""
pco.py — Planning Center Services API client + resolution helpers.

Used by build_binder.py for the `pco-resolve` and `pco-build` subcommands.
Authenticates with a Personal Access Token (HTTP Basic), walks a plan to
extract song items in order, finds each song's chord-sheet attachments, and
downloads them via the documented two-step "open" action.

The HTTP client is built on `httpx`, which is declared in the PEP 723 inline
header of build_binder.py and installed automatically by `uv` on first run.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    import httpx
except ModuleNotFoundError as e:
    if e.name == "httpx":
        sys.stderr.write(
            "MISSING_DEPENDENCY: httpx was not installed automatically.\n"
            "\n"
            "This usually means your `uv` is too old to read PEP 723 inline script\n"
            "dependencies (need uv >= 0.4.4). Check and upgrade:\n"
            "\n"
            "    uv --version\n"
            "    brew upgrade uv\n"
        )
        sys.exit(3)
    raise


PCO_BASE = "https://api.planningcenteronline.com"


class PCOError(Exception):
    pass


# ---------------------------------------------------------------------------
# Config + client
# ---------------------------------------------------------------------------


@dataclass
class PCOConfig:
    application_id: str
    secret: str
    default_service_type_id: str | None


def load_pco_config(env: dict[str, str]) -> PCOConfig:
    """Build a PCOConfig from the merged env dict (`.env` + `.env.local`).
    Exits with MISSING_CONFIG (exit code 2) if either credential is unset."""
    app_id = env.get("PCO_APPLICATION_ID", "")
    secret = env.get("PCO_SECRET", "")
    missing: list[str] = []
    if not app_id:
        missing.append("PCO_APPLICATION_ID")
    if not secret:
        missing.append("PCO_SECRET")
    if missing:
        sys.stderr.write("MISSING_CONFIG: Planning Center credentials are not set in .env.local:\n")
        for k in missing:
            sys.stderr.write(f"  - {k}\n")
        sys.stderr.write(
            "\nGenerate a Personal Access Token at:\n"
            "    https://api.planningcenteronline.com/oauth/applications\n"
            "and add both halves to .env.local:\n"
            "\n"
            "    PCO_APPLICATION_ID=<your application id>\n"
            "    PCO_SECRET=<your secret>\n"
        )
        sys.exit(2)
    return PCOConfig(
        application_id=app_id,
        secret=secret,
        default_service_type_id=env.get("PCO_DEFAULT_SERVICE_TYPE_ID", "") or None,
    )


class PCOClient:
    """Thin httpx wrapper for the PCO Services API. Adds Basic auth, JSON
    parsing, and pagination."""

    def __init__(self, config: PCOConfig):
        self.config = config
        self.client = httpx.Client(
            base_url=PCO_BASE,
            auth=(config.application_id, config.secret),
            headers={"Accept": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PCOClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _raise_for_status(self, r: httpx.Response, method: str, url: str) -> None:
        if r.status_code >= 400:
            body = r.text[:400] if r.text else ""
            raise PCOError(f"{method} {url} -> HTTP {r.status_code}: {body}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.client.get(path, params=params)
        self._raise_for_status(r, "GET", path)
        return r.json()

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.client.post(path, json=json_body)
        self._raise_for_status(r, "POST", path)
        return r.json() if r.text else {}

    def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        """Yield (record, page_payload) tuples across all pages. The page
        payload is included so callers can access `included` and other
        top-level keys (notably needed when using `?include=...`)."""
        url: str | None = path
        first = True
        while url:
            # On follow-up pages, use the absolute URL (PCO returns the full
            # URL in links.next) and drop params.
            if first:
                r = self.client.get(url, params=params)
                first = False
            else:
                r = self.client.get(url)
            self._raise_for_status(r, "GET", url)
            payload = r.json()
            for record in payload.get("data", []):
                yield record, payload
            url = (payload.get("links") or {}).get("next")


# ---------------------------------------------------------------------------
# Service type / plan / items
# ---------------------------------------------------------------------------


def get_service_type(client: PCOClient, service_type_id: str) -> dict[str, Any]:
    return client.get(f"/services/v2/service_types/{service_type_id}")["data"]


def find_service_type(
    client: PCOClient,
    requested_id: str | None,
) -> dict[str, Any]:
    """Resolve the service type to use. Order: explicit --service-type arg,
    PCO_DEFAULT_SERVICE_TYPE_ID env var, single-service-type org.

    Exits (code 6) when there are multiple service types and none is
    selected, so the agent can show the user the list."""
    if requested_id:
        return get_service_type(client, requested_id)
    if client.config.default_service_type_id:
        return get_service_type(client, client.config.default_service_type_id)
    types = [
        record
        for record, _payload in client.get_all("/services/v2/service_types")
        if not record["attributes"].get("archived_at")
    ]
    if len(types) == 1:
        st = types[0]
        sys.stderr.write(
            f"Note: using the only service type in this org: "
            f"{st['attributes']['name']} (id={st['id']})\n"
        )
        return st
    if not types:
        sys.stderr.write("PCO_NO_SERVICE_TYPES: no active service types found in this org.\n")
        sys.exit(6)
    sys.stderr.write("PCO_AMBIGUOUS_SERVICE_TYPE: multiple service types exist:\n")
    for t in types:
        sys.stderr.write(f"  id={t['id']}  {t['attributes']['name']}\n")
    sys.stderr.write(
        "\nRe-run with --service-type <ID>, or set PCO_DEFAULT_SERVICE_TYPE_ID\n"
        "in .env.local to one of these IDs.\n"
    )
    sys.exit(6)


def find_plan_for_date(
    client: PCOClient,
    service_type_id: str,
    target_date: date,
) -> dict[str, Any]:
    """Find the plan whose sort_date falls on `target_date`. Uses the
    `after` filter and orders by sort_date ascending to bound the search.

    Exits (code 6) when zero or multiple plans match — the agent then
    surfaces the situation to the user."""
    after = (target_date - timedelta(days=1)).isoformat() + "T00:00:00Z"
    params = {
        "filter": "after",
        "after": after,
        "order": "sort_date",
        "per_page": 25,
    }
    matches: list[dict[str, Any]] = []
    iso = target_date.isoformat()
    for plan, _payload in client.get_all(
        f"/services/v2/service_types/{service_type_id}/plans",
        params=params,
    ):
        sort_date = plan["attributes"].get("sort_date", "")
        if sort_date.startswith(iso):
            matches.append(plan)
        elif sort_date and sort_date[:10] > iso:
            # Ordered ascending — we've passed the target window.
            break
    if not matches:
        sys.stderr.write(
            f"PCO_NO_PLAN_FOR_DATE: no plan found for {iso} in service type {service_type_id}.\n"
            "Double-check the date and the service type. You can also pass --plan-id\n"
            "directly if you know the plan ID.\n"
        )
        sys.exit(6)
    if len(matches) > 1:
        sys.stderr.write(f"PCO_AMBIGUOUS_PLAN: multiple plans on {iso}:\n")
        for p in matches:
            attrs = p["attributes"]
            title = attrs.get("title") or "(untitled)"
            series = attrs.get("series_title") or ""
            sys.stderr.write(f"  id={p['id']}  {title}  series={series!r}\n")
        sys.stderr.write("\nRe-run with --plan-id <ID> to pick one.\n")
        sys.exit(6)
    return matches[0]


def list_song_items(
    client: PCOClient,
    service_type_id: str,
    plan_id: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    """Return (song_items_in_sequence_order, included_by_type_id).

    `included_by_type_id` is keyed by JSON-API type ("Song", "Arrangement",
    "Key", …) and inner-keyed by id. The items list contains only items
    whose `item_type` is "song"."""
    items: list[dict[str, Any]] = []
    included: dict[str, dict[str, dict[str, Any]]] = {}
    for item, payload in client.get_all(
        f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/items",
        params={
            "include": "song,arrangement,key",
            "per_page": 100,
            "order": "sequence",
        },
    ):
        for inc in payload.get("included", []):
            included.setdefault(inc.get("type", "?"), {})[inc["id"]] = inc
        if item["attributes"].get("item_type") == "song":
            items.append(item)
    items.sort(key=lambda it: it["attributes"].get("sequence", 0))
    return items, included


def list_song_attachments(client: PCOClient, song_id: str) -> list[dict[str, Any]]:
    """List attachments directly attached to a Song record. Kept for the
    pco-doctor diagnostic; the resolver uses `list_plan_all_attachments`
    instead because chord charts typically live on Keys, not on Songs."""
    return [
        att
        for att, _payload in client.get_all(
            f"/services/v2/songs/{song_id}/attachments",
            params={"per_page": 100},
        )
    ]


def list_plan_all_attachments(
    client: PCOClient,
    service_type_id: str,
    plan_id: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Fetch every attachment hanging off a plan (regardless of which
    resource it's attached to — Song, Arrangement, Key, Item, Plan, Media)
    and group them by (attachable_type, attachable_id).

    Uses the `/all_attachments` endpoint, which is a single network call
    covering the whole plan instead of N+1 calls walking each song."""
    by_attachable: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for att, _payload in client.get_all(
        f"/services/v2/service_types/{service_type_id}/plans/{plan_id}/all_attachments",
        params={"per_page": 100},
    ):
        attachable = (att.get("relationships") or {}).get("attachable", {}).get("data") or {}
        key = (attachable.get("type") or "", attachable.get("id") or "")
        by_attachable.setdefault(key, []).append(att)
    return by_attachable


# ---------------------------------------------------------------------------
# Attachment download (two-step: POST .../open → GET signed URL)
# ---------------------------------------------------------------------------


def open_attachment_url(client: PCOClient, parent_path: str, attachment_id: str) -> str:
    """POST to the attachment's `open` action at the given parent path (e.g.
    `/services/v2/songs/{sid}/arrangements/{aid}/keys/{kid}`) and return the
    short-lived signed download URL from the AttachmentActivity response."""
    resp = client.post(f"{parent_path}/attachments/{attachment_id}/open")
    try:
        return resp["data"]["attributes"]["attachment_url"]
    except (KeyError, TypeError) as e:
        raise PCOError(
            f"PCO open action for attachment {attachment_id} returned an unexpected shape: {resp!r}"
        ) from e


def download_resolved_attachments(
    client: PCOClient,
    resolutions: list[SongResolution],
    dest_dir: Path,
) -> list[Path]:
    """Download every resolution's picked attachment to dest_dir. Uses an
    anonymous httpx client for the signed-URL GET so Basic auth isn't sent
    to the CDN. Returns the downloaded file paths in the same order as
    `resolutions`."""
    files: list[Path] = []
    with httpx.Client(timeout=60.0, follow_redirects=True) as anon:
        for r in resolutions:
            assert r.picked is not None, "download called on unresolved song"
            assert r.picked_parent_path is not None, "resolution missing parent path"
            url = open_attachment_url(client, r.picked_parent_path, r.picked["id"])
            resp = anon.get(url)
            if resp.status_code != 200:
                raise PCOError(
                    f"Failed to download attachment {r.picked['id']} "
                    f"({r.song_title!r}): HTTP {resp.status_code}"
                )
            filename = r.picked["attributes"].get("filename") or f"attachment-{r.picked['id']}"
            dest = _unique_path(dest_dir / filename, files)
            dest.write_bytes(resp.content)
            files.append(dest)
            sys.stdout.write(f"  ✓ {r.song_title} → {dest.name}\n")
    return files


def _unique_path(candidate: Path, existing: list[Path]) -> Path:
    """If `candidate` is already used (case-insensitively) in `existing`,
    append ` (2)`, ` (3)`, ... before the extension until it's unique."""
    used = {p.name.lower() for p in existing}
    if candidate.name.lower() not in used:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        attempt = candidate.with_name(f"{stem} ({counter}){suffix}")
        if attempt.name.lower() not in used:
            return attempt
        counter += 1


# ---------------------------------------------------------------------------
# Resolution: songs → chord attachments → picks
# ---------------------------------------------------------------------------


def is_chord_doc_attachment(att: dict[str, Any]) -> bool:
    """True if the attachment is a .doc/.docx with 'chord' in its filename
    or display name (case-insensitive)."""
    attrs = att.get("attributes", {})
    filename = (attrs.get("filename") or "").lower()
    if not (filename.endswith(".doc") or filename.endswith(".docx")):
        return False
    display = (attrs.get("display_name") or "").lower()
    return "chord" in filename or "chord" in display


def is_doc_attachment(att: dict[str, Any]) -> bool:
    """True if the attachment is any .doc/.docx — used as a fallback list
    when no chord-named attachments exist on a song."""
    filename = (att.get("attributes", {}).get("filename") or "").lower()
    return filename.endswith(".doc") or filename.endswith(".docx")


@dataclass
class SongResolution:
    sequence: int
    song_id: str
    song_title: str
    item_title: str
    arrangement_id: str | None  # the item's selected arrangement, if any
    key_id: str | None  # the item's selected key, if any
    chord_candidates: list[
        dict[str, Any]
    ]  # chord-named .doc/.docx attachments found at the best level
    chord_source: str | None  # "Key" | "Arrangement" | "Song" — where chord_candidates were found
    doc_candidates: list[
        dict[str, Any]
    ]  # all .doc/.docx across Song/Arr/Key (for the 0-chord fallback display)
    picked: dict[str, Any] | None  # the chosen attachment, or None if ambiguous
    pick_source: str | None  # "auto" | "pick" | None
    picked_parent_path: str | None  # parent URL path for the picked attachment's /open action


def _attachable_parent_path(
    att: dict[str, Any],
    song_id: str,
    arrangement_id: str | None,
) -> str | None:
    """Build the URL path that prefixes `/attachments/{id}/open` for `att`,
    based on the attachment's `attachable` relationship. Returns None if
    we can't construct a usable path (e.g. attachable on something we
    don't know how to address)."""
    attachable = (att.get("relationships") or {}).get("attachable", {}).get("data") or {}
    a_type = attachable.get("type")
    a_id = attachable.get("id")
    if not a_type or not a_id:
        return None
    if a_type == "Key":
        if not arrangement_id:
            return None
        return f"/services/v2/songs/{song_id}/arrangements/{arrangement_id}/keys/{a_id}"
    if a_type == "Arrangement":
        return f"/services/v2/songs/{song_id}/arrangements/{a_id}"
    if a_type == "Song":
        return f"/services/v2/songs/{a_id}"
    return None


def resolve_plan_songs(
    client: PCOClient,
    service_type_id: str,
    plan_id: str,
    picks: dict[str, str],
) -> list[SongResolution]:
    """Walk a plan's song items, find each song's chord-sheet attachment
    (preferring Key-level over Arrangement-level over Song-level), apply
    auto-resolution (exactly one chord-named .doc/.docx → use it) and any
    --pick overrides. Returns one SongResolution per song item in sequence
    order. Songs that remain unresolved have `picked=None`."""
    items, included = list_song_items(client, service_type_id, plan_id)
    songs_by_id = included.get("Song", {})
    by_attachable = list_plan_all_attachments(client, service_type_id, plan_id)

    resolutions: list[SongResolution] = []
    for item in items:
        rels = item.get("relationships") or {}
        song_rel = rels.get("song", {}).get("data")
        if not song_rel:
            sys.stderr.write(
                f"  ⚠ Item {item['id']} ({item['attributes'].get('title')!r}) has "
                f"item_type=song but no song relationship; skipping.\n"
            )
            continue
        song_id = song_rel["id"]
        arr_rel = rels.get("arrangement", {}).get("data") or {}
        key_rel = rels.get("key", {}).get("data") or {}
        arrangement_id = arr_rel.get("id")
        key_id = key_rel.get("id")

        song = songs_by_id.get(song_id)
        item_attrs = item["attributes"]
        song_title = (
            (song or {}).get("attributes", {}).get("title")
            or item_attrs.get("title")
            or "(untitled)"
        )
        item_title = item_attrs.get("title") or song_title

        # Collect candidates at every level (for both the chord filter and
        # the 0-chord fallback display).
        key_atts = by_attachable.get(("Key", key_id), []) if key_id else []
        arr_atts = by_attachable.get(("Arrangement", arrangement_id), []) if arrangement_id else []
        song_atts = by_attachable.get(("Song", song_id), [])

        # Look for chord-named .doc/.docx, preferring the most specific
        # level. In this org's convention chord charts live on Keys.
        chords: list[dict[str, Any]] = []
        chord_source: str | None = None
        for level_name, level_atts in (
            ("Key", key_atts),
            ("Arrangement", arr_atts),
            ("Song", song_atts),
        ):
            matched = [a for a in level_atts if is_chord_doc_attachment(a)]
            if matched:
                chords = matched
                chord_source = level_name
                break

        all_docs = [a for a in (key_atts + arr_atts + song_atts) if is_doc_attachment(a)]
        # De-duplicate by id while preserving order.
        seen_ids: set[str] = set()
        doc_candidates: list[dict[str, Any]] = []
        for a in all_docs:
            if a["id"] in seen_ids:
                continue
            seen_ids.add(a["id"])
            doc_candidates.append(a)

        picked: dict[str, Any] | None = None
        pick_source: str | None = None
        if song_id in picks:
            target = picks[song_id]
            picked = next((a for a in doc_candidates if a["id"] == target), None)
            if picked:
                pick_source = "pick"
            else:
                sys.stderr.write(
                    f"  ⚠ --pick {song_id}={target}: attachment not found among "
                    f".doc/.docx attachments for song {song_title!r}; treating as unresolved.\n"
                )
        elif len(chords) == 1:
            picked = chords[0]
            pick_source = "auto"

        picked_parent_path = (
            _attachable_parent_path(picked, song_id, arrangement_id) if picked else None
        )
        if picked and picked_parent_path is None:
            sys.stderr.write(
                f"  ⚠ Could not construct a download path for attachment "
                f"{picked['id']} on song {song_title!r}; treating as unresolved.\n"
            )
            picked = None
            pick_source = None

        resolutions.append(
            SongResolution(
                sequence=item_attrs.get("sequence", 0),
                song_id=song_id,
                song_title=song_title,
                item_title=item_title,
                arrangement_id=arrangement_id,
                key_id=key_id,
                chord_candidates=chords,
                chord_source=chord_source,
                doc_candidates=doc_candidates,
                picked=picked,
                pick_source=pick_source,
                picked_parent_path=picked_parent_path,
            )
        )
    return resolutions
