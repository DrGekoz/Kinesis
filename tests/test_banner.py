"""Guard the console banner: it advertised replaced bindings for three releases.

The banner is the first thing anyone sees when they run the app, and it is hand-written text, so it
silently went stale while the engine and the README moved on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.app import GESTURE_HELP                       # noqa: E402

GONE = ("adaptive scroll", "open hand swipe", "pinky pinch (hold)",
        # v1.13.0: the open->fist flick and fist->open maximise were removed. The banner listed
        # them for three releases after that, which is worse than no banner - it tells someone to
        # make a gesture that now does something else entirely (it holds Alt / Ctrl-Tab).
        "open hand -> closed fist", "closed fist -> open hand", "flick")
PRESENT = ("thumb + ring pinch", "drag", "zoom in", "zoom out", "right fist held",
           "left fist held", "shaka", "push to talk", "end key",
           # v1.13.0 claw (minimise by dragging, maximise by spreading) and v1.15.0 volume rocker
           "claw", "spread", "volume", "index + pinky")


def test_banner_says_the_eyes_move_the_cursor():
    """v1.11.0: the pointer follows gaze, so the banner must not still offer a pointing hand."""
    text = GESTURE_HELP.lower()
    assert "eye gaze" in text and "move the cursor" in text
    assert "index finger" not in text, "the banner still lists the index finger as the pointer"


def test_banner_does_not_advertise_replaced_bindings():
    text = GESTURE_HELP.lower()
    for gone in GONE:
        assert gone.lower() not in text, f"the banner still advertises {gone!r}"


def test_banner_lists_the_current_gestures():
    text = GESTURE_HELP.lower()
    for present in PRESENT:
        assert present.lower() in text, f"the banner is missing {present!r}"
