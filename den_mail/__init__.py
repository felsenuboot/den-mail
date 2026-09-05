APP_ID = "io.github.felsenuboot.DenMail"
APP_NAME = "Den Mail"
VERSION = "0.6.4"


def build() -> str:
    """The commit this checkout runs from, when the package sits in a git repository (the
    launcher runs it that way); empty for an installed package (#112)."""
    import os
    import subprocess  # nosec B404 - one fixed git command, no user input

    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2, check=False)  # nosec B603 B607
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def version_string() -> str:
    """"0.6.1 · git 6d5956a" from a checkout, "0.6.1" from a package."""
    commit = build()
    return f"{VERSION} · git {commit}" if commit else VERSION
