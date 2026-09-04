# Changelog fragments

One file per change, so that two pull requests never edit the same lines of
`CHANGELOG.md`. The release PR assembles them into a version section.

- Name: `<issue>-<slug>.<kind>.md`, kind one of `features`, `fixes`, `changes`
  (`61-offline-drafts.features.md`).
- Content: the changelog line as the user should read it, without the leading
  dash and without the issue number; the file name carries both.
- `python tools/changelog.py preview` shows the section the fragments make;
  `python tools/changelog.py check` validates them (the test suite does too);
  `python tools/changelog.py release X.Y.Z --intro "…"` writes the section and
  deletes the fragments.
