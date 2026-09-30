"""The Gesture-Map marketplace: the client, the browse tab, and the upload form.

The desktop app talks to a small Cloudflare Worker in front of a D1 database (see cloudflare/), which
means the API token never ships inside Kinesis - the app only ever calls a public read/write endpoint.

Offline is a normal state, not an error: if the marketplace cannot be reached the tab says so and
everything else keeps working.
"""
from __future__ import annotations

import json
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import messagebox
from typing import Callable, Dict, List, Optional

from .gesture_map import GestureMap, validate
from .settings_window import BG, BORDER, MUTED, PANEL, PANEL_HI, TEXT, _font, _round_rect
from .gesture_ui import pill_button

DEFAULT_API = ""          # set by cloudflare/deploy.py once the Worker is published
TIMEOUT = 8.0


def api_base(cfg) -> str:
    return str(cfg.get("marketplace_api") or DEFAULT_API).rstrip("/")


def _request(cfg, path: str, method: str = "GET", payload: Optional[dict] = None,
             timeout: float = TIMEOUT):
    base = api_base(cfg)
    if not base:
        raise RuntimeError("the marketplace address is not set yet (config: marketplace_api)")
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_maps(cfg) -> List[Dict]:
    return list(_request(cfg, "/maps").get("maps", []))


def fetch_map(cfg, slug: str) -> Dict:
    """Download a map and count it. Returns {'map': {...}, 'downloads': n}."""
    return _request(cfg, f"/maps/{slug}/download")


def submit_map(cfg, title: str, description: str, author: str, github: str,
               gmap: GestureMap) -> Dict:
    """Publish a map. The Worker re-validates it, so a bad file is rejected server-side too."""
    return _request(cfg, "/maps", method="POST",
                    payload={"title": title, "description": description, "author_name": author,
                             "github_url": github, "map": gmap.to_json()})


