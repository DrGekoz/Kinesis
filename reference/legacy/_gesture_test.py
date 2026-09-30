"""Deterministic gesture-logic test: synthetic landmarks, no camera, no cursor movement.
Drives mouse_fixed.detect_gestures (and upstream mouse.detect_gestures for contrast)."""
import sys, time
sys.path.insert(0, '.')
import mouse_fixed as mf
import mouse as up

S = 400  # hand scale in screen px (wrist -> middle MCP)


def open_hand(cx=1000, cy=800, thumb_dx=-1, thumb_dy=0, scale=S):
    """Build 21 synthetic screen-space landmarks for an OPEN hand.
    Fingers extended => tip.y < pip.y. thumb sits ~0.9*scale from index tip."""
    lm = {}
    lm[0] = (cx, cy)                                   # wrist
    lm[5] = (cx + 0.10 * scale, cy - 0.30 * scale)     # index MCP
    lm[6] = (cx + 0.12 * scale, cy - 0.55 * scale)     # index PIP
    lm[7] = (cx + 0.13 * scale, cy - 0.70 * scale)     # index DIP
    lm[8] = (cx + 0.14 * scale, cy - 0.82 * scale)     # index TIP
    lm[9] = (cx + 0.45 * scale, cy - 0.45 * scale)     # middle MCP  (defines scale)
    lm[10] = (cx + 0.47 * scale, cy - 0.72 * scale)
    lm[11] = (cx + 0.48 * scale, cy - 0.86 * scale)
    lm[12] = (cx + 0.49 * scale, cy - 0.97 * scale)    # middle TIP
    lm[13] = (cx + 0.72 * scale, cy - 0.38 * scale)
    lm[14] = (cx + 0.75 * scale, cy - 0.62 * scale)
    lm[15] = (cx + 0.76 * scale, cy - 0.75 * scale)
    lm[16] = (cx + 0.77 * scale, cy - 0.86 * scale)    # ring TIP
    lm[17] = (cx + 0.92 * scale, cy - 0.28 * scale)
    lm[18] = (cx + 0.95 * scale, cy - 0.48 * scale)
    lm[19] = (cx + 0.96 * scale, cy - 0.60 * scale)
    lm[20] = (cx + 0.97 * scale, cy - 0.70 * scale)    # pinky TIP
    # thumb out to the left, tip far from index tip
    lm[1] = (cx - 0.25 * scale, cy - 0.10 * scale)
    lm[2] = (cx - 0.48 * scale, cy - 0.20 * scale)
    lm[3] = (cx - 0.68 * scale, cy - 0.28 * scale)
    lm[4] = (cx - 0.85 * scale, cy - 0.34 * scale)     # thumb TIP
    return lm


def move(lm, idx, dx, dy):
    lm = dict(lm)
    x, y = lm[idx]
    lm[idx] = (x + dx, y + dy)
    return lm


def curl(lm, *fingers):
    """Push a fingertip below its PIP so it reads as curled."""
    lm = dict(lm)
    pairs = {8: 6, 12: 10, 16: 14, 20: 18}
    for f in fingers:
        x, y = lm[f]
        lm[f] = (x, lm[pairs[f]][1] + 0.15 * S)
    return lm


def new_state():
    return dict(pi=False, pm=False, pr=False, pi_prev=False, pm_prev=False,
                last_left=0.0, last_right=0.0, last_scroll=0.0, last_action=0.0,
                dragging=False, action='', dry=True)


class Stub:
    def __init__(self):
        self.log = []
    def __getattr__(self, n):
        def _f(*a, **k):
            self.log.append((n,) + tuple(int(v) for v in a))
        return _f


results = []
def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(('PASS  ' if ok else 'FAIL  ') + f'{name}: got={got} want={want}')


def run_fixed(stub, state, frames, delay=0.07):
    """Realistic frame pacing: the capture loop runs ~15fps, and gesture logic is
    time-based, so a zero-delay loop is not a valid test of it."""
    for lm in frames:
        mf.pyautogui = stub
        mf.detect_gestures(lm, state)
        time.sleep(delay)


# ---------------------------------------------------------------- fixed build
print('--- mouse_fixed.py ---')
open_lm = open_hand()

# A: open hand must be silent (this is what upstream gets wrong)
s = Stub(); st = new_state()
run_fixed(s, st, [open_lm] * 30)
check('open hand -> no actions', s.log, [])
check('open hand -> no scroll', [a for a in s.log if 'scroll' in a[0]], [])

