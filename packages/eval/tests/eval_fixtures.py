"""Neutral synthetic experiment inputs for eval tests."""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def synthetic_inputs():
    """Point experiment inputs and the source-document root at the neutral fixture in packages/eval/tests/fixtures."""
    from unittest.mock import patch as _patch
    import safety as _safety, prepare as _prepare
    root = Path(__file__).resolve().parent / 'fixtures/inputs'
    with _patch.object(_safety, 'INPUTS_HOME', root), _patch.object(_prepare, 'STATE_HOME', root):
        yield root
