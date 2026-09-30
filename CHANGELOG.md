# Changelog

All notable changes to Kinesis. The README stays compact on purpose: this is where the detail lives.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[semantic](https://semver.org/spec/v2.0.0.html).

## [1.8.0] — two-hand pinch zoom

Both hands pinch index+thumb at once, then move apart to zoom in and together to zoom out.

### Added

- **`Ctrl+=` / `Ctrl+-` from a two-hand pinch.** The distance between the two hands' *pinch points*
  (thumb tip and index tip, averaged) is what gets measured, not their centres of mass — centres
  drift when the hands rotate, pinch points do not. Travel is accumulated and spent in steps
  (`zoom_step_px`, 22 px), so a small move is a small zoom and a big sweep is a big one, at a rate
  the hand controls rather than a timer. Capped at `zoom_max_steps_per_frame` so a fast sweep cannot
  flood the key queue.
- The zoom takes the gesture lock and suppresses the click outright: both index pinches are the zoom,
  so neither of them is a click while it runs. A pending click is cancelled when a zoom starts.
- `zoom_pinch_enabled`, `zoom_keys_in`, `zoom_keys_out`, `zoom_step_px`, `zoom_deadband_px`,
  `zoom_max_steps_per_frame`, `zoom_confirm_frames`, `zoom_session_timeout_s`.

### Three bugs found by running it rather than by reading it

1. **The zoom could never start.** The confirm counter accumulated correctly, then the `else` branch
   reset it *after every failed confirm* — so it was wiped each frame and never reached two. The
   reset now only happens when the pose genuinely is not a two-hand pinch.
2. **The zoom ran on one hand.** `_select_hands` returns the *same hand* for both roles when only one
   is in frame, so a single pinching hand satisfied "both hands pinching" and kept zooming out on a
   phantom 200 px span. Same trap that broke the close-flick in v1.6.0; `_other_hand()` fixes it.
3. **A regression I caught mid-edit:** a patch to `release_all` had swapped the dictation key release
   for a Ctrl release, which would have left `Ctrl+Space` stuck down. Both are released now.

### Also

- `=` and `-` are now in the key map (`VK_OEM_PLUS` / `VK_OEM_MINUS`), with `plus` / `minus` /
  `equal` / `hyphen` aliases, so any zoom binding in an app can be configured.
- 13 hand gestures now, up from 11.

---

## [1.7.0] — drag moves to the ring pinch, scrolling becomes gaze-only

Eye-gaze scrolling replaced the pinch scroll, so the held pinches were free to be re-cut.

### Changed

- **Thumb + ring pinch, held → drag** (text selection, moving files). It was the adaptive scroll. The
  drag keeps the frozen cursor and now also keeps the release tolerance: three dropped frames of the
  pinch will not lift the mouse button mid-selection.
- **Thumb + pinky pinch → unbound.** It was the drag, and it is the one pose that had to go: with the
  shaka (thumb + pinky *extended*) already in the vocabulary, a thumb-pinky *pinch* was the easiest
  thing in the set to trigger by accident.
- **Scrolling is gaze-only** (`gaze_scroll_mode: edge`, which already existed).
- `pinch_ring_action` (`drag` | `scroll` | `none`) and `pinch_pinky_action` (`drag` | `none`) make both
  old bindings recoverable by config. `scroll_confirm_frames` became `ring_confirm_frames`, since the
  ring pinch is no longer necessarily a scroll.

### The trap this creates, and the warning for it

Scrolling now depends entirely on a calibrated gaze model. If you have neither a model nor the pinch
fallback, you have **no scrolling at all**, so startup says so explicitly and names both fixes:

    [scroll] gaze scrolling is the only scroll binding, and it cannot work without a model
    [scroll] either run calibrate_gaze.bat, or keep the pinch scroll as a fallback:
             run.bat --tune pinch_ring_action=scroll --save-config

### Verified

- 165 tests. The ring pinch drags and freezes the cursor; the pinky pinch does nothing by default and
  drags when bound; a ring+pinky overlap produces exactly one drag; a drag survives two dropped pinch
  frames without releasing the button; the hand vanishing mid-drag releases it; and the pinch scroll
  still works when bound, including its confirm frames, flicker tolerance and speed ramp.
- One test was **passing vacuously** after the rebind — `test_drag_does_not_freeze_cursor` used the
  pinky pinch, so with pinky unbound the cursor moved for the wrong reason and the assertion held. It
  now asserts it is actually in a drag before checking the cursor.

---

## [1.6.0] — Ctrl+Tab gets its own two-hand gesture

The open-hand lateral swipe stopped switching browser tabs. In its place, the mirror of Alt-Tab.

### Added

- **Right-hand fist + left index pinch → `Ctrl+Tab`** (next tab), and **+ left middle pinch →
  `Ctrl+Shift+Tab`** (previous tab). The right fist is held `ctrl_tab_hold_s` (0.6 s) to open the
  session, `Ctrl` stays down for the whole session, each pinch taps on its rising edge and repeats
  while held, and `Ctrl` is released when the fist opens or after
  `ctrl_tab_session_timeout_s`. The session takes the gesture lock, so nothing else can start
  mid-switch.
- **Taps inside the session send bare `Tab`/`Shift+Tab`**, deliberately not `Ctrl+Tab`: a tap that
  pressed Ctrl itself would lift the modifier on release and end the session after one tab.

### The collision this created, and the rule for it

A right fist is *already* the close-flick that minimises a window. Which hand holds the modifier is
the only thing that distinguishes Ctrl-Tab from Alt-Tab, so the right fist has to mean "hold Ctrl" —
and that clashes head-on with minimise. `ctrl_tab_flick_guard` decides:

    two_hands  (default)  a right fist never minimises while the left hand is in frame
    left_pinch            only while the left hand is actually pinching
    off                   the flick always wins; Ctrl-Tab then needs the pinch held first

The first implementation of this guard fired on a single hand, because `_select_hands` returns the
same hand for both roles when only one hand is in frame — which silently broke ordinary minimise.
`_other_hand()` now excludes exactly that case, and `test_close_flick_minimises` and
`test_landmarks_to_minimise_end_to_end` catch it.

### Changed

- **`swipe_action: none`.** The swipe is still detected and still tested, it just does nothing.
  `swipe_action=tab` restores the old binding.

### Verified

- 162 tests. The new gesture: Ctrl down on open, forward tap on the rising edge, backward tap with
  the middle finger, Ctrl released when the fist opens, and a left pinch with no session sending
  nothing at all. The flick guard: a right fist alone still minimises (both unit and landmark-level),
  while a right fist with the left hand in frame holds Ctrl instead.
- The four swipe tests now opt in with `swipe_action="tab"`, plus a new test that the default is
  silent — so the replaced behaviour is verified rather than deleted.

---

## [1.5.0] — dictate into the field you are looking at

The dictation gesture now puts the caret where you are looking before it starts listening.

### Added

- **`ptt_focus_mode`** (`auto` | `uia` | `always` | `off`). When the shaka arms, the gaze point is hit
  tested; if it is somewhere you can type, Kinesis inserts a click on that point and a
  `ptt_focus_settle_ms` pause (40 ms) ahead of the `Ctrl+Space` the gesture already emitted. The
  pointer is parked with the same warp the tab-click uses, so it is on the field 1 ms before the click.
- **`kinesis/focus_target.py`** — hit testing with three sources of trust: UI Automation (definitive
  for native controls), the window class under the point (Edit/RichEdit/Scintilla), and an explicit
  "inconclusive" answer. `decide()` is pure and takes a plain `Hit`, so the whole policy is testable
  without a desktop.
- **`tools/check_focus.py`** — prints what the hit test sees, and what it would do, at the centre of
  every monitor and over the taskbar. Nothing is clicked or moved.

### What the live probing found

- **Chromium does not expose editable nodes to UI Automation here.** Even minutes after a UIA client
  exists, a browser window yields `Pane`/`Image` nodes and no `Document` or `Edit`. So the browser case
  is *inconclusive*, and in `auto` an inconclusive web page counts as somewhere you meant to type —
  that is what makes the feature work at all in Opera. `tools/check_focus.py` over Opera returned
  `controlType=50006` (Image) at a point on the page, which the policy correctly refuses; over
  Discord's `View` pane it clicks, which is right, because that is where you were looking.
- **Native controls are exact**: a real Win32 EDIT reports `controlType 50004` and
  `GetFocusedElement` agrees, which is the signal `uia` mode trusts.
- **The desktop and the taskbar read as bare panes**, so they needed explicit exclusions or `auto`
  would have clicked them. Caught by running the live check, not by a unit test.
- **The cursor shape is not usable as a text-box detector across processes** — `GetCursorInfo`
  returned handles that do not correspond to `LoadCursor` handles in another process, so that
  approach was dropped rather than shipped half-working.

### Changed

- **`gaze_focus_dwell_s` 0.7 → 0.15 s.** Looking at a window focuses it far more responsively.

### Verified

- 158 tests: the policy across every mode and control type, buttons/links/images never clicked, an
  already-focused field left alone (clicking it would move the caret), read-only fields refused, the
  click + pause inserted immediately before the hotkey and nothing else touched, other hotkeys ignored,
  uncalibrated gaze ignored, and the pause honouring its milliseconds while staying out of a dry run.
- Live: UIA client ready in ~100 ms, hit tests 4–54 ms, desktop/taskbar refused, Opera page refused at
  an image and clicked at a bare pane.

---

## [1.4.0] — click the tab you are looking at

Gaze stops being only a *target* and starts being an *input*: a click made while looking at a browser tab lands on that tab.

### Added

- **Gaze-assisted tab switching.** The click gesture now carries an optional warp point. When a click
  is committed and the gaze point is inside a browser's tab strip, Kinesis moves the pointer to the
  gaze point exactly, waits `gaze_click_warp_delay_ms` (1 ms by default), and only then sends the
  click. The pointer is already hand-driven at 15 fps, so it snaps back on the next frame on its own.
- **`kinesis/tabs.py`** — the tab strip as geometry. Browser identity comes from the window class
  (`Chrome_WidgetWin_1`, `MozillaWindowClass`) *and* the process image name, because Electron apps
  share the Chromium class: the exe decides. The strip is the top of the client area, with the first
  6 logical px excluded because Chromium spends them on the window drag region — and heights scale
  with the window's DPI, so a 150% display gets a 60 px band.
- `tools/check_tabs.py` — reports the strip detection against whatever browsers are actually open.

### Fixed (found by running it against a real browser)

- Opera enumerates **hidden helper windows with class `IME` and a zero-size client rect**. They share
  the browser's executable, so they matched, and their "tab strip" would have been the top of the
  screen. `tab_strip_band` now rejects degenerate client rects.
- `WindowInfo` gained `class_name` and `exe`, and `class_name()`/`process_exe()`/`client_rect_on_screen()`/
  `window_dpi()` are all prototype-declared — the same 64-bit-handle trap that broke the desktop
  overlay (`int too long to convert`) applies to every new Win32 call.

### Verified

- 143 tests: the strip band at 100% and 150% DPI, drag-region and page-content rejection, browser
  vs Electron identification, the warp → 1 ms → click ordering, a configurable delay, that a click
  without a warp never moves the pointer, and that uncalibrated gaze is ignored entirely.
- Against the live desktop: `opera.exe` / `Chrome_WidgetWin_1`, client `(0, 0, 1920, 1000)`, strip
  `(6.0, 46.0)`, a point at the middle of the strip classified as a tab and 60 px lower as content.

---

## [1.3.1] — compact README, detail in the changelog

### Changed

- README **566 lines → 242** (49 KB → 14 KB). The pitch stays on top, then features as a table, the
  gesture reference, quick start, install, calibration, and short dense sections. Nothing was
  dropped: prose became bullets and the reasoning moved into this file.
- Fixed the licence badge and credits link, which pointed at `/blob/main/` on a `master` repo.

### Added

- This changelog, and the rule behind it: compact README, full detail here and in the release notes.

---

## [1.3.0] — one gesture at a time, and an overlay on the desktop

Four problems reported from live use, four fixes.

### Fixed — Alt-Tab

The root cause was in hand selection, not the switcher. When the right hand left the frame, the left
fist was promoted to the **cursor** hand (`by_label.get("Right") or by_label.get("Left")`), and its
fist pose then fired the close-flick at whatever had focus. So holding the Alt-Tab modifier
minimised windows. The modifier is now never the cursor hand.

The session itself was also hardened:

- The whole session latches: it owns the engine while it is open, so nothing else can start.
- `Tab` fires on the **rising edge** of the pinch as well as on repeat — the first pinch used to be
  swallowed by the repeat timer, making the switcher feel dead for a third of a second.
- A 30 s session timeout force-releases `Alt`, and opening the left fist commits the switch.
- While `Alt` is held, a right-hand pinch is a `Tab` and can never also be a click.

### Fixed — scrolling

- The ring pinch must hold for `scroll_confirm_frames` (2) before scrolling starts, so a transient
  pinch no longer begins one.
- A pinch that flickers off for a frame or two no longer ends a scroll in progress
  (`scroll_release_frames`, 3). A hand is never perfectly still, and this was most of what made
  scrolling feel broken.
- The hand position is EMA-smoothed on the scroll axis (`scroll_smooth`), and the jitter floor is
  configurable (`scroll_deadband_px`).

### Fixed — gestures reading each other

Reported as "the gesture tracker keeps grabbing gestures from other things". A gesture now **takes
the hand**: nothing else may start until the hand opens again (4+ fingers), leaves the frame, or a
6 s safety timeout fires, so a pose can never wedge the engine.

Letting go of a scroll sweeps the hand up through the open-hand pose with lateral motion — exactly
what the tab swipe looks for. The swipe cooldown now restarts when the lock is released, so that
motion cannot become a swipe.

Two deliberate exceptions, because the alternatives are worse:

- A **fist** ends a click window immediately. Closing a hand into a fist sweeps through a pinch, and
  that sweep has to be able to reach the minimise.
- A **click** resolves on pinch release rather than a full open, or double-click could never work.

### Fixed — eye tracking and the focused window

Two things were true at once: the gaze-to-window path works but needs a calibration that had not been
run, and Kinesis never actually *focused* anything from gaze. Both are addressed.

- **Gaze now focuses.** Dwell on a window for `gaze_focus_dwell_s` (0.7 s) and it comes to the front,
  so typing, scrolling and the keyboard land where you are looking. It skips windows that already
  have focus, has a cooldown (`gaze_focus_cooldown_s`) so it cannot thrash, ignores full-screen
  windows by default, and never focuses its own windows.
- An uncalibrated gaze model is announced loudly at startup, with the fix in the message, instead of
  quietly falling back.

### Added — the overlay on your actual desktop

`kinesis/desktop_overlay.py`: transparent, click-through, always on top, one layered window per
monitor, toggled with INSERT (`desktop_overlay_hotkey`).

- Real per-pixel transparency: `WS_EX_LAYERED` + `UpdateLayeredWindow` with a premultiplied BGRA
  bitmap, so trails and glow blend with whatever is behind them instead of sitting in a black box.
- `WS_EX_TRANSPARENT` means every click passes through; `WS_EX_NOACTIVATE` + `WS_EX_TOOLWINDOW` mean
  it never takes focus.
- Each monitor has its own trail buffer, and the marker is stamped only on the screen your gaze is
  actually on; the others keep fading. `advance(gaze, present=False)` decays without stamping.
- Renders at `desktop_overlay_scale` (6) and redraws `desktop_overlay_panels_per_tick` (2) monitors
  per tick; the rest keep decaying, so a round-robin costs nothing visible. ~10 ms/tick measured.

### Fixed — a ctypes trap worth recording

Unprototyped functions pass Python ints as C ints, so a 64-bit window or GDI handle above 2^31 raises
`OverflowError: int too long to convert`. It worked in isolation and failed inside the loop purely
because handles had grown. `winapi.py` now declares argtypes and restypes for every GDI and layered-
window call it makes.

### Verified

- **134 tests** — 16 new: the lock holding until open, the fist escape, the timeout, the release
  sweep, the Alt-Tab session (rising-edge Tab, no clicks, ends on fist open, times out), the
  left-fist guard, and gaze-dwell focus (dwell, restart on change, cooldown, already-focused, target
  lost).
- `tools/verify_overlay.py` — **12/12** against the real compositor: exact extended window styles,
  full-screen coverage, premultiplied colour (never brighter than its alpha), a mostly transparent
  DIB, and a trail present rather than a single dot.
- `tools/verify_actions.py` — 16/16 live OS checks still pass.

## [1.2.0] — the overlay grows up

The gaze layer was a red dot and a crosshair in OpenCV's Hershey fonts. It is now a renderer.

### Added

**Overlay styles.** Everything is drawn into one float buffer: faded each frame, stamped with this
frame's contribution, blurred into itself for bloom, composited additively. Trails, phosphor
persistence and glow all fall out of that single trick.

| Style | Look |
| --- | --- |
| `pointer` | Crisp reticle, nothing persists — the functional choice |
| `comet` | Tapered tail behind the reticle (default) |
| `path` | Last ~48 samples as a tapering polyline — the eye-tracking gaze-plot look |
| `heatmap` | Accumulated dwell through a colormap |
| `heatmap_comet` | Both |
| `none` | Hands only |

**Five themes** (`ember`, `cyan`, `violet`, `lime`, `ice`) recolouring reticle, tail and colormap
together. **Real typography**: telemetry in Bahnschrift through Pillow, per-string tiles cached, soft
glow behind the text. **`--vcam-demo`** drives a synthetic gaze path into the virtual camera so the
styles can be watched without a face in frame.

### Fixed

- **The overlay cost 59–140 ms per composed 1080p frame.** Full-resolution float layers meant ~74 MB
  of temporaries per frame. Rendering at 1/3 and upscaling once on composite brings it to 14–25 ms.
  A test pins the reduction so it cannot regress.
- State mutation moved out of `draw()`: the reticle is stamped in `advance()`, so a frame drawn twice
  cannot double-expose.
- `cv2.COLORMAP_ICE` does not exist — an invented theme value, now caught by a test that validates
  every theme's colormap.

### Verified

**117 tests** (19 for the renderer). `tools/bench_overlay.py`: pointer/comet/path 14–15 ms, heatmap
21 ms, heatmap_comet 25 ms, none 5.5 ms — all inside a 33 ms frame, on the virtual camera's own
sender thread, so hand latency is untouched. `--vcam-demo` sent 211 frames to the real device.

### Also

`plan.md` gained the research pass: appearance-based gaze models that output *angles* and would slot
straight into the desk geometry (L2CS-Net, MobileGaze, MobGazeNet), a held-key watchdog, a MediaPipe
GestureRecognizer cross-check for the destructive gestures, hand-distance-aware pointing, and
packaging — rated by impact per unit of risk.

## [1.1.1] — the gaze wizard actually runs

Two latent crashes in 1.1.0's gaze calibration, found by running the wizard rather than trusting the
test suite.

### Fixed

- **`calibrate_gaze.py` read `monitor.index`, which does not exist.** `Monitor` is a plain dataclass;
  the position in the left-to-right list is the index. The wizard crashed the moment the first dot
  appeared.
- **`monitor_at` was called with its arguments in the wrong order** — `(x, y, monitors)`, not
  `(monitors, x, y)`. This sat behind the line above, so it would have crashed on the first
  *successful* calibration: right after training, in the per-screen hit-rate report.

### Why they got through

The geometry tests built monitors as `SimpleNamespace` fakes, so a wrong attribute name was never
exercised against the real class. They now use the real `Monitor`, and
`tests/test_source_consistency.py` exercises the helpers that consume it (`monitor_at`,
`physical_layout`, `build_geometry`) so this class of slip fails in tests rather than in a wizard.

### Verified

**95 tests.** The wizard was run end to end with a single point: it opened the camera, placed the dot
on the right screen, detected a face, measured the seat distance (56 cm) and correctly refused to
train on one sample instead of writing a junk model.

## [1.1.0] — desk geometry

A webcam sees you, not your desk. This release teaches Kinesis the desk and feeds it into gaze.

### Added

**`geometry.py`** — a physical model of the desk:

- **Which camera, and how wide it sees.** Webcams do not expose their field of view, so device names
  are matched against a table of known models (C920/C922 78°, Brio 90°, C270 60°, …) and converted
  from diagonal to horizontal for the actual capture aspect — 78° diagonal on a 4:3 frame is 66°
  across. Unknown cameras fall back to a documented 68° and say so. `camera_name` and
  `camera_fov_deg` override both.
- **Which monitors, and how big they really are.** Each screen's EDID is read from the registry,
  keyed by the vendor+product code Windows reports per display: physical size from the basic display
  parameters, falling back to the preferred timing descriptor, falling back to an estimate from
  resolution. That gives true diagonal inches and PPI, and the real model name where Windows only
  says "Generic PnP Monitor".
- **How they sit.** Screens are placed as a physical row in millimetres with a `bezel_mm` allowance
  between active areas; pixel-derived vertical offsets are reported as alignment.
- **How far away you are.** Monocular, from the apparent width of your eyes: outer eye corners span
  ~90 mm and the focal length in pixels comes from the camera's FoV, so
  `distance = focal × 90 mm ÷ span`. Roughly ±15%; `eye_corner_mm` (or `scale_reference: ipd` with
  `ipd_mm`) tightens it. It costs nothing — the landmarks come from the face detection EyeTrax
  already runs, tapped off its landmarker rather than a second model.

**`run.bat --desk-report`** prints the lot, including angular span and per-screen separation from
your seat.

**Calibration is planned by geometry.** Screens are sampled by the angle they subtend from your seat
— wide or off-axis screens get 9 points, narrow ones 5 — because those are the screens a linear model
gets wrong. Per-screen hit rates are reported, not just one overall score.

**Seat distance is measured during calibration, saved next to the model, and monitored at runtime.**
Drift past `distance_warn_fraction` (25%) produces "seat moved 34% since calibration (600 → 804 mm)
— recalibrate gaze". A changed monitor arrangement is reported at startup too.

**Warnings that explain failures:** under 8° between adjacent screen centres is reported as too tight
for reliable gaze discrimination from that seat.

**Pointing triangulates.** The hand's pointing angle is matched against each screen's true angular
span instead of a fixed ±70° guess.

### From the desk this was built on

Four screens, 276 cm of active area: a 42" TV, two Lenovo L27i-30 and a Kogan KAMN27F18WA, all
1920×1080, spanning −69° to +52° from a 70 cm seat.

### Verified

**93 tests** (20 new): EDID parsing against a hand-built block laid out to the real spec,
FoV-from-diagonal across aspect ratios, the distance round trip, physical layout with bezels,
vertical misalignment, PPI, angular spans, screen selection by angle, seat-drift gating, and the
landmark tap turning eye corners into a distance. The desk model was checked against the real
hardware (C920 focal length 494 px at 640×480).

## [1.0.1] — dictation with Handy

### Added

- **Handy support documented**, because it is the gesture that matters most in practice. The
  thumb-and-pinky pose holds `Ctrl+Space` while you keep the pose, and `Ctrl+Space` is the Windows
  default *transcribe* binding in [Handy](https://github.com/cjpais/Handy) — free, open source,
  fully offline speech-to-text (Whisper or Parakeet, on your own machine). Hold, talk, release, and
  the text lands in the focused field. No setup on either side.
- **`ptt_keys` config key** — the dictation hotkey is no longer hardcoded, so a remap in Handy can be
  mirrored: `--tune ptt_keys=ctrl,alt,d --save-config`.

### Fixed

- **The safety release path no longer guesses key names.** It released a hardcoded `ctrl+space`;
  with a remapped hotkey that would have left the *real* keys stuck down on exit. The runner records
  what it actually pressed and releases exactly that, in reverse press order.

### Verified

73 tests (3 new), 16/16 live OS checks, and every outbound link and in-page anchor resolving.

## [1.0.0] — first release

Control Windows with your hands through one webcam, built for desks with several monitors.

### Added

- **11 hand gestures** — left click, right click, double click, drag, adaptive scroll, browser tabs,
  Alt-Tab, minimise, maximise, fullscreen and push-to-talk.
- **Multi-monitor support** — built with several monitors in mind from the first commit. Your eyes
  track what you are focusing on; your hands tell the PC what to do. Gestures carry the window *and*
  the monitor they apply to.
- **Gaze-targeted windows** — eye tracking resolves the topmost window under the gaze point, and that
  window becomes the target of the next gesture.
- **~46 ms end-to-end latency**, cursor snapping to the hand rather than animating there.
- **A genuinely smooth cursor** — a One-Euro filter removes landmark jitter without paying for it in
  lag: hard smoothing at rest, near-raw tracking during fast motion.
- **Virtual camera** — the webcam feed goes back out with the tracking overlays drawn on it, so OBS,
  Discord, Zoom or Teams can use the camera while Kinesis is using it. Plus a chroma-keyable green
  overlay mode for compositing.
- **Gaze-driven scrolling** — hold your gaze at the top or bottom of the screen and it scrolls.

### Measured latency

| Metric | Measured |
| --- | --- |
| Camera capture | 15.0 fps (device ceiling at 640×480) |
| MediaPipe inference | 17–39 ms/frame at `model_complexity=0`, no dropped frames |
| **End-to-end (capture to decision)** | **45–48 ms average, 81–104 ms peak** |
| Main loop | 340–500 fps |

Snap-don't-animate cursor, a deadband so a still hand makes a still cursor, newest-frame-only capture
on its own thread, no pyautogui anywhere, and every blocking call (including virtual-camera frame
pacing) kept off the decision loop.

### The gesture conflicts this solves

Closing an open hand into a fist sweeps *through* a pinch, so a naive pinch-to-click fires a stray
click on the way to minimising. Here a pinch *arms* a click rather than firing one: it commits 120 ms
after release, a pinch held past 350 ms clicks while held, and a fist arriving inside that grace
window cancels it. Finger states come from joint angles rather than tip heights, so they survive hand
rotation. Pinch thresholds scale with hand size instead of using fixed pixels, which is what stopped
the original firing right-clicks nearly every frame.

### Verified

70 tests, 16/16 live checks (`tools/verify_actions.py` creates a real window and asserts the OS
changed state), and the virtual camera verified against the real device (45 frames synchronous plus
30 through the threaded publish path, 0 dropped).

[1.3.0]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.3.0
[1.2.0]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.2.0
[1.1.1]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.1.1
[1.1.0]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.1.0
[1.0.1]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.0.1
[1.0.0]: https://github.com/DrGekoz/Kinesis/releases/tag/v1.0.0
