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
    target_hwnd: Optional[int] = None      # window resolved from gaze (or aim); None = foreground
    focus_hwnd: Optional[int] = None       # focus this first (keyboard needs a focused window)
    warp: Optional[tuple] = None           # move the pointer here, wait, then act (gaze tab click)
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
        self.lock: Optional[str] = None            # the gesture that owns the hand right now
        self.ctrl_held = False                     # Ctrl-Tab modifier session
        self._right_fist_since: Optional[float] = None
        self._ctrl_tab_session_start = 0.0
        self._t_ctrl_tab = 0.0
        self._prev_left_finger: Optional[str] = None
        self._lock_since = 0.0
        self._scroll_release = 0
        self._scroll_smooth_y: Optional[float] = None
        self._alt_tab_session_start = 0.0
        self._prev_alt_pinch = False
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
    @property
    def ptt_keys(self) -> Tuple[str, ...]:
        """The dictation hotkey the shaka gesture holds.

        Defaults to ctrl+space, which is the Windows default transcribe binding in Handy
        (https://github.com/cjpais/Handy) - so push-to-talk dictation works with no setup on either
        side. Remap `ptt_keys` if Handy's shortcut is changed.
        """
        keys = self.cfg["ptt_keys"]
        return tuple(str(k) for k in keys) if keys else ("ctrl", "space")

    def set_frame_size(self, frame_w: int, frame_h: int):
        if frame_w and frame_h:
            self.frame_w, self.frame_h = frame_w, frame_h

    @staticmethod
    def _is_fist(hand: Optional[HandPose]) -> bool:
        return hand is not None and hand.num_extended == 0 and not any(
            hand.pinches.get(f) for f in ("index", "middle", "ring", "pinky"))

    # ------------------------------------------------------------------ lock
    def _release_lock(self, now: float = 0.0):
        """Let go of the hand. The swipe cooldown restarts here on purpose: the pose that ends most
        gestures is an open hand, and the motion that follows the release is exactly what a swipe
        looks for."""
        self.lock = None
        if now:
            self._t_swipe = now
        self._swipe_samples.clear()

    def _take_lock(self, name: str, now: float):
        self.lock = name
        self._lock_since = now

    def _lock_gate(self, primary: HandPose, n_ext: int, pinches: dict, left_hand, now: float) -> bool:
        """True when a NEW gesture may start.

        This is the fix for gestures being read out of the tail of another one: releasing a scroll
        sweeps the hand up through the open-hand pose with lateral motion, which is exactly what a
        tab swipe looks for. Once a gesture owns the hand, nothing else starts until the hand opens
        again - or the hand is lost, or the safety timeout expires.
        """
        if not bool(self.cfg["gesture_lock"]) or self.lock is None:
            return True
        if n_ext >= int(self.cfg["gesture_lock_open_fingers"]):
            self._release_lock(now)
            return True
        if self.lock == "click":
            # a click resolves on pinch release, not on a full open, or double-clicks could not work
            # - and a FIST ends it immediately: closing a hand into a fist sweeps through a pinch,
            # and that sweep must reach the close-flick rather than being eaten by the lock
            if n_ext == 0 or (not pinches.get("index") and self._pending_click is None):
                self._release_lock(now)
                return True
        elif self.lock == "alt_tab":
            # the session belongs to the other hand: it ends when the left fist opens
            if not self._is_fist(left_hand) and not self.alt_held:
                self._release_lock(now)
                return True
        if (now - self._lock_since) > float(self.cfg["gesture_lock_timeout_s"]):
            self._release_lock(now)       # never let a hand pose wedge the engine
            return True
        return False

    def _other_hand(self, primary: Optional[HandPose],
                    left_hand: Optional[HandPose]) -> Optional[HandPose]:
        """The hand that is NOT the cursor hand.

        With one hand in frame `_select_hands` can return the same hand for both, which would make
        every two-handed rule fire on a single hand - that is how a lone right fist stopped being a
        minimise.
        """
        if left_hand is None or left_hand is primary:
            return None
        return left_hand

    def _left_tap_finger(self, left_hand: Optional[HandPose]) -> Optional[str]:
        """Which tab-tap the left hand is making: index = next, middle = previous."""
        if left_hand is None:
            return None
        if left_hand.pinches.get("index"):
            return "index"
        if left_hand.pinches.get("middle"):
            return "middle"
        return None

    def _ctrl_tab_holding(self, primary: Optional[HandPose],
                          left_hand: Optional[HandPose] = None) -> bool:
        """Is this right-hand fist the Ctrl modifier rather than a close-flick?

        Which hand holds the modifier is the only thing that tells Ctrl-Tab from Alt-Tab, so a right
        fist has to be able to mean "hold Ctrl" - and that collides with the right hand's own
        close-flick (minimise). The guard is deliberate: while the left hand is in frame, a right
        fist is treated as the modifier. `ctrl_tab_flick_guard` relaxes it to `left_pinch` (only
        while the left hand is actually pinching) or `off` if you would rather the flick always win.
        """
        if not bool(self.cfg["ctrl_tab_enabled"]) or not self._is_fist(primary):
            return False
        if self.ctrl_held:
            return True
        guard = str(self.cfg["ctrl_tab_flick_guard"]).lower()
        if guard == "off":
            return False
        other = self._other_hand(primary, left_hand)
        if self._left_tap_finger(other) is not None:
            return True
        return guard == "two_hands" and other is not None

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
    def update(self, poses: Sequence[HandPose], aim_index: Optional[int], now: float, dt: float,
               target_hwnd: Optional[int] = None) -> List[Intent]:
        """target_hwnd is the window the gaze says you are looking at (or None). It is resolved by
        the caller so this module stays free of OS calls, and it is what makes gestures act on the
        window you are looking at rather than on whatever has focus."""
        out: List[Intent] = []
        primary, left_hand, by_label = self._select_hands(poses)

        # A left fist is the Alt-Tab modifier, never the cursor hand. When the right hand leaves
        # the frame the fist used to be promoted to primary (by_label.get("Right") or ...), and
        # its fist pose then fired the close-flick - minimising whatever had focus while the user
        # was simply holding the modifier.
        if (primary is left_hand and self._is_fist(primary) and not self.alt_held
                and str(self.cfg["cursor_hand"]).lower() == "right"):
            primary = None

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
        can_start = self._lock_gate(primary, n_ext, pinches, left_hand, now)

        # ---------------- Alt-Tab modifier (left hand fist, held) ----------------
        out.extend(self._update_alt_tab(left_hand, primary, now, target_hwnd, can_start))
        if self.alt_held:
            # while Alt is down only cursor movement and Tab taps are allowed
            if not self._owner_blocks():
                out.extend(self._cursor_intent(primary))
            return out

        # ---------------- Ctrl-Tab modifier (right hand fist, left hand taps) ----------------
        out.extend(self._update_ctrl_tab(primary, left_hand, now, target_hwnd, can_start))
        if self.ctrl_held:
            # the cursor hand is a fist here: nothing to point with, and nothing else may start
            return out

        # ---------------- pose transitions: flicks ----------------
        if fist_conf and self._state != "fist":
            if (can_start and not self._ctrl_tab_holding(primary, left_hand)
                    and (now - self._t_open) <= float(self.cfg["flick_window_s"])
                    and (now - self._t_flick) > 0.8):
                out.append(Intent("window.minimise", monitor=aim_index, target_hwnd=target_hwnd,
                                  note="close flick"))
                self._t_flick = now
                self._pending_click = None       # the close swallowed the pinch
                self._take_lock("flick", now)
                self.last_note = "close flick -> minimise"
            self._state = "fist"
            self._t_fist = now
        elif open_conf and self._state != "open":
            if can_start and (now - self._t_fist) <= float(self.cfg["flick_window_s"]) \
                    and (now - self._t_flick) > 0.8:
                out.append(Intent("window.maximise", monitor=aim_index, target_hwnd=target_hwnd,
                                  note="open flick"))
                self._t_flick = now
                self._take_lock("flick", now)
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
                out.append(Intent("keys.up", keys=self.ptt_keys))
                self.last_note = "push-to-talk release"
        elif shaka and self.active is None and can_start:
            if self._shaka_since is None:
                self._shaka_since = now
            elif now - self._shaka_since >= float(self.cfg["ptt_arm_s"]):
                self.ptt_held = True
                out.append(Intent("keys.down", keys=self.ptt_keys))
                self._take_lock("ptt", now)
                self.last_note = "push-to-talk hold"
        else:
            self._shaka_since = None

        # ---------------- held actions: scroll / drag ----------------
        ring = bool(pinches.get("ring"))
        pinky = bool(pinches.get("pinky"))
        ring_conf = self._confirm("ring", ring, int(self.cfg["scroll_confirm_frames"]))
        if self.active == "scroll":
            if ring:
                self._scroll_release = 0
                out.extend(self._scroll_step(primary, now, dt))
            else:
                # a pinch that flickers off for a frame or two must not end a scroll in progress
                self._scroll_release += 1
                if self._scroll_release >= int(self.cfg["scroll_release_frames"]):
                    self.active = None
                    self._scroll_anchor = None
                    self._scroll_smooth_y = None
                    self.last_note = "scroll end"
        elif self.active == "drag":
            if not pinky:
                out.append(Intent("mouse.up", button="left"))
                self.active = None
                self.last_note = "drag end"
        elif self.active is None and not fist_conf and can_start:
            if ring_conf:
                self.active = "scroll"
                self._scroll_anchor = primary.palm_px[1]
                self._scroll_smooth_y = None
                self._scroll_release = 0
                self._take_lock("scroll", now)
                self.last_note = "scroll start"
            elif self._confirm("pinky", pinky, int(self.cfg["drag_confirm_frames"])):
                out.append(Intent("mouse.down", button="left"))
                self.active = "drag"
                self._take_lock("drag", now)
                self.last_note = "drag start"

        # ---------------- tab swipe (open hand, fast lateral move) ----------------
        pinching_any = any(pinches.get(f) for f in ("index", "middle", "ring", "pinky"))
        if self.active is None and can_start and n_ext >= 4 and not pinching_any:
            swipe = self._swipe_detect(primary, now, target_hwnd)
            if swipe is not None:
                out.append(swipe)
        else:
            self._swipe_samples.clear()

        # ---------------- clicks ----------------
        if (not fist_conf and self.active is None and not self.ptt_held
                and self.lock in (None, "click")):
            out.extend(self._click_logic(primary, now))
            if self._pending_click is not None and self.lock is None:
                self._take_lock("click", now)
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
        smooth = float(self.cfg["scroll_smooth"])
        if smooth > 0.0:
            # hand tracking jitter reads as scrolling; a little EMA on this axis removes it without
            # making the scroll feel late
            self._scroll_smooth_y = y if self._scroll_smooth_y is None else (
                smooth * self._scroll_smooth_y + (1.0 - smooth) * y)
            y = self._scroll_smooth_y
        if self._scroll_anchor is None:
            self._scroll_anchor = y
            return []
        dy = self._scroll_anchor - y                    # hand up = positive = scroll up
        self._scroll_anchor = y
        if abs(dy) < float(self.cfg["scroll_deadband_px"]):
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

    def _update_ctrl_tab(self, primary: Optional[HandPose], left_hand: Optional[HandPose],
                         now: float, target_hwnd: Optional[int] = None,
                         can_start: bool = True) -> List[Intent]:
        """Right-hand fist holds Ctrl; the left hand taps through browser tabs.

        The mirror of Alt-Tab, and the one gesture pair that is deliberately not interchangeable:
        which hand holds the modifier is the only thing that distinguishes Ctrl-Tab from Alt-Tab.
        `Ctrl` stays down for the whole session, so a tap is just `Tab` (or `Shift+Tab` back) - a tap
        that pressed Ctrl itself would lift the modifier again on release.
        """
        out: List[Intent] = []
        if not bool(self.cfg["ctrl_tab_enabled"]):
            return out
        right_fist = self._is_fist(primary)
        other = self._other_hand(primary, left_hand)
        finger = self._left_tap_finger(other)

        if not self.ctrl_held:
            if right_fist and can_start:
                if self._right_fist_since is None:
                    self._right_fist_since = now
                    self.last_note = "right fist: hold to open Ctrl-Tab"
                elif (now - self._right_fist_since) >= float(self.cfg["ctrl_tab_hold_s"]):
                    self.ctrl_held = True
                    self._ctrl_tab_session_start = now
                    self._prev_left_finger = finger
                    self._take_lock("ctrl_tab", now)
                    out.append(Intent("keys.down", keys=("ctrl",), focus_hwnd=target_hwnd,
                                      note="ctrl-tab modifier"))
                    self.last_note = "ctrl-tab open (Ctrl held)"
            else:
                self._right_fist_since = None
            return out

        # ---- session open: Ctrl is down ----
        if not right_fist:
            self.ctrl_held = False
            out.append(Intent("keys.up", keys=("ctrl",), note="ctrl-tab commit"))
            self._release_lock(now)
            self._prev_left_finger = None
            self.last_note = "ctrl-tab done (Ctrl released)"
            return out
        if (now - self._ctrl_tab_session_start) > float(self.cfg["ctrl_tab_session_timeout_s"]):
            self.ctrl_held = False
            self._right_fist_since = None
            out.append(Intent("keys.up", keys=("ctrl",), note="ctrl-tab timeout"))
            self._release_lock(now)
            self._prev_left_finger = None
            self.last_note = "ctrl-tab timeout (Ctrl released)"
            return out

        rising = finger is not None and finger != self._prev_left_finger
        held = finger is not None and finger == self._prev_left_finger
        if finger == "index" and (rising or (held and (now - self._t_ctrl_tab)
                                              >= float(self.cfg["ctrl_tab_repeat_s"]))):
            out.append(Intent("keys.tap", keys=("tab",), focus_hwnd=target_hwnd, note="next tab"))
            self._t_ctrl_tab = now
            self.last_note = "ctrl-tab: next tab"
        elif finger == "middle" and (rising or (held and (now - self._t_ctrl_tab)
                                                >= float(self.cfg["ctrl_tab_repeat_s"]))):
            out.append(Intent("keys.tap", keys=("shift", "tab"), focus_hwnd=target_hwnd,
                              note="previous tab"))
            self._t_ctrl_tab = now
            self.last_note = "ctrl-tab: previous tab"
        self._prev_left_finger = finger
        return out

    def _swipe_detect(self, primary: HandPose, now: float,
                      target_hwnd: Optional[int] = None) -> Optional[Intent]:
        if str(self.cfg["swipe_action"]).lower() == "none":
            return None                     # the Ctrl-Tab gesture replaced the swipe binding
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
        self._take_lock("swipe", now)
        self._swipe_until = now + 0.18          # freeze the cursor just long enough to settle
        if dx_frac > 0:
            self.last_note = "swipe right -> next tab"
            return Intent("keys.tap", keys=("ctrl", "tab"), focus_hwnd=target_hwnd,
                          note="swipe right")
        self.last_note = "swipe left -> previous tab"
        return Intent("keys.tap", keys=("ctrl", "shift", "tab"), focus_hwnd=target_hwnd,
                      note="swipe left")

    def _update_alt_tab(self, left_hand: Optional[HandPose], primary: HandPose, now: float,
                        target_hwnd: Optional[int] = None,
                        can_start: bool = True) -> List[Intent]:
        """Left fist held opens Alt; the right hand then moves between windows.

        Hardened over the first cut: the session latches (so nothing else can fire mid-switch), Tab
        fires on the rising edge of the pinch as well as while it is held, Alt is force-released on
        a timeout, and the whole thing ends the moment the left fist opens.
        """
        out: List[Intent] = []
        left_fist = self._is_fist(left_hand)

        if not self.alt_held:
            if left_fist and can_start:
                if self._left_fist_since is None:
                    self._left_fist_since = now
                    self.last_note = "left fist: hold to open Alt-Tab"
                elif now - self._left_fist_since >= float(self.cfg["alt_tab_hold_s"]):
                    self.alt_held = True
                    self._alt_tab_session_start = now
                    self._take_lock("alt_tab", now)
                    self._prev_alt_pinch = bool(primary.pinches.get("index"))
                    out.append(Intent("keys.down", keys=("alt",), focus_hwnd=target_hwnd))
                    out.append(Intent("keys.tap", keys=("tab",)))
                    self._t_tab = now
                    self.last_note = "alt-tab open (Alt held)"
            elif not left_fist:
                self._left_fist_since = None
        else:
            if not left_fist:
                self.alt_held = False
                self._left_fist_since = None
                out.append(Intent("keys.up", keys=("alt",)))
                self._release_lock(now)
                self.last_note = "alt-tab commit (Alt released)"
            elif (now - self._alt_tab_session_start) > float(self.cfg["alt_tab_session_timeout_s"]):
                self.alt_held = False
                self._left_fist_since = None
                out.append(Intent("keys.up", keys=("alt",)))
                self._release_lock(now)
                self.last_note = "alt-tab timeout (Alt released)"
            else:
                pinch = bool(primary.pinches.get("index"))
                rising = pinch and not self._prev_alt_pinch
                repeat = pinch and (now - self._t_tab) >= float(self.cfg["alt_tab_repeat_s"])
                if rising or repeat:
                    out.append(Intent("keys.tap", keys=("tab",)))
                    self._t_tab = now
                    self.last_note = "alt-tab next window"
        self._prev_alt_pinch = bool(primary.pinches.get("index"))
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
            out.append(Intent("keys.up", keys=self.ptt_keys))
        self.active = None
        self.lock = None
        self.alt_held = False
        self.ctrl_held = False
        self._right_fist_since = None
        self._prev_left_finger = None
        self.ptt_held = False
        self._scroll_anchor = None
        self._scroll_smooth_y = None
        self._scroll_release = 0
        self._left_fist_since = None
        self._shaka_since = None
        self._prev_alt_pinch = False
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


