"""Reuse authorized presentation text while always returning fresh learning state.

Clients opt in with content_revision. The normal endpoint and its authorization
still run on every request; there is no content-by-hash endpoint. A bounded
index of fingerprints (never course text) also reuses unchanged blocks on edits.
Old clients keep the original JSON contract.
"""
import hashlib
import json
import re
from collections import OrderedDict
from threading import Lock

from fastapi.responses import Response

REVISION = re.compile(r"(?:1|[a-f0-9]{64})\Z")
PATHS = {
    "/api/state", "/api/guide", "/api/training/group", "/api/training/state",
    "/api/materials/content",
}
MUTATIONS = {"/api/" + action for action in ("choose", "continue", "select", "reveal", "skip", "submit")}
_revisions = OrderedDict()
_lock = Lock()


def text_delta(texts, revision, known):
    fingerprints = tuple(hashlib.sha256(text.encode()).digest() for text in texts)
    with _lock:
        previous = _revisions.get(known, ())
        _revisions[revision] = fingerprints
        _revisions.move_to_end(revision)
        while len(_revisions) > 128 or sum(map(len, _revisions.values())) > 8192:
            _revisions.popitem(last=False)
    if known == revision:
        return None
    positions = {digest: index for index, digest in enumerate(previous)}
    return [{"$gme_previous": positions[digest]} if digest in positions else text
            for digest, text in zip(fingerprints, texts)]


def eligible(path, method):
    if method == "POST":
        return path in MUTATIONS or bool(re.fullmatch(
            r"/api/review/(?:begin|sessions/[^/]+/(?:continue|reveal|submit))", path))
    return method == "GET" and (path in PATHS or bool(re.fullmatch(
        r"/api/(?:tasks/[^/]+/answers|submissions/[^/]+|review/(?:sessions|submissions)/[^/]+)", path)))


def pack_content(value, known):
    texts, positions = [], {}

    def visit(item, key=""):
        if isinstance(item, str) and (key == "html" or key.endswith("_html") or key == "math_css"):
            if len(item) < 128:
                return item
            if item not in positions:
                positions[item] = len(texts)
                texts.append(item)
            return {"$gme_text": positions[item]}
        if isinstance(item, dict):
            return {name: visit(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    state = visit(value)
    if not texts:
        return value
    encoded = json.dumps(texts, ensure_ascii=False, separators=(",", ":")).encode()
    revision = hashlib.sha256(encoded).hexdigest()
    return {"$gme_content": 1, "state": state, "revision": revision,
            "texts": text_delta(texts, revision, known)}


async def compact_response(request, response):
    known = request.query_params.get("content_revision", "")
    if (not eligible(request.url.path, request.method)
            or not REVISION.fullmatch(known) or response.status_code != 200
            or "application/json" not in response.headers.get("content-type", "")
            or response.headers.get("content-encoding")):
        return response
    body = b"".join([part async for part in response.body_iterator])
    try:
        result = pack_content(json.loads(body), known)
        body = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        # A representation optimization must not turn a valid response into an error.
        pass
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(body, status_code=response.status_code, headers=headers,
                    background=response.background)
