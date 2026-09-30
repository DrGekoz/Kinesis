"""
Virtual Mouse - fixed build (Windows).

Same idea as whitehatboy005/Virtual-Mouse but with the gesture logic repaired:

  * pinch detection is scale-relative (thresholds derive from the hand's own size)
    instead of a fixed 70-screen-pixel vertical gap, so an open hand never
    triggers a click;
  * every action has hysteresis (press threshold != release threshold) and a
    cooldown, so nothing repeats every frame;
  * the blocking pyautogui.sleep(1) after right-click is gone (it stalled the
    capture loop for a full second);
  * double-click requires a real press/release/press, not a held pose;
  * the index finger is mapped through an inner active area so the whole screen
    is reachable without shoving your hand out of frame;
  * coordinates are clamped to the desktop and pyautogui.FAILSAFE is off
    (the original crashed with FailSafeException the moment the cursor hit a
    screen corner, which the full-frame mapping made easy).

Gestures:
  move        index finger
  left click  pinch thumb + index
  double      pinch thumb + index twice quickly
  right click pinch thumb + middle
  drag        pinch thumb + ring (hold to drag, release to drop)
  scroll      thumbs up / thumbs down (only when the other fingers are curled)
  quit        q / ESC

Run:  .venv\\Scripts\\python.exe mouse_fixed.py
      .venv\\Scripts\\python.exe mouse_fixed.py --dry     (no cursor movement)
"""
import argparse
import time

import cv2
import mediapipe as mp
import pyautogui

# --- drawing ---
CIRCLE_RADIUS = 5
CIRCLE_COLOR = (0, 255, 0)
LINE_COLOR = (0, 255, 0)
LINE_THICKNESS = 2
HUD_COLOR = (0, 220, 255)

# --- tuning ---
FRAME_W, FRAME_H = 1280, 720
SMOOTHENING = 5          # higher = smoother/slower cursor
ACTIVE_MARGIN = 0.12     # fraction of frame edge trimmed from the tracking area
PINCH_ON = 0.34          # thumb-tip to fingertip distance, as a fraction of hand size
PINCH_OFF = 0.55         # must open past this before the next pinch registers
CLICK_COOLDOWN = 0.35
DOUBLE_WINDOW = 0.45     # two pinches inside this window = double click
ACTION_COOLDOWN = 0.60   # right click / scroll / drag repeat guard
SCROLL_STEP = 300

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def init_webcam():
    # CAP_DSHOW opens noticeably faster than the default MSMF backend on Windows
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Error: Could not open video device.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    return cap


def process_frame(frame):
    frame = cv2.flip(frame, 1)
    return frame, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def draw_landmarks(frame, hands, drawing_utils):
    last = None
    for hand in hands:
        drawing_utils.draw_landmarks(frame, hand)
        last = hand.landmark
        for lm in last:
            cv2.circle(frame, (int(lm.x * frame.shape[1]), int(lm.y * frame.shape[0])),
                       CIRCLE_RADIUS, CIRCLE_COLOR, -1)
        for a, b in mp.solutions.hands.HAND_CONNECTIONS:
            pa = (int(last[a].x * frame.shape[1]), int(last[a].y * frame.shape[0]))
            pb = (int(last[b].x * frame.shape[1]), int(last[b].y * frame.shape[0]))
            cv2.line(frame, pa, pb, LINE_COLOR, LINE_THICKNESS)
    return last


def get_landmark_coordinates(landmarks, frame_width, frame_height):
    return {i: (int(lm.x * frame_width), int(lm.y * frame_height))
            for i, lm in enumerate(landmarks)}


def map_to_screen(coords, screen_width, screen_height, frame_width, frame_height):
    """Map the inner active area of the frame onto the full desktop."""
    x0, x1 = frame_width * ACTIVE_MARGIN, frame_width * (1 - ACTIVE_MARGIN)
    y0, y1 = frame_height * ACTIVE_MARGIN, frame_height * (1 - ACTIVE_MARGIN)
    mapped = {}
    for i, (x, y) in coords.items():
        mx = (x - x0) / (x1 - x0) * screen_width
        my = (y - y0) / (y1 - y0) * screen_height
        mapped[i] = (min(max(mx, 0), screen_width - 1), min(max(my, 0), screen_height - 1))
    return mapped


