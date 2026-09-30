"""The settings window: which screens Kinesis tracks, and recalibrating what it learned.

Opened with F2 while Kinesis is running, or with `run.bat --settings`. It exists because the monitor
choice is the setting people actually need to change - a TV that is not on the desk scans as if it
were, and gaze and hand aiming both go wrong on a screen that is not where Windows thinks it is - and
because "run this .bat file" is not a feature anyone finds.

Everything here is drawn with the same palette, fonts and rounded shapes as the on-screen overlay, so
the window reads as part of Kinesis rather than a Tk dialog bolted to the side of it.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence

# ---------------------------------------------------------------- style
BG = "#0e0f13"
PANEL = "#171a20"
PANEL_HI = "#1e222a"
BORDER = "#2a3039"
TEXT = "#e9ecf2"
MUTED = "#8d96a5"
DIM = "#5c6572"

# accent pairs, matching gazevis.THEMES head/tail
ACCENTS: Dict[str, tuple] = {
    "ember": ("#ffb25c", "#ff6014"),
    "cyan": ("#98f0ff", "#00a8e0"),
    "violet": ("#d6aaff", "#924ee8"),
    "lime": ("#c4ff96", "#38d05c"),
    "ice": ("#f0faff", "#78b4fa"),
}

FONT_STACK = ("Bahnschrift", "Segoe UI Semibold", "Segoe UI", "Helvetica")


def accent_for(theme: str) -> tuple:
    return ACCENTS.get(str(theme or "").lower(), ACCENTS["ember"])


# ---------------------------------------------------------------- pure logic (tested)
def toggle(devices: Sequence[str], device: str) -> List[str]:
    """Add or remove one device, preserving order."""
    out = [d for d in devices if d != device]
    if len(out) == len(devices):
        out.append(device)
    return out


def normalise(devices: Sequence[str], monitors: Sequence) -> List[str]:
    """Drop devices that are not present. An empty result means every screen."""
    present = [str(getattr(m, "device", "") or "") for m in monitors]
    kept = [str(d) for d in devices if str(d) in present]
    return kept if kept else present


def selected_indices(monitors: Sequence, devices: Sequence[str]) -> List[int]:
    """1-based numbers of the selected screens, as the calibration wizards take them."""
    out = []
    for i, m in enumerate(monitors, 1):
        if str(getattr(m, "device", "") or "") in {str(d) for d in devices}:
            out.append(i)
    return out


def calibration_arg(monitors: Sequence, devices: Sequence[str]) -> str:
    """The --monitors value for the enabled screens. Empty string means all of them."""
    idx = selected_indices(monitors, devices)
    if not idx or len(idx) == len(monitors):
        return ""
    return ",".join(str(i) for i in idx)


def summary(monitors: Sequence, devices: Sequence[str]) -> str:
    total = len(monitors)
    if not [d for d in (devices or []) if str(d)]:
        return f"all {total} screens tracked"          # an empty selection means every screen
    chosen = len(selected_indices(monitors, devices))
    if chosen == total:
        return f"all {total} screens tracked"
    if chosen == 0:
        return "no screens selected"
    return f"{chosen} of {total} screens tracked"


# ---------------------------------------------------------------- drawing helpers
def _font(size: int, weight: str = "normal") -> tuple:
    return tuple([*FONT_STACK[:1], size, weight])


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """A rounded rectangle, the shape everything else in Kinesis already uses."""
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class _Switch:
    """A pill toggle drawn on its own canvas, so it matches the overlay instead of Tk defaults."""

    W, H = 40, 22

    def __init__(self, parent, on: bool, accent: tuple, command: Callable[[bool], None] = None):
        self.on = bool(on)
        self.accent = accent
        self.command = command
        self.canvas = tk.Canvas(parent, width=self.W, height=self.H, bg=PANEL,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _event=None):
        self.on = not self.on
        self.draw()
        if self.command:
            self.command(self.on)

    def draw(self):
        c = self.canvas
        c.delete("all")
        track = self.accent[1] if self.on else "#2f3540"
        _round_rect(c, 1, 1, self.W - 1, self.H - 1, self.H / 2 - 1, fill=track, outline="")
        knob_x = self.W - self.H / 2 - 1 if self.on else self.H / 2 + 1
        r = self.H / 2 - 3
        c.create_oval(knob_x - r, self.H / 2 - r, knob_x + r, self.H / 2 + r,
                      fill="#ffffff" if self.on else "#8d96a5", outline="")


class _PillButton:
    """A rounded button, filled (primary) or outlined (secondary)."""

    def __init__(self, parent, text: str, command: Callable[[], None], accent: tuple,
                 primary: bool = False, width: int = 190):
        self.command = command
        self.accent = accent
        self.fill = accent[1] if primary else PANEL_HI
        self.hover = accent[0] if primary else "#262c36"
        self.primary = primary
        self.canvas = tk.Canvas(parent, width=width, height=38, bg=BG, highlightthickness=0, bd=0,
                                cursor="hand2")
        self.canvas.create_text(width / 2, 19, text=text, font=_font(11, "bold"),
                                fill="#0e0f13" if primary else TEXT)
        self._paint(self.fill)
        self.canvas.bind("<Enter>", lambda e: self._paint(self.hover))
        self.canvas.bind("<Leave>", lambda e: self._paint(self.fill))
        self.canvas.bind("<Button-1>", lambda e: self.command())
        self.canvas.pack(side="left", padx=(0, 10))

    def _paint(self, colour):
        c = self.canvas
        c.delete("shape")
        w = int(c["width"])
        _round_rect(c, 1, 1, w - 1, 37, 12, fill=colour,
                    outline=self.accent[1] if self.primary else BORDER, tags="shape")
        c.tag_lower("shape")
        c.tag_raise("all")


# ---------------------------------------------------------------- the window
def run(cfg, monitors: Sequence, theme: Optional[str] = None,
        status: str = "", labels: Optional[dict] = None,
        _auto_close_ms: int = 0) -> Optional[str]:
    """Show the window. Returns 'saved', 'gaze', 'aim', or None when closed.

    Runs its own mainloop and blocks: the caller stops feeding gestures while it is open, so a pinch
    cannot click buttons in here. `_auto_close_ms` exists for the self-test tool, which renders the
    real window and closes it again without a human.
    """
    accent = accent_for(theme if theme is not None else cfg.get("vcam_theme", "ember"))
    devices: List[str] = list(cfg.get("enabled_monitors") or []) or \
        [str(getattr(m, "device", "") or "") for m in monitors]
    result: Dict[str, Optional[str]] = {"action": None}
    state = {"devices": devices}

    root = tk.Tk()
    root.title("Kinesis - settings")
    root.configure(bg=BG)
    root.geometry("760x580")
    root.minsize(700, 520)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    # ---------------- header
    head = tk.Canvas(root, height=92, bg=BG, highlightthickness=0, bd=0)
    head.pack(fill="x")
    head.create_text(30, 34, anchor="w", text="KINESIS", font=_font(26, "bold"), fill=TEXT)
    head.create_text(31, 62, anchor="w", text="screens and calibration", font=_font(11),
                     fill=MUTED)
    head.create_line(30, 88, 730, 88, fill=BORDER)

    # ---------------- why this matters
    note = tk.Canvas(root, height=54, bg=BG, highlightthickness=0, bd=0)
    note.pack(fill="x")
    note.create_text(30, 12, anchor="w", fill=MUTED, font=_font(10),
                     text="Hand and eye tracking only work on screens you actually face. A screen")
    note.create_text(30, 30, anchor="w", fill=MUTED, font=_font(10),
                     text="Windows places somewhere else than it really is should be switched off.")

    # ---------------- monitor rows
    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=18, pady=(4, 0))
    switches: List[tuple] = []

    def refresh_footer():
        state["devices"] = [d for d, sw in switches if sw.on]
        status_lbl.configure(text=summary(monitors, state["devices"]))
        for _d, sw in switches:
            sw.draw()

    for m in monitors:
        device = str(getattr(m, "device", "") or "")
        row = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        row.pack(fill="x", pady=4)
        inner = tk.Frame(row, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=11)

        left = tk.Frame(inner, bg=PANEL)
        left.pack(side="left", fill="x", expand=True)
        name = (labels or {}).get(device, "")
        label = name or (device.split("\\")[-1] if "\\" in device else device)
        tk.Label(left, text=label + ("   PRIMARY" if getattr(m, "primary", False) else ""),
                 bg=PANEL, fg=accent[0] if getattr(m, "primary", False) else TEXT,
                 font=_font(12, "bold"), anchor="w").pack(anchor="w")
        tk.Label(left, text=f"{int(getattr(m, 'width', 0))}x{int(getattr(m, 'height', 0))}   "
                            f"at {int(m.left)},{int(m.top)}" + (f"   {name}" if name else ""),
                 bg=PANEL, fg=DIM, font=_font(10), anchor="w").pack(anchor="w")

        sw = _Switch(inner, device in state["devices"], accent, command=lambda _v: refresh_footer())
        sw.canvas.pack(side="right")
        switches.append((device, sw))

    # ---------------- footer
    foot = tk.Frame(root, bg=BG)
    foot.pack(fill="x", padx=30, pady=(10, 0))
    status_lbl = tk.Label(foot, text=summary(monitors, state["devices"]), bg=BG, fg=accent[0],
                          font=_font(11, "bold"), anchor="w")
    status_lbl.pack(anchor="w")
    if status:
        tk.Label(foot, text=status, bg=BG, fg=MUTED, font=_font(10), anchor="w").pack(anchor="w")

    buttons = tk.Frame(root, bg=BG)
    buttons.pack(fill="x", padx=30, pady=16)

    def act(kind: str):
        if kind == "saved":
            state["devices"] = [d for d, sw in switches if sw.on]
            cfg["enabled_monitors"] = normalise(state["devices"], monitors)
            cfg["monitors_configured"] = True
            cfg.save()
        result["action"] = kind
        root.destroy()

    _PillButton(buttons, "Save and apply", lambda: act("saved"), accent, primary=True, width=180)
    _PillButton(buttons, "Recalibrate gaze", lambda: act("gaze"), accent, width=180)
    _PillButton(buttons, "Recalibrate aiming", lambda: act("aim"), accent, width=180)
    tk.Button(buttons, text="Close", command=root.destroy, bg=BG, fg=MUTED, bd=0,
              activebackground=BG, activeforeground=TEXT, font=_font(11)).pack(side="right")

    tk.Label(root, text="F2 opens this window while Kinesis is running. Changes save to "
                        "kinesis_config.json.", bg=BG, fg=DIM, font=_font(9)).pack(pady=(0, 12))

    root.update_idletasks()
    try:                                   # centre it on the primary screen
        x = (root.winfo_screenwidth() - root.winfo_width()) // 2
        y = (root.winfo_screenheight() - root.winfo_height()) // 3
        root.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except Exception:
        pass
    root.focus_force()
    if _auto_close_ms:
        root.after(int(_auto_close_ms), root.destroy)
    root.mainloop()
    return result["action"]
