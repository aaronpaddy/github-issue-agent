"""Apply a deliberate bug to a source file, so a test suite can be asked whether it notices."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.evals.cases import Mutant


class MutantNotApplicable(Exception):
    """The text the mutant expects to replace is not in the file."""


def apply_mutant(source: str, mutant: Mutant) -> str:
    if mutant.old not in source:
        raise MutantNotApplicable(f"{mutant.file}: {mutant.old!r} not found")
    return source.replace(mutant.old, mutant.new, mutant.count)


@contextmanager
def mutated(root: Path, mutant: Mutant) -> Iterator[None]:
    """Apply the mutant on disk for the duration of the block, then restore the file."""
    path = root / mutant.file
    original = path.read_text()
    path.write_text(apply_mutant(original, mutant))
    try:
        yield
    finally:
        path.write_text(original)
