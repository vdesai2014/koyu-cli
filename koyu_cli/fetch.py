"""koyu fetch — mirror a (public) project's files into a directory.

A mirror, not a clone: no identity, no sync relationship, no workspace. The
files land exactly as listed in the project's `files` manifest; existing files
whose blake3 already matches are skipped, so re-fetch is cheap and idempotent.

Selective mirror: --only/--exclude glob patterns (fnmatch on the stored path)
let an agent take the code and skip the checkpoint. `koyu ls` first to see
what's there and how big it is.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .client import ApiError, Client, extract_id, hash_file, log


def selected(path: str, only: list[str] | None, exclude: list[str] | None) -> bool:
    """Glob selection: keep iff it matches some --only (when given) and no --exclude."""
    if only and not any(fnmatch(path, pat) for pat in only):
        return False
    if exclude and any(fnmatch(path, pat) for pat in exclude):
        return False
    return True


def fetch(client: Client, ref: str, dest: Path,
          only: list[str] | None = None, exclude: list[str] | None = None) -> dict:
    """Mirror a project's or a run's files. Runs inherit visibility from their
    project, so a public template's reference runs fetch anonymously too."""
    if "run_" in ref:
        kind, eid = "runs", extract_id(ref, "run")
    else:
        kind, eid = "projects", extract_id(ref, "proj")
    entity = client.json("GET", f"/api/{kind}/{eid}")
    files: dict = entity.get("files", {})
    if only or exclude:
        all_count = len(files)
        files = {p: m for p, m in files.items() if selected(p, only, exclude)}
        log(f"selected {len(files)} of {all_count} files")
    if not files:
        log(f"warning: {kind[:-1]} {entity.get('name', eid)} has no files"
            + (" matching the filters" if only or exclude else ""))
    dest.mkdir(parents=True, exist_ok=True)

    fetched = skipped = 0
    todo = {p: m for p, m in sorted(files.items())
            if not (dest / p).is_file() or not m.get("blake3")
            or hash_file(dest / p) != m["blake3"]}
    skipped = len(files) - len(todo)
    if todo:
        # batch presigned URLs (works for both entity kinds; anonymous on public)
        urls = client.json("POST", f"/api/{kind}/{eid}/files/download",
                           json={"paths": list(todo)}).get("urls", {})
        for path, meta in todo.items():
            if path not in urls:
                raise ApiError(f"{path}: server returned no download URL")
            n = client.download(urls[path], dest / path)
            want = int(meta.get("size", -1))
            if want >= 0 and n != want:
                raise ApiError(f"{path}: downloaded {n} bytes, expected {want}")
            fetched += 1
            log(f"  {path} ({n:,} B)")

    log(f"fetched {fetched}, skipped {skipped} (already current) -> {dest}")
    return {kind[:-1]: eid, "name": entity.get("name"), "fetched": fetched,
            "skipped": skipped}
