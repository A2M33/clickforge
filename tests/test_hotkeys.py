from clickforge_core import HotkeyState


class Rig:
    def __init__(self, combo=("f6",), **kwargs):
        self.triggers = 0
        self.captured = []

        def trigger():
            self.triggers += 1

        self.keys = HotkeyState(combo, trigger, self.captured.append, **kwargs)


def tap(keys, name):
    keys.key_down(name)
    keys.key_up(name)


def test_plain_key_triggers():
    rig = Rig()
    tap(rig.keys, "f6")
    assert rig.triggers == 1
    tap(rig.keys, "f7")
    assert rig.triggers == 1


def test_auto_repeat_triggers_once():
    rig = Rig()
    for _ in range(5):
        rig.keys.key_down("f6")
    rig.keys.key_up("f6")
    assert rig.triggers == 1


def test_combo_needs_its_modifier():
    rig = Rig(("ctrl", "g"))
    tap(rig.keys, "g")
    assert rig.triggers == 0
    rig.keys.mod_down("ctrl")
    tap(rig.keys, "g")
    assert rig.triggers == 1


def test_extra_modifiers_are_fine():
    rig = Rig()
    rig.keys.mod_down("shift")  # holding Shift to sprint
    tap(rig.keys, "f6")
    assert rig.triggers == 1


def test_capture_binds_the_next_key_with_its_modifiers():
    rig = Rig()
    rig.keys.capturing = True
    rig.keys.mod_down("ctrl")
    tap(rig.keys, "g")
    assert rig.captured == [["ctrl", "g"]]
    assert rig.triggers == 0  # binding a key doesn't toggle
    assert not rig.keys.capturing
    tap(rig.keys, "g")
    assert rig.triggers == 1


def test_escape_cancels_capture():
    rig = Rig()
    rig.keys.capturing = True
    tap(rig.keys, "esc")
    assert rig.captured == [None]
    assert rig.keys.combo == {"f6"}


def test_bound_mouse_button_is_consumed_down_and_up():
    rig = Rig(("mouse4",))
    assert rig.keys.mouse("mouse4", True) is True
    assert rig.triggers == 1
    assert rig.keys.mouse("mouse4", False) is True
    assert rig.keys.mouse("mouse5", True) is False


def test_plain_left_click_cannot_be_bound():
    rig = Rig()
    rig.keys.capturing = True
    assert rig.keys.mouse("mouse_left", True) is False
    assert rig.keys.capturing  # still waiting
    rig.keys.mod_down("ctrl")
    assert rig.keys.mouse("mouse_left", True) is True
    assert rig.captured == [["ctrl", "mouse_left"]]
