"""The koyu command line.

    koyu ls    <project|run|manifest ref>    list files + sizes before downloading
    koyu fetch <project-id-or-url> <dir>     mirror a public project's files (no auth)
    koyu pull  <manifest-id-or-url> <dir>    materialize a dataset (no auth if public)
    koyu push  <run-or-project-id> <path…>   upload results (requires KOYU_TOKEN)
    koyu whoami                              check which account the token maps to

fetch and pull take --only/--exclude glob filters, so an agent can `koyu ls` a
run, notice the 20 GB checkpoint, and fetch just the code around it.

koyu is also the umbrella for the rest of the platform: other koyu packages
plug their verbs into this command via the `koyu.plugins` entry-point group
(koyu-runtime brings `koyu up/down/status/…`, koyu-workspace brings
`koyu ingest`). One command, capabilities appear as packages are installed.

No workspace semantics here: fetch is a mirror, not a clone. For an
identity-bearing clone that syncs with your koyu cloud, use koyu-workspace.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .client import ApiError, Client
from .fetch import fetch
from .ls import ls
from .pull import pull
from .push import push

CORE_VERBS = {"ls", "fetch", "pull", "push", "whoami"}

# Verbs we know live in other koyu packages: a miss becomes a signpost, not a
# usage error. Plugins can never shadow CORE_VERBS (core is checked first).
PLUGIN_HINTS = {
    **dict.fromkeys(
        ("up", "down", "status", "restart", "apply", "logs",
         "set", "get", "tail", "frame"),
        ("koyu-runtime", "git clone koyu-runtime and `pip install -e .` "
                         "(see koyu.dev/docs/runtime)")),
    "ingest": ("koyu-workspace", "git clone koyu-workspace and `pip install -e .` "
                                 "(see koyu.dev/docs/workspace)"),
}

_VALUE_FLAGS = {"--token", "--api"}     # global flags that consume the next token


def _first_verb(argv: list[str]) -> str | None:
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg in _VALUE_FLAGS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def _dispatch_plugin(verb: str, argv: list[str]) -> int | None:
    """Route a non-core verb to an installed plugin, or signpost where it
    lives. None = truly unknown; let argparse produce its usage error."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="koyu.plugins"):
        if ep.name == verb:
            return int(ep.load()(argv[argv.index(verb):]) or 0)
    hint = PLUGIN_HINTS.get(verb)
    if hint:
        package, how = hint
        print(f"error: '{verb}' is a {package} verb and {package} is not "
              f"installed in this environment.\n  {how}, then `koyu {verb}` "
              f"will work here.", file=sys.stderr)
        return 2
    return None


def _patterns(values: list[str] | None) -> list[str] | None:
    """Flatten repeatable, comma-separable glob flags into one pattern list."""
    if not values:
        return None
    return [p.strip() for v in values for p in v.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = _first_verb(argv)
    if verb and verb not in CORE_VERBS:
        rc = _dispatch_plugin(verb, argv)
        if rc is not None:
            return rc

    ap = argparse.ArgumentParser(prog="koyu", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"koyu {__version__}")
    ap.add_argument("--token", default=None, help="bearer token (default: $KOYU_TOKEN)")
    ap.add_argument("--api", default=None, help="API base (default: $KOYU_API or koyu.dev)")
    ap.add_argument("--json", action="store_true", help="machine-readable result on stdout")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls", help="list an entity's files (or a manifest's episodes) with sizes")
    p.add_argument("ref", help="proj_/run_/mf_<32hex> or a koyu.dev URL containing it")

    p = sub.add_parser("fetch", help="mirror a public project's files into a directory")
    p.add_argument("ref", help="proj_<32hex> or a koyu.dev URL containing it")
    p.add_argument("dir", type=Path)
    p.add_argument("--only", action="append", metavar="GLOB",
                   help="fetch only paths matching this glob (repeatable, comma-separable)")
    p.add_argument("--exclude", action="append", metavar="GLOB",
                   help="skip paths matching this glob (repeatable, comma-separable)")

    p = sub.add_parser("pull", help="materialize a manifest's episodes into a dataset dir")
    p.add_argument("ref", help="mf_<32hex> or a koyu.dev URL containing it")
    p.add_argument("dir", type=Path)
    p.add_argument("--only", action="append", metavar="GLOB",
                   help="pull only episode files matching this glob (e.g. 'data.parquet')")
    p.add_argument("--exclude", action="append", metavar="GLOB",
                   help="skip episode files matching this glob (e.g. '*.mp4')")

    p = sub.add_parser("push", help="upload files to a run or project (two-step, blake3-diffed)")
    p.add_argument("entity", help="run_<32hex> or proj_<32hex>")
    p.add_argument("paths", nargs="+", help="files or directories to upload")
    p.add_argument("--base", type=Path, default=Path("."),
                   help="paths are stored relative to this dir (default: cwd)")

    sub.add_parser("whoami", help="verify the token and print the account")

    args = ap.parse_args(argv)
    client = Client(token=args.token, api_base=args.api)
    try:
        if args.cmd == "ls":
            result = ls(client, args.ref)
        elif args.cmd == "fetch":
            result = fetch(client, args.ref, args.dir,
                           only=_patterns(args.only), exclude=_patterns(args.exclude))
        elif args.cmd == "pull":
            result = pull(client, args.ref, args.dir,
                          only=_patterns(args.only), exclude=_patterns(args.exclude))
        elif args.cmd == "push":
            result = push(client, args.entity, args.paths, args.base.resolve())
        elif args.cmd == "whoami":
            me = client.json("GET", "/api/me")
            result = {"username": me.get("username"), "name": me.get("name")}
            print(f"{result['username']} ({result.get('name') or 'no display name'})",
                  file=sys.stderr)
        if args.json:
            print(json.dumps(result))
        return 0
    except ApiError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
