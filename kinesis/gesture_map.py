"""Gesture-Maps: the user's own gesture -> action bindings, in a shareable file.

A Gesture-Map is a JSON document. That is the whole point: it can be exported, sent to someone,
dropped into the marketplace, and imported back, and importing one cannot run code - only the action
types below, which the engine already knows how to perform.

The vocabulary is deliberately limited to what MediaPipe actually hands us (`pose`, per-finger
`pinches`, per-finger `extended`, and handedness), so a map can never promise a gesture the tracker
cannot see:

    poses      open, fist, point, shaka, peace, three, four, pinky
    pinches    pinch_index, pinch_middle, pinch_ring, pinch_pinky
    two hands  any of the above on the left, the right, or either, at the same time

Built-in gestures (click, right click, drag, push to talk, minimise, maximise, the tab gestures, the
zoom) stay in charge unless a map explicitly claims one - then the map owns it and the built-in stops
firing for that gesture. Everything else in the vocabulary is free to bind.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = "kinesis.gesture-map"
VERSION = 1

# ---------------------------------------------------------------- vocabulary
POSES: Dict[str, str] = {
    "open": "open hand, all fingers out",
    "fist": "closed fist",
    "point": "index finger only",
    "peace": "index + middle out (peace sign)",
    "three": "index + middle + ring out",
    "four": "four fingers out, thumb tucked",
    "pinky": "pinky only",
    "shaka": "thumb + pinky out",
    "pinch_index": "thumb + index pinched together",
    "pinch_middle": "thumb + middle pinched",
    "pinch_ring": "thumb + ring pinched",
    "pinch_pinky": "thumb + pinky pinched",
}

# Which of those the built-in engine already uses, and what for. Shown in the editor so nobody
# silently steals the drag.
BUILT_IN: Dict[str, str] = {
    "pinch_index": "left click",
    "pinch_middle": "right click",
    "pinch_ring": "drag",
    "shaka": "push to talk",
    "open": "the open half of minimise / maximise",
    "fist": "the fist half of minimise / maximise",
}

HANDS = ("left", "right", "either")
HOLD = ("tap", "hold")

# Deliberately no "run a program" and no "type text": an imported map presses keys, clicks, or
# moves a window, and nothing else. That is what makes a shared file safe to open.
ACTION_TYPES = ("keys", "hold_keys", "mouse", "mouse_hold", "system", "none")
MOUSE_BUTTONS = ("left", "right", "middle", "double", "wheel_up", "wheel_down")
SYSTEM_ACTIONS = ("minimise", "maximise", "fullscreen", "alt_tab", "ctrl_tab_next", "ctrl_tab_prev")

MODIFIERS = ("ctrl", "shift", "alt", "win")


# ---------------------------------------------------------------- model
@dataclass
class Gesture:
    """One gesture, on one hand or on both at once."""

    posed: str = ""                      # a key of POSES, for a one-hand gesture
    hand: str = "either"
    left: str = ""                       # two-hand: the pose the left hand must be making
    right: str = ""                      # two-hand: and the right

    @property
    def two_hand(self) -> bool:
        return bool(self.left or self.right)

    def describe(self) -> str:
        if self.two_hand:
            l = POSES.get(self.left, self.left or "anything")
            r = POSES.get(self.right, self.right or "anything")
            return f"LEFT {l}  +  RIGHT {r}"
        where = "" if self.hand == "either" else f"{self.hand} hand: "
        return where + POSES.get(self.posed, self.posed)

    def to_json(self) -> dict:
        if self.two_hand:
            return {k: v for k, v in (("left", self.left), ("right", self.right)) if v}
        return {"pose": self.posed, "hand": self.hand}

    @staticmethod
    def from_json(data: dict) -> "Gesture":
        return Gesture(posed=str(data.get("pose", "")), hand=str(data.get("hand", "either")),
                       left=str(data.get("left", "")), right=str(data.get("right", "")))


@dataclass
class Action:
    """What a gesture does. Only these types exist - an imported map cannot run code."""

    kind: str = "none"
    keys: List[str] = field(default_factory=list)          # for keys / hold_keys
    modifiers: List[str] = field(default_factory=list)     # ctrl / shift / alt / win
    button: str = "left"                                   # for mouse
    value: str = ""                                        # system name, text to type, or command
    note: str = ""

    def describe(self) -> str:
        if self.kind in ("keys", "hold_keys"):
            combo = "+".join([*self.modifiers, *self.keys])
            return combo + ("  (held)" if self.kind == "hold_keys" else "")
        if self.kind == "mouse_hold":
            return f"drag with the {self.button} button held"
        if self.kind == "mouse":
            return f"mouse {self.button.replace('_', ' ')}"
        if self.kind == "system":
            return self.value.replace("_", " ")
        if self.kind == "text":
            shown = self.value if len(self.value) <= 24 else self.value[:21] + "..."
            return f"type {shown!r}"
        if self.kind == "app":
            return f"run {self.value}"
        return "nothing"

    def to_json(self) -> dict:
        out: dict = {"type": self.kind}
        if self.kind in ("keys", "hold_keys"):
            out["keys"] = list(self.keys)
            if self.modifiers:
                out["modifiers"] = list(self.modifiers)
        elif self.kind in ("mouse", "mouse_hold"):
            out["button"] = self.button
        elif self.kind in ("system", "text", "app"):
            out["value"] = self.value
        if self.note:
            out["note"] = self.note
        return out

    @staticmethod
    def from_json(data: dict) -> "Action":
        return Action(kind=str(data.get("type", "none")),
                      keys=[str(k) for k in data.get("keys", [])],
                      modifiers=[str(m) for m in data.get("modifiers", [])],
                      button=str(data.get("button", "left")),
                      value=str(data.get("value", "")),
                      note=str(data.get("note", "")))

    @property
    def combo(self) -> Tuple[str, ...]:
        """The key tuple the engine presses, modifiers first."""
        return tuple([*self.modifiers, *self.keys])


@dataclass
class Binding:
    gesture: Gesture = field(default_factory=Gesture)
    action: Action = field(default_factory=Action)
    enabled: bool = True
    id: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "enabled": self.enabled, "gesture": self.gesture.to_json(),
                "action": self.action.to_json()}

    @staticmethod
    def from_json(data: dict) -> "Binding":
        return Binding(gesture=Gesture.from_json(data.get("gesture", {})),
                       action=Action.from_json(data.get("action", {})),
                       enabled=bool(data.get("enabled", True)),
                       id=str(data.get("id", "")))


@dataclass
class GestureMap:
    name: str = "My gesture map"
    author: str = ""
    description: str = ""
    bindings: List[Binding] = field(default_factory=list)
    created: str = ""

    # ------------------------------------------------------------ files
    def to_json(self) -> dict:
        return {
            "schema": SCHEMA,
            "version": VERSION,
            "name": self.name,
            "author": self.author,
            "description": self.description,
            "created": self.created or datetime.date.today().isoformat(),
            "bindings": [b.to_json() for b in self.bindings],
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def from_json(data: dict) -> "GestureMap":
        return GestureMap(name=str(data.get("name", "imported")),
                          author=str(data.get("author", "")),
                          description=str(data.get("description", "")),
                          created=str(data.get("created", "")),
                          bindings=[Binding.from_json(b) for b in data.get("bindings", [])])

    @staticmethod
    def load(path: Path) -> "GestureMap":
        return GestureMap.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------ behaviour
    def owns(self, pose: str) -> bool:
        """True when this map has claimed a built-in gesture, so the built-in must stand down."""
        if pose not in BUILT_IN:
            return False
        return any(b.enabled and not b.gesture.two_hand and b.gesture.posed == pose
                   and str(b.gesture.hand) in ("either",) for b in self.bindings)

    def matching(self, left, right) -> List[Binding]:
        """Bindings whose gesture the given hands are making right now."""
        out = []
        for b in self.bindings:
            if not b.enabled:
                continue
            if b.gesture.two_hand:
                if _pose_of(left) == b.gesture.left or not b.gesture.left:
                    if _pose_of(right) == b.gesture.right or not b.gesture.right:
                        if (b.gesture.left or b.gesture.right) and (left or right):
                            out.append(b)
                continue
            for hand in (left, right):
                if hand is None:
                    continue
                if b.gesture.hand != "either" and str(getattr(hand, "handedness", "")).lower() \
                        != b.gesture.hand:
                    continue
                if _pose_key(hand) == b.gesture.posed:
                    out.append(b)
                    break
        return out


def _pose_of(hand) -> str:
    return _pose_key(hand) if hand is not None else ""


def _pose_key(hand) -> str:
    """The vocabulary name for what this hand is doing.

    Pinches win over poses: a thumb+index pinch with the other fingers out classifies as MIXED by
    pose, and the pinch is what the user means.
    """
    if hand is None:
        return ""
    pinches = getattr(hand, "pinches", {}) or {}
    for finger in ("index", "middle", "ring", "pinky"):
        if pinches.get(finger):
            return f"pinch_{finger}"
    extended = getattr(hand, "extended", {}) or {}
    out = [f for f in ("index", "middle", "ring", "pinky") if extended.get(f)]
    thumb = bool(extended.get("thumb"))
    if len(out) == 0:
        return "shaka" if thumb else "fist"
    if len(out) == 1 and out[0] == "pinky" and thumb:
        return "shaka"
    if len(out) == 1 and out[0] == "pinky":
        return "pinky"
    if len(out) == 1 and out[0] == "index":
        return "point"
    if out == ["index", "middle"]:
        return "peace"
    if out == ["index", "middle", "ring"]:
        return "three"
    if len(out) == 4:
        return "four"
    return "open" if len(out) >= 4 else str(getattr(hand, "pose", "")).lower() or "open"


# ---------------------------------------------------------------- defaults
def default_map() -> GestureMap:
    """The built-in thirteen, expressed as a map, so the editor opens on something real."""
    def keys(*combo, note=""):
        mods = [c for c in combo if c in MODIFIERS]
        rest = [c for c in combo if c not in MODIFIERS]
        return Action(kind="keys", keys=rest, modifiers=mods, note=note)

    return GestureMap(
        name="Kinesis defaults",
        author="Kinesis",
        description="The gestures Kinesis ships with. Clicks, drags and push to talk are built in; "
                    "the rest are listed here so they can be changed.",
        bindings=[
            Binding(id="default-click", gesture=Gesture(posed="pinch_index"),
                    action=Action(kind="mouse", button="left", note="built in"),
                    enabled=True),
            Binding(id="default-right-click", gesture=Gesture(posed="pinch_middle"),
                    action=Action(kind="mouse", button="right", note="built in"), enabled=True),
            Binding(id="default-drag", gesture=Gesture(posed="pinch_ring"),
                    action=Action(kind="mouse_hold", button="left", note="built in: drag"),
                    enabled=True),
            Binding(id="default-ptt", gesture=Gesture(posed="shaka"),
                    action=keys("ctrl", "space", note="built in"), enabled=True),
            Binding(id="default-minimise", gesture=Gesture(posed="fist"),
                    action=Action(kind="system", value="minimise", note="built in"), enabled=True),
            Binding(id="default-maximise", gesture=Gesture(posed="open"),
                    action=Action(kind="system", value="maximise", note="built in"), enabled=True),
        ])


def empty_map() -> GestureMap:
    return GestureMap(name="New gesture map", bindings=[])


# ---------------------------------------------------------------- validation
def _key_known(name: object) -> bool:
    """Can Kinesis actually press this? An imported map from anywhere must not break at runtime."""
    try:
        from .winapi import VK
    except Exception:                                     # pragma: no cover - import guard
        return True
    return str(name).strip().lower() in VK


def active_map_path(cfg) -> Path:
    """Where the user's current map lives. Defaults to the config folder, not the repo."""
    stored = str(cfg.get("gesture_map_path") or "").strip()
    if stored:
        return Path(stored)
    return Path(__file__).resolve().parent.parent / "gesture_map.json"


