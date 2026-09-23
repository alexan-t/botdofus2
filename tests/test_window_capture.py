import ctypes
from ctypes import wintypes
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from combatbot.vision import capture, window
from combatbot.vision.models import ClientRect


class FakeUser32:
    def EnumWindows(self, callback, lparam):
        for hwnd in (101, 202, 303):
            callback(hwnd, 0)
        return True

    def IsWindowVisible(self, hwnd):
        return hwnd != 303

    def IsIconic(self, hwnd):
        return hwnd == 202

    def IsWindow(self, hwnd):
        return hwnd in (101, 202)

    def GetClientRect(self, hwnd, pointer):
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 800, 600
        return True

    def ClientToScreen(self, hwnd, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x, point.y = 10, 20
        return True


def test_window_selection_by_handle_and_client_rect(monkeypatch) -> None:
    monkeypatch.setattr(window, "_user32", lambda: FakeUser32())
    monkeypatch.setattr(window, "_title", lambda hwnd: {101: "DOFUS joueur", 202: "DOFUS autre", 303: "DOFUS caché"}[hwnd])
    windows = window.list_dofus_windows()
    assert [(item.hwnd, item.minimized) for item in windows] == [(101, False), (202, True)]
    assert window.client_geometry(101) == ClientRect(10, 20, 800, 600)
    with pytest.raises(RuntimeError, match="minimisé"):
        window.client_geometry(202)


def test_capture_only_client_and_detects_movement(monkeypatch) -> None:
    regions = []
    rect = ClientRect(10, 20, 8, 6)
    monkeypatch.setattr(capture, "activate_window", lambda hwnd: None)
    monkeypatch.setattr(capture, "client_geometry", lambda hwnd: rect)
    monkeypatch.setattr(capture.ImageGrab, "grab", lambda **kwargs: (_ for _ in ()).throw(OSError("unavailable")))

    def screenshot(*, region):
        regions.append(region)
        return Image.new("RGB", (region[2], region[3]), (255, 0, 0))

    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(screenshot=screenshot))
    frame = capture.capture_client(101)
    assert regions == [(10, 20, 8, 6)]
    assert frame.image.shape == (6, 8, 3)
    assert frame.image[0, 0].tolist() == [0, 0, 255]

    geometries = iter((rect, ClientRect(11, 20, 8, 6)))
    monkeypatch.setattr(capture, "client_geometry", lambda hwnd: next(geometries))
    with pytest.raises(RuntimeError, match="bougé"):
        capture.capture_client(101)


def test_capture_by_handle_crops_nonclient_borders(monkeypatch) -> None:
    client = ClientRect(110, 220, 8, 6)
    outer = ClientRect(100, 200, 28, 36)
    image = Image.new("RGB", (28, 36), (0, 0, 0))
    image.paste((0, 255, 0), (10, 20, 18, 26))
    monkeypatch.setattr(capture, "activate_window", lambda hwnd: True)
    monkeypatch.setattr(capture, "client_geometry", lambda hwnd: client)
    monkeypatch.setattr(capture, "window_bounds", lambda hwnd: outer)
    monkeypatch.setattr(capture.ImageGrab, "grab", lambda **kwargs: image)
    frame = capture.capture_client(101)
    assert frame.image.shape == (6, 8, 3)
    assert frame.image[0, 0].tolist() == [0, 255, 0]
    assert frame.activation_succeeded
