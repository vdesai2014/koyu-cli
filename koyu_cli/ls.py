"""koyu ls — see what's there before you download any of it.

Agents decide what to transfer by looking first: a run's checkpoint can be
100x the size of its code, and mirroring blindly wastes minutes and disk.
Projects and runs list their files with sizes (projects also list their runs,
so run ids are discoverable without leaving the CLI); manifests summarize
their episodes. Pair with `koyu fetch --only/--exclude` to take just what
you need.
"""
from __future__ import annotations

from .client import Client, extract_id, log


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.0f} B"


def _entity_ls(client: Client, kind: str, eid: str) -> dict:
    entity = client.json("GET", f"/api/{kind}/{eid}")
    files: dict = entity.get("files", {})
    total = sum(int(m.get("size", 0)) for m in files.values())
    log(f"{kind[:-1]} {entity.get('name', eid)} ({eid})")
    for path, meta in sorted(files.items()):
        log(f"  {human(int(meta.get('size', 0))):>12}  {path}")
    log(f"  {human(total):>12}  total ({len(files)} files)")

    result = {
        "kind": kind[:-1], "id": eid, "name": entity.get("name"),
        "files": {p: int(m.get("size", 0)) for p, m in sorted(files.items())},
        "total_bytes": total,
    }
    if kind == "projects":
        runs = client.json("GET", f"/api/projects/{eid}/runs").get("runs", [])
        if runs:
            log("  runs:")
            for run in runs:
                log(f"    {run['id']}  {run.get('name', '')}")
        result["runs"] = [{"id": r["id"], "name": r.get("name")} for r in runs]
    if kind == "runs":
        manifests = entity.get("manifest_ids") or []
        if manifests:
            log("  linked manifests (koyu ls <id> for metadata):")
            for mf_id in manifests:
                log(f"    {mf_id}")
        result["manifest_ids"] = manifests
    return result


def _manifest_ls(client: Client, mf_id: str) -> dict:
    manifest = client.json("GET", f"/api/manifests/{mf_id}")
    episodes = client.paginate(f"/api/manifests/{mf_id}/episodes", "episodes",
                               params={"limit": 100})
    total = sum(int(e.get("size_bytes") or 0) for e in episodes)
    rated = [e["reward"] for e in episodes if e.get("reward") is not None]
    log(f"manifest {manifest.get('name', mf_id)} ({mf_id})")
    log(f"  type {manifest.get('type')}, fps {manifest.get('fps')}, "
        f"{len(episodes)} episodes, {human(total)} total")
    if rated:
        log(f"  success rate {sum(rated) / len(rated):.2f} ({len(rated)} rated)")
    for key, spec in (manifest.get("features") or {}).items():
        shape = "video" if spec.get("dtype") == "video" else str(spec.get("shape", "?"))
        log(f"  feature {key}: {shape}")
    return {
        "kind": "manifest", "id": mf_id, "name": manifest.get("name"),
        "type": manifest.get("type"), "episode_count": len(episodes),
        "total_bytes": total, "features": manifest.get("features", {}),
        "success_rate": (sum(rated) / len(rated)) if rated else None,
        "episodes": [{k: e.get(k) for k in
                      ("id", "length", "task", "reward", "size_bytes")}
                     for e in episodes],
    }


def ls(client: Client, ref: str) -> dict:
    if "run_" in ref:
        return _entity_ls(client, "runs", extract_id(ref, "run"))
    if "mf_" in ref:
        return _manifest_ls(client, extract_id(ref, "mf"))
    return _entity_ls(client, "projects", extract_id(ref, "proj"))
