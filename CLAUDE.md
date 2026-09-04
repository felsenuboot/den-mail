# Den Mail: how work is organised

Read this before changing anything. The full development notes are in
`docs/DEVELOPMENT.md`; this file is the process every session follows.

## Issues and milestones
- Every piece of work starts from a GitHub issue. No issue yet: create one
  first (`gh issue create`), with the labels the others use (`feature`, `bug`,
  `documentation`, `maintenance`, plus cost/benefit/priority where known).
- Issues belong to milestones, and a milestone is a release: its title carries
  the version it ships as (`v0.3 · Release engineering`). Put a new issue in the
  milestone it belongs to, or none if it is unplanned.

## Branches and pull requests
- Never commit on `master`. Branch from it as `<issue>-<slug>` for issue work
  (`21-cleanup-dialog`), `release/<version>` for a release, `chore/<slug>` for
  the rest.
- Open a pull request when the branch is ready: the title is the changelog line
  (what changed, for the user), the body says why and how, and ends with
  `Closes #<issue>`. CI must be green.
- Merge with **squash** (`gh pr merge --squash --delete-branch`), so master has
  one commit per issue whose message is the PR title and body.
- Add the change to `CHANGELOG.md` under *Unreleased* in the same PR, with the
  issue number.

## Releases
- When a milestone's issues are closed: bump `VERSION` in `den_mail/__init__.py`
  and `version` in `pyproject.toml`, move the *Unreleased* entries into a new
  `## [X.Y.Z] - date` section, do it on a `release/X.Y.Z` branch through a PR,
  then tag the merge commit `vX.Y.Z` and push the tag. The Release workflow
  makes the GitHub release from the changelog section and refuses a tag that
  does not match `VERSION`. Close the milestone.
- A milestone is a minor version; a fix shipped between milestones is a patch
  version with the same steps.

## Commits
- Messages say what changed for the user and why, in full sentences; the first
  line under 72 characters. Reference the issue (`#21`) in the body.
- `git push` may fail when the SSH agent is unavailable; then push once over
  HTTPS with `git -c credential.helper='!gh auth git-credential' push https://github.com/felsenuboot/den-mail.git <branch>`.

## Checks before a PR
`ruff check .`, `bandit -q -c pyproject.toml -r den_mail data` and the test
suite (`DEN_MAIL_NO_WEBKIT=1 .venv/bin/python -m pytest -q`) must pass; a
change to the UI is also run once headlessly with the fake server and the
autopilot (see `docs/DEVELOPMENT.md`) and the relevant screenshot in
`data/screenshots/` refreshed if it changed.