class GazeScroller:
    """Eye-driven scrolling: hold your gaze in the top or bottom band of the screen and it scrolls
    for as long as you keep looking.

    Lives here with the other gesture logic rather than in the gaze engine - it is a gesture, it
    just happens to be driven by eyes instead of hands. Emission is continuous while engaged, with a
    dwell before it starts (so a glance is not a scroll), a ramp to full speed (so it does not jerk
    into motion), and a cooldown after it stops (so it does not flap on and off at the boundary).

    The wheel goes to whichever window is under the *cursor*, so unless the cursor is parked on the
    gaze point this would scroll the wrong window - hence the cursor warp.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = str(cfg["gaze_scroll_mode"]).lower()
        self.edge = float(cfg["gaze_scroll_edge"])
        self.dwell = float(cfg["gaze_scroll_dwell_s"])
        self.ramp = max(float(cfg["gaze_scroll_ramp_s"]), 1e-3)
        self.speed = float(cfg["gaze_scroll_speed"])
        self.cooldown = float(cfg["gaze_scroll_cooldown_s"])
        self.warp = bool(cfg["gaze_scroll_warp_cursor"])
        self.active = False
        self.direction = 0
        self._band_since: Optional[float] = None
        self._active_since = 0.0
        self._stopped_at = 0.0

    def _band(self, gaze) -> int:
        """-1 = top band (scroll up), +1 = bottom band (scroll down), 0 = neither."""
        if gaze is None or not getattr(gaze, "valid", False):
            return 0
        _left, top, _w, h = virtual_screen()
        rel = (gaze.y - top) / max(h, 1)
        if rel <= self.edge:
            return -1
        if rel >= 1.0 - self.edge:
            return 1
        return 0

    def update(self, gaze, now: float, dt: float) -> List[Intent]:
        if self.mode != "edge":
            return []
        band = self._band(gaze)
        if band == 0:
            if self.active:
                self._stopped_at = now
            self.active = False
            self.direction = 0
            self._band_since = None
            return []
        if self._band_since is None or band != self.direction:
            self._band_since = now                 # direction change restarts the dwell timer
            self.direction = band
            if self.active:
                self.active = False
                self._stopped_at = now
        if not self.active:
            if self._stopped_at and now - self._stopped_at < self.cooldown:
                return []
            if now - self._band_since < self.dwell:
                return []
            self.active = True
            self._active_since = now

        strength = min(1.0, 0.15 + (now - self._active_since) / self.ramp)
        units = self.speed * strength * max(dt, 1e-4)
        amount = int(units) * (-self.direction)     # top band (-1) scrolls up (+)
        out: List[Intent] = []
        if self.warp:
            out.append(Intent("cursor.warp", x=gaze.x, y=gaze.y, note="gaze scroll"))
        if amount:
            out.append(Intent("mouse.wheel", amount=amount))
        return out

    def release(self):
        self.active = False
        self.direction = 0
        self._band_since = None

    def status(self) -> str:
        if not self.active:
            return "gaze-scroll idle"
        return f"gaze-scroll {'up' if self.direction < 0 else 'down'}"
