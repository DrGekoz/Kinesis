"""Live camera check for Virtual-Mouse: proves detection works on this machine.
Runs the same pipeline as mouse.py but STUBS pyautogui so nothing moves,
and reports what actions WOULD have fired."""
import time, sys
import cv2
import mediapipe as mp

sys.path.insert(0, '.')
import mouse as vm  # noqa: E402

# --- stub pyautogui so the real gesture logic runs without touching the cursor
class Stub:
    def __init__(self):
        self.log = []
    def moveTo(self, x, y): self.log.append(('move', int(x), int(y)))
    def click(self): self.log.append(('left_click',))
    def doubleClick(self): self.log.append(('double_click',))
    def rightClick(self): self.log.append(('right_click',))
    def mouseDown(self): self.log.append(('drag_start',))
    def mouseUp(self): self.log.append(('drag_end',))
    def scroll(self, n): self.log.append(('scroll', n))
    def sleep(self, n): pass

stub = Stub()
vm.pyautogui = stub

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0

cap = vm.init_webcam()
detector = mp.solutions.hands.Hands(min_detection_confidence=0.5, min_tracking_confidence=0.5)
drawing = mp.solutions.drawing_utils
sw, sh = 1920, 1080
sm, plocx, plocy = 7, 0, 0
click_time, cthr, sflag, dragging = 0, 0.3, False, False

frames = hands_seen = 0
t0 = time.time()
best = None
while time.time() - t0 < SECONDS:
    ret, frame = cap.read()
    if not ret:
        break
    frame, rgb = vm.process_frame(frame)
    fh, fw, _ = frame.shape
    out = detector.process(rgb)
    if out.multi_hand_landmarks:
        hands_seen += 1
        lm = vm.draw_landmarks(frame, out.multi_hand_landmarks, drawing)
        coords = vm.get_landmark_coordinates(lm, fw, fh)
        mapped = vm.map_to_screen(coords, sw, sh, fw, fh)
        plocx, plocy = vm.move_cursor(mapped[8], plocx, plocy, sm)
        click_time, sflag, dragging = vm.detect_gestures(
            mapped, mapped[4], click_time, cthr, sflag, dragging)
        best = frame.copy()
    vm.add_user_instructions(frame)
    frames += 1

cap.release()
el = time.time() - t0
print(f'frames={frames}  hands_detected_frames={hands_seen}  fps={frames/el:.1f}')
print(f'actions_that_would_fire={stub.log[:15]}  (total {len(stub.log)})')
if best is not None:
    cv2.imwrite('_hand_seen.jpg', best)
    print('saved _hand_seen.jpg (frame with landmarks drawn)')
else:
    print('NO HAND DETECTED during the run')
