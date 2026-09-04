"""The version string (#112): the version, plus the commit when run from a checkout."""

from __future__ import annotations

import re

import den_mail


def test_version_string_carries_the_commit_from_a_checkout():
    s = den_mail.version_string()
    assert s.startswith(den_mail.VERSION)
    if den_mail.build():
        assert re.fullmatch(rf"{re.escape(den_mail.VERSION)} · git [0-9a-f]{{7,}}", s)
    else:
        assert s == den_mail.VERSION
