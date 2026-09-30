"""Settings window logic: which screens are on, and what the wizards get told.

The Tk window itself is exercised by tools/check_settings_window.py, which renders the real thing;
what is tested here is everything that decides an outcome.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest                                                 # noqa: E402
from types import SimpleNamespace                             # noqa: E402

from kinesis import settings_window as sw                     # noqa: E402
from kinesis.app import KinesisApp                            # noqa: E402


def _mon(device, primary=False):
    return SimpleNamespace(device=device, left=0, top=0, right=1920, bottom=1080,
                           width=1920, height=1080, primary=primary)


TV = r"\\?\DISPLAY#DTV0000#1"
LEFT = r"\\?\DISPLAY#LEN27#2"
MAIN = r"\\?\DISPLAY#KAMN27#3"
RIGHT = r"\\?\DISPLAY#LEN27#4"
DESK = [_mon(TV), _mon(LEFT), _mon(MAIN, primary=True), _mon(RIGHT)]


# ------------------------------------------------------------------ toggling
def test_toggle_adds_and_removes_one_device():
    assert sw.toggle([LEFT], TV) == [LEFT, TV]
    assert sw.toggle([LEFT, TV], TV) == [LEFT]
    assert sw.toggle([], MAIN) == [MAIN]


def test_toggle_keeps_the_order_stable():
    assert sw.toggle([LEFT, MAIN, RIGHT], MAIN) == [LEFT, RIGHT]


def test_the_tv_can_be_switched_off(): 
    """The case this exists for: a TV that scans as if it were on the desk."""
    devices = [m.device for m in DESK]
    devices = sw.toggle(devices, TV)
    assert TV not in devices
    assert sw.selected_indices(DESK, devices) == [2, 3, 4]


# ------------------------------------------------------------------ normalising
def test_normalise_drops_screens_that_are_gone():
    assert sw.normalise([TV, LEFT], DESK) == [TV, LEFT]
    assert sw.normalise([TV, r"\\?\DISPLAY#GONE#9"], DESK) == [TV]


def test_normalise_returns_everything_when_nothing_matches():
    """Switching every screen off would leave the app with no desktop at all."""
    out = sw.normalise([r"\\?\DISPLAY#GONE#9"], DESK)
    assert out == [m.device for m in DESK]


def test_normalise_returns_everything_for_an_empty_selection():
    assert sw.normalise([], DESK) == [m.device for m in DESK]


# ------------------------------------------------------------------ what the wizards are told
def test_calibration_arg_names_only_the_enabled_screens():
    assert sw.calibration_arg(DESK, [LEFT, MAIN]) == "2,3"
    assert sw.calibration_arg(DESK, [MAIN]) == "3"


def test_calibration_arg_is_empty_when_everything_is_enabled():
    """No --monitors argument means the wizard uses its own default: all screens."""
    assert sw.calibration_arg(DESK, [m.device for m in DESK]) == ""
    assert sw.calibration_arg(DESK, []) == ""


def test_calibration_arg_ignores_unknown_devices():
    assert sw.calibration_arg(DESK, [TV, r"\\?\DISPLAY#GONE#9"]) == "1"


def test_summary_reads_like_a_sentence():
    assert sw.summary(DESK, [m.device for m in DESK]) == "all 4 screens tracked"
    assert sw.summary(DESK, [LEFT, MAIN]) == "2 of 4 screens tracked"
    assert sw.summary(DESK, [r"\\?\DISPLAY#GONE#9"]) == "no screens selected"


def test_an_empty_selection_reads_as_every_screen():
    """An empty config means "use everything", not "use nothing" - the window must not imply
    that tracking is off when it is on."""
    assert sw.summary(DESK, []) == "all 4 screens tracked"
    assert sw.summary(DESK, None) == "all 4 screens tracked"
    assert sw.summary(DESK, [""]) == "all 4 screens tracked"


# ------------------------------------------------------------------ style
def test_accent_falls_back_to_the_default_theme():
    assert sw.accent_for("lime") == sw.ACCENTS["lime"]
    assert sw.accent_for("nonsense") == sw.ACCENTS["ember"]
    assert sw.accent_for(None) == sw.ACCENTS["ember"]


def test_every_accent_is_a_dark_theme_pair():
    for name, (head, tail) in sw.ACCENTS.items():
        assert head.startswith("#") and len(head) == 7, name
        assert tail.startswith("#") and len(tail) == 7, name
        assert head != tail


def test_the_window_uses_the_project_font():
    assert sw.FONT_STACK[0] == "Bahnschrift", "the overlay draws in Bahnschrift; match it"


# ------------------------------------------------------------------ hotkey
@pytest.mark.parametrize("name,expected", [("f2", 0x71), ("F2", 0x71), ("f10", 0x79)])
def test_hotkey_names_map_to_virtual_keys(name, expected):
    assert KinesisApp._hotkey_vk(name) == expected


@pytest.mark.parametrize("name", ["", "none", "off", "disabled", "0"])
def test_hotkey_can_be_switched_off(name):
    assert KinesisApp._hotkey_vk(name) == 0


def test_an_unknown_hotkey_disables_itself_rather_than_crashing():
    assert KinesisApp._hotkey_vk("banana") == 0
