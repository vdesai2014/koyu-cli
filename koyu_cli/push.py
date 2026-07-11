"""koyu push — upload files to a run (or project) via the two-step sync.

blake3 diffing means unchanged files cost nothing; the server returns presigned
plans (single or multipart) only for new/changed content, and commit finalizes.
Paths are uploaded relative to --base (default: cwd).
"""
from __future__ import annotations

from pathlib import Path

from .client import ApiError, Client, extract_id, hash_file, log


def _collect(paths: list[str], base: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in paths:
        pth = Path(p).resolve()
        try:
            if pth.is_dir():
                for f in sorted(pth.rglob("*")):
                    if f.is_file():
                        out[str(f.relative_to(base))] = f
            elif pth.is_file():
                out[str(pth.relative_to(base))] = pth
            else:
                raise ApiError(f"no such file or directory: {p}")
        except ValueError:
            raise ApiError(f"{p} is outside --base {base} — pass --base pointing at "
                           f"the common parent (paths are stored relative to it)")
    return out


def push(client: Client, entity_ref: str, paths: list[str], base: Path) -> dict:
    if not client.token:
        raise ApiError("push requires a token — export KOYU_TOKEN "
                       "(mint one at koyu.dev/settings/tokens)")
    if entity_ref.startswith("run_") or "run_" in entity_ref:
        kind, eid = "runs", extract_id(entity_ref, "run")
    else:
        kind, eid = "projects", extract_id(entity_ref, "proj")

    files = _collect(paths, base)
    if not files:
        raise ApiError("nothing to push")
    log(f"hashing {len(files)} files…")
    manifest = {rel: {"blake3": hash_file(f), "size": f.stat().st_size}
                for rel, f in files.items()}

    up = client.json("POST", f"/api/{kind}/{eid}/files/upload", json={"files": manifest})
    to_upload = up.get("to_upload", {})
    synced = up.get("synced", [])
    for rel, plan in to_upload.items():
        log(f"  ↑ {rel} ({manifest[rel]['size']:,} B"
            f"{', multipart' if plan.get('multipart') else ''})")
        client.upload(files[rel], plan)
    pending = up.get("pending_upload_ids", [])
    if pending:
        client.json("POST", f"/api/{kind}/{eid}/files/commit",
                    json={"pending_upload_ids": pending})
    log(f"pushed {len(to_upload)}, already-synced {len(synced)} -> {kind[:-1]} {eid}")
    return {"entity": eid, "uploaded": len(to_upload), "synced": len(synced)}
