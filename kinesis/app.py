"""Wiring and main loop."""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import cv2

from . import winapi as w
from . import tabs
from . import monitor_set
from . import gesture_map
from . import focus_target
from .actions import ActionRunner
from .aim import AimClassifier
from .config import Calibration, Config, load_calibration
from .desktop_overlay import DesktopOverlay
from .gaze import GazeEngine
from .gestures import GazeScroller, GestureEngine, Intent
from .hud import Hud
from .tracking import TrackingEngine
from .vcam import VirtualCamera

BANNER = r"""
  _  ___              _
 | |/ (_)_ __   ___ ___(_)___
 | ' /| | '_ \ / _ \/ __| / __|
 | . \| | | | |  __/\__ \ \__ \
 |_|\_\_|_| |_|\___||___/_|___/     webcam human-computer use
"""

GESTURE_HELP = """
 gesture                            action
 ---------------------------------  --------------------------------------------------
 eye gaze                           MOVE THE CURSOR (run calibrate_gaze.bat once)
 thumb + index pinch                left click          (twice quickly = double click)
 thumb + middle pinch               right click
 thumb + ring pinch (hold)          drag                (text selection, file drags)
 both hands pinch, hands apart      zoom in             (Ctrl+=)
 both hands pinch, hands together   zoom out            (Ctrl+-)
 RIGHT fist held 0.6s               hold Ctrl; LEFT thumb+index taps Tab (next tab)
                                    LEFT thumb+middle taps Shift+Tab (previous tab)
 LEFT fist held 2s                  hold Alt; right-hand thumb+index pinches tap Tab
 claw (4 fingertips to thumb) + drag down   minimise the window you are looking at
 claw, then spread the fingers      maximise / fullscreen  (f = YouTube, F11 = otherwise)
 index + pinky out, held, up/down   system volume        (middle + ring stay curled)
 thumb + pinky out (shaka)          hold Ctrl+Space     (push to talk)
 eye gaze, continued                scroll, pick the target window, click browser tabs
 END key                            quit

 Window actions need a target: if your eyes are not on a window they do nothing at all.
"""


