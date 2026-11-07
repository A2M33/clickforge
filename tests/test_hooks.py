"""The glue around the Windows hooks. No hook is installed: these call the filters directly."""
import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="clickforge.py imports Windows APIs")

WM_LBUTTONDOWN = 0x201
WM_XBUTTONDOWN = 0x20B


@pytest.fixture
def clickforge():
    pytest.importorskip("customtkinter")
    pytest.importorskip("pynput")
    import clickforge

    return clickforge


class Suppressed(Exception):
    """Stands in for the exception pynput uses to swallow an event."""


def fake_engine(hotkeys=None):
    def suppress():
        raise Suppressed

    return SimpleNamespace(hotkeys=hotkeys, _listener=SimpleNamespace(suppress_event=suppress))


def test_our_own_clicks_never_reach_the_multiplier(clickforge):
    injected = SimpleNamespace(flags=clickforge.LLMHF_INJECTED, mouseData=0)
    # False keeps the event from on_click; without this each generated click would multiply again.
    assert clickforge.ClickEngine._filter(fake_engine(), WM_LBUTTONDOWN, injected) is False


def test_our_own_clicks_never_reach_the_shortcut(clickforge):
    seen = []
    hotkeys = SimpleNamespace(mouse=lambda name, pressed: seen.append(name))
    injected = SimpleNamespace(flags=clickforge.LLMHF_INJECTED, mouseData=0)
    clickforge.ClickEngine._filter(fake_engine(hotkeys), WM_LBUTTONDOWN, injected)
    assert seen == []


def test_real_clicks_pass_through(clickforge):
    real = SimpleNamespace(flags=0, mouseData=0)
    assert clickforge.ClickEngine._filter(fake_engine(), WM_LBUTTONDOWN, real) is True


def test_a_bound_mouse_button_is_swallowed(clickforge):
    hotkeys = SimpleNamespace(mouse=lambda name, pressed: name == "mouse4")
    real = SimpleNamespace(flags=0, mouseData=1 << 16)
    with pytest.raises(Suppressed):
        clickforge.ClickEngine._filter(fake_engine(hotkeys), WM_XBUTTONDOWN, real)


def test_button_names_match_the_multiplier(clickforge):
    # The hook passes button.name and the worker looks it up, so the two sides must agree.
    assert clickforge.mouse.Button.left.name == "left"
    assert clickforge.mouse.Button.right.name == "right"
    assert clickforge.mouse.Button["left"] is clickforge.mouse.Button.left


def bare_hotkeys(clickforge, combo, triggers):
    """A HotkeyManager without its keyboard hook."""
    from clickforge_core import HotkeyState

    manager = clickforge.HotkeyManager.__new__(clickforge.HotkeyManager)
    HotkeyState.__init__(manager, combo, lambda: triggers.append(1), lambda combo: None)
    return manager


def test_a_key_pynput_cannot_translate_does_not_kill_the_hook(clickforge):
    # An exception in the callback stops pynput's listener, and the shortcut with it.
    triggers = []
    manager = bare_hotkeys(clickforge, ["f6"], triggers)
    manager._press(None)
    manager._release(None)
    manager._press(clickforge.keyboard.Key.f6)
    assert triggers == [1]
