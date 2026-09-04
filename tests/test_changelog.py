"""Changelog fragments (#57): the files in changelog.d/ are well formed and assemble into a section."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import changelog as tool  # tools/ is not a package

SAMPLE = """# Changelog

Intro text.

## [Unreleased]

### Fixes
- A line still written by hand (#80)

## [0.5.0] - 2026-09-04

Milestone *Assistant*.

### Features
- Older (#68)
"""


def test_the_repository_fragments_are_valid():
    fragments = tool.read_fragments()
    assert fragments, "changelog.d/ has at least this PR's fragment"
    for f in fragments:
        assert f.kind in tool.ORDER and f.text and f.line.endswith(f"(#{f.issue})")


def test_fragments_are_read_validated_and_ordered(tmp_path):
    (tmp_path / "61-offline-drafts.features.md").write_text("Offline drafts:\n  kept and\n  created later\n")
    (tmp_path / "80-search-tooltip.fixes.md").write_text("The tooltip is a sentence")
    (tmp_path / "57-fragments.changes.md").write_text("Fragments")
    (tmp_path / "README.md").write_text("ignored")
    fragments = tool.read_fragments(tmp_path)
    assert [f.issue for f in fragments] == [80, 61, 57]
    assert fragments[1].line == "- Offline drafts: kept and created later (#61)"
    assert fragments[0].kind == "Fixes" and fragments[2].kind == "Changes"
    for bad_name, content, message in (
        ("notes.md", "x", "not <issue>"),
        ("12-thing.docs.md", "x", "not <issue>"),
        ("12-thing.fixes.md", "", "empty"),
        ("12-thing.fixes.md", "- with a dash", "leading dash"),
        ("12-thing.fixes.md", "with the number (#12)", "issue number"),
    ):
        d = tmp_path / "bad"
        d.mkdir(exist_ok=True)
        for old in d.iterdir():
            old.unlink()
        (d / bad_name).write_text(content)
        with pytest.raises(ValueError, match=message):
            tool.read_fragments(d)


def test_preview_and_release_merge_fragments_with_handwritten_lines(tmp_path):
    (tmp_path / "61-offline-drafts.features.md").write_text("Offline drafts")
    (tmp_path / "62-badge.fixes.md").write_text("The badge")
    fragments = tool.read_fragments(tmp_path)
    assert tool.preview(SAMPLE, fragments) == (
        "## [Unreleased]\n\n### Features\n- Offline drafts (#61)\n\n### Fixes\n- The badge (#62)\n"
        "- A line still written by hand (#80)\n")
    out = tool.release(SAMPLE, fragments, "0.6.0", "2026-09-05", "Milestone *Offline*.")
    assert out.startswith("# Changelog\n\nIntro text.\n\n## [Unreleased]\n\n" + tool.UNRELEASED_NOTE)
    assert "\n## [0.6.0] - 2026-09-05\n\nMilestone *Offline*.\n\n### Features\n- Offline drafts (#61)\n" in out
    assert "- A line still written by hand (#80)\n\n## [0.5.0] - 2026-09-04\n" in out
    assert out.count("## [") == 3
    with pytest.raises(ValueError, match="already has"):
        tool.release(out, fragments, "0.6.0", "2026-09-05")
    with pytest.raises(ValueError, match="nothing to release"):
        tool.release("# C\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n- x (#1)\n", [], "0.2.0", "2026-01-02")
    # a fresh Unreleased with only the note assembles from fragments alone
    again = tool.release(out, tool.read_fragments(tmp_path), "0.7.0", "2026-09-06")
    assert "## [0.7.0] - 2026-09-06\n\n### Features\n- Offline drafts (#61)\n" in again
    assert "(#80)" not in again.split("## [0.7.0]")[1].split("## [0.6.0]")[0]   # the note is not a line
