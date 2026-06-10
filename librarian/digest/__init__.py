"""Digest — parsers that turn uploaded source into Knowledge Units.

Each source module parses a repo/export into candidate KUs + derived graphs, all
committed through the Librarian (never written directly). See
plan/03_ARCHITECTURE.md §6.

- ``graphbuilder`` — Salesforce (force-app) digest, backed by the vendored
  graph-builder engine (``vendor/graphbuilder/``). Vocabulary mapping +
  reconciliation notes live in the ``graphbuilder`` module docstring.
- ``mule`` — Mule flow-graph digest.
"""
