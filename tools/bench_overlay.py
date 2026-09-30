"""Measure the overlay's compose cost per frame at full overlay resolution.

The 1080p overlay is the worst case; it runs on the virtual-camera sender thread, which needs to
sustain the camera rate. This prints the real number rather than an assumption.
"""
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "F:/aaaaaVIBECODING/Kinesis")

from kinesis.config import Config
from kinesis.vcam import VirtualCamera

STYLES = ("pointer", "comet", "path", "heatmap", "heatmap_comet", "none")
N = 40

for style in STYLES:
    cfg = Config()
    cfg.set("vcam_mode", "overlay")
    cfg.set("vcam_style", style)
    cam = VirtualCamera(cfg, dry=True)
    w, h = cam.size
    gaze = SimpleNamespace(x=-1920.0, y=540.0, valid=True, age=0.0)
    lines = ["Kinesis - overlay demo", f"style {style}", "gaze -1920,540  monitor 2",
             "seat 700 mm  target: Chrome"]
    times = []
    for i in range(N):
        gaze.x = -3840.0 + (i / N) * 7680.0          # sweep so the trail actually builds
        t0 = time.perf_counter()
        cam.composite(None, poses=(), gaze=gaze, lines=lines)
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    print(f"  {style:14} {w}x{h}  median {times[len(times)//2]:5.1f} ms  p90 {times[int(N*0.9)]:5.1f} ms")
