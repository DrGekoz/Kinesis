"""Gesture state machine: poses in, intents out. No Windows calls in here at all, so the whole
thing is testable with synthetic landmarks.

Per frame it does five things:
  1. pick the cursor hand (right by default) and the modifier hand (left, for Alt-Tab)
  2. move the cursor - unless a gesture owns it
  3. resolve held actions (scroll / drag / Alt hold / push-to-talk)
  4. look for edge-triggered gestures (flicks, swipes, clicks) with cooldowns
  5. arbitrate: exactly one held action at a time, and pinch-into-fist cancels a pending click
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .pose import POSE_FIST, POSE_OPEN, POSE_SHAKA, HandPose
from .winapi import virtual_screen

PX_PER_SCROLL_CLICK = 40.0
CONFIRM_FRAMES = 2          # a pose must repeat this many frames before it counts


@dataclass
class Intent:
    kind: str
    button: str = ""
    keys: Tuple[str, ...] = ()
    amount: int = 0
    monitor: Optional[int] = None
    x: float = 0.0
    y: float = 0.0
    note: str = ""

    def __str__(self):
        bits = [self.kind]
        if self.button:
            bits.append(self.button)
        if self.keys:
            bits.append("+".join(self.keys))
        if self.amount:
            bits.append(str(self.amount))
        if self.monitor is not None:
            bits.append(f"monitor{self.monitor + 1}")
        if self.kind == "cursor.move":
            bits = [self.kind, f"{int(self.x)},{int(self.y)}"]
        return " ".join(bits)


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


class GestureEngine:
    def __init__(self, cfg, frame_size: Tuple[int, int] = (640, 480)):
        self.cfg = cfg
        self.frame_w, self.frame_h = frame_size
        self._virtual = virtual_screen()
        # held-state
        self.active: Optional[str] = None          # scroll | drag | swipe
        self.alt_held = False
        self.ptt_held = False
        # edge state
        self._counters: Dict[str, int] = {}
        self._prev_index_pinch = False
        self._prev_middle_pinch = False
        self._prev_pinky_pinch = False
        self._pending_click: Optional[float] = None
        self._pending_release: Optional[float] = None
        self._pending_pos: Tuple[float, float] = (0.0, 0.0)
        self._t_last_click = 0.0
        self._t_right_click = 0.0
        self._state = "none"
        self._t_open = 0.0
        self._t_fist = 0.0
        self._t_flick = 0.0
        self._left_fist_since: Optional[float] = None
        self._t_tab = 0.0
        self._shaka_since: Optional[float] = None
        self._scroll_anchor: Optional[float] = None
        self._swipe_samples: List[Tuple[float, float, float]] = []
        self._swipe_until = 0.0
        self._t_swipe = 0.0
        self._cursor = (self._virtual[0] + self._virtual[2] // 2,
                        self._virtual[1] + self._virtual[3] // 2)
        self.last_note = ""

    def primary_hand(self, poses: Sequence[HandPose]) -> Optional[HandPose]:
        """The cursor hand (right unless configured otherwise, falling back to whichever
        hand is actually present)."""
        return self._select_hands(poses)[0]

    # ------------------------------------------------------------------ helpers
    def set_frame_size(self, frame_w: int, frame_h: int):
        if frame_w and frame_h:
            self.frame_w, self.frame_h = frame_w, frame_h

    def _confirm(self, key: str, value: bool, need: int = CONFIRM_FRAMES) -> bool:
        n = self._counters.get(key, 0)
        n = n + 1 if value else 0
        self._counters[key] = n
        return n >= need

    def _select_hands(self, poses: Sequence[HandPose]):
        by_label = {p.handedness: p for p in poses}
        pref = str(self.cfg["cursor_hand"]).lower()
        if pref == "left":
            primary = by_label.get("Left") or by_label.get("Right")
        else:
            primary = by_label.get("Right") or by_label.get("Left")
        left = by_label.get("Left")
        return primary, left, by_label

    def map_cursor(self, norm_xy: Tuple[float, float]) -> Tuple[float, float]:
        margin = float(self.cfg["active_margin"])
        span = max(1.0 - 2.0 * margin, 1e-6)
        fx = _clamp((norm_xy[0] - margin) / span, 0.0, 1.0)
        fy = _clamp((norm_xy[1] - margin) / span, 0.0, 1.0)
        gain = float(self.cfg["cursor_gain"])
        if gain != 1.0:
            fx = _clamp(0.5 + (fx - 0.5) * gain, 0.0, 1.0)
            fy = _clamp(0.5 + (fy - 0.5) * gain, 0.0, 1.0)
        left, top, width, height = self._virtual
        x = left + fx * max(width - 1, 1)
        y = top + fy * max(height - 1, 1)
        return (x, y)

    def _owner_blocks(self) -> bool:
        """True when a held action owns the cursor."""
        return self.active in ("scroll", "swipe")

    # ------------------------------------------------------------------ main
    def update(self, poses: Sequence[HandPose], aim_index: Optional[int], now: float, dt: float
               ) -> List[Intent]:
        out: List[Intent] = []
        primary, left_hand, by_label = self._select_hands(poses)

        if self.active == "swipe" and now >= self._swipe_until:
            self.active = None

        if primary is None:
            # nothing tracked: make sure nothing is left held down
            out.extend(self.release_all(now, reason="no hand"))
            return out

        n_ext = primary.num_extended
        open_conf = self._confirm("open", n_ext >= 3)
        fist_conf = self._confirm("fist", n_ext == 0)
        pinches = primary.pinches

        # ---------------- Alt-Tab modifier (left hand fist, held) ----------------
        out.extend(self._update_alt_tab(left_hand, primary, now))
        if self.alt_held:
            # while Alt is down only cursor movement and Tab taps are allowed
            if not self._owner_blocks():
                out.extend(self._cursor_intent(primary))
            return out

        # ---------------- pose transitions: flicks ----------------
        if fist_conf and self._state != "fist":
            if (now - self._t_open) <= float(self.cfg["flick_window_s"]) \
                    and (now - self._t_flick) > 0.8:
                out.append(Intent("window.minimise", monitor=aim_index, note="close flick"))
                self._t_flick = now
                self._pending_click = None       # the close swallowed the pinch
                self.last_note = "close flick -> minimise"
            self._state = "fist"
            self._t_fist = now
        elif open_conf and self._state != "open":
            if (now - self._t_fist) <= float(self.cfg["flick_window_s"]) \
                    and (now - self._t_flick) > 0.8:
                out.append(Intent("window.maximise", monitor=aim_index, note="open flick"))
                self._t_flick = now
                self.last_note = "open flick -> maximise/fullscreen"
            self._state = "open"
            self._t_open = now
        elif self._state == "none":
            self._state = "open" if n_ext >= 3 else ("fist" if n_ext == 0 else "mid")
            if self._state == "open":
                self._t_open = now
            elif self._state == "fist":
                self._t_fist = now
        elif open_conf:
            self._t_open = now
        elif fist_conf:
            self._t_fist = now

        # ---------------- push to talk (shaka) ----------------
        shaka = primary.pose == POSE_SHAKA
        if self.ptt_held:
            if not shaka:
                self.ptt_held = False
                out.append(Intent("keys.up", keys=("ctrl", "space")))
                self.last_note = "push-to-talk release"
        elif shaka and self.active is None:
            if self._shaka_since is None:
                self._shaka_since = now
            elif now - self._shaka_since >= float(self.cfg["ptt_arm_s"]):
                self.ptt_held = True
                out.append(Intent("keys.down", keys=("ctrl", "space")))
                self.last_note = "push-to-talk hold"
        else:
            self._shaka_since = None

        # ---------------- held actions: scroll / drag ----------------
        ring = bool(pinches.get("ring"))
        pinky = bool(pinches.get("pinky"))
        if self.active == "scroll":
            if ring:
                out.extend(self._scroll_step(primary, now, dt))
            else:
                self.active = None
                self._scroll_anchor = None
                self.last_note = "scroll end"
        elif self.active == "drag":
            if not pinky:
                out.append(Intent("mouse.up", button="left"))
                self.active = None
                self.last_note = "drag end"
        elif self.active is None and not fist_conf:
            if ring:
                self.active = "scroll"
                self._scroll_anchor = primary.palm_px[1]
                self.last_note = "scroll start"
            elif pinky:
                out.append(Intent("mouse.down", button="left"))
                self.active = "drag"
                self.last_note = "drag start"

        # ---------------- tab swipe (open hand, fast lateral move) ----------------
        pinching_any = any(pinches.get(f) for f in ("index", "middle", "ring", "pinky"))
        if self.active is None and n_ext >= 4 and not pinching_any:
            swipe = self._swipe_detect(primary, now)
            if swipe is not None:
                out.append(swipe)
        else:
            self._swipe_samples.clear()

        # ---------------- clicks ----------------
        if not fist_conf and self.active is None and not self.ptt_held:
            out.extend(self._click_logic(primary, now))
        elif fist_conf:
            self._pending_click = None

        # ---------------- cursor ----------------
        if not self._owner_blocks():
            out.extend(self._cursor_intent(primary))

        self._prev_index_pinch = bool(pinches.get("index"))
        self._prev_middle_pinch = bool(pinches.get("middle"))
        self._prev_pinky_pinch = bool(pinches.get("pinky"))
        return out

    # ------------------------------------------------------------------ pieces
    def _cursor_intent(self, primary: HandPose) -> List[Intent]:
        if not primary.extended.get("index"):
            return []
        x, y = self.map_cursor(primary.index_tip_norm)
        deadband = float(self.cfg["deadband_px"])
        if abs(x - self._cursor[0]) < deadband and abs(y - self._cursor[1]) < deadband:
            return []
        self._cursor = (x, y)
        return [Intent("cursor.move", x=x, y=y)]

    def _click_logic(self, primary: HandPose, now: float) -> List[Intent]:
        out: List[Intent] = []
        index_now = bool(primary.pinches.get("index"))
        middle_now = bool(primary.pinches.get("middle"))
        arm = float(self.cfg["click_arm_s"])
        grace = float(self.cfg["click_release_grace_s"])
        rising = index_now and not self._prev_index_pinch

        # pinch, release, pinch quickly: commit the first so the pair reads as a double click
        if rising and self._pending_click is not None and self._pending_release is not None:
            out.extend(self._emit_click(now))

        if self._pending_click is not None:
            if index_now:
                self._pending_release = None
                # a deliberate pinch-and-HOLD also clicks, but only after long enough that a hand
                # closing into a fist (which sweeps through a pinch) can never reach it
                if now - self._pending_click >= arm:
                    out.extend(self._emit_click(now))
            else:
                # pinch released: wait a beat before calling it a click, because an open hand
                # closing into a fist passes through a pinch on the way
                if self._pending_release is None:
                    self._pending_release = now
                elif now - self._pending_release >= grace:
                    out.extend(self._emit_click(now))
        elif rising:
            self._pending_click = now
            self._pending_release = None

        if middle_now and not self._prev_middle_pinch and not index_now:
            if now - self._t_right_click >= float(self.cfg["right_click_cooldown_s"]):
                out.append(Intent("mouse.click", button="right"))
                self._t_right_click = now
                self.last_note = "right click"
        return out

    def _emit_click(self, now: float) -> List[Intent]:
        """Always a plain click. Two quick pinches produce two clicks inside the double-click
        window, which is what a physical mouse does and what the receiving app already
        interprets as a double click - so there is no synthetic double to get wrong."""
        self._pending_click = None
        self._pending_release = None
        self._t_last_click = now
        self.last_note = "left click"
        return [Intent("mouse.click", button="left")]

    def _scroll_step(self, primary: HandPose, now: float, dt: float) -> List[Intent]:
        y = primary.palm_px[1]
        if self._scroll_anchor is None:
            self._scroll_anchor = y
            return []
        dy = self._scroll_anchor - y                    # hand up = positive = scroll up
        self._scroll_anchor = y
        if abs(dy) < 0.5:
            return []
        clicks = dy / PX_PER_SCROLL_CLICK * float(self.cfg["scroll_gain"])
        if bool(self.cfg["scroll_adaptive"]):
            velocity = abs(dy) / max(dt, 1e-3)          # px per second
            clicks *= 1.0 + float(self.cfg["scroll_adaptive_k"]) * min(velocity / 1500.0, 2.0)
        units = int(round(clicks * 120))
        limit = int(self.cfg["scroll_max_per_frame"])
        units = int(_clamp(units, -limit, limit))
        if units == 0:
            return []
        return [Intent("mouse.wheel", amount=units)]

    def _swipe_detect(self, primary: HandPose, now: float) -> Optional[Intent]:
        win = float(self.cfg["swipe_window_s"])
        self._swipe_samples.append((now, primary.palm_px[0], primary.palm_px[1]))
        self._swipe_samples = [s for s in self._swipe_samples if now - s[0] <= win]
        if len(self._swipe_samples) < 3 or now - self._t_swipe < float(self.cfg["swipe_cooldown_s"]):
            return None
        t0, x0, y0 = self._swipe_samples[0]
        t1, x1, y1 = self._swipe_samples[-1]
        if t1 - t0 < 1e-3:
            return None
        dx_frac = (x1 - x0) / max(self.frame_w, 1)
        dy_frac = abs(y1 - y0) / max(self.frame_h, 1)
        if abs(dx_frac) < float(self.cfg["swipe_min_fraction"]):
            return None
        if dy_frac > float(self.cfg["swipe_max_vertical_fraction"]):
            return None
        self._t_swipe = now
        self._swipe_samples.clear()
        self.active = "swipe"
        self._swipe_until = now + 0.18          # freeze the cursor just long enough to settle
        if dx_frac > 0:
            self.last_note = "swipe right -> next tab"
            return Intent("keys.tap", keys=("ctrl", "tab"), note="swipe right")
        self.last_note = "swipe left -> previous tab"
        return Intent("keys.tap", keys=("ctrl", "shift", "tab"), note="swipe left")

    def _update_alt_tab(self, left_hand: Optional[HandPose], primary: HandPose, now: float
                        ) -> List[Intent]:
        out: List[Intent] = []
        left_fist = left_hand is not None and left_hand.num_extended == 0 and not any(
            left_hand.pinches.get(f) for f in ("index", "middle", "ring", "pinky"))

        if not self.alt_held:
            if left_fist:
                if self._left_fist_since is None:
                    self._left_fist_since = now
                    self.last_note = "left fist: hold to open Alt-Tab"
                elif now - self._left_fist_since >= float(self.cfg["alt_tab_hold_s"]):
                    self.alt_held = True
                    out.append(Intent("keys.down", keys=("alt",)))
                    out.append(Intent("keys.tap", keys=("tab",)))
                    self._t_tab = now
                    self.last_note = "alt-tab open (Alt held)"
            else:
                self._left_fist_since = None
        else:
            if not left_fist:
                self.alt_held = False
                out.append(Intent("keys.up", keys=("alt",)))
                self.last_note = "alt-tab commit (Alt released)"
            else:
                is_right = primary.handedness == "Right" or primary is not left_hand
                if is_right and primary.pinches.get("index"):
                    if now - self._t_tab >= float(self.cfg["alt_tab_repeat_s"]):
                        out.append(Intent("keys.tap", keys=("tab",)))
                        self._t_tab = now
                        self.last_note = "alt-tab next window"
        return out

    # ------------------------------------------------------------------ safety
    def release_all(self, now: float = 0.0, reason: str = "") -> List[Intent]:
        """Release every key and button this engine could be holding."""
        out: List[Intent] = []
        if self.active == "drag":
            out.append(Intent("mouse.up", button="left"))
        if self.alt_held:
            out.append(Intent("keys.up", keys=("alt",)))
        if self.ptt_held:
            out.append(Intent("keys.up", keys=("ctrl", "space")))
        self.active = None
        self.alt_held = False
        self.ptt_held = False
        self._scroll_anchor = None
        self._left_fist_since = None
        self._shaka_since = None
        self._pending_click = None
        self._pending_release = None
        self._swipe_samples.clear()
        self._swipe_until = 0.0
        if reason:
            self.last_note = f"released ({reason})"
        return out

    def state_summary(self) -> str:
        bits = []
        if self.active:
            bits.append(self.active)
        if self.alt_held:
            bits.append("alt")
        if self.ptt_held:
            bits.append("ptt")
        return ",".join(bits) if bits else "idle"
