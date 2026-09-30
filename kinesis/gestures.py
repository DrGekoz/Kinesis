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

import math

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
        self._drag_source: Optional[str] = None
        # Gesture-Maps: the user's own bindings, loaded from JSON (see kinesis/gesture_map.py)
        self.map = None
        self._map_held: Dict[str, bool] = {}
        self._map_confirm: Dict[str, int] = {}
        # The pointer comes from the eyes now. The app turns this on only when gaze cannot drive it
        # (no calibration, or looking away) so the mouse is never left dead.
        self.cursor_from_hand = False
        self._zoom_active = False                  # two-hand pinch zoom
        self._zoom_start = 0.0
        self._zoom_last = 0.0
        self._zoom_accum = 0.0
        self._zoom_release = 0
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

        # claw drag (minimise / maximise)

        self._claw_active = False

        self._claw_y0 = 0.0

        self._claw_t0 = 0.0

        self._claw_fired = False
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

    def _begin_held(self, name: str, source: str, now: float) -> None:
        """Start a held action (drag/scroll). It owns the hand until the pinch that started it lets go."""
        self.active = name
        self._drag_source = source
        self._scroll_release = 0
        self._take_lock(name, now)
        self.last_note = f"{name} start"

    def _end_held(self, note: str) -> None:
        self.active = None
        self._drag_source = None
        self._zoom_active = False
        self._zoom_accum = 0.0
        self._zoom_release = 0
        self._scroll_anchor = None
        self._scroll_smooth_y = None
        self._scroll_release = 0
        self.last_note = note

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
        """Is this right-hand fist the Ctrl modifier?

        This used to need a guard, because a right fist was ALSO the close-flick that minimised,
        and only the two-hand mode could tell "hold Ctrl" from "close the window". The flick is
        gone (see the claw drag), so a right fist simply means Ctrl. `ctrl_tab_flick_guard` is
        retained in the config for backwards compatibility and is no longer read.
        """
        if not bool(self.cfg["ctrl_tab_enabled"]) or not self._is_fist(primary):
            return False
        return True

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

        # ---------------- two-hand pinch zoom ----------------
        out.extend(self._update_zoom(primary, left_hand, now, target_hwnd, can_start))
        if self._zoom_active:
            # both index pinches are the zoom, so no click, no flick, and no cursor to follow
            return out

        # ---------------- pose transitions ----------------
        # The open->fist flick used to minimise and fist->open used to maximise. Both are gone.
        # The fist is the Alt-Tab and Ctrl-Tab modifier, so every attempt at either gesture was
        # minimising whatever had focus, and a hand passing through a fist on its way open was
        # maximising windows nobody asked it to. The fist still tracks state because other logic
        # needs to know it is a fist - it just does not do anything any more.
        if fist_conf and self._state != "fist":
            self._state = "fist"
            self._t_fist = now
            # A fist still swallows a pinch that is in flight. It no longer minimises, but reaching
            # for the Alt-Tab fist should not leave a click waiting to fire either.
            self._pending_click = None
        elif open_conf and self._state != "open":
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

        # ---------------- claw drag: minimise / maximise ----------------
        # Pinch everything (all four fingertips to the thumb) and drag down to minimise, up to
        # maximise. It cannot be confused with the fist, so it can never fire during an alt-tab,
        # and it is ignored outright when the gaze is not over a window - acting on whatever
        # happens to have focus is what made the old gesture so destructive.
        claw = bool(self.cfg["claw_minimise"]) and all(
            pinches.get(finger) for finger in ("index", "middle", "ring", "pinky"))
        if claw and self.active is None and not self.alt_held and not self.ctrl_held \
                and not self.ptt_held and not self._zoom_active:
            if not self._claw_active:
                self._claw_active = True
                self._claw_y0 = float(primary.palm_px[1])
                self._claw_t0 = now
                self._claw_fired = False
            elif not self._claw_fired:
                travel = float(primary.palm_px[1]) - self._claw_y0
                if (now - self._claw_t0) <= float(self.cfg["claw_window_s"]) \
                        and abs(travel) >= abs(float(self.cfg["claw_travel_px"])):
                    going_down = travel > 0
                    if target_hwnd is None:
                        self._claw_fired = True
                        self.last_note = ("claw " + ("down" if going_down else "up")
                                          + " ignored - not looking at a window")
                    else:
                        out.append(Intent("window.minimise" if going_down else "window.maximise",
                                          monitor=aim_index, target_hwnd=target_hwnd,
                                          note="claw drag"))
                        self._claw_fired = True
                        self._take_lock("claw", now)
                        self.last_note = ("claw drag -> "
                                          + ("minimise" if going_down else "maximise"))
        elif not claw:
            self._claw_active = False
            self._claw_fired = False
            self._claw_y0 = 0.0
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
        # --- held pinches: thumb+ring carries the drag now, thumb+pinky is unbound ---
        ring = bool(pinches.get("ring")) and not claw
        pinky = bool(pinches.get("pinky")) and not claw
        ring_action = str(self.cfg["pinch_ring_action"]).lower()      # drag | scroll | none
        pinky_action = str(self.cfg["pinch_pinky_action"]).lower()    # drag | none
        ring_conf = self._confirm("ring", ring, int(self.cfg["ring_confirm_frames"]))
        pinky_conf = self._confirm("pinky", pinky, int(self.cfg["drag_confirm_frames"]))

        if self.active == "scroll":
            if ring and ring_action == "scroll":
                self._scroll_release = 0
                out.extend(self._scroll_step(primary, now, dt))
            else:
                # a pinch that flickers for a frame or two must not end a held action
                self._scroll_release += 1
                if self._scroll_release >= int(self.cfg["scroll_release_frames"]):
                    self._end_held("scroll end")
        elif self.active == "drag":
            src = self._drag_source
            held = ((src == "ring" and ring and ring_action == "drag")
                    or (src == "pinky" and pinky and pinky_action == "drag"))
            if held:
                self._scroll_release = 0
            else:
                # releasing a drag holds the selection for a few frames: one dropped frame of the
                # pinch should not end a text selection mid-drag
                self._scroll_release += 1
                if self._scroll_release >= int(self.cfg["scroll_release_frames"]):
                    out.append(Intent("mouse.up", button="left"))
                    self._end_held("drag end")
        elif self.active is None and not fist_conf and can_start:
            if ring_action == "drag" and ring_conf:
                out.append(Intent("mouse.down", button="left"))
                self._begin_held("drag", "ring", now)
            elif ring_action == "scroll" and ring_conf:
                self._scroll_anchor = primary.palm_px[1]
                self._scroll_smooth_y = None
                self._begin_held("scroll", "ring", now)
            elif pinky_action == "drag" and pinky_conf:
                out.append(Intent("mouse.down", button="left"))
                self._begin_held("drag", "pinky", now)

        # ---------------- tab swipe (open hand, fast lateral move) ----------------
        pinching_any = any(pinches.get(f) for f in ("index", "middle", "ring", "pinky"))
        if self.active is None and can_start and n_ext >= 4 and not pinching_any:
            swipe = self._swipe_detect(primary, now, target_hwnd)
            if swipe is not None:
                out.append(swipe)
        else:
            self._swipe_samples.clear()

        # ---------------- the user's gesture map ----------------
        out.extend(self._map_intents(primary, self._other_hand(primary, left_hand), now,
                                     target_hwnd))

        # ---------------- clicks ----------------
        # both hands pinching index is the zoom, so it cannot also be a click
        both_index = (left_hand is not None and left_hand is not primary
                      and bool(pinches.get("index")) and bool(left_hand.pinches.get("index")))
        if (not self.map_owns("pinch_index") and not self.map_owns("pinch_middle")
                and not fist_conf and not claw and self.active is None and not self.ptt_held \
                and not both_index
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
    # ------------------------------------------------------------------ gesture maps
    def load_map(self, gmap) -> None:
        """Use a Gesture-Map (or None for built-ins only). Called at startup and after an import."""
        self.map = gmap
        self._map_held = {}
        self._map_confirm = {}

    def map_owns(self, pose: str) -> bool:
        return bool(self.map is not None and self.map.owns(pose))

    def _map_intents(self, left, right, now: float, target_hwnd) -> List[Intent]:
        """Fire the user's bindings.

        Tap actions fire on a confirmed rising edge; hold actions press on the way in and release on
        the way out. Nothing here can fire while the built-in engine owns the hand, so an imported
        map cannot fight a drag that is already in progress.
        """
        out: List[Intent] = []
        if self.map is None or self.active is not None:
            return out
        frames = max(int(self.cfg.get("map_confirm_frames", 2)), 1)
        matching = {b.id: b for b in self.map.matching(left, right)}
        for bid, held in list(self._map_held.items()):
            binding = next((b for b in self.map.bindings if b.id == bid), None)
            if binding is None or bid not in matching:
                if binding is not None and held:
                    if binding.action.kind == "hold_keys":
                        out.append(Intent("keys.up", keys=binding.action.combo,
                                          focus_hwnd=target_hwnd))
                    elif binding.action.kind == "mouse_hold":
                        button = binding.action.button if binding.action.button in (
                            "left", "right", "middle") else "left"
                        out.append(Intent("mouse.up", button=button, target_hwnd=target_hwnd))
                self._map_held[bid] = False
                self._map_confirm[bid] = 0
        for bid, binding in matching.items():
            self._map_confirm[bid] = self._map_confirm.get(bid, 0) + 1
            if self._map_confirm[bid] < frames:
                continue
            rising = not self._map_held.get(bid, False)
            self._map_held[bid] = True
            if binding.action.kind == "hold_keys":
                if rising:
                    out.append(Intent("keys.down", keys=binding.action.combo,
                                      focus_hwnd=target_hwnd))
                continue
            if binding.action.kind == "mouse_hold":
                if rising:
                    out.extend(map_action_intents(binding.action, target_hwnd, note=binding.id))
                continue
            if rising:
                out.extend(map_action_intents(binding.action, target_hwnd, note=binding.id))
        return out

    def _cursor_intent(self, primary: HandPose) -> List[Intent]:
        if not self.cursor_from_hand:
            return []                    # the pointer follows the eyes; see KinesisApp._gaze_cursor
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

    def _hand_span(self, a: Optional[HandPose], b: Optional[HandPose]) -> float:
        """Distance between the two hands' pinch points, in pixels."""
        if a is None or b is None:
            return 0.0
        ax, ay = a.pinch_point_px("index")
        bx, by = b.pinch_point_px("index")
        return math.hypot(bx - ax, by - ay)

    def _update_zoom(self, primary: Optional[HandPose], other: Optional[HandPose],
                     now: float, target_hwnd: Optional[int] = None,
                     can_start: bool = True) -> List[Intent]:
        """Both hands pinching index+thumb, then apart to zoom in and together to zoom out.

        Travel is accumulated and spent in steps, so a small move is a small zoom and a big sweep is
        a big one, at a rate the hand controls rather than a timer.
        """
        out: List[Intent] = []
        if not bool(self.cfg["zoom_pinch_enabled"]):
            return out
        # another hand must be the SAME object as primary when only one is in frame - and then the
        # two pinches are one pinch, which zoomed on a single hand until this was normalised
        other = self._other_hand(primary, other)
        both = (primary is not None and other is not None
                and bool(primary.pinches.get("index")) and bool(other.pinches.get("index")))
        both_index = (other is not None and bool(primary) and bool(other.pinches.get("index"))
                      and bool(primary.pinches.get("index")))
        if not self._zoom_active:
            # `lock == "click"` counts too: a single pinch that becomes a two-hand pinch has already
            # armed a click and taken the lock, and the pending click is cancelled below.
            if both and (can_start or self.lock == "click"):
                if self._confirm("zoom", True, int(self.cfg["zoom_confirm_frames"])):
                    self._zoom_active = True
                    self._zoom_start = now
                    self._zoom_last = self._hand_span(primary, other)
                    self._zoom_accum = 0.0
                    self._zoom_release = 0
                    self._pending_click = None  # both index pinches belong to the zoom now
                    self._take_lock("zoom", now)
                    self.last_note = "zoom: two-hand pinch"
            else:
                # reset ONLY when it is not a two-hand pinch: resetting after every failed confirm
                # wiped the accumulator each frame, so the confirm could never reach its threshold
                self._confirm("zoom", False)
            return out

        if both:
            self._zoom_release = 0
        else:
            self._zoom_release += 1
            if self._zoom_release >= int(self.cfg["scroll_release_frames"]):
                self._zoom_active = False
                self._release_lock(now)
                self.last_note = "zoom end"
                return out
        if (now - self._zoom_start) > float(self.cfg["zoom_session_timeout_s"]):
            self._zoom_active = False
            self._release_lock(now)
            self.last_note = "zoom timeout"
            return out

        span = self._hand_span(primary, other)
        delta = span - self._zoom_last
        self._zoom_last = span
        if abs(delta) < float(self.cfg["zoom_deadband_px"]):
            return out
        self._zoom_accum += delta
        step = max(1.0, float(self.cfg["zoom_step_px"]))
        steps = int(abs(self._zoom_accum) // step)
        if not steps:
            return out
        steps = min(steps, max(1, int(self.cfg["zoom_max_steps_per_frame"])))
        zoom_in = self._zoom_accum > 0.0            # hands apart = zoom in
        self._zoom_accum -= steps * step * (1.0 if zoom_in else -1.0)
        keys = tuple(self.cfg["zoom_keys_in"] if zoom_in else self.cfg["zoom_keys_out"])
        for _ in range(steps):
            out.append(Intent("keys.tap", keys=keys, focus_hwnd=target_hwnd,
                              note="zoom in" if zoom_in else "zoom out"))
        self.last_note = f"zoom {'in' if zoom_in else 'out'} x{steps}"
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
        if self.ctrl_held:
            out.append(Intent("keys.up", keys=("ctrl",)))
        self.active = None
        self.lock = None
        self.alt_held = False
        self.ctrl_held = False
        self._zoom_active = False
        self._zoom_accum = 0.0
        self._zoom_release = 0
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


def map_action_intents(action, target_hwnd, note: str = "") -> List["Intent"]:
    """Turn one Gesture-Map action into the intents the runner already knows how to perform.

    Only the action types listed in gesture_map.ACTION_TYPES reach here, so a map imported from
    anywhere can press keys, click, or minimise a window - and nothing else. It cannot run a command.
    """
    from .gesture_map import SYSTEM_ACTIONS                     # noqa: F401 (documentation value)
    kind = action.kind
    if kind == "keys":
        return [Intent("keys.tap", keys=action.combo, focus_hwnd=target_hwnd, note=note)]
    if kind == "mouse":
        button = action.button
        if button in ("left", "right", "middle"):
            return [Intent("mouse.click", button=button, target_hwnd=target_hwnd, note=note)]
        if button == "double":
            return [Intent("mouse.double", button="left", target_hwnd=target_hwnd, note=note)]
        if button == "wheel_up":
            return [Intent("mouse.wheel", amount=1, target_hwnd=target_hwnd, note=note)]
        if button == "wheel_down":
            return [Intent("mouse.wheel", amount=-1, target_hwnd=target_hwnd, note=note)]
        return []
    if kind == "mouse_hold":
        # a real drag: the button goes down while the gesture is held and up when it is released
        button = action.button if action.button in ("left", "right", "middle") else "left"
        return [Intent("mouse.down", button=button, target_hwnd=target_hwnd, note=note)]
    if kind == "system":
        value = action.value
        if value == "minimise":
            return [Intent("window.minimise", target_hwnd=target_hwnd, note=note)]
        if value == "maximise":
            return [Intent("window.maximise", target_hwnd=target_hwnd, note=note)]
        combos = {"fullscreen": ("f11",), "alt_tab": ("alt", "tab"),
                  "ctrl_tab_next": ("ctrl", "tab"), "ctrl_tab_prev": ("ctrl", "shift", "tab")}
        if value in combos:
            return [Intent("keys.tap", keys=combos[value], focus_hwnd=target_hwnd, note=note)]
    return []


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
