from clickforge_core import ClickMultiplier

CFG = {"multiplier": 4, "delay_ms": 10, "buttons": "left"}


class Rig:
    """A multiplier with a fake mouse. `run()` plays queued work on this thread."""

    def __init__(self, cfg=CFG, own_window=False):
        self.clicks = []
        self.waits = []
        self.mult = ClickMultiplier(cfg, self.clicks.append,
                                    is_own_window=lambda x, y: own_window, sleep=self.waits.append)
        self.mult.enabled = True

    def real_click(self, button="left"):
        self.mult.on_click(5, 5, button, True)
        self.mult.on_click(5, 5, button, False)

    def run(self):
        while not self.mult._jobs.empty():
            self.mult._replay(self.mult._jobs.get())


def test_one_real_click_becomes_multiplier_clicks():
    rig = Rig()
    rig.real_click()
    rig.run()
    assert rig.clicks == ["left"] * 3  # plus the user's own click
    assert rig.mult.real_clicks == 1 and rig.mult.generated == 3
    assert rig.waits == [0.01] * 3


def test_acts_on_release_only():
    rig = Rig()
    rig.mult.on_click(5, 5, "left", True)
    assert rig.mult.real_clicks == 0


def test_off_means_no_clicks():
    rig = Rig()
    rig.mult.enabled = False
    rig.real_click()
    rig.run()
    assert rig.clicks == [] and rig.mult.real_clicks == 0


def test_only_the_chosen_button_counts():
    rig = Rig()
    rig.real_click("right")
    assert rig.mult.real_clicks == 0
    rig.mult.buttons = "both"
    rig.real_click("right")
    rig.run()
    assert rig.clicks == ["right"] * 3


def test_multiplier_of_one_adds_nothing():
    rig = Rig({**CFG, "multiplier": 1})
    rig.real_click()
    rig.run()
    assert rig.mult.real_clicks == 1 and rig.clicks == []


def test_clicks_on_our_own_window_stay_single():
    rig = Rig(own_window=True)
    rig.real_click()
    rig.run()
    assert rig.mult.real_clicks == 0 and rig.clicks == []


def test_a_full_queue_drops_work_instead_of_blocking_the_hook():
    rig = Rig()
    for _ in range(200):
        rig.real_click()
    assert rig.mult.real_clicks == 200
    assert rig.mult._jobs.qsize() == 64


def test_delay_has_a_one_millisecond_floor():
    rig = Rig({**CFG, "delay_ms": 0})
    rig.real_click()
    rig.run()
    assert rig.waits == [0.001] * 3
