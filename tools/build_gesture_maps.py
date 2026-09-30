"""Build the ten shipped Gesture-Maps, and the SQL to seed the marketplace with them.

Run it from the Kinesis folder:

    .venv\\Scripts\\python.exe tools\\build_gesture_maps.py

It writes gesture_maps/*.json and cloudflare/seed.sql. Every map is validated with the same
`gesture_map.validate()` the importer uses, so a map that cannot be imported cannot be shipped, and
it prints the validation result for each one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinesis.gesture_map import Action, Binding, Gesture, GestureMap, validate   # noqa: E402

MODS = ("ctrl", "shift", "alt", "win")


def keys(*combo: str, note: str = "") -> Action:
    return Action(kind="keys", keys=[c for c in combo if c not in MODS],
                  modifiers=[c for c in combo if c in MODS], note=note)


def hold(*combo: str, note: str = "") -> Action:
    return Action(kind="hold_keys", keys=[c for c in combo if c not in MODS],
                  modifiers=[c for c in combo if c in MODS], note=note)


def mouse(button: str, note: str = "") -> Action:
    return Action(kind="mouse", button=button, note=note)


def system(value: str, note: str = "") -> Action:
    return Action(kind="system", value=value, note=note)


def one(pose: str, action: Action, hand: str = "either", ident: str = "") -> Binding:
    return Binding(id=ident or f"{pose}-{len(action.combo)}", gesture=Gesture(posed=pose, hand=hand),
                   action=action)


def two(left: str, right: str, action: Action, ident: str = "") -> Binding:
    return Binding(id=ident or f"{left}-{right}", gesture=Gesture(left=left, right=right),
                   action=action)


def browser() -> GestureMap:
    return GestureMap(
        name="Browser Power User",
        author="Kinesis",
        description="Tabs, pages and search without leaving the page you are reading. "
                    "Peace sign is a new tab, three fingers closes one.",
        bindings=[
            one("peace", keys("ctrl", "t"), ident="new-tab"),
            one("three", keys("ctrl", "w"), ident="close-tab"),
            one("four", keys("ctrl", "shift", "t"), ident="reopen-tab"),
            one("pinky", keys("ctrl", "tab"), ident="next-tab"),
            one("pinch_pinky", keys("ctrl", "shift", "tab"), ident="prev-tab"),
            one("point", keys("ctrl", "l"), ident="address-bar"),
            two("open", "peace", keys("ctrl", "shift", "n"), ident="new-window"),
            two("fist", "point", keys("ctrl", "shift", "tab"), ident="reopen-last"),
        ])


def media() -> GestureMap:
    return GestureMap(
        name="Media Player",
        author="Kinesis",
        description="Play, seek, volume and mute for YouTube, Spotify or VLC. "
                    "Works anywhere a space bar does.",
        bindings=[
            one("peace", keys("space"), ident="play-pause"),
            one("pinky", keys("right"), ident="seek-forward"),
            one("point", keys("left"), ident="seek-back"),
            one("three", keys("up"), ident="volume-up"),
            one("four", keys("down"), ident="volume-down"),
            one("pinch_pinky", keys("m"), ident="mute"),
            two("fist", "peace", keys("f"), ident="fullscreen"),
        ])


def windows() -> GestureMap:
    return GestureMap(
        name="Windows Navigation",
        author="Kinesis",
        description="Snap windows, task view and show desktop. Three fingers for task view, "
                    "pinch your little finger to drop back to the desktop.",
        bindings=[
            one("three", keys("win", "tab"), ident="task-view"),
            one("four", keys("win", "d"), ident="show-desktop"),
            one("point", keys("win", "left"), ident="snap-left"),
            one("peace", keys("win", "right"), ident="snap-right"),
            one("pinky", keys("win", "up"), ident="maximise"),
            one("pinch_pinky", keys("win", "down"), ident="restore"),
            two("open", "fist", keys("alt", "tab"), ident="alt-tab"),
        ])


def video_edit() -> GestureMap:
    return GestureMap(
        name="Video Editing",
        author="Kinesis",
        description="Scrub, cut and undo on the timeline. Peace plays, three fingers cuts.",
        bindings=[
            one("peace", keys("space"), ident="play"),
            one("point", keys("left"), ident="step-back"),
            one("pinky", keys("right"), ident="step-fwd"),
            one("three", keys("ctrl", "k"), ident="cut"),
            one("four", keys("ctrl", "z"), ident="undo"),
            one("pinch_pinky", keys("m"), ident="marker"),
            two("fist", "three", keys("ctrl", "shift", "z"), ident="redo"),
        ])


def presentation() -> GestureMap:
    return GestureMap(
        name="Presentation Remote",
        author="Kinesis",
        description="Drive a slide deck from across the room. Peace advances, a point goes back, "
                    "pinch your little finger to black the screen.",
        bindings=[
            one("peace", keys("right"), ident="next-slide"),
            one("point", keys("left"), ident="prev-slide"),
            one("three", keys("f5"), ident="start-show"),
            one("pinch_pinky", keys("b"), ident="black-screen"),
            one("pinky", keys("esc"), ident="end-show"),
            one("four", keys("b"), ident="toggle-black"),
        ])


def coding() -> GestureMap:
    return GestureMap(
        name="Coding / IDE",
        author="Kinesis",
        description="Command palette, go-to-file, find references and the terminal. "
                    "Built for VS Code, works in any JetBrains editor with the same habits.",
        bindings=[
            one("peace", keys("ctrl", "shift", "p"), ident="command-palette"),
            one("point", keys("ctrl", "p"), ident="go-to-file"),
            one("three", keys("shift", "f12"), ident="find-references"),
            one("four", keys("ctrl", "/"), ident="comment-line"),
            one("pinky", keys("ctrl", "`"), ident="terminal"),
            one("pinch_pinky", keys("ctrl", "shift", "g"), ident="git-panel"),
            two("fist", "peace", keys("ctrl", "shift", "f"), ident="search-all"),
        ])


def one_handed() -> GestureMap:
    return GestureMap(
        name="One-Handed Essentials",
        author="Kinesis",
        description="The whole set on your right hand only, for when the other hand is busy, "
                    "holding something, or out of action.",
        bindings=[
            one("point", keys("enter"), hand="right", ident="confirm"),
            one("peace", keys("tab"), hand="right", ident="next-field"),
            one("three", keys("esc"), hand="right", ident="cancel"),
            one("four", keys("alt", "tab"), hand="right", ident="switch-app"),
            one("pinky", keys("ctrl", "z"), hand="right", ident="undo"),
            one("pinch_pinky", keys("ctrl", "shift", "t"), hand="right", ident="reopen"),
        ])


def meeting() -> GestureMap:
    return GestureMap(
        name="Meeting Controls",
        author="Kinesis",
        description="Mute, camera, raise hand and hang up, matching the Teams and Zoom shortcuts.",
        bindings=[
            one("peace", keys("ctrl", "shift", "m"), ident="mute"),
            one("three", keys("ctrl", "shift", "v"), ident="camera"),
            one("point", keys("ctrl", "shift", "u"), ident="raise-hand"),
            one("four", keys("ctrl", "shift", "h"), ident="hang-up"),
            one("pinky", keys("ctrl", "shift", "e"), ident="share-screen"),
        ])


def reading() -> GestureMap:
    return GestureMap(
        name="Reading Comfort",
        author="Kinesis",
        description="Page through and resize long articles without touching the keyboard. "
                    "Three fingers zooms in, four zooms out.",
        bindings=[
            one("peace", keys("pagedown"), ident="page-down"),
            one("point", keys("pageup"), ident="page-up"),
            one("three", keys("ctrl", "+"), ident="zoom-in"),
            one("four", keys("ctrl", "-"), ident="zoom-out"),
            one("pinky", keys("ctrl", "0"), ident="zoom-reset"),
            one("pinch_pinky", keys("ctrl", "d"), ident="bookmark"),
            two("fist", "four", keys("home"), ident="top-of-page"),
        ])


def gaming() -> GestureMap:
    return GestureMap(
        name="Gaming Hotbar",
        author="Kinesis",
        description="Slots one to five on your fingers, map and inventory on the extra gestures. "
                    "Keyboard-only games; anything with an anti-cheat will ignore injected input.",
        bindings=[
            one("peace", keys("1"), ident="slot-1"),
            one("three", keys("2"), ident="slot-2"),
            one("four", keys("3"), ident="slot-3"),
            one("pinky", keys("4"), ident="slot-4"),
            one("point", keys("5"), ident="slot-5"),
            one("pinch_pinky", keys("tab"), ident="map"),
            two("fist", "pinky", keys("m"), ident="inventory"),
        ])


BUILDERS = [browser, media, windows, video_edit, presentation, coding, one_handed, meeting,
            reading, gaming]


def main() -> int:
    out_dir = ROOT / "gesture_maps"
    out_dir.mkdir(exist_ok=True)
    rows = []
    failures = 0
    for build in BUILDERS:
        gmap = build()
        data = gmap.to_json()
        problems = validate(data)
        status = "ok" if not problems else "INVALID: " + "; ".join(problems)
        if problems:
            failures += 1
        print(f"{gmap.name:<26} {len(gmap.bindings)} bindings  {status}")
        slug = gmap.name.lower().replace("/", "-").replace(" ", "-")
        path = out_dir / f"{slug}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        rows.append((slug, gmap, data))

    # seed.sql: publish the ten so the marketplace is not empty on day one
    lines = ["-- Generated by tools/build_gesture_maps.py - the ten shipped Gesture-Maps.",
             "-- Apply with:  wrangler d1 execute kinesis-gesture-maps --remote --file=seed.sql",
             ""]
    for slug, gmap, data in rows:
        title = gmap.name.replace("'", "''")
        desc = gmap.description.replace("'", "''")
        payload = json.dumps(data).replace("'", "''")
        lines.append(
            "INSERT OR IGNORE INTO gesture_maps\n"
            "  (id, slug, title, description, author_name, github_url, map_json, binding_count,\n"
            "   downloads, featured, created_at)\n"
            "VALUES (\n"
            f"  '{(slug + '-0000-0000-0000-000000000000')[:36]}', '{slug}', '{title}', '{desc}',\n"
            "  'Kinesis', 'https://github.com/DrGekoz/Kinesis',\n"
            f"  '{payload}', {len(gmap.bindings)}, 0, 1, '2026-10-01T00:00:00Z'\n"
            ");\n")
    (ROOT / "cloudflare" / "seed.sql").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {len(rows)} maps to gesture_maps/ and cloudflare/seed.sql")
    if failures:
        print(f"{failures} map(s) FAILED validation - fix before shipping")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