def move_cursor(index_coords, plocx, plocy, screen_width, screen_height, smoothening):
    ix, iy = index_coords
    cx = min(max(plocx + (ix - plocx) / smoothening, 0), screen_width - 1)
    cy = min(max(plocy + (iy - plocy) / smoothening, 0), screen_height - 1)
    pyautogui.moveTo(int(cx), int(cy))
    return cx, cy


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def detect_gestures(mapped, state):
    """mapped: landmark dict in screen space. state: dict of mutable gesture state."""
    # hand size = wrist -> middle-finger MCP; makes every threshold scale-relative
    scale = _dist(mapped[0], mapped[9]) or 1.0
    now = time.time()

    def pinch(finger, key):
        on, off = PINCH_ON * scale, PINCH_OFF * scale
        d = _dist(mapped[4], mapped[finger])
        held = state[key]
        if held:
            state[key] = d < off
        else:
            state[key] = d < on
        return state[key]

    index_pinch = pinch(8, 'pi')
    middle_pinch = pinch(12, 'pm')
    ring_pinch = pinch(16, 'pr')

    action = ''

    # left / double click
    if index_pinch and not state['pi_prev']:
        if now - state['last_left'] < DOUBLE_WINDOW and now - state['last_left'] > 0.05:
            pyautogui.doubleClick()
            state['last_left'] = 0
            action = 'DOUBLE CLICK'
        else:
            pyautogui.click()
            state['last_left'] = now
            action = 'LEFT CLICK'
    state['pi_prev'] = index_pinch

    # drag (ring pinch held)
    if ring_pinch and not state['dragging'] and now - state['last_action'] > 0.25:
        pyautogui.mouseDown()
        state['dragging'] = True
        state['last_action'] = now
        action = 'DRAG START'
    elif not ring_pinch and state['dragging']:
        pyautogui.mouseUp()
        state['dragging'] = False
        state['last_action'] = now
        action = 'DRAG END'

    # right click
    if (middle_pinch and not state['pm_prev']
            and not index_pinch and now - state['last_right'] > ACTION_COOLDOWN):
        pyautogui.rightClick()
        state['last_right'] = now
        action = 'RIGHT CLICK'
    state['pm_prev'] = middle_pinch

    # scroll: thumb pointing up/down with the other four fingers curled
    curled = all(mapped[i][1] > mapped[i - 2][1] for i in (8, 12, 16, 20))
    if curled and not index_pinch and not middle_pinch and not ring_pinch:
        thumb_y, wrist_y = mapped[4][1], mapped[0][1]
        gap = 0.45 * scale
        if thumb_y < wrist_y - gap and now - state['last_scroll'] > ACTION_COOLDOWN:
            pyautogui.scroll(SCROLL_STEP)
            state['last_scroll'] = now
            action = 'SCROLL UP'
        elif thumb_y > wrist_y + gap and now - state['last_scroll'] > ACTION_COOLDOWN:
            pyautogui.scroll(-SCROLL_STEP)
            state['last_scroll'] = now
            action = 'SCROLL DOWN'

    state['action'] = action
    return state


def add_user_instructions(frame, state):
    lines = [
        "Virtual Mouse (fixed build)",
        "Move: index finger   |   Left click: pinch thumb+index",
        "Double: same pinch twice   |   Right click: pinch thumb+middle",
        "Drag: pinch thumb+ring (hold)   |   Scroll: thumbs up/down",
        "Quit: q or ESC",
    ]
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (10, 22 + i * 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 255, 0), 1, cv2.LINE_AA)
    if state.get('action'):
        cv2.putText(frame, state['action'], (10, frame.shape[0] - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, HUD_COLOR, 2, cv2.LINE_AA)
    status = "DRY RUN (no cursor movement)" if state.get('dry') else "LIVE"
    cv2.putText(frame, status, (frame.shape[1] - 260, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, HUD_COLOR, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='log gestures without moving the cursor')
    ap.add_argument('--camera', type=int, default=0)
    args = ap.parse_args()

    if args.dry:
        class _Dry:
            def __getattr__(self, name):
                def _f(*a, **k):
                    if a:
                        print(f'  [dry] {name}{a}')
                return _f
        globals()['pyautogui'] = _Dry()

    cap = init_webcam()
    hand_detector = mp.solutions.hands.Hands(
        max_num_hands=1, min_detection_confidence=0.6, min_tracking_confidence=0.6)
    drawing_utils = mp.solutions.drawing_utils
    screen_width, screen_height = pyautogui.size()

    state = dict(pi=False, pm=False, pr=False, pi_prev=False, pm_prev=False,
                 last_left=0.0, last_right=0.0, last_scroll=0.0, last_action=0.0,
                 dragging=False, action='', dry=args.dry)
    plocx = plocy = screen_width / 2, screen_height / 2
    plocx, plocy = plocx
    print(f'screen {screen_width}x{screen_height} | running. Press q or ESC in the window to quit.')

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: Failed to capture image.")
            break
        frame, rgb_frame = process_frame(frame)
        frame_height, frame_width, _ = frame.shape
        output = hand_detector.process(rgb_frame)
        hands = output.multi_hand_landmarks

        if hands:
            landmarks = draw_landmarks(frame, hands, drawing_utils)
            coords = get_landmark_coordinates(landmarks, frame_width, frame_height)
            mapped = map_to_screen(coords, screen_width, screen_height, frame_width, frame_height)
            plocx, plocy = move_cursor(mapped[8], plocx, plocy,
                                       screen_width, screen_height, SMOOTHENING)
            detect_gestures(mapped, state)

        add_user_instructions(frame, state)
        cv2.imshow('Virtual Mouse', frame)
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            break

    cap.release()
    cv2.destroyAllWindows()
    if state['dragging']:
        pyautogui.mouseUp()


if __name__ == "__main__":
    main()
