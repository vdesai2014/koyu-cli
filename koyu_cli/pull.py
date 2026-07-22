"""koyu pull — materialize a manifest's episodes into the koyu dataset layout.

    <dest>/manifest.json                     (what dataloaders read: fps, features, episode index)
    <dest>/episodes/<ep_id>/<files…>
    <dest>/episodes/<ep_id>/episode.json       (generated from cloud metadata)

Presigned URLs expire in 1 hour, so episodes are batch-getted in chunks and each
chunk is fully downloaded before the next is requested. Downloads run on a
thread pool and stream to disk. Files whose size already matches are skipped
(the batch-get response carries no blake3 yet), so pull is resumable.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from .client import ApiError, Client, extract_id, log
from .fetch import selected

BATCH = 64          # episodes per batch-get: URLs stay comfortably inside expiry
WORKERS = 16
SCHEMA_VERSION = 1


def _episode_sidecar(manifest: dict, detail: dict) -> dict:
    """Materialize the cloud's canonical episode metadata for local readers.

    ``recorded_at`` and ``record_hz`` are intentionally absent: the episode API
    does not retain those capture-time values, and upload ``created_at`` is not a
    valid substitute for recording time.
    """
    sidecar = {
        "schema_version": SCHEMA_VERSION,
        "capture_id": detail["id"].removeprefix("ep_"),
        "length": detail["length"],
        "task": detail.get("task"),
        "task_description": detail.get("task_description"),
        "collection_mode": detail.get("collection_mode"),
        "features": detail.get("features") or manifest.get("features", {}),
        "encoding": manifest.get("encoding") or {},
        "source_project_id": detail.get("source_project_id"),
        "source_run_id": detail.get("source_run_id"),
        "source_checkpoint": detail.get("source_checkpoint"),
        "policy_name": detail.get("policy_name"),
        "reward": detail.get("reward"),
    }
    if manifest.get("fps") is not None:
        sidecar["fps"] = manifest["fps"]
    return sidecar


def _write_sidecar(dest: Path, manifest: dict, detail: dict) -> bool:
    """Write episode.json atomically. Return True only when content changed."""
    target = dest / "episodes" / detail["id"] / "episode.json"
    content = json.dumps(_episode_sidecar(manifest, detail), indent=2) + "\n"
    if target.is_file() and target.read_text() == content:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(".episode.json.part")
    try:
        tmp.write_text(content)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def _episode_dl(client: Client, dest: Path, detail: dict,
                manifest: dict, only: list[str] | None,
                exclude: list[str] | None) -> tuple[int, int, int]:
    fetched = skipped = 0
    ep_dir = dest / "episodes" / detail["id"]
    for fname, meta in (detail.get("files") or {}).items():
        # episode.json is a local projection of canonical cloud metadata, not a
        # separately stored blob. Ignore one if legacy data happens to list it.
        if fname == "episode.json":
            continue
        if not selected(fname, only, exclude):
            continue
        target = ep_dir / fname
        if target.is_file() and target.stat().st_size == int(meta.get("size", -1)):
            skipped += 1
            continue
        n = client.download(meta["url"], target)
        if n != int(meta.get("size", n)):
            raise ApiError(f"{detail['id']}/{fname}: got {n} B, expected {meta['size']}")
        fetched += 1
    written = int(_write_sidecar(dest, manifest, detail))
    return fetched, skipped, written


def pull(client: Client, ref: str, dest: Path,
         only: list[str] | None = None, exclude: list[str] | None = None) -> dict:
    """--only/--exclude filter per-episode file names (e.g. --exclude '*.mp4'
    for a state-only pull). A filtered dataset is partial by construction:
    dataloaders that expect the skipped files will need the full pull."""
    mf_id = extract_id(ref, "mf")
    manifest = client.json("GET", f"/api/manifests/{mf_id}")
    episodes = client.paginate(f"/api/manifests/{mf_id}/episodes", "episodes",
                               params={"limit": 100})
    log(f"manifest {manifest.get('name', mf_id)}: {len(episodes)} episodes")
    dest.mkdir(parents=True, exist_ok=True)

    fetched = skipped = sidecars_written = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for i in range(0, len(episodes), BATCH):
            ids = [e["id"] for e in episodes[i:i + BATCH]]
            batch = client.json("POST", f"/api/manifests/{mf_id}/episodes/batch-get",
                                json={"episode_ids": ids})
            results = pool.map(lambda d: _episode_dl(client, dest, d, manifest,
                                                      only, exclude),
                               batch.get("episodes", []))
            for f, s, w in results:
                fetched += f
                skipped += s
                sidecars_written += w
            log(f"  {min(i + BATCH, len(episodes))}/{len(episodes)} episodes "
                f"({fetched} files fetched, {skipped} current)")

    local = {
        "id": manifest["id"], "name": manifest.get("name"),
        "type": manifest.get("type"), "fps": manifest.get("fps"),
        "features": manifest.get("features", {}),
        "episode_count": len(episodes),
        "success_rate": manifest.get("success_rate"),
        "episodes": [{k: e.get(k) for k in
                      ("id", "length", "task", "task_description", "reward", "size_bytes")}
                     for e in episodes],
    }
    (dest / "manifest.json").write_text(json.dumps(local, indent=2) + "\n")
    log(f"pulled -> {dest} (manifest.json written; ready for dataloader.py)")
    return {"manifest": mf_id, "episodes": len(episodes), "files_fetched": fetched,
            "files_skipped": skipped, "sidecars_written": sidecars_written}
