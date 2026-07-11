"""Thin koyu.dev API client. Anonymous by default; token only where writes need it.

Auth resolution: --token flag > KOYU_TOKEN env > anonymous.
Base URL: KOYU_API env (default https://koyu.dev). Public entities are readable
with no token at all — that property is load-bearing for template replication.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

DEFAULT_API = "https://koyu.dev"
_ID_RE = re.compile(r"(proj_[0-9a-f]{32}|run_[0-9a-f]{32}|mf_[0-9a-f]{32}|ep_[0-9a-f]{32})")


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def extract_id(ref: str, kind: str) -> str:
    """Accept a bare entity id or any URL containing one; return the id.

    kind: 'proj' | 'run' | 'mf' | 'ep'
    """
    m = _ID_RE.search(ref)
    if m and m.group(1).startswith(kind + "_"):
        return m.group(1)
    raise ApiError(
        f"could not find a {kind}_<32-hex> id in {ref!r} — pass the entity id "
        f"(shown on its koyu.dev page) or a URL containing it")


class Client:
    def __init__(self, token: str | None = None, api_base: str | None = None):
        self.base = (api_base or os.environ.get("KOYU_API") or DEFAULT_API).rstrip("/")
        self.token = token or os.environ.get("KOYU_TOKEN")
        headers = {"User-Agent": f"koyu-cli/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        timeout = httpx.Timeout(connect=20.0, read=120.0, write=300.0, pool=120.0)
        self._api = httpx.Client(base_url=self.base, headers=headers, timeout=timeout,
                                 follow_redirects=True)
        # R2 presigned URLs must NOT carry the Authorization header.
        self._blob = httpx.Client(timeout=timeout, follow_redirects=True)

    # ---- json api ----------------------------------------------------------

    def json(self, method: str, path: str, **kwargs) -> dict:
        try:
            r = self._api.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ApiError(f"{method} {path}: {exc}") from exc
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:
                detail = r.text[:200]
            hint = ""
            if r.status_code in (401, 403) and not self.token:
                hint = " (no token set — export KOYU_TOKEN for private entities)"
            raise ApiError(f"{method} {path} -> {r.status_code}: {detail}{hint}", r.status_code)
        return r.json() if r.content else {}

    def paginate(self, path: str, list_key: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        params = dict(params or {})
        while True:
            page = self.json("GET", path, params=params)
            out.extend(page.get(list_key, []))
            cursor = page.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    # ---- blob transfer -----------------------------------------------------

    def download(self, url: str, dest: Path) -> int:
        """Stream a presigned GET to disk atomically. Returns bytes written."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.part")
        n = 0
        try:
            with self._blob.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise ApiError(f"GET blob -> {r.status_code} for {dest.name}", r.status_code)
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        n += len(chunk)
            tmp.replace(dest)
            return n
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def upload(self, path: Path, plan: dict) -> None:
        """PUT a file to its presigned plan — single or multipart.

        Multipart wire shape (matches the backend + workspace sync):
        {"multipart": true, "part_size": N, "parts": [{"url", "headers"?}, ...]}
        The server completes the multipart at commit; no client-side etags.
        """
        if plan.get("multipart"):
            part_size = int(plan["part_size"])
            with path.open("rb") as f:
                for i, part in enumerate(plan.get("parts", [])):
                    url = part["url"] if isinstance(part, dict) else str(part)
                    headers = {k: str(v) for k, v in (part.get("headers", {})
                               if isinstance(part, dict) else {}).items()}
                    chunk = f.read(part_size)
                    if i == 0 and not chunk:
                        raise ApiError(f"multipart upload of empty file {path}")
                    r = self._blob.put(url, headers=headers, content=chunk)
                    if r.status_code >= 400:
                        raise ApiError(f"upload part {i + 1} of {path.name} -> {r.status_code}")
        else:
            headers = {k: str(v) for k, v in plan.get("headers", {}).items()}
            with path.open("rb") as f:
                r = self._blob.put(plan["url"], headers=headers, content=f.read())
            if r.status_code >= 400:
                raise ApiError(f"upload {path.name} -> {r.status_code}")

    def close(self) -> None:
        self._api.close()
        self._blob.close()


def hash_file(path: Path) -> str:
    from blake3 import blake3
    h = blake3()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