def _maybe_calibrate(cfg, gaze, engine=None) -> bool:
    """Offer to run the gaze calibration wizard from inside the app.

    Printing "run calibrate_gaze.bat" was never enough. To someone who does not know what that file
    is, an uncalibrated app is simply an app whose eye tracking does not work, and it stays that way -
    so ask, and run it here.

    THE CAMERA HAS TO BE HANDED OVER. This runs while the app is already up and tracking, so the
    webcam is open in this process. The wizard is a separate process, and a webcam can only be held
    by one of them: the wizard died with

        could not open the camera - close anything else using it and retry

    which is a message aimed at the user when the thing using it is the app asking the question. So
    `engine` is stopped first (which releases the device), the wizard runs, and the engine is started
    again afterwards - on both paths, including a failed one, so declining the calibration cannot
    leave the app with no camera.
    """
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return False
    except Exception:
        return False
    try:
        answer = input(" Calibrate now?  [F]ull sweep / [Q]uick / [N]ot now: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer or answer.startswith("n"):
        return False
    quick = answer.startswith("q")
    script = Path(__file__).resolve().parent.parent / "calibrate_gaze.py"
    if not script.is_file():
        print(f"[gaze] cannot find {script}")
        return False

    released = False
    if engine is not None:
        print("[gaze] releasing the camera so the wizard can have it...")
        engine.stop()
        released = True
    print(f"[gaze] starting the {'quick' if quick else 'full'} calibration - sit normally and look "
          f"at each dot without moving your head")
    try:
        rc = subprocess.call([sys.executable, "-u", str(script)] + (["--quick"] if quick else []))
    except Exception as exc:
        print(f"[gaze] could not run the wizard: {exc}")
        rc = 1
    finally:
        if released and not engine.start():
            print("[gaze] the camera did not come back - restart Kinesis")
            raise SystemExit(2)
        if released:
            print("[gaze] camera reconnected to the main loop")
    if rc != 0 or not gaze.model_path.exists():
        print("[gaze] no model came out of that - continuing without gaze")
        return False
    print("[gaze] model saved - gaze is live")
    return True


class KinesisApp:
    def __init__(self, cfg: Config, calibration: Optional[Calibration] = None,
                 dry: Optional[bool] = None, preview: Optional[bool] = None):
        self.cfg = cfg
        # Which screens to use. Asked once, on first run; everything downstream - aim, gaze, the
        # canvas mapping, the overlays, calibration - narrows to this set.
        all_monitors = w.enumerate_monitors()
        if not cfg["monitors_configured"] and sys.stdin and sys.stdin.isatty() and not cfg["dry_run"]:
            chosen = monitor_set.ask(all_monitors, labels=self._monitor_labels(all_monitors))
            if chosen is not None:
                cfg.set("enabled_monitors", chosen)
                cfg.set("monitors_configured", True)
                cfg.save()
        kept, disabled = monitor_set.select_monitors(all_monitors, cfg["enabled_monitors"])
        self.virtual_rect = monitor_set.union_rect(kept)
        w.set_virtual_screen_override(self.virtual_rect if disabled else None)
        self.monitors = kept
        self.disabled_monitors = disabled
        self.calibration = calibration if calibration is not None else load_calibration()
        self.aim = AimClassifier(cfg, self.monitors, self.calibration)
        self.engine = TrackingEngine(cfg)
        self.gestures = GestureEngine(cfg)
        # the user's Gesture-Map (their bindings) - loaded before the loop starts so the very first
        # gesture already obeys it
        self.gesture_map = gesture_map.load_active(cfg)
        self.gestures.load_map(self.gesture_map)
        self.runner = ActionRunner(cfg, self.monitors,
                                  dry=cfg["dry_run"] if dry is None else dry)
        self.gaze = GazeEngine(cfg)
        self.gaze_scroller = GazeScroller(cfg)
        self.geometry = None
        self.vcam = VirtualCamera(cfg, dry=cfg["dry_run"] if dry is None else dry)
        self.desktop_overlay = DesktopOverlay(cfg, self.monitors, lambda: self.gaze.state)
        self._gaze_dwell_hwnd = None
        self._gaze_dwell_since = 0.0
        self._gaze_focus_at = 0.0
        self.last_ptt_focus = ""
        # settings window: F2 by default, disabled with settings_hotkey = "none"
        self._settings_hotkey = self._hotkey_vk(str(cfg.get("settings_hotkey", "f2") or ""))
        self._settings_armed = True
        self._open_settings_at_start = bool(cfg.get("open_settings", False))
        self._eye_cursor: Optional[tuple] = None    # smoothed eye-driven pointer position
        self.hud = Hud(cfg, self.monitors)
        self.preview = bool(cfg["preview"] if preview is None else preview)
        self._stop = False
        self._window = "Kinesis"
        self._last_report = 0.0
        self._latencies = []
        self._frames = 0
        self._t0 = time.perf_counter()
        self._gaze_target = None
        self._gaze_target_at = 0.0
        self._gaze_target_title = ""
        self._prev = None

    # ------------------------------------------------------------------ lifecycle
    def _install_signals(self):
        def handler(signum, frame):
            self._stop = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def describe_environment(self):
        print(BANNER)
        print(f"monitors ({len(self.monitors)}):")
        for i, m in enumerate(self.monitors):
            print(f"  {i + 1}. {m}")
        if self.disabled_monitors:
            names = ", ".join(str(getattr(m, "device", "?")).split("\\")[-1]
                              for m in self.disabled_monitors)
            print(f"  left out of tracking: {names}")
            print("  change with: run.bat --enable-monitors 1,2   (or --all-monitors)")
        print(f"virtual desktop: {w.virtual_screen()}")
        if str(self.cfg["cursor_source"]).lower() == "gaze":
            if self.gaze.is_calibrated:
                print("cursor source: your eyes (cursor_source=gaze)")
            else:
                print("cursor source: your eyes once calibrated - until then the hand still moves "
                      "it (cursor_fallback=" + str(self.cfg["cursor_fallback"]) + ")")
        if self.calibration.targets:
            print("aim calibration:")
            for t in self.calibration.targets:
                print(f"  monitor {t.index + 1}: yaw {t.yaw:+.1f} pitch {t.pitch:+.1f} "
                      f"(+/-{t.yaw_std:.1f})")
        else:
            print("aim calibration: none yet - run calibrate.py for accurate screen aiming")
        print(f"gaze: {'model ' + str(self.gaze.model_path.name) if self.gaze.is_calibrated else 'no model - run calibrate_gaze.bat'}"
              f"   target window from gaze: {self.cfg['gaze_target_enabled']}"
              f"   scroll: {self.cfg['gaze_scroll_mode']}")
        print(f"virtual camera: {self.cfg['vcam_mode'] if self.cfg['vcam_enabled'] else 'off'} "
              f"({self.cfg['vcam_width']}x{self.cfg['vcam_height']} @ {self.cfg['vcam_fps']})")
        print(f"cursor hand: {self.cfg['cursor_hand']}   filter: {self.cfg['filter']} "
              f"(cutoff {self.cfg['filter_min_cutoff']}, beta {self.cfg['filter_beta']})   "
              f"model_complexity: {self.cfg['model_complexity']}")
        if self.runner.dry:
            print("DRY RUN: gestures are logged, nothing is injected")
        if self.cfg["exclusive"]:
            print("EXCLUSIVE: cursor movement only, gesture actions disabled")
        print(GESTURE_HELP)

    # ------------------------------------------------------------------ loop
    def _build_geometry(self, frame_w: int, frame_h: int):
        """Work out the physical desk once the camera size is known."""
        if not bool(self.cfg.get("geometry_enabled", True)):
            return
        cal_distance = self.gaze.calibration_distance_mm
        from .geometry import build_geometry
        self.geometry = build_geometry(
            self.cfg, self.monitors, frame_w, frame_h,
            distance_mm=cal_distance or float(self.cfg["assumed_distance_mm"]),
            distance_source="gaze calibration" if cal_distance else "assumed")
        self.aim.set_geometry(self.geometry)
        self.gaze.set_camera(self.geometry.camera)
        print(self.geometry.report())
        stored = self.gaze.load_metadata()
        if stored:
            px_now = [p.px_w for p in self.geometry.layout.panels]
            px_then = [m.get("px", [0])[0] for m in stored.get("monitors", [])]
            if px_then and px_then != px_now:
                print("[gaze] the screens are arranged differently to when gaze was calibrated "
                      "- window targeting may be off, re-run calibrate_gaze.bat")
            elif stored.get("distance_mm"):
                print(f"[gaze] calibrated with your eyes {stored['distance_mm']:.0f} mm from the "
                      f"screen; will warn past "
                      f"{float(self.cfg['distance_warn_fraction']) * 100:.0f}% drift")

    def _prepare_ptt_focus(self, intents, gaze, now: float) -> None:
        """Dictating while looking at a text field: click into the field first.

        The gesture engine has no idea what is on screen, so the field is found here. Hit test the
        gaze point; if it is somewhere you can type - or a page we cannot read, which is what a
        browser looks like - park the pointer there, click, pause for the focus to take, then let the
        Ctrl+Space the gesture emitted go out. Pure `focus_target.decide()` makes the call, so the
        behaviour is testable without a desktop.
        """
        if gaze is None or not getattr(gaze, "valid", False):
            return
        mode = str(self.cfg["ptt_focus_mode"])
        if mode == "off":
            return
        keys = tuple(self.cfg["ptt_keys"] or ())
        where = None
        for i, intent in enumerate(intents):
            if intent.kind == "keys.down" and tuple(intent.keys or ()) == keys:
                where = i
                break
        if where is None:
            return
        hit = focus_target.hit_test(int(gaze.x), int(gaze.y))
        ok, why = focus_target.decide(mode, hit)
        self.last_ptt_focus = f"{'click' if ok else 'no click'}: {why}"
        if not ok:
            return
        settle = int(float(self.cfg["ptt_focus_settle_ms"]))
        intents[where:where] = [
            Intent("mouse.click", button="left", warp=(float(gaze.x), float(gaze.y)),
                   note=f"dictation focus ({why})"),
            Intent("pause", amount=settle, note="let the field take focus"),
        ]

    def _annotate_gaze_click(self, intents, gaze, now: float) -> None:
        """Clicking while looking at a browser tab switches to that tab.

        The pointer has to be *on* the tab for the click to land there, so the intent carries a warp
        point and the runner moves the pointer, waits `gaze_click_warp_delay_ms`, then clicks. The
        window is re-resolved here rather than reusing the rate-limited gaze target, because this
        runs only when a click actually fires.
        """
        if not bool(self.cfg["gaze_click_tabs"]):
            return
        clicks = [i for i in intents if i.kind in ("mouse.click", "mouse.double")]
        if not clicks or gaze is None or not getattr(gaze, "valid", False):
            return
        hwnd = w.topmost_window_at(int(gaze.x), int(gaze.y), self.monitors,
                                   {w.own_process_id()},
                                   self.cfg.get("window_title_blocklist") or ())
        if not hwnd:
            return
        info = w.window_info(hwnd, self.monitors)
        if info is None or not tabs.is_browser(info):
            return
        if not tabs.in_tab_strip(info, gaze.x, gaze.y,
                                 float(self.cfg["tab_strip_top_px"]),
                                 float(self.cfg["tab_strip_height_px"])):
            return
        for intent in clicks:
            intent.warp = (float(gaze.x), float(gaze.y))
            intent.note = (intent.note + " | gaze tab").strip(" |")
        self.last_gaze_click = f"{info.exe or info.title[:22]} tab strip"

    def _resolve_gaze_target(self, gaze, now: float):
        """Turn the gaze point into the window the user is looking at. Rate limited: the window
        walk is an EnumWindows sweep and gaze only refreshes at gaze_hz anyway."""
        if not bool(self.cfg["gaze_target_enabled"]) or not getattr(gaze, "valid", False):
            return self._gaze_target
        if now - self._gaze_target_at < float(self.cfg["gaze_target_refresh_s"]):
            return self._gaze_target
        self._gaze_target_at = now
        hwnd = w.topmost_window_at(int(gaze.x), int(gaze.y), self.monitors,
                                   {w.own_process_id()},
                                   self.cfg.get("window_title_blocklist") or ())
        if hwnd and hwnd != self._gaze_target:
            info = w.window_info(hwnd, self.monitors)
            self._gaze_target_title = (info.title[:48] if info else "")
        self._gaze_target = hwnd
        return hwnd

    def _gaze_focus(self, hwnd, now: float) -> None:
        """Looking at a window for a moment makes it the focused window.

        Gaze already decides which window a *gesture* acts on; this is the other half - bringing the
        window you are looking at to the front, so typing and the keyboard land there too. Dwell
        gated and cooled down so it cannot fight the user.
        """
        if not bool(self.cfg["gaze_focus_enabled"]) or not hwnd:
            self._gaze_dwell_hwnd = None
            self._gaze_dwell_since = 0.0
            return
        if hwnd != self._gaze_dwell_hwnd:
            self._gaze_dwell_hwnd = hwnd
            self._gaze_dwell_since = now
            return
        if (now - self._gaze_dwell_since) < float(self.cfg["gaze_focus_dwell_s"]):
            return
        if (now - self._gaze_focus_at) < float(self.cfg["gaze_focus_cooldown_s"]):
            return
        if w.user32.GetForegroundWindow() == hwnd:
            self._gaze_focus_at = now
            return
        info = w.window_info(hwnd, self.monitors)
        if info is None or info.process_id in {w.own_process_id()}:
            return
        if bool(self.cfg["gaze_focus_skip_fullscreen"]) and info.is_fullscreen:
            return
        ok = w.focus_and_verify(hwnd, allow_alt_trick=not self.runner.alt_held)
        self._gaze_focus_at = now
        self.last_action = f"gaze focus {'ok' if ok else 'failed'}: {info.title[:36]}"

    def _publish_vcam(self, frame, poses, gaze) -> None:
        if not self.vcam.enabled:
            return
        extra = []
        if self._gaze_target_title:
            extra.append(f"target: {self._gaze_target_title}")
        composed = self.vcam.composite(frame, poses, gaze, extra)
        if composed is not None:
            self.vcam.publish(composed)

    def run(self) -> int:
        self._install_signals()
        if not self.engine.start():
            print(f"camera error: {self.engine.camera.error}")
            return 2
        w0, h0 = self.engine.frame_size
        self.gestures.set_frame_size(w0, h0)
        if self.gaze.enabled:
            self.gaze.start()
        self._build_geometry(w0, h0)     # after gaze.start: the model knows the calibrated seat distance
        if focus_target.prewarm():
            print("[focus] UI Automation ready (dictation clicks into the field you are looking at)")
        if self.gaze.enabled and not self.gaze.is_calibrated:
            # A user cannot act on "run calibrate_gaze.bat" printed into a scrolling console - this
            # is the step that leaves eye tracking dead for good, so offer to do it right here.
            print()
            print("=" * 78)
            print(" EYE TRACKING IS NOT CALIBRATED YET")
            print("=" * 78)
            print(" The camera, face detection and features all work - Kinesis checked them.")
            print(" What is missing is one training pass: gaze cannot come from geometry alone,")
            print(" it has to be fitted to YOUR eyes and YOUR seat position.")
            print()
            print("    quick   ~15 s, one dot per monitor  - enough to pick a monitor, coarse")
            print("    full    ~40 s, a grid per monitor   - precise, and what you want")
            print()
            if _maybe_calibrate(self.cfg, self.gaze, self.engine):
                self.gaze.start()
            else:
                print(" continuing without gaze. Run calibrate_gaze.bat whenever you are ready:")
                print("   calibrate_gaze.bat            full sweep")
                print("   calibrate_gaze.bat --quick    the 15 second version")
                if str(self.cfg["gaze_scroll_mode"]).lower() != "off" \
                        and str(self.cfg["pinch_ring_action"]).lower() != "scroll":
                    print("[scroll] gaze scrolling is the only scroll binding and it needs a model, "
                          "so you have NO scrolling until you calibrate.")
                    print("[scroll] or keep a pinch fallback: "
                          "run.bat --tune pinch_ring_action=scroll --save-config")
            print("=" * 78)
            print()
        print("Created by DrGekoz - report issues on GitHub:")
        print("  https://github.com/DrGekoz/Kinesis")
        print()
        if self.desktop_overlay.enabled:
            if self.desktop_overlay.start():
                print(f"[overlay] drawing on the desktop: {self.desktop_overlay.style}/"
                      f"{self.desktop_overlay.theme}, {self.desktop_overlay.fps:.0f} fps, "
                      f"press {str(self.cfg['desktop_overlay_hotkey']).upper()} to hide/show")
            else:
                print(f"[overlay] unavailable: {self.desktop_overlay.error}")
        cam_fps = self.engine.camera.fps or float(self.cfg["vcam_fps"])
        self.vcam.start(cam_fps)
        time.sleep(0.3)                       # let the threads fill their slots
        try:
            if self._open_settings_at_start:
                self._open_settings_at_start = False
                print("[settings] opening the settings window (--settings)")
                self._open_settings()
            while not self._stop:
                now = time.perf_counter()
                poses, frame, latency = self.engine.update()
                w1, h1 = self.engine.frame_size
                self.gestures.set_frame_size(w1, h1)

                # gaze is fed the raw (unmirrored) frame the estimator was calibrated on
                gaze_state = self.gaze.update(self.engine.raw_frame(), now)
                target_hwnd = self._resolve_gaze_target(gaze_state, now)
                self._gaze_focus(target_hwnd, now)

                primary = self.gestures.primary_hand(poses)
                aim = None
                if primary is not None and bool(self.cfg["aim_enabled"]):
                    aim = self.aim.classify(primary.yaw, primary.pitch)

                dt = max(now - self._prev, 1e-4) if self._prev else 1e-4
                intents = self.gestures.update(poses, aim.index if aim else None, now, dt,
                                              target_hwnd=target_hwnd)
                intents.extend(self._gaze_cursor(gaze_state))
                intents.extend(self.gaze_scroller.update(gaze_state, now, dt))
                self._annotate_gaze_click(intents, gaze_state, now)
                self._prepare_ptt_focus(intents, gaze_state, now)
                self._prev = now
                if bool(self.cfg["exclusive"]):
                    intents = [i for i in intents if i.kind == "cursor.move"]

                self.runner.tick(now)
                self.runner.execute(intents)

                self._publish_vcam(frame, poses, gaze_state)

                if frame is not None and self.preview:
                    extra = []
                    if self._gaze_target_title:
                        extra.append(f"target: {self._gaze_target_title}")
                    extra.append(self.vcam.status_line() if self.vcam.enabled else "vcam: off")
                    extra.append(self.gaze_scroller.status())
                    view = self.hud.render(
                        frame, poses, aim, self.runner.last_action,
                        self.gestures.state_summary(), self.engine.stats, latency * 1000.0,
                        note=self.gestures.last_note, cursor=self.gestures._cursor,
                        gaze=gaze_state, extra=extra)
                    if view is not None:
                        cv2.imshow(self._window, view)
                        key = cv2.waitKey(1) & 0xFF
                        if key in (ord("q"), 27):
                            break
                        try:
                            if cv2.getWindowProperty(self._window, cv2.WND_PROP_VISIBLE) < 1:
                                break
                        except cv2.error:
                            pass
                else:
                    time.sleep(0.001)

                self._latencies.append(latency)
                if len(self._latencies) > 60:
                    self._latencies.pop(0)
                self._frames += 1
                if bool(self.cfg["latency_report"]) and now - self._last_report > 5.0:
                    self._last_report = now
                    self._print_latency()
                if self._settings_hotkey and self._settings_armed and \
                        w.key_pressed(self._settings_hotkey):
                    self._open_settings()
                    self._prev = 0.0                       # a fresh dt after the window has been up
                    continue
                if self._settings_hotkey and not w.key_pressed(self._settings_hotkey):
                    self._settings_armed = True            # re-arm only once the key is released
                if w.panic_pressed():
                    print("END pressed - stopping")
                    break
        except KeyboardInterrupt:
            pass
        except Exception as exc:               # pragma: no cover - safety net
            print(f"error: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            self.shutdown()
        return 0

    def _print_latency(self):
        stats = self.engine.stats
        avg = sum(self._latencies) / max(len(self._latencies), 1) * 1000.0
        top = max(self._latencies) * 1000.0 if self._latencies else 0.0
        elapsed = time.perf_counter() - self._t0
        print(f"[latency] loop {self._frames / elapsed:5.1f} fps | cam {stats.capture_fps:5.1f} fps | "
              f"inference {stats.inference_fps:5.1f} fps / {stats.inference_ms:4.1f} ms | "
              f"end-to-end avg {avg:4.0f} ms peak {top:4.0f} ms"
              + (f" | gaze {self.gaze.status_line()} | vcam {self.vcam.status_line()}"
                 if self.gaze.enabled or self.vcam.enabled else "")
              + (f" | {self.desktop_overlay.status_line()}"
                if self.desktop_overlay.enabled else ""))

    @staticmethod
    def _hotkey_vk(name: str) -> int:
        """Virtual-key code for a named hotkey; 0 means the hotkey is off."""
        key = (name or "").strip().lower()
        if not key or key in ("none", "off", "0", "disabled"):
            return 0
        try:
            return int(w.vk_for(key))
        except Exception:
            print(f"[settings] unknown settings hotkey {name!r} - use run.bat --settings instead")
            return 0

    def apply_gesture_map(self, gmap) -> None:
        """Save the map and hand it to the engine, so the next gesture uses the new binding."""
        self.gesture_map = gmap
        try:
            path = gesture_map.active_map_path(self.cfg)
            gmap.save(path)
            print(f"[map] saved {len(gmap.bindings)} bindings to {path}")
        except Exception as exc:
            print(f"[map] could not save the map: {exc}")
        self.gestures.load_map(gmap)
        owns = sorted({b.gesture.posed for b in gmap.bindings
                       if b.enabled and b.gesture.posed and not b.gesture.two_hand})
        print(f"[map] running {gmap.name}: {len(gmap.bindings)} bindings"
              + (f"; overriding built-ins for {', '.join(owns)}" if owns else ""))

    @staticmethod
    def _monitor_labels(monitors) -> dict:
        """EDID model names, so screens are named rather than numbered DISPLAY14 etc."""
        try:
            from .geometry import monitor_hardware
            return monitor_set.labels_from_hardware(monitor_hardware(monitors), monitors)
        except Exception:
            return {}

    def _gaze_cursor(self, gaze) -> list:
        """The pointer, driven by the eyes.

        One place decides it: gestures._cursor_intent refuses to move the pointer while
        `cursor_from_hand` is false, and this feeds the moves instead. Actions are unchanged - a pinch
        still clicks, a ring pinch still drags - but they act where you are LOOKING rather than where
        your hand is, and a drag follows your eyes.
        """
        want_gaze = str(self.cfg["cursor_source"]).lower() == "gaze"
        if not want_gaze:
            self.gestures.cursor_from_hand = True
            return []

        usable = bool(self.gaze._estimator) and gaze is not None and gaze.valid
        if not usable:
            # Never leave the mouse dead: before calibration (or while looking away) either fall back
            # to the hand, or hold the pointer exactly where it is if that is what was asked for.
            self.gestures.cursor_from_hand = str(self.cfg["cursor_fallback"]).lower() != "hold"
            return []

        self.gestures.cursor_from_hand = False
        x, y = float(gaze.x), float(gaze.y)
        if self._eye_cursor is None:
            self._eye_cursor = (x, y)
            return [Intent("cursor.move", x=int(x), y=int(y))]

        a = min(max(float(self.cfg["gaze_cursor_smoothing"]), 0.0), 0.98)
        sx = a * self._eye_cursor[0] + (1.0 - a) * x
        sy = a * self._eye_cursor[1] + (1.0 - a) * y
        dead = float(self.cfg["gaze_cursor_deadband_px"])
        if abs(sx - self._eye_cursor[0]) < dead and abs(sy - self._eye_cursor[1]) < dead:
            return []                    # a resting eye does not shake the pointer
        self._eye_cursor = (sx, sy)
        return [Intent("cursor.move", x=int(sx), y=int(sy))]

    def _open_settings(self) -> None:
        """Pause input, show the settings window, apply whatever came back.

        Input is released and the gesture pipeline is skipped while the window is up, so a pinch
        cannot press buttons in it and no modifier is left held.
        """
        from . import settings_window
        self._settings_armed = False
        try:
            self.runner.execute(self.gestures.release_all(reason="settings"))
        except Exception:
            pass
        action = None
        try:
            monitors = w.enumerate_monitors()
            action = settings_window.run(self.cfg, monitors,
                                         theme=self.cfg.get("vcam_theme", "ember"),
                                         labels=self._monitor_labels(monitors),
                                         gesture_map=self.gesture_map,
                                         on_map=self.apply_gesture_map)
        except Exception as exc:                       # pragma: no cover - GUI failure
            print(f"[settings] window failed: {exc}")
        if action == "saved":
            self.apply_monitor_selection()
        elif action == "gaze":
            self.recalibrate("gaze")
        elif action == "aim":
            self.recalibrate("aim")

    def apply_monitor_selection(self) -> None:
        """Re-read the enabled screens and make every part of the app follow them."""
        all_monitors = w.enumerate_monitors()
        kept, disabled = monitor_set.select_monitors(all_monitors, self.cfg["enabled_monitors"])
        self.monitors = kept
        self.disabled_monitors = disabled
        self.virtual_rect = monitor_set.union_rect(kept)
        w.set_virtual_screen_override(self.virtual_rect if disabled else None)
        for holder in (self.aim, self.runner, self.desktop_overlay, self.hud):
            if hasattr(holder, "monitors"):            # they all keep their own copy
                holder.monitors = list(kept)
        if self.geometry is not None:
            fw, fh = self.engine.frame_size
            self._build_geometry(fw, fh)               # desk maths follows the screens too
        names = ", ".join(str(getattr(m, "device", "") or "").split("\\")[-1] for m in kept)
        print(f"[screens] tracking {len(kept)} of {len(all_monitors)}: {names}")
        if disabled:
            out = ", ".join(str(getattr(m, "device", "") or "").split("\\")[-1] for m in disabled)
            print(f"[screens] left out: {out}")
        if not self.gaze.is_calibrated:
            print("[screens] gaze has no model yet - use Recalibrate in the settings window, or "
                  "calibrate_gaze.bat")

    def recalibrate(self, kind: str) -> None:
        """Run a calibration wizard over the enabled screens only, then reload what it produced."""
        from . import settings_window
        root = Path(__file__).resolve().parent.parent
        script = root / ("calibrate_gaze.py" if kind == "gaze" else "calibrate.py")
        if not script.is_file():
            print(f"[recalibrate] cannot find {script}")
            return
        arg = settings_window.calibration_arg(w.enumerate_monitors(), self.cfg["enabled_monitors"])
        cmd = [sys.executable, "-u", str(script)] + (["--monitors", arg] if arg else [])
        print(f"[recalibrate] {kind}: {' '.join(cmd)}")
        try:
            rc = subprocess.call(cmd)
        except Exception as exc:
            print(f"[recalibrate] could not run it: {exc}")
            return
        if rc != 0:
            print(f"[recalibrate] {kind} calibration exited with {rc} - nothing changed")
            return
        if kind == "gaze":
            self.gaze.stop()
            if self.gaze.is_calibrated:
                self.gaze.start()
                self._build_geometry(*self.engine.frame_size)
            print(f"[recalibrate] gaze {'live' if self.gaze._estimator else 'not started'}")
        else:
            self.calibration = load_calibration()
            self.aim = AimClassifier(self.cfg, self.monitors, self.calibration)
            print(f"[recalibrate] aiming updated "
                  f"({len(self.calibration.targets)} screens calibrated)")

    def shutdown(self):
        self.gaze_scroller.release()
        intents = self.gestures.release_all(reason="shutdown")
        try:
            self.runner.execute(intents)
            self.runner.release_all()
        except Exception:
            pass
        self.vcam.stop()
        self.desktop_overlay.stop()
        self.gaze.stop()
        self.engine.stop()
        if self.preview:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
        if bool(self.cfg["latency_report"]):
            self._print_latency()
        print("stopped - all keys and buttons released")
