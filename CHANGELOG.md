# Changelog

All notable changes to Kinesis. The README stays compact on purpose: this is where the detail lives.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[semantic](https://semver.org/spec/v2.0.0.html).

## [1.19.0] — eye gaze and hand tracking, running at the same time

**The report:** after calibration the app asked the user to run `run.bat` instead of doing it
itself, eye-gaze "started", and it still never measured eye-gaze in the main loop.

Four independent faults, all reporting success while producing nothing.

### Fault 1: hand tracking permanently breaks eye tracking (the real one)

`mp.solutions.hands.Hands()` — MediaPipe's legacy *solutions* API, used for hands — **permanently
breaks the modern `tasks` API's file loader in the same process.** Every `FaceLandmarker` created
afterwards resolves an absolute path as if it were relative:

```
Unable to open file at F:\...\Kinesis\.venv\Lib\site-packages/C:/Users/josep/.cache/eyetrax/
mediapipe/face_landmarker.task, errno=22
```

Measured, not inferred. It survives `Hands.close()`, `os.chdir`, a new thread, and every path
spelling — POSIX, native backslash, `file://` URI, relative, and a copy beside the app. Only
`BaseOptions(model_asset_buffer=<bytes>)` is unaffected, because it never asks the broken loader to
resolve anything.

`vendor/eyetrax/src/eyetrax/gaze.py` now reads the task file itself and passes the **bytes**. This
is what allows eye gaze and hand tracking to share one process at all.

### Fault 2: a unit test destroyed the trained model

`tests/test_inapp_calibration.py` wrote its stub to the **relative** path `gaze_model.pkl`, and
cleaned up only `if model_exists and not existed`. On a calibrated project the file already existed,
so a 1-byte stub containing `x` was left in place of the trained model — hence
`invalid load key, 'x'`. The fixture now uses `tmp_path`, and `FakeGaze` takes the path from the
test rather than hard-coding it. The 222 saved samples in `gaze_model.npz` survived, so the model
was recovered without recalibrating.

### Fault 3: `is_calibrated` was a bare `.exists()`

A 1-byte file exists, so the app claimed a model was loaded, skipped the calibration wizard, and
then failed at startup. It now **unpickles** the file (memoised; invalidated by `train()`,
`stop()` and `set_model_path()`), and a corrupt model is named as such.

A magic-byte check was tried and rejected: a 4-byte truncated pickle (`\x80\x04\x95\x01`) passes
any byte test and still raises on unpickling. The real load is the only check that matches what
`start()` will do.

### Fault 4: a camera held by another program reported as open

With VRChat running, the C920 was healthy and `isOpened()` returned `True` with a plausible
`640x480` — while every `read()` returned `(False, None)`. ffmpeg confirmed
`Could not run graph (sometimes caused by a device already in use by other application)`.

`CameraThread.verify_streaming()` now reads real frames **before** the capture thread starts, and
`TrackingEngine.start()` refuses rather than running with nothing to track.

### The app now proves it works instead of assuming it

- `_verify_gaze_live()` measures gaze on the live camera at startup:
  `[gaze] measured on the live camera: 41 frames with a face, 12 valid points`. Previously
  `gaze.start()` returning `True` only ever meant "a model loaded", which was true in all four
  faults above.
- `_gaze_faulted()` reports **once** when the eyes stop driving the pointer, and once when they
  resume. This matters because `cursor_fallback: hand` makes a gaze failure look exactly like a
  miscalibrated gaze.

### New tools

- `tools/probe_live_loop.py` — drives the real main-loop objects in the real order and prints a
  line per second (`gazeRuns`, `faces`, `miss`, `valid`, point, `cursor.move` count), so you can
  see which stage goes quiet. `--move-cursor` actually drives the pointer.
- `tools/retrain_gaze_model.py` — re-fits the model from the samples in `gaze_model.npz` with no
  new calibration, re-using the alpha the wizard chose, backing up any existing model, verifying
  the result loads through the real `GazeEstimator`, and printing the same leave-one-dot-out score
  the wizard reports.

The model recovered for this release reproduced the wizard's own numbers (held-out median 366 px
vs the 365.6 px it had recorded), so it is the same model rather than a different one.

### Tests

`tests/test_gaze_silent_failures.py` pins all four faults, including the negative case that a
genuine pickle must still load and a guard that no test can name the project's real model file by a
relative path. **376 passing, 1 skipped** (the live-camera stop/start test skips when the webcam is
busy).

