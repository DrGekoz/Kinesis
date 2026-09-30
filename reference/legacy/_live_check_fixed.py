"""Stubbed live check for mouse_fixed.py - runs real detection against the camera,
counts every action the fixed gesture logic WOULD fire (cursor never moves)."""
import sys, time
import cv2, mediapipe as mp

sys.path.insert(0, '.')
import mouse_fixed as mf

LOG = []
class Stub:
    def __getattr__(self, name):
        def _f(*a, **k):
            LOG.append((name,) + tuple(int(v) for v in a))
        return _f

mf.pyautogui = Stub()
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

cap = mf.init_webcam()
det = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.6,
                               min_tracking_confidence=0.6)
drawing = mp.solutions.drawing_utils
sw, sh = 1920, 1080
state = dict(pi=False, pm=False, pr=False, pi_prev=False, pm_prev=False,
             last_left=0.0, last_right=0.0, last_scroll=0.0, last_action=0.0,
             dragging=False, action='', dry=True)
pcx, pcy = sw / 2, sh / 2

frames = seen = 0
t0 = time.time()
while time.time() - t0 < SECONDS:
    ok, frame = cap.read()
    if not ok:
        break
    frame, rgb = mf.process_frame(frame)
    fh, fw, _ = frame.shape
    out = det.process(rgb)
    if out.multi_hand_landmarks:
        seen += 1
        lm = mf.draw_landmarks(frame, out.multi_hand_landmarks, drawing)
        coords = mf.get_landmark_coordinates(lm, fw, fh)
        mapped = mf.map_to_screen(coords, sw, sh, fw, fh)
        pcx, pcy = mf.move_cursor(mapped[8], pcx, pcy, sw, sh, mf.SMOOTHENING)
        mf.detect_gestures(mapped, state)
        if state['action']:
            LOG.append(('ACTION', state['action']))
    frames += 1
cap.release()

clicks = [x for x in LOG if x[0] != 'moveTo']
print(f'frames={frames} hands_frames={seen} fps={frames/(time.time()-t0):.1f}')
print(f'total_cursor_moves={sum(1 for x in LOG if x[0]=="moveTo")}')
print(f'non-move actions={len(clicks)}')
for a in clicks[:20]:
    print('  ', a)