# ---------------------------------------------------------------- the upload form
def submit_dialog(root, cfg, gmap: GestureMap, accent: tuple, status: tk.Label) -> None:
    win = tk.Toplevel(root)
    win.title("Submit to the marketplace")
    win.configure(bg=BG)
    win.geometry("520x460")
    win.transient(root)
    win.grab_set()

    fields = {"title": gmap.name, "description": gmap.description, "author": gmap.author,
              "github": ""}
    entries: Dict[str, tk.Entry] = {}

    tk.Label(win, text="Submit to the marketplace", bg=BG, fg=TEXT,
             font=_font(13, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
    tk.Label(win, text=f"{len(gmap.bindings)} bindings will be published as "
                       f"{gmap.name!r}. Anyone can download it.",
             bg=BG, fg=MUTED, font=_font(10), wraplength=470, justify="left").pack(
        anchor="w", padx=18, pady=(0, 12))

    for key, label, hint in (("title", "Title", "what it is for, in a few words"),
                             ("description", "Description", "what someone gets from it"),
                             ("author", "Your name", "shown as the author"),
                             ("github", "Your GitHub link", "required - github.com/you")):
        tk.Label(win, text=label, bg=BG, fg=MUTED, font=_font(10, "bold")).pack(
            anchor="w", padx=18, pady=(8, 2))
        entry = tk.Entry(win, bg=PANEL, fg=TEXT, insertbackground=accent[0], relief="flat",
                         font=_font(11))
        entry.insert(0, fields.get(key, ""))
        entry.pack(fill="x", padx=18, ipady=5)
        tk.Label(win, text=hint, bg=BG, fg=MUTED, font=_font(9)).pack(anchor="w", padx=18)
        entries[key] = entry

    note = tk.Label(win, text="", bg=BG, fg=accent[0], font=_font(10), wraplength=470,
                    justify="left")
    note.pack(anchor="w", padx=18, pady=(10, 0))

    def send():
        values = {k: e.get().strip() for k, e in entries.items()}
        missing = [k for k, v in values.items() if not v]
        if missing:
            note.configure(text="still needed: " + ", ".join(missing), fg="#e0b15c")
            return
        note.configure(text="uploading...", fg=MUTED)
        win.update_idletasks()
        try:
            result = submit_map(cfg, values["title"], values["description"], values["author"],
                                values["github"], gmap)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                problems = detail.get("problems") or [detail.get("error", str(exc))]
            except Exception:
                problems = [str(exc)]
            note.configure(text="rejected: " + "; ".join(problems[:4]), fg="#e07a5c")
            return
        except Exception as exc:
            note.configure(text=f"could not reach the marketplace: {exc}", fg="#e07a5c")
            return
        status.configure(text=f"submitted: /maps/{result.get('slug')}")
        messagebox.showinfo("Kinesis",
                           f"Live at the marketplace:\n\n{api_base(cfg)}/maps/"
                           f"{result.get('slug')}")
        win.destroy()

    footer = tk.Frame(win, bg=BG)
    footer.pack(side="bottom", fill="x", padx=18, pady=16)
    pill_button(footer, "Submit", send, accent, width=130, filled=True).pack(side="right")
    pill_button(footer, "Cancel", win.destroy, accent, width=110).pack(side="right", padx=(0, 8))
    root.wait_window(win)


# ---------------------------------------------------------------- the browse tab
def marketplace_tab(parent, cfg, accent: tuple, on_download: Callable[[GestureMap, str], None],
                    status: tk.Label) -> None:
    outer = tk.Frame(parent, bg=BG)
    outer.pack(fill="both", expand=True)

    head = tk.Frame(outer, bg=BG)
    head.pack(fill="x")
    tk.Label(head, text="Gesture-Map marketplace", bg=BG, fg=TEXT,
             font=_font(13, "bold")).pack(side="left")
    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
    scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview, bg=PANEL,
                          troughcolor=BG, activebackground=accent[1], highlightthickness=0,
                          bd=0, width=10)
    holder = tk.Frame(canvas, bg=BG)
    canvas.create_window((0, 0), window=holder, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True, pady=(8, 0))
    scroll.pack(side="right", fill="y", pady=(8, 0))
    holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

    def render(maps: List[Dict], message: str = "") -> None:
        for child in holder.winfo_children():
            child.destroy()
        if message:
            tk.Label(holder, text=message, bg=BG, fg=MUTED, font=_font(10), wraplength=740,
                     justify="left").pack(anchor="w", pady=6)
        if not maps:
            return
        for item in maps:
            row = tk.Frame(holder, bg=PANEL)
            row.pack(fill="x", pady=3)
            inner = tk.Frame(row, bg=PANEL)
            inner.pack(fill="x", padx=12, pady=10)
            text = tk.Frame(inner, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            title = item.get("title", "untitled")
            if item.get("featured"):
                title += "  ★"
            tk.Label(text, text=title, bg=PANEL, fg=TEXT, font=_font(11, "bold"),
                     anchor="w").pack(anchor="w")
            meta = (f"{item.get('binding_count', '?')} bindings · by {item.get('author_name', '?')}"
                    f" · {item.get('downloads', 0)} downloads")
            tk.Label(text, text=meta, bg=PANEL, fg=accent[0], font=_font(10),
                     anchor="w").pack(anchor="w")
            if item.get("description"):
                tk.Label(text, text=item["description"], bg=PANEL, fg=MUTED, font=_font(9),
                         wraplength=560, justify="left", anchor="w").pack(anchor="w")

            def grab(row_item=item):
                try:
                    payload = fetch_map(cfg, str(row_item.get("slug")))
                except Exception as exc:
                    status.configure(text=f"download failed: {exc}")
                    return
                problems = validate(payload.get("map") or {})
                if problems:
                    messagebox.showerror("Kinesis", "That map cannot be used:\n\n"
                                         + "\n".join(f"· {p}" for p in problems[:10]))
                    return
                on_download(GestureMap.from_json(payload["map"]), str(row_item.get("slug")))

            pill_button(inner, "Download", grab, accent, width=110, height=30, small=True).pack(
                side="right")

    def refresh():
        render([], "loading...")
        try:
            maps = list_maps(cfg)
        except Exception as exc:
            render([], f"The marketplace could not be reached ({exc}).\n"
                       f"Set the address with:  run.bat --tune marketplace_api=<worker url>")
            return
        render(maps, f"{len(maps)} map(s) shared so far.")

    pill_button(head, "Refresh", refresh, accent, width=100, height=30, small=True).pack(
        side="right")
    refresh()