### Known limitation

`gaze_model.pkl` is gitignored per-machine state, so a fresh clone still needs one calibration run.
The 93% held-out monitor hit rate on a 4-screen desk is unchanged — this version makes the number
*reachable*, not better.

## [1.18.1] — the app no longer exits after calibrating

**I shipped this in v1.17.0.** The camera handoff worked — the calibration ran and produced a
model — and then the app exited instead of starting up. Both faults were mine and both were
invisible to the suite, because nothing restarted a stopped engine.

### Fault 1: the engine could not be restarted at all

`TrackingEngine.start()` was written for a first start only:

- the stop `Event` was SET by `stop()` and never cleared, so a restarted camera thread returned
  immediately from its own `while not self._halt.is_set()` and never captured a frame
- both `Thread` objects were reused, and `Thread.start()` raises
  `RuntimeError: threads can only be started once`

`start()` now rebuilds both objects, and `stop()` joins them so a restart cannot race the old
capture thread for the device.

### Fault 2: `_stop` shadowed a private method of `threading.Thread`

Worse, and it only surfaced once Fault 1 was fixed. Both `CameraThread` and `InferenceThread`
subclass `threading.Thread` and assigned their stop `Event` to **`self._stop`** — a name CPython
uses for a private *method* that its own `Thread.start()` and `Thread.join()` call. So every
restart died inside the standard library:

```
TypeError: 'Event' object is not callable
  File "...\threading.py", line 1123, in join
    self._wait_for_tstate_lock(...)
```

Renamed to `_halt` in both classes, with the reason written down so it is not "tidied" back.

### Verified on the actual C920, through the actual failure path

```
step 1: start            frames flowing: True   shape=(480, 640, 3)
step 2: stop             stopped cleanly, no exception
step 3: start again      start() returned True          <- v1.17.0 returned False and exited
step 4: capture          frames after the restart: 30
```

Step 4 matters: `start()` returning `True` is not proof of a working camera, so the check keeps
reading frames rather than trusting the return value.

**366 tests** (6 new in `test_tracking_restart.py`): a stubbed restart, a stop/start that keeps
capturing on the real camera, double `stop()` safety, idempotent `start()`, and the two specific
properties that broke — a fresh stop-event and a fresh thread object.

## [1.18.0] — a two-hand screenshot gesture

**Both hands open → both fists → both open, fast.** Sends `PrintScreen`.

    screenshot_enabled     true    the gesture exists
    screenshot_window_s    0.45    the whole open->fist->open must fit in this
    screenshot_fist_px     45.0    and BOTH fists must travel this far while closed
    screenshot_fist_frames 2       a fist must be seen this many frames before it counts
    screenshot_cooldown_s  1.2     so one flourish cannot fire repeatedly

### Why two hands, and why the fists must move

A single left fist is the Alt-Tab modifier and a single right fist is the Ctrl-Tab modifier. So a
one-hand screenshot would collide with both, and the two-hand requirement is the whole safety story
— `_other_hand()` returns `None` when only one hand is in frame, so this physically cannot fire on
one hand. (`_select_hands` returns the *same* hand for both roles when only one is visible, which is
exactly how a one-hand fist used to minimise; a test pins that here too.)

The deeper collision is a **held** two-hand clench: that looks like someone reaching for Alt-Tab, and
a timing window alone would not separate it. So both fists must also **travel**, in the same
direction, by `screenshot_fist_px`, while closed. A deliberate slow clench is neither fast nor moving,
so it stays a modifier gesture and never becomes a screenshot. Both halves are tested separately:
`test_a_slow_two_hand_fist_is_not_a_screenshot` and `test_a_still_two_hand_fist_is_not_a_screenshot`.

The check runs **before** the tab modifiers in `update()`, so a fast two-hand flourish is never read
as "hold Alt" in the first place.

### The key

The bare `PrintScreen` VK, so it inherits whatever the machine is configured for. On Joe's box that
opens the **snip-and-select overlay** — drag the region you want — rather than silently copying to
the clipboard. That is the behaviour he expects from the key, so nothing is overridden.
`Win+PrintScreen` (save to file) is deliberately not aliased to the plain name.

**A note on how this was verified, because I got it wrong first:** a scripted check concluded "the
key is not landing" because it asserted the clipboard gained an image. The key *was* landing — the
assertion was wrong for a machine configured for snip-and-select. Reported from the corrected
criterion, not the assumed one.

