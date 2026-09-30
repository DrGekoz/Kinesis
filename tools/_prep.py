"""One-off housekeeping: CRLF for the new launcher, append the gaze/vcam cards to kanban.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

p = ROOT / "calibrate_gaze.bat"
d = p.read_bytes()
if b"\r\n" not in d:
    p.write_bytes(d.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    print("calibrate_gaze.bat -> CRLF")
else:
    print("calibrate_gaze.bat already CRLF")

kb = ROOT / "kanban.json"
data = json.loads(kb.read_text(encoding="utf-8"))
have = {c["id"] for c in data["cards"]}
new = [
    ("kin010", "P9 - Gaze engine + multi-monitor calibration", "done",
     "EyeTrax 0.4.0 installed editable from vendor/ so the code that runs is the code that was read. "
     "GazeEngine wraps GazeEstimator on Kinesis's own raw frames (no second camera client, no frame "
     "drift from a second stream), rate limited to gaze_hz, KalmanEMA smoothing, model at "
     "gaze_model.pkl. calibrate_gaze.py runs its own multi-monitor wizard: EyeTrax's own calibration "
     "only knows the primary monitor, so dots are placed across the whole virtual desktop with a "
     "borderless topmost Tk window positioned via SetWindowPos (Tk geometry cannot express a negative "
     "origin) and the model trains on virtual-desktop coordinates. Reports in-sample pixel error and "
     "the monitor hit rate."),
    ("kin011", "P10 - Gaze-targeted actions", "done",
     "topmost_window_at walks the z-order for the window under the gaze point, honouring the title "
     "blocklist and skipping our own windows. Intent gained target_hwnd and focus_hwnd, so gaze "
     "resolution stays outside the gesture engine (which remains OS-free and testable). Window "
     "gestures act on the looked-at window; keyboard gestures (tab swipes, Alt-Tab) focus it first "
     "and verify via GetForegroundWindow before sending keys. Verified live: 16/16 checks including "
     "point-in-window resolution, blocklist skip and an outside point."),
    ("kin012", "P11 - Virtual camera with hand overlays", "done",
     "pyvirtualcam -> OBS Virtual Camera (OBS Studio's driver, verified present). Two modes: "
     "passthrough (webcam frame + hand skeleton + gaze + status) and overlay (EyeTrax's "
     "chroma-keyable green canvas with the gaze cursor, hand overlays composited on top as asked). "
     "Sending happens on a dedicated thread with a newest-frame-only slot because "
     "sleep_until_next_frame blocks for a whole frame interval. --vcam-test proves the device: 45 "
     "frames synchronous at 28.4 fps plus 30 through the threaded path, 0 dropped."),
    ("kin013", "P12 - Gaze-driven scrolling", "done",
     "GazeScroller in gestures.py: hold your gaze in the top or bottom 12% band and it scrolls while "
     "you keep looking. Dwell before engaging (a glance is not a scroll), ramp to full speed, "
     "cooldown after release, immediate stop when the gaze leaves the band, stale-gaze rejection. "
     "The wheel goes to the window under the cursor, so it warps the cursor onto the gaze point "
     "first (gaze_scroll_warp_cursor)."),
    ("kin014", "Gaze calibration with Joe's eyes", "backlog",
     "Run calibrate_gaze.bat and check the reported monitor hit rate (target 85%+). Re-run with "
     "--points 9 if it is low. Needs Joe at the desk - cannot be verified from the agent side."),
    ("kin015", "Tune against live feel", "backlog",
     "After real use: pinch_on at his seated distance, Alt-Tab 2s delay, swipe_min_fraction for tab "
     "swipes, gaze scroll speed/band. All exposed via --tune / kinesis_config.json."),
]
added = 0
for cid, title, status, desc in new:
    if cid in have:
        continue
    data["cards"].append({"id": cid, "title": title, "status": status, "description": desc})
    added += 1
kb.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"kanban.json: {added} cards added, {len(data['cards'])} total")
