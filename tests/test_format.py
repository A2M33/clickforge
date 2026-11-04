from clickforge_core import blend, format_combo, mouse_name, vk_name


class TestMouseName:
    def test_buttons(self):
        assert mouse_name(0x201, 0) == ("mouse_left", True)
        assert mouse_name(0x205, 0) == ("mouse_right", False)
        assert mouse_name(0x207, 0) == ("mouse_middle", True)

    def test_side_buttons_use_the_high_word(self):
        assert mouse_name(0x20B, 1 << 16) == ("mouse4", True)
        assert mouse_name(0x20C, 2 << 16) == ("mouse5", False)

    def test_other_messages_are_ignored(self):
        assert mouse_name(0x200, 0) == (None, False)  # WM_MOUSEMOVE


def test_vk_names():
    assert vk_name(0x47) == "g"
    assert vk_name(0x35) == "5"
    assert vk_name(0x63) == "num3"
    assert vk_name(186) == "vk186"


def test_format_combo_puts_modifiers_first():
    assert format_combo(["g", "shift", "ctrl"]) == "CTRL + SHIFT + G"
    assert format_combo(["mouse4"]) == "MOUSE 4"
    assert format_combo(["ctrl", "mouse_left"]) == "CTRL + MOUSE LEFT"


def test_blend():
    assert blend("#000000", "#FFFFFF", 0) == "#000000"
    assert blend("#000000", "#FFFFFF", 1) == "#FFFFFF"
    assert blend("#102030", "#102030", 0.5) == "#102030"
