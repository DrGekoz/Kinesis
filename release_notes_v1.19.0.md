# v1.19.0 — eye gaze and hand tracking, running at the same time

The report: *after calibration the app told me to run run.bat, eye-gaze said it had started, and
it still never measured my eyes.*

Four separate faults, all wearing the same disguise. Each one reported success while producing
nothing, which is why the symptom survived every layer of the app and why every status line
pointed somewhere else.

---

## 1. Hand tracking silently destroyed the eye-gaze model

**The real bug, and the reason the two could never work together.**

`mp.solutions.hands.Hands()` (MediaPipe's legacy *solutions* API, used for hands) permanently
breaks the *modern* `tasks` API's **file loader** in the same process. Every `FaceLandmarker`
created afterwards resolves an absolute path as if it were relative:

```
Unable to open file at F:\...\Kinesis\.venv\Lib\site-packages/C:/Users/josep/.cache/eyetrax/
mediapipe/face_landmarker.task, errno=22
```

A real, absolute path with the site-packages prefix glued in front of it. Measured, not inferred.
It survives:

| Attempted remedy | Result |
| --- | --- |
| `Hands.close()` | still broken |
| `os.chdir()` elsewhere and back | still broken |
| a new thread | still broken |
| POSIX path, native backslash path, `file://` URI, relative path, a copy beside the app | all still broken |
| **`BaseOptions(model_asset_buffer=<bytes>)`** | **works** |

`vendor/eyetrax/src/eyetrax/gaze.py` now reads the task file itself and hands MediaPipe the
**bytes**, so the landmarker never asks the broken loader to resolve anything. This is what makes
eye gaze and hand tracking able to share one process at all — the thing you asked for.

## 2. A unit test had destroyed the trained model

`tests/test_inapp_calibration.py` created its stub with the **relative** path `gaze_model.pkl`,
and its cleanup was conditional:

```python
if model_exists and not existed:
    model.unlink(missing_ok=True)
```

On a real, calibrated project the file *did* already exist, so the one-byte stub was written over
the trained model and never removed. `gaze_model.pkl` was **1 byte containing `x`** — which is why
`start()` reported `invalid load key, 'x'`.

The fixture now uses `tmp_path`, and `FakeGaze` takes the model path from the test instead of
hard-coding it. The training samples in `gaze_model.npz` had survived (222 samples, 15 dots), so
the model was recovered **without recalibrating** — see below.

## 3. `is_calibrated` was a bare `.exists()` — a lie that hid all of the above

A 1-byte file counts as existing. So the app printed `gaze: model gaze_model.pkl`, decided there
was nothing to calibrate, skipped the wizard, and then failed to load the model at startup.

`is_calibrated` now actually **unpickles** the file (memoised; cleared on retrain, on `stop()` and
by `set_model_path()`), and `start()` names the real problem:

```
gaze_model.pkl is not a usable gaze model (corrupt or not a pickle) - re-fit it with
tools\retrain_gaze_model.py, or recalibrate with calibrate_gaze.bat
```

A magic-byte check was tried first and rejected: a 4-byte truncated pickle
(`\x80\x04\x95\x01` — protocol and frame header, no model) passes any byte test and still raises
on unpickling.

## 4. A camera held by another program reported as open

VRChat was holding the C920. The device was healthy (`Get-PnpDevice -Class Camera` → `OK`) and
`VideoCapture.isOpened()` returned `True` with a plausible `640x480` — and then **every single
`read()` returned `(False, None)`**. ffmpeg, a completely separate stack, confirmed it:

```
Could not run graph (sometimes caused by a device already in use by other application)
```

A webcam is a single-client device, so this is expected behaviour, not a driver fault — but the
app treated it as success and ran happily with nothing to track. `CameraThread.verify_streaming()`
now reads real frames *before* the capture thread starts, and `TrackingEngine.start()` refuses
rather than running blind:

```
camera error: camera 0 (dshow) opened but delivered NO frames - another program is holding the
webcam. Close it (VRChat, Discord camera, Zoom, OBS, Teams, the Windows Camera app) and start
Kinesis again.
```

---

## The app now proves it works instead of assuming it

**`run.bat` measures eye gaze on the live camera at startup.** `gaze.start()` returning `True` only
ever meant "a model was loaded" — which was true in all four faults above. It now drives the real
pipeline for a few seconds and reports what it counted:

```
[gaze] measured on the live camera: 41 frames with a face, 12 valid points - eye tracking is on
```

If it cannot measure, it says which stage stopped, rather than leaving the hand quietly in charge.

**`[gaze] NOT controlling the pointer: <reason>`** is printed once when the eyes stop driving the
pointer, and once when they take it back. This matters because `cursor_fallback: hand` means a
gaze failure looks exactly like a gaze that is merely miscalibrated.

---

## New tools

| Tool | Answers |
| --- | --- |
| `tools/probe_live_loop.py` | **Is the main loop measuring?** Drives the real objects in the real order and prints per second: `gazeRuns`, `faces`, `miss`, `valid`, the point, the `cursor.move` count and the inference time — so you can see exactly which stage goes quiet. `--move-cursor` actually drives the pointer. |
| `tools/retrain_gaze_model.py` | **Is the model file broken?** Re-fits from the samples the wizard already saved to `gaze_model.npz` — no new calibration. Re-uses the alpha the wizard chose, backs up any existing model, verifies the result loads through the real `GazeEstimator`, and prints the same leave-one-dot-out score the wizard reports. |

The model recovered for this release reproduced the wizard's own numbers — held-out median
**366 px** against the **365.6 px** it had recorded — so the recovery is the same model, not a
different one.

---

## Tests

**376 passing, 1 skipped** (the live-camera stop/start test skips itself when the webcam is busy).
`tests/test_gaze_silent_failures.py` is new and pins all four faults, including the negative case
that a genuine pickle must still load, and a check that no test can name the project's real model
file by a relative path.

## Known limitation

`gaze_model.pkl` is gitignored per-machine state, so a fresh clone still needs one calibration
run. The 93% held-out monitor hit rate on a 4-screen desk is unchanged by this release — this
version makes the number *reachable*, not better.
