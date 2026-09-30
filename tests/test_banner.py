"""Guard the console banner: it advertised replaced bindings for three releases.

The banner is the first thing anyone sees when they run the app, and it is hand-written text, so it
silently went stale while the engine and the README moved on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.app import GESTURE_HELP                       # noqa: E402

GONE = ("adaptive scroll", "open hand swipe", "pinky pinch (hold)")
PRESENT = ("thumb + ring pinch", "drag", "zoom in", "zoom out", "right fist held",
           "left fist held", "shaka", "push to talk", "end key")


def test_banner_does_not_advertise_replaced_bindings():
    text = GESTURE_HELP.lower()
    for gone in GONE:
        assert gone.lower() not in text, f"the banner still advertises {gone!r}"


def test_banner_lists_the_current_gestures():
    text = GESTURE_HELP.lower()
    for present in PRESENT:
        assert present.lower() in text, f"the banner is missing {present!r}"
