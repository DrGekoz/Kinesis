"""Which monitors Kinesis is allowed to use.

A four-screen desk is not four usable screens. The outer ones sit at extreme angles from wherever
anyone actually sits, and gaze there is guesswork - measured on this desk, the two outer screens
scored 0% while the two inner ones scored 100%. Letting the user say which screens they actually use
keeps calibration, cursor targeting, gaze and the overlays on those screens only.

The selection is stored as *device names*, not indices: Windows reorders monitors between sessions
and between docks, and "the Kogan" should stay the Kogan after a reboot.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

DEFER = "never asked"


def devices_of(monitors: Sequence) -> List[str]:
    return [str(getattr(m, "device", "") or "") for m in monitors]


def select_monitors(monitors: Sequence, enabled: Optional[Sequence[str]]) -> Tuple[List, List]:
    """Split monitors into (enabled, disabled) by device name.

    An empty or unrecognised selection means everything: never make the app unusable because a saved
    device name no longer exists.
    """
    monitors = list(monitors)
    wanted = {str(d) for d in (enabled or []) if str(d)}
    if not wanted:
        return monitors, []
    kept = [m for m in monitors if str(getattr(m, "device", "") or "") in wanted]
    if not kept:
        return monitors, []
    return kept, [m for m in monitors if m not in kept]


def union_rect(monitors: Sequence) -> Tuple[int, int, int, int]:
    """(left, top, width, height) covering exactly these monitors."""
    if not monitors:
        return (0, 0, 0, 0)
    left = min(int(m.left) for m in monitors)
    top = min(int(m.top) for m in monitors)
    right = max(int(m.right) for m in monitors)
    bottom = max(int(m.bottom) for m in monitors)
    return (left, top, right - left, bottom - top)


def describe(monitors: Sequence, labels: Optional[dict] = None) -> List[str]:
    """One line per monitor, for the first-run question.

    `labels` maps a device path to a human name (the EDID model), because "DISPLAY16" tells nobody
    which physical screen they are choosing.
    """
    labels = labels or {}
    rows = []
    for i, m in enumerate(monitors, 1):
        device = str(getattr(m, "device", "") or "")
        bits = [f"{int(getattr(m, 'width', 0))}x{int(getattr(m, 'height', 0))}",
                f"at {int(m.left)},{int(m.top)}"]
        if getattr(m, "primary", False):
            bits.append("primary")
        name = labels.get(device) or (device.split("\\")[-1] if "\\" in device else device)
        if name:
            bits.append(name)
        rows.append(f"  [{i}] " + "  ".join(bits))
    return rows


def labels_from_hardware(hardware: Sequence, monitors: Sequence) -> dict:
    """Readable names for the monitors, from the EDID rows geometry already queries."""
    labels = {}
    for m, hw in zip(monitors, hardware or []):
        name = getattr(hw, "model", "") or getattr(hw, "edid_name", "")
        device = str(getattr(m, "device", "") or "")
        if name and device:
            labels[device] = str(name)
    return labels


def parse_selection(text: str, count: int) -> Optional[List[int]]:
    """Parse a first-run answer into 1-based monitor numbers.

    Accepts "1,3", "1 3", "all", "primary", and an empty answer (meaning all). Returns None for
    anything unparseable so the caller can ask again instead of guessing.
    """
    raw = (text or "").strip().lower()
    if raw in ("", "all", "*", "every"):
        return list(range(1, count + 1))
    if raw in ("primary", "main", "1x"):
        return [1] if count else []
    picked: List[int] = []
    for chunk in raw.replace(",", " ").replace(";", " ").split():
        if not chunk.isdigit():
            return None
        n = int(chunk)
        if not 1 <= n <= count:
            return None
        if n not in picked:
            picked.append(n)
    return picked or None


def ask(monitors: Sequence, input_fn=input, print_fn=print,
        labels: Optional[dict] = None) -> Optional[List[str]]:
    """Ask which monitors to use. Returns device names, or None to leave the config untouched."""
    if not monitors:
        return None
    print_fn("")
    print_fn("=" * 78)
    print_fn(" WHICH SCREENS SHOULD KINESIS USE?")
    print_fn("=" * 78)
    print_fn(" Hand and eye tracking work best on the screens you actually face. Screens at the far")
    print_fn(" edges of a wide desk sit at extreme angles, where gaze is guesswork - leaving them out")
    print_fn(" makes targeting on the rest more reliable, and they can still be used with the mouse.")
    print_fn("")
    for row in describe(monitors, labels):
        print_fn(row)
    print_fn("")
    try:
        answer = input_fn(" Use which screens? [1,3 / all / primary] (all): ")
    except (EOFError, KeyboardInterrupt):
        print_fn("")
        return None
    picked = parse_selection(answer, len(monitors))
    if picked is None:
        print_fn(" did not understand that - using all of them for now, change it with")
        print_fn("   run.bat --enable-monitors 1,2")
        return [str(getattr(m, "device", "") or "") for m in monitors]
    chosen = [monitors[i - 1] for i in picked]
    devices = [str(getattr(m, "device", "") or "") for m in chosen]
    if len(chosen) == len(monitors):
        print_fn(f" using all {len(monitors)} screens")
    else:
        left_out = [str(i) for i in range(1, len(monitors) + 1) if i not in picked]
        print_fn(f" using {len(chosen)} of {len(monitors)} screens "
                 f"(monitor{'s' if len(left_out) > 1 else ''} {', '.join(left_out)} left out)")
    print_fn("=" * 78)
    print_fn("")
    return devices
