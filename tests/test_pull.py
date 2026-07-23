from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from koyu_cli.pull import pull


MF_ID = "mf_11111111111111111111111111111111"
EP_ID = "ep_22222222222222222222222222222222"


class FakeClient:
    manifest = {
        "id": MF_ID,
        "name": "fixture",
        "type": "teleop",
        "fps": 20,
        "encoding": {"video_codec": "libx264"},
        "features": {"action": {"dtype": "float32", "shape": [7]}},
        "success_rate": None,
    }
    episode = {
        "id": EP_ID,
        "length": 12,
        "task": "pick",
        "task_description": "pick the object",
        "collection_mode": "eval",
        "source_project_id": "proj_33333333333333333333333333333333",
        "source_run_id": "run_44444444444444444444444444444444",
        "source_checkpoint": "checkpoints/last.pt",
        "policy_name": "act",
        "reward": 1.0,
        "features": {"action": {"dtype": "float32", "shape": [7]}},
        "size_bytes": 4,
        "recorded_at": "2026-07-21T23:59:00Z",
        "record_hz": 20.0,
        "uploaded_at": "2026-07-22T00:00:00Z",
        "files": {
            "data.parquet": {"url": "blob:data", "size": 4},
            "eef.npy": {"url": "blob:eef", "size": 3},
            "episode.json": {"url": "blob:legacy-sidecar", "size": 99},
        },
    }

    def json(self, method: str, path: str, **kwargs) -> dict:
        if method == "GET" and path == f"/api/manifests/{MF_ID}":
            return self.manifest
        if method == "POST" and path.endswith("/episodes/batch-get"):
            return {"episodes": [self.episode]}
        raise AssertionError((method, path, kwargs))

    def paginate(self, path: str, list_key: str, params=None) -> list[dict]:
        assert list_key == "episodes"
        return [{k: self.episode.get(k) for k in (
            "id", "length", "recorded_at", "record_hz", "task",
            "task_description", "reward", "size_bytes"
        )}]

    def download(self, url: str, dest: Path) -> int:
        payload = {"blob:data": b"data", "blob:eef": b"eef"}[url]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return len(payload)


class PullSidecarTests(unittest.TestCase):
    def test_pull_synthesizes_complete_available_metadata(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            result = pull(FakeClient(), MF_ID, dest)
            sidecar = json.loads((dest / "episodes" / EP_ID / "episode.json").read_text())

            self.assertEqual(sidecar["schema_version"], 1)
            self.assertEqual(sidecar["capture_id"], EP_ID.removeprefix("ep_"))
            self.assertEqual(sidecar["fps"], 20)
            self.assertEqual(sidecar["encoding"], {"video_codec": "libx264"})
            self.assertEqual(sidecar["source_run_id"], FakeClient.episode["source_run_id"])
            self.assertEqual(sidecar["reward"], 1.0)
            self.assertEqual(sidecar["recorded_at"], FakeClient.episode["recorded_at"])
            self.assertEqual(sidecar["record_hz"], 20.0)
            self.assertNotIn("uploaded_at", sidecar)
            self.assertTrue((dest / "episodes" / EP_ID / "eef.npy").is_file())
            self.assertEqual(result["sidecars_written"], 1)

    def test_sidecar_is_structural_and_idempotent(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "data"
            first = pull(FakeClient(), MF_ID, dest, only=["data.parquet"])
            second = pull(FakeClient(), MF_ID, dest, only=["data.parquet"])

            self.assertTrue((dest / "episodes" / EP_ID / "episode.json").is_file())
            self.assertFalse((dest / "episodes" / EP_ID / "eef.npy").exists())
            self.assertEqual(first["sidecars_written"], 1)
            self.assertEqual(second["sidecars_written"], 0)
            self.assertEqual(second["files_skipped"], 1)


if __name__ == "__main__":
    unittest.main()
