"""On-demand skills — importable capability modules that are NOT loaded eagerly.

Unlike :mod:`librarian.digest` (parsers run during ingest), a module here is a
pure agent-invoked capability. Modules in this package are deliberately **not**
imported by ``librarian/__init__.py``, so they add *zero* always-loaded context:
the agent reaches one only when it needs it (e.g.
``from librarian.skills import pptx_draft``), and a single routing line in a
profile overlay is the only thing that tells the agent the skill exists. The
module body stays on disk until imported.

See docs/ARCHITECTURE.md §14.2 ("adding a skill").
"""
