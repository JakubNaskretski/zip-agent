import os
import sys

import pytest

# make the repo root importable so tests can reach `scripts.*` regardless of how
# the editable install exposes the `librarian` package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from librarian import Librarian, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "mem")


@pytest.fixture
def lib(store):
    return Librarian(store)