def load_active(cfg) -> "GestureMap":
    """The map to run with: the user's if they have one, otherwise the shipped defaults."""
    path = active_map_path(cfg)
    if path.is_file():
        try:
            loaded = GestureMap.load(path)
            if not validate(loaded.to_json()):
                return loaded
            print(f"[map] {path.name} has problems, using the defaults instead: "
                  f"{validate(loaded.to_json())[0]}")
        except Exception as exc:
            print(f"[map] could not read {path}: {exc} - using the defaults")
    return default_map()


def validate(data: dict) -> List[str]:
    """Everything wrong with a candidate file, in plain words. Empty list = importable."""
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["that file is not a Gesture-Map (it is not even a JSON object)"]
    if str(data.get("schema", "")) != SCHEMA:
        problems.append(f"not a Kinesis Gesture-Map: schema is {data.get('schema')!r}, "
                        f"expected {SCHEMA!r}")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        problems.append("missing or unreadable version")
    elif version > VERSION:
        problems.append(f"made by a newer Kinesis (map version {version}, this build reads "
                        f"{VERSION}) - update Kinesis")
    bindings = data.get("bindings")
    if bindings is None:
        problems.append("no bindings in the file")
        return problems
    if not isinstance(bindings, list):
        problems.append("bindings is not a list")
        return problems
    for i, raw in enumerate(bindings, 1):
        if not isinstance(raw, dict):
            problems.append(f"binding {i} is not an object")
            continue
        gesture = raw.get("gesture") or {}
        action = raw.get("action") or {}
        pose = str(gesture.get("pose", ""))
        two_hand = bool(gesture.get("left") or gesture.get("right"))
        if not two_hand and pose not in POSES:
            problems.append(f"binding {i}: {pose!r} is not a gesture Kinesis can see. "
                            f"Known: {', '.join(sorted(POSES))}")
        for side in ("left", "right"):
            val = str(gesture.get(side, ""))
            if val and val not in POSES:
                problems.append(f"binding {i}: {side} hand {val!r} is not a known gesture")
        hand = str(gesture.get("hand", "either"))
        if hand not in HANDS:
            problems.append(f"binding {i}: hand must be one of {', '.join(HANDS)}")
        kind = str(action.get("type", "none"))
        if kind not in ACTION_TYPES:
            problems.append(f"binding {i}: unknown action type {kind!r}")
        if kind in ("keys", "hold_keys"):
            keys = action.get("keys", [])
            if not keys:
                problems.append(f"binding {i}: a key action needs at least one key")
            unknown = [k for k in keys if not _key_known(k)]
            if unknown:
                problems.append(f"binding {i}: Kinesis cannot press "
                                f"{', '.join(repr(u) for u in unknown)}")
            bad = [m for m in action.get("modifiers", []) if m not in MODIFIERS]
            if bad:
                problems.append(f"binding {i}: unknown modifier(s) {', '.join(bad)}")
        if kind in ("mouse", "mouse_hold") and str(action.get("button", "left")) not in MOUSE_BUTTONS:
            problems.append(f"binding {i}: unknown mouse button {action.get('button')!r}")
        if kind == "system" and str(action.get("value", "")) not in SYSTEM_ACTIONS:
            problems.append(f"binding {i}: unknown system action {action.get('value')!r}")
        if kind == "app" and not str(action.get("value", "")).strip():
            problems.append(f"binding {i}: an app action needs a command to run")
    return problems
