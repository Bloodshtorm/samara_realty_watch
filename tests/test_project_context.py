"""Offline reference checks only; never load runtime configuration or execute docs."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "AGENTS.md",
    "README.md",
    "docs/OPERATIONS.md",
    "docs/PROJECT_CONTEXT.md",
    "docs/CONTEXT_PROMPTS.md",
    "docs/CONTEXT_RUBRIC.md",
)


def validate_reference(root: Path, document: Path, reference: str) -> None:
    target = (document.parent / reference.split("#", 1)[0]).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Reference leaves repository")
    if not target.exists():
        raise ValueError("Missing context reference")


@pytest.mark.parametrize("name", DOCS)
def test_context_references_stay_in_repository_and_exist(name):
    document = ROOT / name
    for reference in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
        if "://" not in reference and not reference.startswith("#"):
            validate_reference(ROOT, document, reference)


@pytest.mark.parametrize("reference", ["../outside-secret", "missing-private-config"])
def test_bad_references_fail_without_echoing_private_paths(tmp_path, reference):
    with pytest.raises(ValueError) as error:
        validate_reference(tmp_path, tmp_path / "CONTEXT.md", reference)
    assert reference not in str(error.value)
    assert str(tmp_path) not in str(error.value)
