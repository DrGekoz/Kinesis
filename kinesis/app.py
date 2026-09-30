"""Wiring and main loop."""
from __future__ import annotations

import signal
import time
from typing import Optional

import cv2

from . import winapi as w
from .actions import ActionRunner
from .aim import AimClassifier
from .config import Calibration, Config, load_calibration
from .gestures import GestureEngine
from .hud import Hud
from .tracking import TrackingEngine

BANNER = r"""
  _  ___              _
 | |/ (_)_ __   ___ ___(_)___
 | ' /| | '_ \ / _ \/ __| / __|
 | . \| | | | |  __/\__ \ \__ \
 |_|\_\_|_| |_|\___||___/_|___/     webcam human-computer use
"""

GESTURE_HELP = """
 gesture                     action
 --------------------------  ----------------------------------------------------
 index finger                move cursor (snaps, no easing)
 thumb + index pinch         left click          (twice quickly = double click)
 thumb + middle pinch        right click
 thumb + ring pinch (hold)   adaptive scroll     (hand travel drives the wheel)
 thumb + pinky pinch (hold)  drag                (text selection, file drags)
 open hand -> closed fist    minimise            (exits fullscreen first if needed)
 closed fist -> open hand    maximise / fullscreen  (f = YouTube, F11 = otherwise)
 open hand swipe left/right  previous / next tab  (Ctrl+Shift+Tab / Ctrl+Tab)
 LEFT fist held 2s           hold Alt; right-hand thumb+index pinches tap Tab
 thumb + pinky out (shaka)   hold Ctrl+Space     (push to talk)
 END key                     quit
"""


class KinesisApp:
    def __init__(self, cfg: Config, calibration: Optional[Calibration] = None,
                 dry: Optional[bool] = None, preview: Optional[bool] = None):
        self.cfg = cfg
        self.monitors = w.enumerate_monitors()
        self.calibration = calibration if calibration is not None else load_calibration()
        self.aim = AimClassifier(cfg, self.monitors, self.calibration)
        self.engine = TrackingEngine(cfg)
        self.gestures = GestureEngine(cfg)
        self.runner = ActionRunner(cfg, self.monitors,
                                  dry=cfg["dry_run"] if dry is None else dry)
        self.hud = Hud(cfg, self.monitors)
        self.preview = bool(cfg["preview"] if preview is None else preview)
        self._stop = False
        self._window = "Kinesis"
        self._last_report = 0.0
        self._latencies = []
        self._frames = 0
        self._t0 = time.perf_counter()

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
        print(f"virtual desktop: {w.virtual_screen()}")
        if self.calibration.targets:
            print("aim calibration:")
            for t in self.calibration.targets:
                print(f"  monitor {t.index + 1}: yaw {t.yaw:+.1f} pitch {t.pitch:+.1f} "
                      f"(+/-{t.yaw_std:.1f})")
        else:
            print("aim calibration: none yet - run calibrate.py for accurate screen aiming")
        print(f"cursor hand: {self.cfg['cursor_hand']}   filter: {self.cfg['filter']} "
              f"(cutoff {self.cfg['filter_min_cutoff']}, beta {self.cfg['filter_beta']})   "
              f"model_complexity: {self.cfg['model_complexity']}")
        if self.runner.dry:
            print("DRY RUN: gestures are logged, nothing is injected")
        if self.cfg["exclusive"]:
            print("EXCLUSIVE: cursor movement only, gesture actions disabled")
        print(GESTURE_HELP)

    # ------------------------------------------------------------------ loop
    def run(self) -> int:
        self._install_signals()
        if not self.engine.start():
            print(f"camera error: {self.engine.camera.error}")
            return 2
        w0, h0 = self.engine.frame_size
        self.gestures.set_frame_size(w0, h0)
        time.sleep(0.3)                       # let the threads fill their slots
        try:
            while not self._stop:
                now = time.perf_counter()
                poses, frame, latency = self.engine.update()
                w1, h1 = self.engine.frame_size
                self.gestures.set_frame_size(w1, h1)

                primary = self.gestures.primary_hand(poses)
                aim = None
                if primary is not None and bool(self.cfg["aim_enabled"]):
                    aim = self.aim.classify(primary.yaw, primary.pitch)

                intents = self.gestures.update(poses, aim.index if aim else None, now,
                                               max(now - getattr(self, "_prev", now), 1e-4))
                self._prev = now
                if bool(self.cfg["exclusive"]):
                    intents = [i for i in intents if i.kind == "cursor.move"]

                self.runner.tick(now)
                self.runner.execute(intents)

                if frame is not None and self.preview:
                    view = self.hud.render(
                        frame, poses, aim, self.runner.last_action,
                        self.gestures.state_summary(), self.engine.stats, latency * 1000.0,
                        note=self.gestures.last_note, cursor=self.gestures._cursor)
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
              f"end-to-end avg {avg:4.0f} ms peak {top:4.0f} ms")

    def shutdown(self):
        intents = self.gestures.release_all(reason="shutdown")
        try:
            self.runner.execute(intents)
            self.runner.release_all()
        except Exception:
            pass
        self.engine.stop()
        if self.preview:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
        if bool(self.cfg["latency_report"]):
            self._print_latency()
        print("stopped - all keys and buttons released")
