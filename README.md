# koyu

The koyu command line: move robot-learning data and template files between
[koyu.dev](https://koyu.dev) and any machine. Four verbs, two dependencies
(`httpx`, `blake3`), zero opinions about your ML stack — it installs cleanly
inside any training environment.

```bash
pip install koyu-cli
```

## Verbs

```bash
# See what's there before downloading — files with sizes, a project's runs,
# a manifest's episode count. Look, then decide.
koyu ls proj_a1b2…              # project files + its run ids
koyu ls run_e5f6…               # run files (spot the 20 GB checkpoint)
koyu ls mf_c3d4…                # episodes, total size, features

# Mirror a public template/project's files — no account needed
koyu fetch proj_a1b2… ./my-workdir

# Selective mirror: take the code, skip the weights
koyu fetch run_e5f6… ./run-info --exclude 'checkpoints/*'
koyu fetch run_e5f6… ./run-info --only 'stats.json,README.md'

# Materialize a dataset (manifest → episodes) — no account needed if public
koyu pull mf_c3d4… ./data/libero-spatial
koyu pull mf_c3d4… ./data/states-only --exclude '*.mp4'   # partial by design

# Upload results to your own run — needs a token
export KOYU_TOKEN=koyu_…        # mint at koyu.dev/settings/tokens
koyu push run_e5f6… checkpoints/best.pt metrics.jsonl results.json

koyu whoami                     # sanity-check the token
```

`--only`/`--exclude` are glob patterns against the stored path, repeatable and
comma-separable. Every verb takes `--json` for machine-readable output on
stdout (progress goes to stderr), so an agent can `koyu ls --json`, decide,
then fetch exactly what it needs.

`pull` writes the [koyu dataset layout](https://koyu.dev/docs/format):
`manifest.json` + `episodes/<ep_id>/{data.parquet, <camera>.mp4…, episode.json}` —
directly readable by any template's vendored `dataloader.py`. Downloads stream
to disk, run concurrently, and resume (already-current files are skipped), so
re-running a pull is always safe.

`push` is blake3-diffed two-step sync: unchanged files cost nothing, large files
upload multipart automatically, and nothing is visible until commit succeeds.

## What this deliberately is not

- **Not a clone.** `fetch` produces an anonymous mirror with no identity or sync
  relationship. If you want *your variant* of a project that syncs with your
  koyu cloud, that is koyu-workspace's `clone`.
- **Not a workspace.** No local store, no catalog, no server. Files in, files out.

## Auth model

Reads of public entities are anonymous by design. A token
(`KOYU_TOKEN` env or `--token`) is needed only to touch *your* resources:
private data, and any `push`. Tokens are personal access tokens from
koyu.dev/settings/tokens; short-lived child tokens are recommended on rented
hardware.

## Environment

| Variable | Meaning |
|---|---|
| `KOYU_TOKEN` | bearer token for authenticated calls |
| `KOYU_API` | API base override (default `https://koyu.dev`) |
