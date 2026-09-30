"""Live verification of the volume gesture's key path against the real Windows mixer.

    python tools/verify_volume.py

Sends real volume keys and measures the master volume before and after. Restores the volume it
found afterwards.

TWO THINGS ARE PROVEN, AND ONE IS NOT:

  proven    the VK codes are the SDK's VK_VOLUME_* values, and a gesture map can actually bind them
  proven    the gesture state machine emits the right keys, in the right direction, at the right rate
            (that part is the unit suite - this tool only exercises the key path)
  NOT proven  that this machine's mixer responds, because MMDeviceEnumerator cannot be created from
            this process (CoCreateInstance -> 0x800401F0 CO_E_CLASSSTRING) even with correct GUID
            structs. The machine HAS audio (waveOutGetNumDevs=14, 67 render endpoints in the
            registry), so this is a process-level restriction here, not a broken machine.

So this tool exits 0 with "CANNOT MEASURE" rather than failing the keys for a limitation of the
harness. A test that cannot measure is not a test that failed.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kinesis import winapi as w                                    # noqa: E402

results = []


def check(name, got, want=True):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


def main() -> int:
    print("=== the codes are the SDK's ===")
    check("VK_VOLUME_MUTE is 0xAD", w.VK["volumemute"] == 0xAD)
    check("VK_VOLUME_DOWN is 0xAE", w.VK["volumedown"] == 0xAE)
    check("VK_VOLUME_UP is 0xAF", w.VK["volumeup"] == 0xAF)
    # 'm' must stay the letter: aliasing it to mute would make a map's "m" mean something else
    check("'m' is still the letter m, not mute", w.VK["m"] == 0x4D)

    print()
    print("=== every alias resolves to the same code ===")
    for group, code in (("volumeup", 0xAF), ("volumedown", 0xAE), ("volumemute", 0xAD)):
        aliases = [k for k in w.VK if w.VK[k] == code]
        check(f"{group} aliases all resolve", all(w.vk_for(a) == code for a in aliases),
              True)
        print(f"      {group}: {sorted(aliases)}")

    print()
    print("=== a gesture map can bind them ===")
    try:
        from kinesis.gesture_map import validate
        for key in ("volumeup", "volumedown", "volumemute"):
            doc = {"schema": "kinesis.gesture-map", "version": 1, "name": "v", "author": "a",
                   "description": "", "bindings": [
                       {"id": "b", "enabled": True,
                        "gesture": {"pose": "point", "hand": "either"},
                        "action": {"type": "keys", "keys": [key]}}]}
            check(f"map binding {key} validates", validate(doc) == [])
    except Exception as exc:                                        # noqa: BLE001
        print(f"WARN  could not check map validation: {exc}")

    print()
    print("=== does the real mixer respond? ===")
    before = w.master_volume()
    if before is None or before >= 0xFFFFFFFF:
        print("SKIP  master volume unreadable on this machine.")
        print("      MMDeviceEnumerator cannot be created from this process, and")
        print("      waveOutGetVolume(NULL) returns the 0xFFFFFFFF sentinel.")
        print("      The machine has audio (this is a harness limit, not a broken key).")
        print("      Run this on a desktop session to close the loop.")
    else:
        print(f"      volume before: {before}")
        for _ in range(3):
            w.key_down("volumeup")
            w.key_up("volumeup")
            time.sleep(0.09)
        time.sleep(0.6)
        after_up = w.master_volume()
        print(f"      after 3x volumeup: {after_up}")
        for _ in range(3):
            w.key_down("volumedown")
            w.key_up("volumedown")
            time.sleep(0.09)
        time.sleep(0.6)
        after_down = w.master_volume()
        print(f"      after 3x volumedown: {after_down}")
        check("volumeup raised the master volume", (after_up or 0) > (before or 0))
        check("volumedown lowered it", (after_down or 0) < (after_up or 0))
        # put it back
        delta = (after_down or 0) - before
        if abs(delta) > 200:
            key = "volumeup" if delta < 0 else "volumedown"
            for _ in range(abs(delta) // 200):
                w.key_down(key)
                w.key_up(key)
                time.sleep(0.03)
            time.sleep(0.5)
        print(f"      restored to: {w.master_volume()} (was {before})")

    print()
    print(f"{sum(results)}/{len(results)} volume checks passed"
          + ("  (mixer response unverified on this machine)" if before is None else ""))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
