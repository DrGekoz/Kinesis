"""Make the vendored EyeTrax importable without installing it.

EyeTrax is a real dependency of the gaze tracking, and its source ships inside this repository under
`vendor/eyetrax`, so a clone runs with no extra install step. This module puts its `src` directory on
`sys.path` so the existing `from eyetrax import ...` calls keep working untouched.

An installed copy still wins if one is present - it is the same code - and an already-correct
interpreter is left alone entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_ENSURED = False


def eyetrax_source_dir() -> Optional[Path]:
    """Where the vendored EyeTrax source lives, if it is there.

    Checks both the `src/` layout and a flat one, and looks inside a PyInstaller bundle
    (`sys._MEIPASS`) as well as the checkout, because the packaged app has neither a CWD to rely on.
    """
    roots = [Path(__file__).resolve().parent.parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass))
    for root in roots:
        for candidate in (root / "vendor" / "eyetrax" / "src", root / "vendor" / "eyetrax"):
            if (candidate / "eyetrax" / "__init__.py").is_file():
                return candidate
    return None


def ensure_eyetrax(verbose: bool = False) -> bool:
    """Guarantee that `import eyetrax` works, from the vendor directory if it has to."""
    global _ENSURED
    try:
        import eyetrax                                            # noqa: F401
        if verbose:
            print(f"[vendored] eyetrax already importable: {getattr(eyetrax, '__file__', '?')}")
        _ENSURED = True
        return True
    except Exception:
        pass                                     # fall through to the vendored copy
    src = eyetrax_source_dir()
    if src is None:
        if verbose:
            print("[vendored] no eyetrax in vendor/ - gaze tracking will be unavailable")
        return False
    path = str(src)
    if path not in sys.path:
        sys.path.insert(0, path)                 # front: a fresh clone must not pick up a stale copy
    try:
        import eyetrax                                            # noqa: F401
        if verbose:
            print(f"[vendored] eyetrax {getattr(eyetrax, '__version__', '?')} from {src}")
        _ENSURED = True
        return True
    except Exception as exc:
        if verbose:
            print(f"[vendored] eyetrax failed to import from {src}: {exc}")
        return False


def eyetrax_ready() -> bool:
    """Was the vendored copy made importable at startup?"""
    return _ENSURED
