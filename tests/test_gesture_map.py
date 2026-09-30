"""Gesture-Maps: the file format, the vocabulary, and whether a map actually fires.

The last test in here is the important one: it feeds a hand making a peace sign through the real
GestureEngine and asserts that the map's binding comes out as a key press. A map system that cannot
do that is decoration.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import gesture_map as gm                               # noqa: E402
from kinesis.config import Config                                    # noqa: E402
from kinesis.gestures import GestureEngine                           # noqa: E402
from kinesis.pose import HandPose                                    # noqa: E402


def hand(pose: str = "OPEN", handedness: str = "Right", extended=None, pinches=None) -> HandPose:
    ext = {"thumb": False, "index": False, "middle": False, "ring": False, "pinky": False}
    ext.update(extended or {})
    pin = {"index": False, "middle": False, "ring": False, "pinky": False}
    pin.update(pinches or {})
    return HandPose(handedness=handedness, pose=pose, extended=ext, pinches=pin)


PEACE = hand(extended={"index": True, "middle": True})
POINT = hand(extended={"index": True})
FIST = hand(pose="FIST")
PINCH = hand(pose="MIXED", pinches={"index": True}, extended={"middle": True, "ring": True})


# ------------------------------------------------------------------ the vocabulary
def test_poses_map_onto_what_mediapipe_gives_us():
    assert gm._pose_key(PEACE) == "peace"
    assert gm._pose_key(POINT) == "point"
    assert gm._pose_key(FIST) == "fist"
    assert gm._pose_key(hand(extended={"index": True, "middle": True, "ring": True})) == "three"
    assert gm._pose_key(hand(extended={"thumb": True, "pinky": True})) == "shaka"
    assert gm._pose_key(hand(extended={"pinky": True})) == "pinky"
    assert gm._pose_key(hand(extended={"index": True, "middle": True, "ring": True,
                                       "pinky": True})) == "four"
    assert gm._pose_key(None) == ""


def test_a_pinch_beats_the_pose_it_is_made_with():
    """Thumb+index with other fingers out classifies as MIXED; the user means the pinch."""
    assert gm._pose_key(PINCH) == "pinch_index"


# ------------------------------------------------------------------ validation
def test_a_clean_map_validates():
    assert gm.validate(gm.default_map().to_json()) == []
    for path in sorted((Path(__file__).resolve().parent.parent / "gesture_maps").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert gm.validate(data) == [], f"{path.name} should be importable"


def test_validation_says_what_is_wrong_with_a_bad_file():
    bad = {"schema": "something.else", "version": 1, "bindings": [
        {"gesture": {"pose": "banana"}, "action": {"type": "keys", "keys": ["banana"]}},
        {"gesture": {"pose": "peace"}, "action": {"type": "run_my_code"}},
        {"gesture": {"pose": "peace"}, "action": {"type": "keys", "keys": ["enter"],
                                                  "modifiers": ["hyper"]}},
        {"gesture": {"pose": "peace"}, "action": {"type": "mouse", "button": "elbow"}},
        {"gesture": {"pose": "peace"}, "action": {"type": "system", "value": "launch_missiles"}},
    ]}
    problems = " | ".join(gm.validate(bad))
    assert "not a Kinesis Gesture-Map" in problems
    assert "banana' is not a gesture" in problems
    assert "cannot press 'banana'" in problems
    assert "unknown action type 'run_my_code'" in problems
    assert "unknown modifier(s) hyper" in problems
    assert "unknown mouse button 'elbow'" in problems
    assert "unknown system action 'launch_missiles'" in problems


def test_there_is_no_way_to_express_running_a_program():
    """An imported map must not be able to execute anything - that is a marketplace requirement."""
    assert "app" not in gm.ACTION_TYPES
    assert "text" not in gm.ACTION_TYPES
    assert set(gm.ACTION_TYPES) == {"keys", "hold_keys", "mouse", "mouse_hold", "system", "none"}


def test_a_newer_map_version_is_refused_with_advice():
    data = {"schema": gm.SCHEMA, "version": gm.VERSION + 5, "bindings": []}
    assert any("newer Kinesis" in p for p in gm.validate(data))


# ------------------------------------------------------------------ round trip
def test_a_map_survives_save_and_load(tmp_path):
    original = gm.default_map()
    path = original.save(tmp_path / "mine.json")
    reloaded = gm.GestureMap.load(path)
    assert reloaded.name == original.name
    assert len(reloaded.bindings) == len(original.bindings)
    assert reloaded.bindings[0].gesture.describe() == original.bindings[0].gesture.describe()
    assert reloaded.bindings[0].action.describe() == original.bindings[0].action.describe()


def test_load_active_falls_back_to_the_defaults(tmp_path):
    cfg = Config()
    cfg.set("gesture_map_path", str(tmp_path / "missing.json"))
    assert gm.load_active(cfg).name == gm.default_map().name
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    cfg.set("gesture_map_path", str(tmp_path / "broken.json"))
    assert gm.load_active(cfg).name == gm.default_map().name


# ------------------------------------------------------------------ owning built-ins
def test_a_map_only_claims_a_built_in_when_it_binds_it():
    quiet = gm.GestureMap(name="quiet", bindings=[gm.Binding(gesture=gm.Gesture(posed="peace"))])
    assert quiet.owns("peace") is False            # peace is not a built-in gesture
    assert quiet.owns("pinch_ring") is False
    claim = gm.GestureMap(name="claim", bindings=[
        gm.Binding(gesture=gm.Gesture(posed="pinch_ring"), action=gm.Action(kind="keys",
                                                                            keys=["enter"]))])
    assert claim.owns("pinch_ring") is True
    assert claim.owns("pinch_index") is False


def test_the_default_map_claims_only_what_it_should():
    default = gm.default_map()
    assert default.owns("pinch_index") is True     # it lists the built-in click
    assert default.owns("shaka") is True
    assert default.owns("peace") is False


def test_the_shipped_maps_leave_push_to_talk_alone():
    """shaka is push to talk. A shared map that silently rebinds it would be a nasty surprise."""
    folder = Path(__file__).resolve().parent.parent / "gesture_maps"
    for path in sorted(folder.glob("*.json")):
        loaded = gm.GestureMap.load(path)
        assert not loaded.owns("shaka"), f"{path.name} steals push to talk"


# ------------------------------------------------------------------ matching
def test_a_two_hand_binding_needs_both_hands():
    binding = gm.Binding(gesture=gm.Gesture(left="fist", right="peace"), action=gm.Action())
    gmap = gm.GestureMap(bindings=[binding])
    assert gmap.matching(FIST, PEACE) == [binding]
    assert gmap.matching(FIST, POINT) == []
    assert gmap.matching(None, PEACE) == []


def test_handedness_is_respected():
    binding = gm.Binding(gesture=gm.Gesture(posed="peace", hand="left"), action=gm.Action())
    gmap = gm.GestureMap(bindings=[binding])
    left = hand(extended={"index": True, "middle": True}, handedness="Left")
    assert gmap.matching(left, None) == [binding]
    assert gmap.matching(PEACE, None) == []        # PEACE is a right hand


def test_a_disabled_binding_never_matches():
    binding = gm.Binding(gesture=gm.Gesture(posed="peace"), action=gm.Action(), enabled=False)
    assert gm.GestureMap(bindings=[binding]).matching(PEACE, None) == []


# ------------------------------------------------------------------ does it actually fire?
def test_the_engine_turns_a_mapped_gesture_into_a_keypress():
    cfg = Config()
    cfg.set("cursor_source", "hand")
    engine = GestureEngine(cfg)
    gmap = gm.GestureMap(name="test", bindings=[
        gm.Binding(id="peace-ctrl-t", gesture=gm.Gesture(posed="peace"),
                   action=gm.Action(kind="keys", keys=["t"], modifiers=["ctrl"]))])
    engine.load_map(gmap)

    fired = []
    for i in range(6):
        fired += engine.update([PEACE], 0, 1.0 + i * 0.04, 0.04)
    taps = [intent for intent in fired if intent.kind == "keys.tap"]
    assert taps, "a mapped gesture must produce a key press"
    assert taps[0].keys == ("ctrl", "t")


def test_a_mapped_gesture_fires_once_not_every_frame():
    cfg = Config()
    engine = GestureEngine(cfg)
    engine.load_map(gm.GestureMap(bindings=[
        gm.Binding(id="peace", gesture=gm.Gesture(posed="peace"),
                   action=gm.Action(kind="keys", keys=["t"]))]))
    count = 0
    for i in range(20):
        count += len([x for x in engine.update([PEACE], 0, 1.0 + i * 0.04, 0.04)
                      if x.kind == "keys.tap"])
    assert count == 1, f"a tap should fire once per make, not {count} times"


def test_mapping_a_built_in_stops_the_built_in_from_firing(monkeypatch):
    """Claim the ring pinch as a key press and the drag must not also start."""
    cfg = Config()
    cfg.set("cursor_source", "hand")
    engine = GestureEngine(cfg)
    engine.load_map(gm.GestureMap(bindings=[
        gm.Binding(id="ring", gesture=gm.Gesture(posed="pinch_ring"),
                   action=gm.Action(kind="keys", keys=["escape"]))]))
    assert engine.map_owns("pinch_ring") is True
    ring = hand(pose="MIXED", pinches={"ring": True})
    fired = []
    for i in range(6):
        fired += engine.update([ring], 0, 1.0 + i * 0.04, 0.04)
    assert any(x.kind == "keys.tap" for x in fired)
    assert engine.active != "drag", "the built-in drag should have stood down"


def test_a_hold_action_presses_and_releases():
    cfg = Config()
    engine = GestureEngine(cfg)
    engine.load_map(gm.GestureMap(bindings=[
        gm.Binding(id="peace-hold", gesture=gm.Gesture(posed="peace"),
                   action=gm.Action(kind="hold_keys", keys=["w"]))]))
    down = []
    for i in range(4):
        down += engine.update([PEACE], 0, 1.0 + i * 0.04, 0.04)
    up = engine.update([hand()], 0, 1.2, 0.04)
    assert any(x.kind == "keys.down" and x.keys == ("w",) for x in down)
    assert any(x.kind == "keys.up" and x.keys == ("w",) for x in up)


def test_actions_become_the_right_intents():
    from kinesis.gestures import map_action_intents
    click = map_action_intents(gm.Action(kind="mouse", button="right"), None)
    assert click[0].kind == "mouse.click" and click[0].button == "right"
    wheel = map_action_intents(gm.Action(kind="mouse", button="wheel_up"), None)
    assert wheel[0].kind == "mouse.wheel" and wheel[0].amount == 1
    minimise = map_action_intents(gm.Action(kind="system", value="minimise"), None)
    assert minimise[0].kind == "window.minimise"
    tab = map_action_intents(gm.Action(kind="system", value="ctrl_tab_prev"), None)
    assert tab[0].kind == "keys.tap" and tab[0].keys == ("ctrl", "shift", "tab")
    assert map_action_intents(gm.Action(kind="none"), None) == []
