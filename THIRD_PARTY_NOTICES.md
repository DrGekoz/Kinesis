# Third-party notices

Kinesis is MIT licensed (see [LICENSE](LICENSE)). It depends on other projects that carry their own
licences. None of them are vendored into this repository; they are installed as dependencies, and
nothing from any of them applies input to your machine — every action Kinesis takes is its own,
through `SendInput` and `SetCursorPos`.

| Project | Licence | How it is used |
| --- | --- | --- |
| [Virtual-Mouse](https://github.com/whitehatboy005/Virtual-Mouse) (whitehatboy005) | MIT | The starting point. Cloned into `vendor/` for reference; not required at runtime. |
| [EyeTrax](https://github.com/ck-zhang/eyetrax) (ck-zhang) | MIT | Gaze estimation. Installed as a dependency (`pip install --no-deps -e vendor/eyetrax`). |
| [awesome-hand-pose-estimation](https://github.com/xinghaochen/awesome-hand-pose-estimation) (Xinghao Chen) | no licence file upstream | Reference material only. Cloned into `vendor/`; no code used. |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) (Google) | Apache-2.0 | Hand and face landmarks. |
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) (Maik Riechert) | **GPL-2.0** | Virtual camera output. This is why it is an installed dependency rather than bundled code. |
| [OBS Studio](https://github.com/obsproject/obs-studio) (OBS Project) | **GPL-2.0** | Provides the "OBS Virtual Camera" DirectShow driver that `pyvirtualcam` writes to. |
| [OpenCV](https://github.com/opencv/opencv) (OpenCV Foundation) | Apache-2.0 | Capture, drawing and the preview window. |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause | Numerics. |
| [scikit-learn](https://github.com/scikit-learn/scikit-learn) | BSD-3-Clause | The gaze model's regression, under EyeTrax. |
| [screeninfo](https://github.com/rr-/screeninfo) (Marwin Baumann) | MIT | Monitor geometry, under EyeTrax. |

`pip` metadata for the versions this project was developed against:

```
mediapipe            0.10.20   Apache 2.0
pyvirtualcam         0.15.0    GNU General Public License v2 (GPLv2)
opencv-contrib-python 4.10.0.84 Apache 2.0
numpy                1.26.4    BSD
eyetrax              0.4.0     MIT
screeninfo           0.8.1     MIT
```