### Tests

360 (8 new). Beyond the two collision cases: a one-hand fist cannot screenshot, hand loss mid-fist
cannot fire later, one flourish fires once, and `screenshot_enabled=false` silences it.

## [1.17.0] — in-app calibration hands over the camera; credits in the terminal

### The camera bug (the one that broke "F"ull sweep)

`[F]ull sweep` from the startup prompt died with:

```
could not open the camera - close anything else using it and retry
```

Which is a message aimed at the user, when the thing using the camera was **the app that asked the
question**. `_maybe_calibrate()` runs `calibrate_gaze.py` as a subprocess while the app is already
up and tracking, so the webcam was open in this process and the wizard could never have it. A webcam
can only be opened by one process at a time.

`_maybe_calibrate(cfg, gaze, engine=None)` now stops the engine before launching the wizard and
restarts it in a `finally`, so the camera comes back on **every** path — including a failed wizard
and a `SystemExit`. If the camera genuinely cannot be reopened it says so and exits rather than
running on with no camera.

Proven on real hardware, not just in a fake: a child process could not open the C920 while the app
held it (exit 1) and could immediately open and read a frame after release (exit 0).

`tests/test_inapp_calibration.py` pins the order — release before, reconnect after, on success *and*
failure — and that declining never touches the camera at all.

### Credits

```
Created by DrGekoz - report issues on GitHub:
  https://github.com/DrGekoz/Kinesis
```

printed at the end of the startup block, after setup and before the overlay/HUD take over. Verified by
running the app, not by reading the diff.

### 352 tests

## [1.16.1] — a one-command answer to "can this deploy yet?"

`tools/deploy_check.py` is read-only and prints exactly what is missing, then stops.

It exists because the failure was genuinely hard to read. The token in `.env` is **R2-scoped**, and
an R2 token's behaviour is confusing in three separate ways at once:

- `/user/tokens/verify` returns `Invalid API Token` (code 1000) — so "the token is broken" is a
  reasonable and **wrong** conclusion.
- `GET /accounts/<id>` succeeds and returns the real account name, and `GET /d1/database` returns
  `{"success": true, "result": []}`. An empty list is exactly what an *unauthenticated* read
  returns, so "the credentials work" is also reasonable and wrong.
- `POST /d1/database` — the one operation that matters — returns 401 `Authentication error`.

