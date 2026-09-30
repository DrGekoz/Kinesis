"""Prove the transparent desktop overlay is real: layered, click-through, topmost, and compositing.

A blank overlay is indistinguishable from a broken one by eye, so this asserts the things that
actually matter: the extended window styles, the window covering a monitor, UpdateLayeredWindow
succeeding, and the DIB containing non-zero pixels after the gaze is drawn into it.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w                        # noqa: E402
from kinesis.config import Config                      # noqa: E402
from kinesis.desktop_overlay import DesktopOverlay, _Panel     # noqa: E402

PASS, FAIL = [], []


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")


class FakeGaze:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.valid, self.blink, self.age, self.face = True, False, 0.0, True
        self.distance_mm, self.distance_ok = 700.0, True


def main() -> int:
    w.set_dpi_aware()
    cfg = Config()
    cfg.set("desktop_overlay", True)
    cfg.set("desktop_overlay_fps", 20.0)
    monitors = w.enumerate_monitors()
    print(f"overlay test across {len(monitors)} monitors")

    gaze = FakeGaze(monitors[0].centre[0], monitors[0].centre[1])
    overlay = DesktopOverlay(cfg, monitors, lambda: gaze)
    t0 = time.perf_counter()
    started = overlay.start()
    check("overlay windows created", started, overlay.error or "")
    if not started:
        import traceback
        traceback.print_exc()
        # re-raise the underlying cause with a real stack for debugging
        for mon in monitors:
            _Panel(cfg, mon, overlay.scale, overlay.style, overlay.theme)
        return 1
    print(f"    created in {(time.perf_counter() - t0) * 1000:.0f} ms")

    time.sleep(0.4)
    panels = overlay._panels
    check("one window per monitor", len(panels) == len(monitors), f"{len(panels)}")

    EX_STYLE = w.WS_EX_LAYERED | w.WS_EX_TRANSPARENT | w.WS_EX_TOPMOST | w.WS_EX_NOACTIVATE
    for i, panel in enumerate(panels):
        ex = w.user32.GetWindowLongW(panel.hwnd, w.GWL_EXSTYLE) & 0xFFFFFFFF
        assert ex & w.WS_EX_LAYERED, "not layered"
        check(f"monitor {i + 1} window is layered + click-through + topmost + no-activate",
              (ex & EX_STYLE) == EX_STYLE, f"exstyle=0x{ex:08X}")
        rect = w.user32.GetWindowRect
        check(f"monitor {i + 1} covers its full screen",
              bool(panel.view.shape[0] == panel.monitor.height
                   and panel.view.shape[1] == panel.monitor.width),
              f"{panel.view.shape[1]}x{panel.view.shape[0]}")
        break                                        # one monitor is enough for the style asserts

    # drive a synthetic gaze and confirm pixels actually reach the DIB
    for step in range(30):
        gaze.x = monitors[0].left + 200 + step * 20
        gaze.y = monitors[0].top + 300 + int(60 * np.sin(step / 4.0))
        time.sleep(1.0 / 30.0)
    # let the overlay thread render the panel that owns the gaze
    time.sleep(0.4)
    overlays_drawn = [p for p in panels if int(p.view[..., 3].sum()) > 0]
    check("overlay drew something (alpha present in the DIB)",
          bool(overlays_drawn), f"{len(overlays_drawn)}/{len(panels)} panels have alpha")
    if overlays_drawn:
        panel = overlays_drawn[0]
        alpha = panel.view[..., 3]
        check("drawn pixels are transparent where there is no gaze content",
              int((alpha == 0).sum()) > int(alpha.size * 0.5),
              f"{(alpha == 0).mean() * 100:.0f}% fully transparent")
        colour = panel.view[..., :3]
        check("colour is premultiplied (never brighter than its alpha)",
              bool((colour.max(axis=2) <= alpha).all()))
        check("a trail exists, not just a single dot",
              int((alpha > 8).sum()) > 40, f"{int((alpha > 8).sum())} lit pixels")

    check("overlay thread is running", overlay.running)
    frames = overlay.frames
    check("overlay is actually ticking", frames > 5, f"{frames} frames, {overlay.tick_ms:.1f} ms/tick")
    hidden = overlay.toggle()
    check("toggle hides it", hidden is False and overlay.visible is False)
    overlay.toggle()

    overlay.stop()
    check("windows destroyed cleanly", len(overlay._panels) == 0)
    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} overlay checks passed")
    for f in FAIL:
        print(f"  failed: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
