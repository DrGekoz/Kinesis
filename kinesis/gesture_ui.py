"""The Gestures and Marketplace tabs.

Same wrapper as the Screens tab (Tk, with every control drawn by hand on Canvases so it matches the
overlay rather than looking like a stock Windows dialog), and the same palette and font stack.

What lives here:
  * the binding list for the current Gesture-Map, each row editable
  * a gesture + action editor that cannot express something the engine cannot do
  * import / export / submit
  * the marketplace list, downloaded straight into the editor
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional, Sequence

from .gesture_map import (ACTION_TYPES, BUILT_IN, HANDS, MODIFIERS, MOUSE_BUTTONS, POSES,
                          SYSTEM_ACTIONS, Action, Binding, Gesture, GestureMap, validate)
from .settings_window import (ACCENTS, BG, BORDER, FONT_STACK, MUTED, PANEL, PANEL_HI, TEXT,
                              _font, _round_rect, accent_for)

MAPS_DIR = Path(__file__).resolve().parent.parent / "gesture_maps"


# ---------------------------------------------------------------- widgets
def pill_button(parent, text: str, command: Callable, accent: tuple, *, width: int = 150,
                height: int = 34, filled: bool = False, small: bool = False) -> tk.Canvas:
    """A rounded button. Canvas-drawn because the native Tk one ignores every colour rule."""
    size = 10 if small else 11
    canvas = tk.Canvas(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0)
    fill = accent[0] if filled else PANEL_HI
    outline = accent[0] if filled else BORDER
    text_colour = "#101216" if filled else TEXT
    shape = _round_rect(canvas, 1, 1, width - 1, height - 1, height // 2, fill=fill,
                        outline=outline, width=1)
    label = canvas.create_text(width // 2, height // 2, text=text, fill=text_colour,
                               font=_font(size, "bold" if filled else "normal"))

    def hover(_event=None):
        canvas.itemconfig(shape, fill=accent[1] if filled else PANEL)
        canvas.itemconfig(label, fill="#101216" if filled else accent[0])
        canvas.configure(cursor="hand2")

    def leave(_event=None):
        canvas.itemconfig(shape, fill=fill)
        canvas.itemconfig(label, fill=text_colour)
        canvas.configure(cursor="")

    for item in (shape, label):
        canvas.tag_bind(item, "<Enter>", hover)
        canvas.tag_bind(item, "<Leave>", leave)
        canvas.tag_bind(item, "<Button-1>", lambda _e: command())
    return canvas


def switch(parent, on: bool, command: Callable[[bool], None], accent: tuple) -> tk.Canvas:
    """The sliding pill switch used on the Screens tab too."""
    width, height = 44, 24
    canvas = tk.Canvas(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0)
    track = _round_rect(canvas, 1, 3, width - 1, height - 3, (height - 6) // 2,
                        fill=accent[0] if on else PANEL_HI, outline=BORDER)
    knob_x = width - height // 2 if on else height // 2
    knob = canvas.create_oval(knob_x - 9, height // 2 - 9, knob_x + 9, height // 2 + 9,
                              fill="#f2f4f8" if on else MUTED, outline="")

    def click(_event=None):
        command(not on)

    for item in (track, knob):
        canvas.tag_bind(item, "<Button-1>", click)
        canvas.tag_bind(item, "<Enter>", lambda _e: canvas.configure(cursor="hand2"))
        canvas.tag_bind(item, "<Leave>", lambda _e: canvas.configure(cursor=""))
    return canvas


def tab_bar(parent, names: Sequence[str], accent: tuple, on_select: Callable[[int], None],
            active: int = 0) -> tk.Frame:
    """The tab strip. Returns the frame so the caller can restyle the active tab."""
    bar = tk.Frame(parent, bg=BG)
    buttons: List[tk.Canvas] = []
    for i, name in enumerate(names):
        button = pill_button(bar, name, lambda i=i: on_select(i), accent, width=132, height=32,
                             filled=(i == active))
        button.pack(side="left", padx=(0, 8))
        buttons.append(button)
    bar.buttons = buttons                    # type: ignore[attr-defined]
    return bar


# ---------------------------------------------------------------- the editor
def edit_action_dialog(root, binding: Binding, accent: tuple) -> bool:
    """Pick a gesture and an action for one binding. True if the user saved."""
    win = tk.Toplevel(root)
    win.title("Edit gesture")
    win.configure(bg=BG)
    win.geometry("560x620")
    win.transient(root)
    win.grab_set()

    state = {"pose": binding.gesture.posed, "hand": binding.gesture.hand,
             "two": binding.gesture.two_hand, "left": binding.gesture.left or "open",
             "right": binding.gesture.right or "peace",
             "kind": binding.action.kind, "keys": list(binding.action.keys),
             "mods": list(binding.action.modifiers), "button": binding.action.button,
             "system": binding.action.value or "minimise", "saved": False}

    def header(text: str):
        tk.Label(win, text=text, bg=BG, fg=MUTED, font=_font(10, "bold")).pack(anchor="w",
                                                                               padx=18, pady=(12, 4))

    header("WHICH GESTURE")
    mode_row = tk.Frame(win, bg=BG)
    mode_row.pack(anchor="w", padx=18)

    def mode_button(label, value):
        return pill_button(mode_row, label, lambda: (state.update(two=value), redraw()),
                           accent, width=118, height=30, filled=state["two"] == value, small=True)

    one_b = mode_button("One hand", False)
    two_b = mode_button("Two hands", True)
    one_b.pack(side="left", padx=(0, 6))
    two_b.pack(side="left")

    pose_area = tk.Frame(win, bg=BG)
    pose_area.pack(fill="x", padx=18, pady=(10, 0))
    hand_row = tk.Frame(win, bg=BG)
    hand_row.pack(anchor="w", padx=18, pady=(6, 0))

    header("WHAT IT DOES")
    kind_row = tk.Frame(win, bg=BG)
    kind_row.pack(anchor="w", padx=18)
    keys_row = tk.Frame(win, bg=BG)
    keys_row.pack(anchor="w", padx=18, pady=(10, 0))
    mouse_row = tk.Frame(win, bg=BG)
    mouse_row.pack(anchor="w", padx=18, pady=(10, 0))
    system_row = tk.Frame(win, bg=BG)
    system_row.pack(anchor="w", padx=18, pady=(10, 0))
    capture_hint = tk.Label(win, text="", bg=BG, fg=MUTED, font=_font(10))
    capture_hint.pack(anchor="w", padx=18, pady=(4, 0))

    def clear(frame):
        for child in frame.winfo_children():
            child.destroy()

    def redraw():
        clear(pose_area)
        clear(hand_row)
        clear(kind_row)
        clear(keys_row)
        clear(mouse_row)
        clear(system_row)
        mode_one = pill_button(mode_row, "One hand", lambda: (state.update(two=False), redraw()),
                               accent, width=118, height=30, filled=not state["two"], small=True)
        mode_two = pill_button(mode_row, "Two hands", lambda: (state.update(two=True), redraw()),
                               accent, width=118, height=30, filled=state["two"], small=True)
        for i, child in enumerate(mode_row.winfo_children()):
            child.pack_forget()
        mode_one.pack(side="left", padx=(0, 6))
        mode_two.pack(side="left")

        if state["two"]:
            for side in ("left", "right"):
                row = tk.Frame(pose_area, bg=BG)
                row.pack(anchor="w", pady=2)
                tk.Label(row, text=f"{side.title()} hand", bg=BG, fg=TEXT,
                         font=_font(10, "bold"), width=11, anchor="w").pack(side="left")
                for pose in sorted(POSES):
                    chosen = state[side] == pose
                    pill_button(row, pose, lambda s=side, p=pose: (state.update({s: p}), redraw()),
                                accent, width=76, height=26, filled=chosen, small=True
                                ).pack(side="left", padx=1)
        else:
            grid = tk.Frame(pose_area, bg=BG)
            grid.pack(anchor="w")
            for i, pose in enumerate(sorted(POSES)):
                note = " *" if pose in BUILT_IN else ""
                pill_button(grid, pose + note,
                            lambda p=pose: (state.update(pose=p), redraw()), accent,
                            width=104, height=28, filled=state["pose"] == pose, small=True
                            ).grid(row=i // 5, column=i % 5, padx=2, pady=2)
            for i, hand in enumerate(HANDS):
                pill_button(hand_row, f"{hand} hand", lambda h=hand: (state.update(hand=h), redraw()),
                            accent, width=104, height=28, filled=state["hand"] == hand, small=True
                            ).grid(row=0, column=i, padx=2)
            if state["pose"] in BUILT_IN:
                tk.Label(pose_area, text=f"* {state['pose']} is built in ({BUILT_IN[state['pose']]})"
                                         " - binding it here overrides that",
                         bg=BG, fg="#e0b15c", font=_font(9)).pack(anchor="w", pady=(4, 0))

        for i, kind in enumerate(ACTION_TYPES):
            pill_button(kind_row, kind.replace("_", " "),
                        lambda k=kind: (state.update(kind=k), redraw()), accent,
                        width=104, height=28, filled=state["kind"] == kind, small=True
                        ).grid(row=0, column=i, padx=2)

        if state["kind"] in ("keys", "hold_keys"):
            for i, mod in enumerate(MODIFIERS):
                pill_button(keys_row, mod.upper(),
                            lambda m=mod: (state["mods"].remove(m) if m in state["mods"]
                                           else state["mods"].append(m), redraw()), accent,
                            width=72, height=28, filled=mod in state["mods"], small=True
                            ).grid(row=0, column=i, padx=2)
            text = "+".join(state["keys"]) or "press a key"
            pill_button(keys_row, text, lambda: capture_key(),
                        accent, width=180, height=28, filled=bool(state["keys"]), small=True
                        ).grid(row=0, column=4, padx=(10, 2))
        if state["kind"] == "mouse":
            for i, button in enumerate(MOUSE_BUTTONS):
                pill_button(mouse_row, button.replace("_", " "),
                            lambda b=button: (state.update(button=b), redraw()), accent,
                            width=112, height=28, filled=state["button"] == button, small=True
                            ).grid(row=0, column=i, padx=2)
        if state["kind"] == "system":
            for i, value in enumerate(SYSTEM_ACTIONS):
                pill_button(system_row, value.replace("_", " "),
                            lambda v=value: (state.update(system=v), redraw()), accent,
                            width=124, height=28, filled=state["system"] == value, small=True
                            ).grid(row=i // 3, column=i % 3, padx=2, pady=2)

    def capture_key():
        capture_hint.configure(text="press the key you want (Esc to cancel)")
        win.focus_force()

        def grabbed(event):
            name = (event.keysym or "").lower()
            aliases = {"control_l": "ctrl", "control_r": "ctrl", "shift_l": "shift",
                       "shift_r": "shift", "alt_l": "alt", "alt_r": "alt", "super_l": "win",
                       "super_r": "win", "return": "enter", "prior": "pageup", "next": "pagedown"}
            name = aliases.get(name, name)
            if name == "escape":
                win.unbind("<KeyPress>")
                capture_hint.configure(text="")
                return
            if name in MODIFIERS:
                if name not in state["mods"]:
                    state["mods"].append(name)
            else:
                state["keys"] = [name]
                win.unbind("<KeyPress>")
            capture_hint.configure(text="")
            redraw()

        win.bind("<KeyPress>", grabbed)

    redraw()

    def save():
        if state["two"]:
            gesture = Gesture(left=state["left"], right=state["right"])
        else:
            gesture = Gesture(posed=state["pose"], hand=state["hand"])
        if state["kind"] in ("keys", "hold_keys"):
            if not state["keys"]:
                messagebox.showerror("Kinesis", "Pick a key for this gesture first.")
                return
            action = Action(kind=state["kind"], keys=list(state["keys"]),
                            modifiers=list(state["mods"]))
        elif state["kind"] == "mouse":
            action = Action(kind="mouse", button=state["button"])
        elif state["kind"] == "system":
            action = Action(kind="system", value=state["system"])
        else:
            action = Action(kind=state["kind"])
        candidate = {"schema": "kinesis.gesture-map", "version": 1,
                     "bindings": [{"gesture": gesture.to_json(), "action": action.to_json()}]}
        problems = validate(candidate)
        if problems:
            messagebox.showerror("Kinesis", "\n".join(problems))
            return
        binding.gesture, binding.action = gesture, action
        state["saved"] = True
        win.destroy()

    footer = tk.Frame(win, bg=BG)
    footer.pack(side="bottom", fill="x", padx=18, pady=16)
    pill_button(footer, "Save", save, accent, width=130, filled=True).pack(side="right")
    pill_button(footer, "Cancel", win.destroy, accent, width=110).pack(side="right", padx=(0, 8))
    root.wait_window(win)
    return state["saved"]


def gestures_tab(parent, cfg, accent: tuple, get_map: Callable[[], GestureMap],
                 set_map: Callable[[GestureMap], None], status: tk.Label) -> None:
    """The binding list and the import / export / submit buttons."""
    outer = tk.Frame(parent, bg=BG)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
    scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                          bg=PANEL, troughcolor=BG, activebackground=accent[1],
                          highlightthickness=0, bd=0, width=10)
    holder = tk.Frame(canvas, bg=BG)
    canvas.create_window((0, 0), window=holder, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

    def redraw():
        for child in holder.winfo_children():
            child.destroy()
        gmap = get_map()
        tk.Label(holder, text=gmap.name, bg=BG, fg=TEXT, font=_font(13, "bold")).pack(
            anchor="w", pady=(0, 2))
        tk.Label(holder, text=gmap.description or "no description", bg=BG, fg=MUTED,
                 font=_font(10), wraplength=760, justify="left").pack(anchor="w", pady=(0, 12))

        for binding in list(gmap.bindings):
            row = tk.Frame(holder, bg=PANEL)
            row.pack(fill="x", pady=3)
            inner = tk.Frame(row, bg=PANEL)
            inner.pack(fill="x", padx=12, pady=10)

            def make_toggle(b=binding):
                return lambda value: (setattr(b, "enabled", value), redraw())

            switch(inner, binding.enabled, make_toggle(), accent).pack(side="left", padx=(0, 12))
            text = tk.Frame(inner, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            builtin = "  (built in)" if binding.gesture.posed in BUILT_IN else ""
            tk.Label(text, text=binding.gesture.describe(), bg=PANEL, fg=TEXT,
                     font=_font(11, "bold"), anchor="w").pack(anchor="w")
            tk.Label(text, text=binding.action.describe() + builtin, bg=PANEL, fg=accent[0],
                     font=_font(10), anchor="w").pack(anchor="w")

            def edit(b=binding):
                if edit_action_dialog(holder.winfo_toplevel(), b, accent):
                    redraw()
                    status.configure(text=f"edited: {b.gesture.describe()}")

            def remove(b=binding):
                gmap.bindings = [x for x in gmap.bindings if x is not b]
                redraw()
                status.configure(text="binding removed - remember to save")

            pill_button(inner, "Edit", edit, accent, width=76, height=30, small=True).pack(
                side="right", padx=(6, 0))
            pill_button(inner, "X", remove, accent, width=42, height=30, small=True).pack(
                side="right")

        def add():
            new = Binding(id=f"custom-{len(gmap.bindings) + 1}", gesture=Gesture(posed="peace"),
                          action=Action(kind="keys", keys=["enter"]))
            if edit_action_dialog(holder.winfo_toplevel(), new, accent):
                gmap.bindings.append(new)
                redraw()
                status.configure(text="gesture added - remember to save")

        buttons = tk.Frame(holder, bg=BG)
        buttons.pack(fill="x", pady=(14, 4))
        pill_button(buttons, "+  Add gesture", add, accent, width=160, filled=True).pack(side="left")
        pill_button(buttons, "Import .json", lambda: do_import(), accent, width=140).pack(
            side="left", padx=8)
        pill_button(buttons, "Export .json", lambda: do_export(), accent, width=140).pack(side="left")
        pill_button(buttons, "Submit to Marketplace", lambda: do_submit(), accent,
                    width=210).pack(side="left", padx=8)

        tk.Label(holder, text=f"{len(gmap.bindings)} bindings  ·  gestures marked (built in) "
                              f"override Kinesis's own unless you leave them alone",
                 bg=BG, fg=MUTED, font=_font(9)).pack(anchor="w", pady=(8, 0))

    # ---------------------------------------------------------------- import / export
    def do_import():
        path = filedialog.askopenfilename(title="Import a Gesture-Map",
                                          filetypes=[("Gesture-Map", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Kinesis", f"That file could not be read as JSON:\n\n{exc}")
            return
        problems = validate(data)
        if problems:
            messagebox.showerror("Kinesis",
                                 "That file is not a usable Gesture-Map:\n\n" + "\n".join(
                                     f"· {p}" for p in problems[:12]))
            status.configure(text="import failed")
            return
        imported = GestureMap.from_json(data)
        set_map(imported)
        redraw()
        status.configure(text=f"imported {imported.name} ({len(imported.bindings)} bindings)")

    def do_export():
        gmap = get_map()
        path = filedialog.asksaveasfilename(title="Export this Gesture-Map",
                                            defaultextension=".json",
                                            initialfile=f"{gmap.name.lower().replace(' ', '-')}.json",
                                            filetypes=[("Gesture-Map", "*.json")])
        if not path:
            return
        try:
            saved = gmap.save(Path(path))
        except Exception as exc:
            messagebox.showerror("Kinesis", f"Could not write that file:\n\n{exc}")
            return
        status.configure(text=f"exported to {saved}")

    def do_submit():
        from .marketplace import submit_dialog
        submit_dialog(holder.winfo_toplevel(), cfg, get_map(), accent, status)

    redraw()


def maps_folder() -> Path:
    """Where the shipped maps live, so the app can offer them straight away."""
    return MAPS_DIR


def shipped_maps() -> List[Path]:
    if not MAPS_DIR.is_dir():
        return []
    return sorted(MAPS_DIR.glob("*.json"))