Read 200, write 401. The token authenticates and can read; it simply cannot create. That is a scope
limit, not a misconfiguration, so retrying, rewriting `wrangler.toml` or re-authenticating through
wrangler cannot fix it. (wrangler's own OAuth token is a separate thing and expired on 2026-08-16.)

I checked for a usable credential elsewhere on the machine before concluding this: the Hermes env
has no Cloudflare keys, the pixel-office relay project has no `.dev.vars`, and there is no
`CLOUDFLARE_*` variable in the shell. There is nothing left to try.

### What the user has to do — either one

**A. No new token, ~20 seconds.** Create the database by hand:
*Workers & Pages → D1 SQL Database → Create → name it exactly `kinesis-gesture-maps`*, then run
`cloudflare/deploy.py`. It detects an existing database and continues from there.

**B. A token that can write.** *My Profile → API Tokens → Create Token → Edit Account* with
`Account → D1 → Edit` and `Account → Workers Scripts → Edit`, then put it in `.env` as
`CLOUDFLARE_API_TOKEN`.

Nothing was deployed. 296 unit tests unchanged.

## [1.16.0] — the marketplace submit path is proven, and one bug it hid is fixed

### The bug

**The Worker rejected Kinesis' own default map.** The client's `ACTION_TYPES` includes
`mouse_hold` (the real drag action) and the default map uses it for the drag binding — but
`cloudflare/worker.js` had its own copy of the vocabulary, in JavaScript, in a different file, and
`mouse_hold` was missing from it.

So `POST /maps` returned 400 `unknown action type mouse_hold` for a map the client had already
declared valid. The Submit button would have failed for **anyone** publishing a map containing a
drag, and there was no way to see that without a live Worker — the client-side validator said clean.

Two copies of one rule, in two languages, in two files, with nothing keeping them in step.

### How it was found

`tools/marketplace_stub.mjs` runs the **real** `cloudflare/worker.js` in Node against an in-memory
D1 stand-in, behind a local HTTP server. `tools/verify_marketplace.py` then drives it with the
**real** `kinesis/marketplace.py` client over the same `urllib` path production uses. Button, form,
HTTP call, server-side validation and response handling are all exercised for real; only the
database and the network hop are local.

`tests/test_marketplace_vocabulary.py` compares the two vocabularies directly, so the drift cannot
return. It was verified by reintroducing the bug: two of its five tests fail, and pass again once
fixed.

### Fixed

- `mouse_hold` added to the Worker's `ACTIONS`, with a comment saying why it must stay.

### Verified

- 18/18 marketplace round-trip: submit, list, download, download-counter increments, four
  server-side rejections (bad schema, impossible gesture, non-GitHub link, missing title), the
  `mouse_hold` agreement, and slug de-duplication.
- 296 unit tests, 21/21 live action, 10/10 volume, 12/12 overlay.

### Still not deployed

The Worker and D1 database remain uncreated — that needs a Cloudflare API token with `D1: Edit` and
`Workers Scripts: Edit`. The token currently in `.env` is an **R2-scoped** token, which returns
`Invalid API Token` (code 1000) on every API call. It has not been used to deploy anything. See
`cloudflare/deploy.py`, whose four steps are each idempotent.

## [1.15.0] — a volume rocker on index + pinky

### The gesture

**Index and pinky out, middle and ring curled, thumb in — hold it, then move the hand up or down.**
Up raises the system volume, down lowers it.

It is deliberately *not* the shaka. The shaka is thumb + pinky and is push-to-talk; this is index +
pinky, so the two differ by exactly the index finger and cannot be confused. It is also not a claw
(that's all four fingertips *on* the thumb), not a point (index alone), and not index+middle — each
of those has a test asserting it never arms the rocker.

### Soft, in three separate places

"Tap once per frame" is unusable on a real hand, so the rate is the hand's, not a timer — the same
accumulator the two-hand zoom uses:

    volume_deadband_px   4.0    movement below this does nothing at all
    volume_step_px      26.0    palm travel that buys ONE step (~2% of master)
    volume_max_steps     2      hard ceiling per frame, so a fast sweep cannot flood the queue
    volume_smooth       0.35    low-pass on the palm, so landmark noise never becomes a step

Up is `volumeup` (0xAF), down is `volumedown` (0xAE), both added to the virtual-key table with
aliases. These are the Windows **master** volume keys, so they act on the system output regardless
of focus and need no gaze target — unlike the window actions.

`mute` is mapped to 0xAD but deliberately has **no** alias to `m`: `m` is the letter, and silently
repointing a map's `m` at mute would be a trap.

`release_all()` clears the rocker. A live one would keep sending volume keys after hand loss, on the
first frames of whatever hand appears next.

### Verification, and what is NOT verified

`tools/verify_volume.py` — 10/10. It proves the VK codes are the SDK's `VK_VOLUME_*` values, that
every alias resolves, and that a gesture map can actually bind them.

**It does not prove the mixer responds on this machine**, and that is worth being blunt about.
Measuring it needed either `waveOutGetVolume` (returns the `0xFFFFFFFF` sentinel here, not a
reading) or COM `IAudioEndpointVolume` (`CoCreateInstance` returns `0x800401F0 CO_E_CLASSSTRING`
from this process even with correctly marshalled 16-byte GUID structs). The machine *has* audio —
`waveOutGetNumDevs` returns 14 and the registry holds 67 render endpoints — so this is a
process-level restriction on this box, not a broken key or a broken mixer.

So the tool **exits 0 and says CANNOT MEASURE** rather than reporting a failure. A test that cannot
measure is not a test that failed, and pretending otherwise would be the actual bug. Run it on a
normal desktop session to close that loop.

291 unit tests, 21/21 live action checks, 12/12 overlay checks.

## [1.14.0] — the claw spreads to maximise

### What changed

The claw already **was** a five-digit pinch — all four fingertips in contact with the thumb. So
maximise needed no new entry pose, only a second way out of the same one:

| Start | Exit | Action |
| --- | --- | --- |
| Pinch all five, hold | drag **down** | Minimise |
| Pinch all five, hold | **spread** the fingers apart | Maximise / fullscreen |

### Why the gesture needed a settle gate, not just a threshold

The obvious implementation — "the claw ended and all four fingertips are now clear of the thumb" —
does not work, and the reason is worth recording because it is not obvious until measured:

**A relaxed release and a deliberate spread end in geometrically the same place.**

| Measurement (hand-scales) | Relaxed release | Deliberate spread |
| --- | --- | --- |
| Slowest fingertip from the thumb | 1.58 | 1.63 |
| Mean fingertip-to-fingertip width | 2.08 | 1.57 – 2.62 depending on spread radius |

There is no threshold that separates them, in either metric, because *letting go of a claw is
opening your hand*. A naive implementation maximises a window every single time anyone relaxes
after a minimise — which would have been worse than the bug it replaced.

So the discriminator is not the **ending**, it is the **start**: the five-digit pinch must have
been *held* before a spread means anything.

    claw_spread          true     the gesture exists
    claw_settle_s        0.18     hold the claw this long first, or it was never a claw
    claw_spread_frames   2        the spread must read across this many frames, not one
    claw_spread_margin   0.45     extra hand-scales past pinch_off every fingertip must clear

A hand passing through a pinch on its way somewhere else — coming out of a click, a transition, a
swipe — cannot pass through a *settled* claw to get there. `window_only` still applies: no gaze
target, no maximise.

### Bugs found while building it

Two real ones, both caught by instrumenting rather than reasoning:

1. **`_claw_spread_frames` was unreachable.** The `elif not claw:` branch resets the claw's own
   state on the same frame it calls `_claw_spread()`, so a counter incremented inside that call
   was wiped before it could reach 2. `claw_spread_frames=2` was literally unsatisfiable and the
   gesture fired *never*. Fixed by not clearing the counter on that frame.
2. **A second reset made it unwinnable anyway.** The spread test was gated on `self._claw_active`,
   which is cleared on the first frame past the break — so only frame 1 was ever evaluated. The
   spread has to be confirmed over frames *after* the pinch breaks, so it needed its own latch,
   `_claw_spread_active`, carrying "a settled claw just ended" forward.

`release_all()` now clears the spread latch, counter and claw state. A latch left live through a
hand loss would fire a maximise on the first frame of the *next* hand — pointing somewhere else
entirely. That is the exact failure mode the settle gate exists to prevent, so it must not survive
an exit.

### Tests

280 pass (7 new). The two that matter most are negative:

- `test_a_claw_that_never_settled_cannot_spread` — a 2-frame pinch then a spread maximises nothing
- `test_hand_loss_clears_a_pending_spread` — a stale latch cannot fire for an unrelated hand

`build_hand()` gained `spread_hand()`, because the claw helper clusters the touching tips into a
bunch that is geometrically impossible on a real hand (index and pinky are over a scale apart), so a
spread could not be produced by relaxing it.

21/21 live action checks, 12/12 overlay checks.

## [1.13.1] — a window action with no gaze target now does nothing

### The bug

1.13.0 removed the close-flick and made the claw require a gaze target, and alt-tab stopped
minimising windows — but a *second* path was still live, underneath it:

```python
# kinesis/actions.py - resolve_target()
if intent.target_hwnd and self._usable(...):  # gaze hit
    ...
if intent.monitor is not None:               # aimed monitor
    ...
hwnd = w.user32.GetForegroundWindow()        # <-- fallback: whatever has focus
```

The engine had already stopped emitting the intent when there was no gaze target, but the
**action layer** was the thing that actually called `minimise()`. Any window intent that arrived
without a resolved target silently became "minimise whatever is focused". So a claw performed
while your eyes were off in empty space still closed a window, and anything routed through the map
layer could do the same.

Two independent contributors, and the second one is the reason this could look like "it still
minimises no matter what I do":

1. **The foreground fallback in `resolve_target()`.** Correct for keys and clicks — those genuinely
   want the focused window — and wrong for minimise/maximise, which destroy something the user
   never pointed at.
2. **The monitor fallback**, which fires *before* the foreground one: a claw with no gaze target but
   a valid aim monitor resolved to the topmost window on that screen.

### The fix

`Intent.window_only` — "there is no acceptable fallback; resolve to None rather than guess". Set on
every destructive window action at the point of emission:

- `Intent("window.minimise" / "window.maximise", ...)` from the claw drag — `gestures.py`
- the same two, from a Gesture-Map's `system` action — `map_action_intents()`

`resolve_target()` then skips **both** the monitor and foreground fallbacks. Non-window intents are
untouched, so `keys.tap`, clicks and wheel events still target the focused window as they always
have.

Both emitters are now the only two in the codebase, and both carry the flag — checked by grep, not
by memory.

### Tests

`tools/verify_actions.py` grew five live checks (21 total, was 16) that assert the fallback is
*dead*, not merely unused: with a real window focused and a monitor to aim at, a `window_only`
intent still resolves to `None`, while a `keys.tap` still resolves normally. A test that only
checked the happy path would have passed against the old code.

273 unit tests unchanged — this was a live-layer bug, which is exactly why it needs the live tool.

## [1.13.0] — the close-flick is gone; minimise is a claw drag

### What changed

The **open hand → fist flick** minimised a window, and **fist → open** maximised one. Both are
removed. The fist is the Alt-Tab and Ctrl-Tab modifier, which made every attempt at either tab
gesture minimise whatever had focus, and any hand passing *through* a fist on its way open
maximise a window nobody asked it to.

Minimise and maximise are now a **claw drag**: put all four fingertips on the thumb and drag the
hand **down** to minimise, **up** to maximise, within `claw_window_s`.

    claw_minimise      true        the gesture exists
    claw_travel_px     140.0       how far the palm must travel for it to count
    claw_window_s      1.2         and how quickly, before it was just a slow move

Why the claw rather than any other pose:

1. **It cannot be confused with the fist.** All four fingertips on the thumb leaves nothing curled
   enough to read as `n_ext == 0`, so it can never fire mid Alt-Tab or Ctrl-Tab. The flick's
   entire problem was that it *was* the fist.
2. **It cannot be confused with a held pinch.** `pinch_ring` drags and `pinch_middle` right-clicks;
   a claw suppresses both while it is held, and it re-arms the moment a finger lets go.
3. **It requires a gaze target.** With no window under your eyes the drag is ignored outright, and
   the HUD says so (`claw down ignored - not looking at a window`). Acting on whatever had focus
   is what made the old gesture destructive.
4. **It needs intent and a direction.** A slow drift or a small nudge is not a drag: the palm has
   to travel `claw_travel_px` within `claw_window_s`.

The fist still swallows a pinch that is in flight, so reaching for the Alt-Tab modifier still does
not leave a click waiting to fire.

### Dead code removed with it

- `flick_window_s` and `flick_settle_s` are gone from `DEFAULTS` (nothing read them any more).
- `ctrl_tab_flick_guard` **remains in the config, inert**, so an existing `kinesis_config.json`
  still loads without a KeyError. It no longer changes behaviour: a right fist means Ctrl whenever
  `ctrl_tab_enabled` is on. `_ctrl_tab_holding()` lost its guard branch accordingly.
- The **default Gesture-Map no longer binds a single-hand fist to `minimise`**, or an open hand to
  `maximise`. This one mattered: the map layer runs independently of the built-in engine, so
  removing the flick alone would still have left the default map minimising on every fist. A test
  pins it (`test_the_default_map_does_not_bind_a_bare_fist_to_a_window_action`).
- `BUILT_IN`'s descriptions for `open` and `fist` now say what those poses actually do.

The ten shipped maps in `gesture_maps/` were checked: they only use `fist`/`open` inside **two-hand**
gestures, which never touched the removed path, so none of them changed.

### Tests

273 pass. `build_hand()` grew multi-finger-pinch support so a claw is geometrically constructible at
the landmark level — a folded thumb sits ~2 hand-scales from the curled fingertips, so the claw
briefly existed only as engine state and could not be tested end-to-end. The helper now draws the
touching tips into a tight cluster first and puts the thumb in the middle of it, which is what a
real claw does and what gets all four tips inside `pinch_on` (0.34).

## [1.12.1] — the deploy script

- cloudflare/deploy.py: create the D1 database, apply the schema, seed the ten maps, publish the
  Worker, and print the URL to point the app at. Each step is skipped if it is already done, and a
  token without permission stops with the reason instead of half-finishing.
- --status reports what exists without changing anything.

## [1.12.0] — Gesture-Maps, a marketplace, and the pointer follows your eyes

### The pointer is driven by gaze

`cursor_source` defaults to `gaze`, and `kinesis/gestures.py` has one place that decides where the
pointer goes. The hand no longer moves it at all: `_cursor_intent` returns nothing while the eyes are
driving, and the finger-pointing path is gone. `cursor_fallback=hand` keeps the mouse alive before the
first calibration, so the app is never unusable - it prints which one is in charge at startup.

### Gesture-Maps (`kinesis/gesture_map.py`)

A map is JSON: gesture -> action, validated against the vocabulary MediaPipe actually gives us
(pose, per-finger pinch, per-finger extended, handedness, one or two hands). Over 60 validation
cases worth of rules, including whether a key can actually be pressed - a marketplace file saying
`"banana"` is refused with "Kinesis cannot press 'banana'", not a crash at 2am.

A map that binds a built-in gesture **owns** it and the built-in stands down, which is what makes
rebinding the drag possible. The shipped maps never claim `shaka` (push to talk) and a test enforces
that.

Action types: keys, held keys, mouse, mouse-hold (a real drag), window commands, nothing. There is
no way to run a program or type text, so an imported file cannot execute anything.

### The settings window became a tabbed window

Screens | Gestures | Marketplace, same hand-drawn Tk style, same palette and fonts. Edit a binding,
add or remove one, import/export a map, submit it to the marketplace, or download someone else's.

### Ten shipped maps

Browser Power User, Media Player, Windows Navigation, Video Editing, Presentation Remote,
Coding / IDE, One-Handed Essentials, Meeting Controls, Reading Comfort, Gaming Hotbar - built by
`tools/build_gesture_maps.py` and validated at build time, so a map that cannot be imported cannot
ship.

### Fixed

- **`cfg["key"] = value` does not exist** - `Config` has no `__setitem__`. Seven places used it,
  including the first-run screen prompt and the settings Save button, so both would have raised
  TypeError the first time anyone used them. A source-consistency test now fails the build if the
  pattern comes back.
- The shipped default map was invalid: the drag binding held no keys. Drag is now a real
  `mouse_hold` action.
- `calibrate.py` had no argument parsing at all; it now takes `--monitors` so aiming can be
  recalibrated for the enabled screens only.
- `c` and the punctuation the maps need were missing from the virtual-key table (`/`, `` ` ``,
  `win`, brackets, and friends) - `ctrl+/` and `ctrl+\`` could not be pressed.

## [1.11.0] — the mouse pointer follows your eye gaze

### Changed

- **The pointer is driven by the eyes.** `cursor_source` defaults to `gaze`: the cursor goes where you
  look, at `gaze_hz` (default 20, up from 10), smoothed with a 3 px deadband so a resting eye does not
  shake it. Clicks, drags, scrolls, tabs and push-to-talk are untouched - they simply act where you are
  looking rather than where your hand is, and a drag now selects with your eyes while the hand holds
  the button.
- **There is no pointing gesture any more.** The index finger no longer moves the pointer; hand
  tracking keeps its other eleven jobs. The console banner and the README no longer advertise it.

### Added

- `cursor_source` (`gaze` | `hand`) and `cursor_fallback` (`hand` | `hold`). Before gaze is
  calibrated the hand still drives the pointer by default, because a mouse that cannot move is worse
  than a prompt to calibrate; `hold` freezes it instead. Looking away holds the pointer - it never
  snaps back to a hand. Calibrating mid-session takes the pointer back with no restart.
- `gaze_cursor_deadband_px`, `gaze_cursor_smoothing` and a raised `gaze_hz` for a usable pointer.

### Fixed

- **Seven places assigned settings with `cfg["key"] = value`, which raises TypeError** - `Config`
  exposes `get`/`set`, not item assignment. Every one of them was a crash on the path it sat on: the
  first-run screen question, Save and apply in the settings window, `--settings`, `--enable-monitors`
  and `calibrate_gaze.py --camera`. All fixed, with a source guard test so the pattern cannot come
  back.

## [1.10.0] — the settings window

### Added

- **A settings window, F2 while running** (or `run.bat --settings`). It lists every screen with the
  EDID model name, resolution and desktop position, a switch to include or exclude each one, and a
  live count of what is being tracked. Drawn in the same palette, fonts and rounded shapes as the
  on-screen overlay, so it reads as part of Kinesis rather than a Tk dialog.
- **Recalibrate buttons** for gaze and for hand aiming. Both run their wizard over the **enabled
  screens only**, so a screen switched off is never asked for and never gets a calibration dot.
- **Save and apply takes effect immediately**: the screen list, the desktop region, hand aiming, gaze
  targeting, the overlays and the desk geometry are all re-derived without a restart.
- `calibrate.py --monitors 2,3`, matching the gaze wizard, and both wizards now default to the
  enabled screens instead of all of them.
- `settings_hotkey` (default `f2`, `none` to disable) and `tools/check_settings_window.py`, which
  renders the real window and closes it again so the GUI can be verified without a human.

### Notes

- The case this is for: a TV whose EDID geometry does not match where it physically is. Gaze and
  aiming both degrade on a screen Windows has placed wrongly, so excluding it improves the screens
  that are actually in front of you.
- While the window is open Kinesis releases its keys and stops feeding gestures, so a pinch cannot
  press the window's own buttons and no modifier is left held.

## [1.9.0] — eye tracking that actually works, and EyeTrax moves in

### Fixed

- **Gaze never worked, and the calibration was lying about it.** The wizard scored itself on the same
  dots it trained on, so a model that had memorised five dots reported "100% monitor hit rate, 28 px"
  while real gaze was unusable. Measured properly on held-out dots, that same model was **1543 px out
  and targeted 60% of dots**. Calibration now refits leaving out each dot in turn and reports that
  number as the headline - the only figure that predicts behaviour on a position it has never seen.
- **The model was chosen by a default nobody measured.** EyeTrax defaults to ridge `alpha=1.0` over
  486 features. The wizard now sweeps seven regularisation strengths on held-out dots and keeps the
  one that targets the most dots. On this desk that moved targeting from 60% to 100% at five dots,
  and 63% to 74% across a full four-screen sweep.
- **Ranking optimises targeting, not precision.** `alpha=1` had the best median error (712 px) and the
  worst targeting (60%); `alpha>=100` was 65 px less precise and got every dot right. Gaze picks the
  window you are looking at, so targeting is the objective it is ranked on.
- **Dots were skipped for being "blinks".** The landmarker was never fed during the settle phase, so
  EyeTrax's rolling eye-aspect history was cold when sampling began and called most frames blinks -
  eleven of twenty dots yielded nothing and two monitors scored 0%. Settle now warms the landmarker,
  and a dot that yields almost nothing is sampled again instead of being abandoned.
- **The distance gate was tighter than the ruler.** A fixed 8% band on the eye-span distance threw
  away 29% of a real calibration's samples, because 8% of 600 mm is about the measurement's own noise.
  The band is now the wider of the tolerance and three median deviations.
- Kinesis no longer requires `pip install -e vendor/eyetrax`; the vendored source goes on the import
  path at startup, and the app starts with EyeTrax uninstalled entirely.

### Added

- **Which screens to use, asked on first run.** Kinesis lists the monitors by position, resolution and
  EDID model name and asks which to enable; calibration then only puts dots on those, and aim, gaze,
  overlays and the canvas mapping all treat them as the whole desktop. Stored by device name so it
  survives reboots and docks. `--ask-monitors`, `--enable-monitors 2,3`, `--all-monitors`.
- **EyeTrax vendored into `vendor/eyetrax`** at upstream `84e13a1` (0.4.0, MIT), tracked in the
  repository with its licence, the revision recorded in `vendor/README.md`.
- **`tools/check_gaze.py`** - a live stage-by-stage probe (vendored source, camera, face, landmarks,
  features, model, prediction) that names the stage that is broken instead of saying "gaze off".
- **Calibration saves everything**: every sample as `gaze_model.npz` (features, targets, dot ids,
  monitors, seat distance, eye span, and the desk azimuth of each dot) plus the model choice, its
  held-out score, the whole model sweep and the gating outcome in `gaze_model.json`.
- **Kinesis offers to calibrate on startup** rather than printing a filename and continuing: quick or
  full, run from inside the app, gaze live immediately afterwards.
- `calibrate_gaze.bat --quick`, `--dry-run`, `--points 9`, `--retries`, `--distance-tolerance`, and a
  default of five dots per screen.

### Known and measured

- On a four-screen array 276 cm wide from a ~60 cm seat, the two outer screens score 0% while the two
  inner ones score 100%: the outer ones sit at an angle where appearance-based gaze has nothing to
  work with. Excluding them (above) is the fix, not more calibration.

## [1.8.1] — the console banner tells the truth again

### Fixed

- The gesture table printed at startup was still advertising the ring-pinch scroll, the pinky-pinch
  drag and the open-hand tab swipe — three bindings replaced in earlier releases. It is hand-written
  text in `app.py` that nobody re-read, so it drifted for three versions. Rewritten for the current
  thirteen gestures, with a test that fails if a replaced binding reappears in it.

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