# B/C: one pinch = one left click, holding it does not repeat
pinch_lm = move(open_lm, 4, 0.85 * S - 0.99 * S, 0)   # bring thumb tip onto index tip
pinch_lm = move(pinch_lm, 4, open_lm[8][0] - pinch_lm[4][0] + 0.20 * S,
                open_lm[8][1] - pinch_lm[4][1])
s = Stub(); st = new_state()
run_fixed(s, st, [open_lm, pinch_lm, pinch_lm, pinch_lm, pinch_lm])
check('single pinch -> 1 left click', [a for a in s.log if a[0] == 'click'], [('click',)])

# D: press/release/press inside the double-click window -> double click
s = Stub(); st = new_state()
run_fixed(s, st, [open_lm, pinch_lm, pinch_lm, open_lm, pinch_lm])
check('two pinches -> 1 double click', [a for a in s.log if a[0] == 'doubleClick'],
      [('doubleClick',)])

# E: thumb close to middle, index open -> right click, once
right_lm = dict(open_lm)
rx, ry = open_lm[12]
right_lm[4] = (rx + 0.20 * S, ry)
s = Stub(); st = new_state()
run_fixed(s, st, [open_lm, right_lm, right_lm, right_lm])
check('middle pinch -> 1 right click', [a for a in s.log if a[0] == 'rightClick'],
      [('rightClick',)])

# F: ring pinch held -> drag start, release -> drag end
drag_lm = dict(open_lm)
dx, dy = open_lm[16]
drag_lm[4] = (dx + 0.20 * S, dy)
s = Stub(); st = new_state()
run_fixed(s, st, [open_lm, drag_lm, drag_lm, open_lm])
acts = [a[0] for a in s.log if a[0] in ('mouseDown', 'mouseUp')]
check('ring pinch -> drag start then end', acts, ['mouseDown', 'mouseUp'])

# G/H: thumbs up / down with the other fingers curled
up_lm = curl(open_lm, 8, 12, 16, 20)
up_lm = dict(up_lm); up_lm[4] = (up_lm[0][0] - 0.4 * S, up_lm[0][1] - 0.9 * S)
s = Stub(); st = new_state()
run_fixed(s, st, [up_lm] * 8)
check('thumbs up -> 1 scroll up', [a for a in s.log if 'scroll' in a[0]], [('scroll', 300)])

down_lm = dict(up_lm); down_lm[4] = (up_lm[0][0] - 0.4 * S, up_lm[0][1] + 0.9 * S)
s = Stub(); st = new_state()
run_fixed(s, st, [down_lm] * 8)
check('thumbs down -> 1 scroll down', [a for a in s.log if 'scroll' in a[0]], [('scroll', -300)])

# I: cursor never leaves the desktop
cx, cy = mf.move_cursor((-9999, -9999), 0, 0, 1920, 1080, 5)
check('cursor clamps low', (cx, cy), (0, 0))
cx, cy = mf.move_cursor((9999, 9999), 0, 0, 1920, 1080, 5)
check('cursor clamps high', (cx, cy), (1919, 1079))

# J: active-area mapping covers the full desktop
corner = mf.map_to_screen({8: (int(1280 * mf.ACTIVE_MARGIN), int(720 * mf.ACTIVE_MARGIN))},
                          1920, 1080, 1280, 720)[8]
check('top-left active corner -> (0,0)', (int(corner[0]), int(corner[1])), (0, 0))

# ---------------------------------------------------------------- upstream
print('--- upstream mouse.py (contrast) ---')
# The pose that spammed during the live capture: hand held up to the camera with
# the thumb roughly level with the fingertips (|dy| under 70 screen px), fingers
# relaxed rather than splayed. Upstream tests only the vertical gap.
us = Stub()
up.pyautogui = us
relaxed = dict(open_lm)
relaxed[4] = (open_lm[0][0] - 0.4 * S, open_lm[8][1])          # thumb level with index tip
t, fl, dr = 0, False, False
for _ in range(30):
    t, fl, dr = up.detect_gestures(relaxed, relaxed[4], t, 0.3, fl, dr)
over = len(us.log)
print(f'info  upstream, thumb-level relaxed hand, 30 frames -> {over} fired actions '
      f'{sorted(set(a[0] for a in us.log))}')

# same pose through the fixed build: must stay silent
fs = Stub(); fst = new_state()
run_fixed(fs, fst, [relaxed] * 30, delay=0)
check('fixed build: same relaxed pose -> silent', fs.log, [])

print()
print(f'{sum(results)}/{len(results)} fixed-build checks passed')
sys.exit(0 if all(results) else 1)
