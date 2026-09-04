#!/usr/bin/env python3
"""Changelog fragments (#57): one file per change in changelog.d/, assembled at release time.

    tools/changelog.py preview                       the Unreleased section as it would be written
    tools/changelog.py release 0.6.0 [--date D] [--intro "..."]
                                                     move the fragments (and any lines still under
                                                     Unreleased) into a "## [0.6.0] - D" section
    tools/changelog.py check                         every fragment is well formed (CI, tests)

A fragment is `changelog.d/<issue>-<slug>.<kind>.md`, kind one of features, fixes, changes;
its text is the changelog line without the leading dash and without the issue number, which
the file name supplies. Two pull requests never touch the same file, so nothing conflicts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
KINDS = {"features": "Features", "fixes": "Fixes", "changes": "Changes"}
ORDER = ["Features", "Fixes", "Changes"]
NAME_RE = re.compile(r"^(?P<issue>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)\.(?P<kind>features|fixes|changes)\.md$")
UNRELEASED_NOTE = ("Changes waiting for the next release are one file each in `changelog.d/`;\n"
                   "`python tools/changelog.py preview` shows the section they will make.\n")


@dataclass(frozen=True)
class Fragment:
    path: Path
    issue: int
    kind: str      # a section title
    text: str      # the line without "- " and without the issue number

    @property
    def line(self) -> str:
        return f"- {self.text} (#{self.issue})"


def read_fragments(directory: Path = FRAGMENTS) -> list[Fragment]:
    """Every fragment, newest issue first (the way the sections have been written by hand)."""
    found = []
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        m = NAME_RE.match(path.name)
        if not m:
            raise ValueError(f"{path.name}: not <issue>-<slug>.<features|fixes|changes>.md")
        text = " ".join(path.read_text(encoding="utf-8").split())
        if not text:
            raise ValueError(f"{path.name}: empty")
        if text.startswith("- "):
            raise ValueError(f"{path.name}: leave out the leading dash")
        if re.search(rf"\(#{m['issue']}\)\s*$", text):
            raise ValueError(f"{path.name}: leave out the issue number, the file name carries it")
        found.append(Fragment(path, int(m["issue"]), KINDS[m["kind"]], text))
    return sorted(found, key=lambda f: -f.issue)


def unreleased_lines(changelog: str) -> dict[str, list[str]]:
    """Lines still written under Unreleased by hand (the way it was before fragments)."""
    if "## [Unreleased]\n" not in changelog:
        return {}
    body = changelog.split("## [Unreleased]\n", 1)[1].split("\n## [", 1)[0]
    out: dict[str, list[str]] = {}
    section = "Changes"
    for line in body.splitlines():
        if line.startswith("### "):
            section = line[4:].strip()
        elif line.startswith("- "):
            out.setdefault(section, []).append(line.rstrip())
    return out


def section_text(fragments: list[Fragment], extra: dict[str, list[str]] | None = None) -> str:
    """The subsections in order, fragments first, then lines written by hand."""
    groups: dict[str, list[str]] = {}
    for f in fragments:
        groups.setdefault(f.kind, []).append(f.line)
    for kind, lines in (extra or {}).items():
        for line in lines:
            if line not in groups.get(kind, []):
                groups.setdefault(kind, []).append(line)
    names = [k for k in ORDER if k in groups] + [k for k in groups if k not in ORDER]
    return "".join(f"\n### {name}\n" + "\n".join(groups[name]) + "\n" for name in names)


def preview(changelog: str, fragments: list[Fragment]) -> str:
    return "## [Unreleased]\n" + section_text(fragments, unreleased_lines(changelog))


def release(changelog: str, fragments: list[Fragment], version: str, date: str, intro: str = "") -> str:
    """The changelog with a new version section in place of what Unreleased held."""
    if f"## [{version}]" in changelog:
        raise ValueError(f"CHANGELOG.md already has a section for {version}")
    body = section_text(fragments, unreleased_lines(changelog))
    if not body.strip():
        raise ValueError("nothing to release: no fragments and nothing under Unreleased")
    head, rest = changelog.split("## [Unreleased]\n", 1)
    tail = rest.split("\n## [", 1)[1] if "\n## [" in rest else ""
    section = f"## [{version}] - {date}\n" + (f"\n{intro.strip()}\n" if intro.strip() else "") + body
    return head + "## [Unreleased]\n\n" + UNRELEASED_NOTE + "\n" + section + ("\n## [" + tail if tail else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preview")
    sub.add_parser("check")
    rel = sub.add_parser("release")
    rel.add_argument("version")
    rel.add_argument("--date", default=dt.date.today().isoformat())
    rel.add_argument("--intro", default="")
    args = ap.parse_args(argv)
    try:
        fragments = read_fragments()
    except ValueError as e:
        print(f"changelog.d: {e}", file=sys.stderr)
        return 1
    changelog = CHANGELOG.read_text(encoding="utf-8")
    if args.cmd == "check":
        print(f"{len(fragments)} fragment{'s' if len(fragments) != 1 else ''} ok")
        return 0
    if args.cmd == "preview":
        print(preview(changelog, fragments), end="")
        return 0
    try:
        CHANGELOG.write_text(release(changelog, fragments, args.version, args.date, args.intro), encoding="utf-8")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    for f in fragments:
        f.path.unlink()
    print(f"CHANGELOG.md: section {args.version} written from {len(fragments)} fragment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
