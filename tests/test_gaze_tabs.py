"""Gaze-assisted tab switching: the strip band, and the warp-then-click ordering."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import tabs                                              # noqa: E402
from kinesis import winapi as w                                       # noqa: E402
from kinesis.actions import ActionRunner                              # noqa: E402
from kinesis.config import Config                                     # noqa: E402
from kinesis.gestures import Intent                                   # noqa: E402


def browser_window(exe="chrome.exe", cls="Chrome_WidgetWin_1", hwnd=7001):
    return SimpleNamespace(hwnd=hwnd, exe=exe, class_name=cls, title="Tab - Chrome",
                           rect=(0, 0, 1920, 1080))


def patch_client(monkeypatch, rect=(0, 0, 1920, 1080), dpi=96):
    monkeypatch.setattr(w, "client_rect_on_screen", lambda hwnd: rect)
    monkeypatch.setattr(w, "window_dpi", lambda hwnd: dpi)


def test_browser_detection_uses_the_executable_not_just_the_class(monkeypatch):
    assert tabs.is_browser(browser_window("chrome.exe"))
    assert tabs.is_browser(browser_window("firefox.exe", "MozillaWindowClass"))
    # Electron apps share the Chromium class - they must not count as browsers
    assert not tabs.is_browser(browser_window("Code.exe"))
    assert not tabs.is_browser(browser_window("slack.exe"))
    assert not tabs.is_browser(None)


def test_tab_strip_band_is_the_top_of_the_client_area(monkeypatch):
    patch_client(monkeypatch, rect=(100, 200, 2020, 1280))
    band = tabs.tab_strip_band(browser_window())
    assert band == (206.0, 246.0)          # client top + 6, plus 40 of strip
    assert tabs.in_tab_strip(browser_window(), 700, 220)
    assert not tabs.in_tab_strip(browser_window(), 700, 300)      # page content
    assert not tabs.in_tab_strip(browser_window(), 700, 202)      # drag region
    assert not tabs.in_tab_strip(browser_window(), 50, 220)       # left of the window


def test_tab_strip_scales_with_dpi(monkeypatch):
    patch_client(monkeypatch, rect=(0, 0, 2560, 1440), dpi=144)   # 150%
    band = tabs.tab_strip_band(browser_window())
    assert band == (9.0, 69.0)             # 6*1.5 and 40*1.5


def test_click_warps_the_pointer_before_clicking(monkeypatch):
    """The pointer has to be on the tab before the click goes out, and the warp has to settle."""
    order = []
    runner = ActionRunner(Config(), [], dry=False)
    monkeypatch.setattr(runner, "_set_cursor", lambda x, y: order.append(("warp", int(x), int(y))))
    monkeypatch.setattr(w, "mouse_click", lambda button: order.append(("click", button)))
    monkeypatch.setattr(time, "sleep", lambda s: order.append(("sleep", round(s, 6))))

    runner.execute([Intent("mouse.click", button="left", warp=(640.0, 220.0))])
    assert order == [("warp", 640, 220), ("sleep", 0.001), ("click", "left")], order


def test_warp_delay_is_configurable(monkeypatch):
    order = []
    cfg = Config()
    cfg.set("gaze_click_warp_delay_ms", 8.0)
    runner = ActionRunner(cfg, [], dry=False)
    monkeypatch.setattr(runner, "_set_cursor", lambda x, y: order.append("warp"))
    monkeypatch.setattr(w, "mouse_click", lambda button: order.append("click"))
    monkeypatch.setattr(time, "sleep", lambda s: order.append(round(s, 6)))
    runner.execute([Intent("mouse.click", button="left", warp=(10.0, 10.0))])
    assert order == ["warp", 0.008, "click"], order


def test_a_click_without_a_warp_does_not_move_the_pointer(monkeypatch):
    order = []
    runner = ActionRunner(Config(), [], dry=False)
    monkeypatch.setattr(runner, "_set_cursor", lambda x, y: order.append("warp"))
    monkeypatch.setattr(w, "mouse_click", lambda button: order.append("click"))
    monkeypatch.setattr(time, "sleep", lambda s: order.append("sleep"))
    runner.execute([Intent("mouse.click", button="left")])
    assert order == ["click"], order


def _app():
    from kinesis.app import KinesisApp
    cfg = Config()
    cfg.set("vcam_enabled", False)
    cfg.set("desktop_overlay", False)
    cfg.set("gaze_enabled", False)
    return KinesisApp(cfg, preview=False)


def test_app_annotates_a_click_made_over_a_browser_tab(monkeypatch):
    app = _app()
    info = SimpleNamespace(hwnd=7001, exe="chrome.exe", class_name="Chrome_WidgetWin_1",
                           title="A tab", process_id=9, is_fullscreen=False)
    monkeypatch.setattr(w, "topmost_window_at", lambda *a, **k: 7001)
    monkeypatch.setattr(w, "window_info", lambda hwnd, monitors: info)
    monkeypatch.setattr(tabs, "is_browser", lambda i: True)
    monkeypatch.setattr(tabs, "in_tab_strip", lambda i, x, y, top, h: True)
    gaze = SimpleNamespace(x=640.0, y=220.0, valid=True)

    click = Intent("mouse.click", button="left")
    app._annotate_gaze_click([click], gaze, 10.0)
    assert click.warp == (640.0, 220.0)
    assert "gaze tab" in click.note


def test_app_leaves_clicks_over_page_content_alone(monkeypatch):
    app = _app()
    info = SimpleNamespace(hwnd=7001, exe="chrome.exe", class_name="Chrome_WidgetWin_1",
                           title="A tab", process_id=9, is_fullscreen=False)
    monkeypatch.setattr(w, "topmost_window_at", lambda *a, **k: 7001)
    monkeypatch.setattr(w, "window_info", lambda hwnd, monitors: info)
    monkeypatch.setattr(tabs, "is_browser", lambda i: True)
    monkeypatch.setattr(tabs, "in_tab_strip", lambda i, x, y, top, h: False)
    click = Intent("mouse.click", button="left")
    app._annotate_gaze_click([click], SimpleNamespace(x=640.0, y=600.0, valid=True), 10.0)
    assert click.warp is None


def test_app_ignores_uncalibrated_gaze(monkeypatch):
    app = _app()
    called = []
    monkeypatch.setattr(w, "topmost_window_at", lambda *a, **k: called.append(1))
    click = Intent("mouse.click", button="left")
    app._annotate_gaze_click([click], SimpleNamespace(x=640.0, y=220.0, valid=False), 10.0)
    assert click.warp is None and not called

