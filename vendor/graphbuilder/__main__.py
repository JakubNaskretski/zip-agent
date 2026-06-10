"""Command-line entry point — build a metadata graph, or run the whole pipeline.

Build mode (the original CLI)::

    python -m graphbuilder <force-app-dir> [-o graph.json]
    graph-builder <force-app-dir> [-o graph.json]      # via the console script

Point it at a single FILE to digest just that file. ``--levels`` controls how many
levels deep to map from the file (1 = just the file's own nodes, 2 = + one hop,
…); ``--types`` restricts the result to given node types; ``--repo`` adds full-tree
context so its edges resolve to real nodes instead of stubs:

    graph-builder path/to/MyClass.cls --levels 2 --repo path/to/force-app
    graph-builder path/to/MyClass.cls --types apexmethod

With no ``-o`` the JSON is written to stdout. A short summary (node/edge/
unresolved/error counts) is always printed to stderr so a redirected stdout stays
clean. Building never raises: unhandled files are skipped and per-extractor
failures land in the graph's ``errors`` list (reflected in the summary).

By default the output is **redacted**: Confluence page bodies (the one free-text
value a node can carry) are dropped so a plain dump can't spill page text. Pass
``--with-text`` to keep them inline.

Pipeline mode — every stage behind ONE command (made for wrapping as an agent
skill)::

    graph-builder pipeline --salesforce force-app --confluence-dump dump --out kb
    graph-builder pipeline --config pipeline.json

Runs (optional) Confluence/Jira collects -> per-source builds -> cross-source
joins -> knowledge-base bundle, producing ``<out>/{manifest.json, graph.json,
content/}`` + zip. ``--config`` reads the same options from a JSON file (explicit
flags win), so a recurring KB refresh is one command + one config. ``--collect``
needs ``--base-url``, ``--spaces`` and ``$CONFLUENCE_TOKEN``; ``--collect-jira``
needs ``--jira-base-url``, ``--projects`` and ``$JIRA_TOKEN`` (tokens are only
ever read from the environment).

Exit codes (both modes): ``0`` clean · ``1`` fatal (bad input/setup) · ``2``
usage · ``3`` finished, but the run recorded errors (build ``errors``, collect
failures, or an incomplete space listing) — output is still written, so agent
harnesses can key off the code without losing the artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import build_graph, build_file
from .analyze import graph_summary
from .persistence import save_graph, to_json

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_ERRORS_RECORDED = 3


def _build_cli(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="graph-builder",
        description="Build a Salesforce force-app metadata graph and emit it as JSON. "
                    "(See `graph-builder pipeline -h` for the all-stages mode.)",
    )
    parser.add_argument(
        "path", help="a force-app directory to scan, or a single metadata file to digest",
    )
    parser.add_argument(
        "-o", "--out", default=None,
        help="output JSON file (default: write to stdout)",
    )
    parser.add_argument(
        "-l", "--levels", type=int, default=None,
        help="single-file digest: levels deep to map from the file (1=just the "
             "file's own nodes, 2=+one hop, ...). Ignored when scanning a directory.",
    )
    parser.add_argument(
        "-t", "--types", default=None,
        help="single-file digest: comma-separated node-type allowlist "
             "(e.g. 'apexmethod,object'). Keeps only nodes of these types.",
    )
    parser.add_argument(
        "--repo", default=None,
        help="single-file digest: force-app root for full cross-file resolution "
             "context (edges resolve to real nodes, not stubs).",
    )
    parser.add_argument(
        "--with-text", action="store_true",
        help="keep inline Confluence page body text in the output (default: "
             "redact it — bodies are the one free-text value a node can carry).",
    )
    args = parser.parse_args(argv)

    if Path(args.path).is_file():
        types = [t.strip() for t in args.types.split(",") if t.strip()] if args.types else None
        graph = build_file(args.path, levels=args.levels, types=types, repo=args.repo)
    else:
        graph = build_graph(args.path)

    redact = not args.with_text
    if args.out:
        save_graph(graph, args.out, redact_text=redact)
    else:
        sys.stdout.write(to_json(graph, redact_text=redact) + "\n")

    summary = graph_summary(graph)
    n_errors = len(graph.get("errors", []))
    print(
        f"nodes={sum(summary['node_counts'].values())} "
        f"edges={sum(summary['edge_counts'].values())} "
        f"unresolved={len(graph.get('unresolved', []))} "
        f"errors={n_errors}"
        + (f"  ->  {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    return EXIT_ERRORS_RECORDED if n_errors else EXIT_OK


# --------------------------------------------------------------------------- #
# pipeline mode
# --------------------------------------------------------------------------- #
def _load_config(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("pipeline config must be a JSON object")
    return data


def _pipeline_cli(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="graph-builder pipeline",
        description="Run the whole knowledge-base pipeline behind one command: "
                    "(optional) Confluence collect -> builds -> join -> bundle.",
    )
    parser.add_argument("--config", default=None,
                        help="JSON file holding any of these options (flags win)")
    parser.add_argument("--salesforce", default=None, help="force-app directory")
    parser.add_argument("--confluence-dump", default=None,
                        help="Confluence dump dir (collect target / build input)")
    parser.add_argument("--collect", action="store_true", default=None,
                        help="refresh the dump from Confluence first "
                             "(needs --base-url, --spaces, $CONFLUENCE_TOKEN)")
    parser.add_argument("--base-url", default=None, help="Confluence instance root URL")
    parser.add_argument("--spaces", default=None,
                        help="comma-separated Confluence space keys to collect")
    parser.add_argument("--jira-dump", default=None,
                        help="Jira dump dir (collect target / build input)")
    parser.add_argument("--collect-jira", action="store_true", default=None,
                        help="refresh the dump from Jira first "
                             "(needs --jira-base-url, --projects, $JIRA_TOKEN)")
    parser.add_argument("--jira-base-url", default=None, help="Jira instance root URL")
    parser.add_argument("--projects", default=None,
                        help="comma-separated Jira project keys to collect")
    parser.add_argument("--remote-links", action="store_true", default=None,
                        help="also fetch each issue's remote links (one extra request "
                             "per issue; the strongest issue->page signal)")
    parser.add_argument("--insecure", action="store_true", default=None,
                        help="skip TLS verification (self-signed on-prem certs) — "
                             "prefer --ca-bundle")
    parser.add_argument("--ca-bundle", default=None,
                        help="PEM file of a private CA to trust (keeps full TLS verification)")
    parser.add_argument("--no-prune", dest="prune", action="store_false", default=None,
                        help="keep dump files for units a complete listing no longer returns")
    parser.add_argument("--out", default=None, help="bundle output directory (default: kb)")
    parser.add_argument("--no-zip", dest="zip", action="store_false", default=None,
                        help="skip writing the bundle zip next to --out")
    args = parser.parse_args(argv)

    cfg = _load_config(args.config) if args.config else {}
    # explicit flags win over the config file; config fills the rest
    opt = {k: v for k, v in vars(args).items() if k != "config" and v is not None}
    merged = {**cfg, **opt}

    salesforce = merged.get("salesforce")
    dump = merged.get("confluence_dump")
    jira_dump = merged.get("jira_dump")
    if not salesforce and not dump and not jira_dump:
        print("pipeline: nothing to do — give --salesforce, --confluence-dump and/or "
              "--jira-dump (or set them in --config)", file=sys.stderr)
        return EXIT_FATAL

    recorded_errors = 0

    def _keys(value):
        return [s.strip() for s in value.split(",") if s.strip()] \
            if isinstance(value, str) else (value or [])

    if merged.get("collect"):
        if not dump:
            print("pipeline: --collect needs --confluence-dump as its target", file=sys.stderr)
            return EXIT_FATAL
        from .confluence.collect import CollectError, collect
        try:
            summary = collect(
                merged.get("base_url"), _keys(merged.get("spaces")), dump,
                insecure=bool(merged.get("insecure")),
                ca_bundle=merged.get("ca_bundle"),
                prune=merged.get("prune", True),
            )
        except CollectError as exc:
            print(f"pipeline: collect failed: {exc}", file=sys.stderr)
            return EXIT_FATAL
        recorded_errors += len(summary["errors"]) + len(summary["incomplete"])
        print(
            f"collect: pages={summary['pages']} unchanged={summary['unchanged']} "
            f"pruned={len(summary['pruned'])} errors={len(summary['errors'])}"
            + (f" INCOMPLETE={','.join(summary['incomplete'])}" if summary["incomplete"] else ""),
            file=sys.stderr,
        )

    if merged.get("collect_jira"):
        if not jira_dump:
            print("pipeline: --collect-jira needs --jira-dump as its target", file=sys.stderr)
            return EXIT_FATAL
        from .jira.collect import CollectError as JiraCollectError, collect as jira_collect
        try:
            summary = jira_collect(
                merged.get("jira_base_url"), _keys(merged.get("projects")), jira_dump,
                insecure=bool(merged.get("insecure")),
                ca_bundle=merged.get("ca_bundle"),
                remote_links=bool(merged.get("remote_links")),
                prune=merged.get("prune", True),
            )
        except JiraCollectError as exc:
            print(f"pipeline: jira collect failed: {exc}", file=sys.stderr)
            return EXIT_FATAL
        recorded_errors += len(summary["errors"]) + len(summary["incomplete"])
        print(
            f"collect-jira: issues={summary['issues']} unchanged={summary['unchanged']} "
            f"pruned={len(summary['pruned'])} errors={len(summary['errors'])}"
            + (f" INCOMPLETE={','.join(summary['incomplete'])}" if summary["incomplete"] else ""),
            file=sys.stderr,
        )

    from .bundle import build_bundle
    try:
        result = build_bundle(
            merged.get("out") or "kb",
            salesforce=salesforce,
            confluence_dump=dump,
            jira_dump=jira_dump,
            zip_path=None if merged.get("zip", True) else False,
        )
    except ValueError as exc:
        print(f"pipeline: {exc}", file=sys.stderr)
        return EXIT_FATAL
    g = result["manifest"]["graph"]
    recorded_errors += g["errors"]
    print(
        f"bundle: nodes={g['nodes']} edges={g['edges']} documents={g['documents_edges']} "
        f"unresolved={g['unresolved']} errors={g['errors']}  ->  {result['out_dir']}"
        + (f" (+ {result['zip']})" if result["zip"] else ""),
        file=sys.stderr,
    )
    return EXIT_ERRORS_RECORDED if recorded_errors else EXIT_OK


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "pipeline":
        return _pipeline_cli(argv[1:])
    return _build_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
