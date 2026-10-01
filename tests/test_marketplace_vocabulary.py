"""The client and the marketplace must agree on the vocabulary.

`kinesis/gesture_map.py` and `cloudflare/worker.js` each hold their own copy of the set of poses,
actions and modifiers a Gesture-Map may use. They are two implementations of one rule, in two
languages, in two files - so nothing but a test stops them drifting apart.

That is not hypothetical: the Worker was missing `mouse_hold`, which the client accepts and the
client's own default map uses for the drag binding. The marketplace therefore rejected Kinesis'
own default map, and the Submit button would have failed for anyone publishing a map with a drag
in it. Found by tools/verify_marketplace.py; pinned here so it cannot come back.

If this test fails, the fix is to make the two agree - not to loosen either check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import gesture_map as gm                            # noqa: E402

WORKER = Path(__file__).resolve().parent.parent / "cloudflare" / "worker.js"


def _js_set(name: str) -> set:
    """Pull a `const NAME = new Set([...])` literal out of the worker."""
    source = WORKER.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.S)
    assert match, f"could not find `const {name} = new Set([...])` in {WORKER.name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_worker_and_the_client_accept_the_same_actions():
    assert _js_set("ACTIONS") == set(gm.ACTION_TYPES), (
        "action types drifted between the client and the marketplace worker.\n"
        f"  client only: {sorted(set(gm.ACTION_TYPES) - _js_set('ACTIONS'))}\n"
        f"  worker only: {sorted(_js_set('ACTIONS') - set(gm.ACTION_TYPES))}\n"
        "  the worker rejects anything the client allows, so a valid map fails to submit."
    )


def test_the_worker_and_the_client_accept_the_same_poses():
    assert _js_set("POSES") == set(gm.POSES), (
        f"pose vocabulary drifted: client-only {sorted(set(gm.POSES) - _js_set('POSES'))}, "
        f"worker-only {sorted(_js_set('POSES') - set(gm.POSES))}"
    )


def test_the_worker_and_the_client_accept_the_same_modifiers():
    assert _js_set("MODIFIERS") == set(gm.MODIFIERS), (
        f"modifier vocabulary drifted: client-only {sorted(set(gm.MODIFIERS) - _js_set('MODIFIERS'))}, "
        f"worker-only {sorted(_js_set('MODIFIERS') - set(gm.MODIFIERS))}"
    )


def test_the_default_map_is_accepted_by_the_worker_vocabulary():
    """The specific regression: the default map uses a drag, and the drag is `mouse_hold`."""
    payload = gm.default_map().to_json()
    assert payload["schema"] == "kinesis.gesture-map"
    for binding in payload["bindings"]:
        kind = binding["action"]["type"]
        assert kind in _js_set("ACTIONS"), (
            f"the default map uses action type {kind!r}, which the worker would reject"
        )
        pose = binding["gesture"].get("pose", "")
        if pose:
            assert pose in _js_set("POSES"), (
                f"the default map uses pose {pose!r}, which the worker would reject"
            )


def test_every_shipped_map_is_accepted_by_the_worker_vocabulary():
    """A shipped map that the marketplace refuses would be a broken download for its author."""
    actions, poses = _js_set("ACTIONS"), _js_set("POSES")
    folder = Path(__file__).resolve().parent.parent / "gesture_maps"
    shipped = sorted(folder.glob("*.json"))
    assert shipped, "no shipped maps found - this test would pass vacuously"
    for path in shipped:
        loaded = gm.GestureMap.load(path)
        for binding in loaded.bindings:
            gesture, action = binding.gesture, binding.action
            if not gesture.two_hand:
                assert gesture.posed in poses, \
                    f"{path.name}: pose {gesture.posed!r} is not in the worker's POSES"
            else:
                for side in (gesture.left, gesture.right):
                    if side:
                        assert side in poses, \
                            f"{path.name}: {side!r} is not in the worker's POSES"
            assert action.kind in actions, \
                f"{path.name}: action {action.kind!r} is not in the worker's ACTIONS"
