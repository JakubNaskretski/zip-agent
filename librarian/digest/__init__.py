"""Digest — parsers that turn uploaded source into Knowledge Units.

Each source module parses a repo/export into candidate KUs + derived graphs, all
committed through the Librarian (never written directly). See
docs/ARCHITECTURE.md §6.

- ``graphbuilder`` — Salesforce (force-app) digest, backed by the vendored
  graph-builder engine (``vendor/graphbuilder/``). Vocabulary mapping +
  reconciliation notes live in the ``graphbuilder`` module docstring.
- ``mule`` — Mule flow-graph digest.
- ``jira`` — Jira collector-dump digest (``<PROJECT>/<KEY>.issue.json`` →
  raw issue KUs + a contained intra-Jira graph; entities = issue keys only).
- ``confluence`` — Confluence collector-dump digest (``<SPACE>/<id>.page.json``
  → raw page KUs + a contained intra-Confluence graph; entities = space key +
  page id only).
"""
