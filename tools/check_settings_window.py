"""Render the settings window for a moment and confirm it draws.

The window is the one part of Kinesis that cannot be verified by a unit test: it is Tk, it needs a
display, and a broken widget only shows up when it is built. This builds the real thing, lets it
paint, and closes it again.

    .venv\\Scripts\\python -u tools\\check_settings_window.py
    .venv\\Scripts\\python -u tools\\check_settings_window.py --seconds 10 --theme lime
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w                                     # noqa: E402
from kinesis import monitor_set as ms                               # noqa: E402
from kinesis import settings_window as sw                           # noqa: E402
from kinesis.config import Config                                    # noqa: E402
from kinesis.geometry import monitor_hardware                        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the Kinesis settings window")
    ap.add_argument("--seconds", type=float, default=2.5, help="how long to leave it on screen")
    ap.add_argument("--theme", default="", help="accent theme (default: the configured one)")
    args = ap.parse_args()

    cfg = Config()
    monitors = w.enumerate_monitors()
    print("=" * 68)
    print(" Kinesis settings window check")
    print("=" * 68)
    print(f"monitors .......... {len(monitors)}")
    labels = ms.labels_from_hardware(monitor_hardware(monitors), monitors)
    for row in ms.describe(monitors, labels):
        print(row)
    print(f"selected .......... {sw.summary(monitors, cfg.get('enabled_monitors') or [])}")
    print(f"wizard argument ... {sw.calibration_arg(monitors, cfg.get('enabled_monitors') or []) or '(all screens)'}")
    print(f"theme ............. {args.theme or cfg.get('vcam_theme')}")

    t0 = time.perf_counter()
    labels = ms.labels_from_hardware(monitor_hardware(monitors), monitors)
    action = sw.run(cfg, monitors, theme=args.theme or None, labels=labels,
                    status="self-test: nothing was changed",
                    _auto_close_ms=int(args.seconds * 1000))
    elapsed = time.perf_counter() - t0
    print(f"window ............ rendered and closed in {elapsed:.1f}s")
    print(f"action ............ {action!r}  (None = closed without a button, which is correct here)")
    if elapsed < 0.2:
        print("FAIL - it closed immediately, so it probably never built")
        return 1
    print("OK - the window builds and paints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
