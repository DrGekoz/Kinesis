"""Kinesis - webcam human-computer use."""

__version__ = "1.14.0"

# EyeTrax ships inside this repository (vendor/eyetrax) and is made importable here, so every
# `from eyetrax import ...` in the package works on a fresh clone with nothing installed.
from .vendored import ensure_eyetrax as _ensure_eyetrax    # noqa: E402

_ensure_eyetrax()

__all__ = ["__version__"]
