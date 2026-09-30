"""Choosing which screens Kinesis may use, and the desktop narrowing that follows from it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from types import SimpleNamespace                              # noqa: E402

from kinesis import monitor_set as ms                          # noqa: E402
from kinesis import winapi as w                                # noqa: E402


def _mon(device, left, top=0, width=1920, height=1080, primary=False):
    return SimpleNamespace(device=device, left=left, top=top, right=left + width,
                           bottom=top + height, width=width, height=height, primary=primary)


DESK = [
    _mon(r"\\?\DISPLAY#DTV0000#1", -3840),
    _mon(r"\\?\DISPLAY#KAMN27#2", -1920),
    _mon(r"\\?\DISPLAY#LEN27#3", 0, primary=True),
    _mon(r"\\?\DISPLAY#LEN27#4", 1920),
]


# ------------------------------------------------------------------ selecting
def test_no_selection_means_every_screen():
    kept, dropped = ms.select_monitors(DESK, [])
    assert kept == DESK and dropped == []
    kept, dropped = ms.select_monitors(DESK, None)
    assert kept == DESK and dropped == []


def test_selection_keeps_only_the_named_devices():
    kept, dropped = ms.select_monitors(DESK, [DESK[1].device, DESK[2].device])
    assert [m.device for m in kept] == [DESK[1].device, DESK[2].device]
    assert [m.device for m in dropped] == [DESK[0].device, DESK[3].device]


def test_a_selection_that_matches_nothing_falls_back_to_everything():
    """Monitors change. An unrecognised saved device must not leave the app with no screens."""
    kept, dropped = ms.select_monitors(DESK, [r"\\?\DISPLAY#GONE#9"])
    assert kept == DESK and dropped == []


def test_union_rect_covers_exactly_the_selected_screens():
    kept, _ = ms.select_monitors(DESK, [DESK[1].device, DESK[2].device])
    assert ms.union_rect(kept) == (-1920, 0, 3840, 1080)
    assert ms.union_rect([]) == (0, 0, 0, 0)


# ------------------------------------------------------------------ the question
def test_parse_selection_accepts_the_obvious_answers():
    assert ms.parse_selection("2", 4) == [2]
    assert ms.parse_selection("1,3", 4) == [1, 3]
    assert ms.parse_selection("1 3", 4) == [1, 3]
    assert ms.parse_selection("3,3,1", 4) == [3, 1]        # deduped, order kept
    assert ms.parse_selection("", 4) == [1, 2, 3, 4]
    assert ms.parse_selection("all", 4) == [1, 2, 3, 4]
    assert ms.parse_selection("primary", 4) == [1]


def test_parse_selection_rejects_nonsense_rather_than_guessing():
    assert ms.parse_selection("0", 4) is None
    assert ms.parse_selection("5", 4) is None
    assert ms.parse_selection("one", 4) is None
    assert ms.parse_selection("1,x", 4) is None


def test_ask_returns_the_chosen_devices():
    lines = []
    devices = ms.ask(DESK, input_fn=lambda prompt: "1,4", print_fn=lines.append)
    assert devices == [DESK[0].device, DESK[3].device]
    text = "\n".join(lines)
    assert "WHICH SCREENS" in text
    assert "primary" in text                       # the listing has to say which one is primary
    assert "left out" in text


def test_ask_lists_every_screen_by_number():
    lines = []
    ms.ask(DESK, input_fn=lambda prompt: "1", print_fn=lines.append)
    listing = [t for t in lines if t.strip().startswith("[")]
    assert len(listing) == 4
    assert listing[2].strip().startswith("[3]")


def test_ask_on_all_keeps_all_and_says_so():
    lines = []
    devices = ms.ask(DESK, input_fn=lambda prompt: "", print_fn=lines.append)
    assert devices == [m.device for m in DESK]
    assert any("using all 4 screens" in t for t in lines)


def test_ask_falls_back_to_all_when_the_answer_is_nonsense():
    lines = []
    devices = ms.ask(DESK, input_fn=lambda prompt: "banana", print_fn=lines.append)
    assert devices == [m.device for m in DESK]
    assert any("did not understand" in t for t in lines)


def test_ask_returns_nothing_when_interrupted():
    def boom(prompt):
        raise EOFError
    assert ms.ask(DESK, input_fn=boom, print_fn=lambda *a: None) is None


def test_hardware_labels_name_the_screen_by_model():
    """The listing has to help someone map a number to a physical screen."""
    hardware = [SimpleNamespace(model='42" TV'), SimpleNamespace(edid_name="Kogan 27"),
                SimpleNamespace(model=""), SimpleNamespace(model="L27i-30")]
    labels = ms.labels_from_hardware(hardware, DESK)
    rows = ms.describe(DESK, labels)
    assert "42" in rows[0] and "Kogan 27" in rows[1]
    assert "L27i-30" in rows[3]
    assert rows[2].strip().startswith("[3]") and "LEN27#3" in rows[2], \
        "a screen with no EDID name must still be identifiable"


def test_hardware_labels_tolerate_a_short_or_missing_hardware_list():
    assert ms.labels_from_hardware([], DESK) == {}
    assert ms.labels_from_hardware(None, DESK) == {}
    assert ms.labels_from_hardware([SimpleNamespace(model="x")], DESK) == {
        DESK[0].device: "x"}


# ------------------------------------------------------------------ narrowing the desktop
def test_virtual_screen_narrows_to_the_selected_screens():
    """Every consumer asks for the virtual screen, so this is what makes the choice apply."""
    try:
        before = w.virtual_screen()
        w.set_virtual_screen_override((-1920, 0, 3840, 1080))
        assert w.virtual_screen() == (-1920, 0, 3840, 1080)
    finally:
        w.set_virtual_screen_override(None)
    assert w.virtual_screen() == before, "clearing the override must restore the real desktop"


def test_a_degenerate_override_is_ignored():
    """A zero-sized rect would silently make every coordinate invalid."""
    try:
        before = w.virtual_screen()
        w.set_virtual_screen_override((0, 0, 0, 0))
        assert w.virtual_screen() == before
    finally:
        w.set_virtual_screen_override(None)
