import pytest

from clickforge_core import blend, fit_scale, format_combo, mouse_name, vk_name


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


class TestFitScale:
    def test_a_layout_that_fits_stays_full_size(self):
        assert fit_scale(1000, 1400) == 1.0
        assert fit_scale(1000, 1000) == 1.0

    def test_a_tall_layout_shrinks_to_fit(self):
        assert fit_scale(1045, 1032) == pytest.approx(0.9876, abs=0.001)  # 1080p with a taskbar
        assert fit_scale(1560, 1020) == pytest.approx(0.654, abs=0.001)  # 150% on a 1080p laptop

    def test_it_stops_before_the_text_is_unreadable(self):
        assert fit_scale(1045, 300) == 0.6


def test_punctuation_keys_get_their_real_names():
    assert format_combo(["ctrl", "vk187"]) == "CTRL + ="  # used to read CTRL + VK187
    assert format_combo(["vk220"]) == "\\"


def test_special_keys_read_as_words():
    assert format_combo(["page_up"]) == "PAGE UP"
    assert format_combo(["num5"]) == "NUM5"
    assert format_combo(["vk255"]) == "VK255"  # no name for it: show the code
