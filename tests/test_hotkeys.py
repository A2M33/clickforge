from clickforge_core import HotkeyState, NumpadNames


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


class FakeKeyboard:
    """What Windows says is down, and a clock we can move."""

    def __init__(self):
        self.down = set()
        self.t = 0.0

    def is_down(self, mod):
        return mod in self.down

    def __call__(self):
        return self.t


def test_a_modifier_released_behind_the_lock_screen_is_forgotten():
    kb = FakeKeyboard()
    rig = Rig(("win", "f6"), mod_is_down=kb.is_down, clock=kb)
    kb.down.add("win")
    rig.keys.mod_down("win")  # Win+L: the release happens on the lock screen, out of our sight
    kb.down.clear()
    tap(rig.keys, "f6")
    assert rig.triggers == 0  # F6 alone used to fire a Win+F6 shortcut


def test_a_stuck_modifier_stays_out_of_a_new_shortcut():
    kb = FakeKeyboard()
    rig = Rig(mod_is_down=kb.is_down, clock=kb)
    kb.down.add("ctrl")
    rig.keys.mod_down("ctrl")  # Ctrl+Alt+Del
    kb.down.clear()
    rig.keys.capturing = True
    tap(rig.keys, "g")
    assert rig.captured == [["g"]]  # used to save CTRL + G


def test_a_stuck_modifier_does_not_make_left_click_bindable():
    kb = FakeKeyboard()
    rig = Rig(mod_is_down=kb.is_down, clock=kb)
    rig.keys.mod_down("ctrl")
    rig.keys.capturing = True
    assert rig.keys.mouse("mouse_left", True) is False


def test_a_real_held_modifier_still_counts():
    kb = FakeKeyboard()
    rig = Rig(("ctrl", "g"), mod_is_down=kb.is_down, clock=kb)
    kb.down.add("ctrl")
    rig.keys.mod_down("ctrl")
    tap(rig.keys, "g")
    assert rig.triggers == 1


def test_a_key_whose_release_we_missed_works_the_next_time():
    kb = FakeKeyboard()
    rig = Rig(mod_is_down=kb.is_down, clock=kb)
    rig.keys.key_down("f6")  # released on the lock screen: no key_up reaches us
    assert rig.triggers == 1
    kb.t = 60.0
    rig.keys.key_down("f6")
    assert rig.triggers == 2  # used to read as auto-repeat and be ignored


def test_slow_auto_repeat_is_still_auto_repeat():
    kb = FakeKeyboard()
    rig = Rig(mod_is_down=kb.is_down, clock=kb)
    for _ in range(5):
        rig.keys.key_down("f6")
        kb.t += 1.0  # the slowest repeat delay Windows offers
    assert rig.triggers == 1


VK_UP, VK_CLEAR, VK_F6 = 0x26, 0x0C, 0x75
EXTENDED = 0x01


def test_a_number_pad_key_keeps_its_name_with_num_lock_off():
    pad = NumpadNames()
    pad.seen(VK_UP, 0)  # number pad 8 with Num Lock off: VK_UP without the extended flag
    assert pad.name(VK_UP, "up") == "num8"
    pad.seen(VK_CLEAR, 0)
    assert pad.name(VK_CLEAR, "vk12") == "num5"


def test_the_real_arrow_keys_keep_their_own_names():
    pad = NumpadNames()
    pad.seen(VK_UP, EXTENDED)
    assert pad.name(VK_UP, "up") == "up"


def test_other_keys_pass_through():
    pad = NumpadNames()
    pad.seen(VK_F6, 0)
    assert pad.name(VK_F6, "f6") == "f6"


def test_events_are_paired_in_order():
    pad = NumpadNames()
    pad.seen(VK_UP, 0)  # number pad 8, then the arrow key, before either callback runs
    pad.seen(VK_UP, EXTENDED)
    assert pad.name(VK_UP, "up") == "num8"
    assert pad.name(VK_UP, "up") == "up"


def test_a_number_pad_shortcut_survives_the_num_lock_led():
    # The Num Lock LED turns Num Lock off while ClickForge is off, so the bound key arrives as
    # an arrow. It used to stop matching, and the shortcut never fired again.
    pad = NumpadNames()
    rig = Rig(("num8",))
    pad.seen(VK_UP, 0)
    tap(rig.keys, pad.name(VK_UP, "up"))
    assert rig.triggers == 1
