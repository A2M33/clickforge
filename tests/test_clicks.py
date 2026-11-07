import queue
import threading
import time

from clickforge_core import ClickMultiplier, run_all, run_pending

CFG = {"multiplier": 4, "delay_ms": 10, "buttons": "left"}


class Rig:
    """A multiplier with a fake mouse. `run()` plays queued work on this thread."""

    def __init__(self, cfg=CFG, own_window=False):
        self.clicks = []
        self.waits = []
        self.own_window = own_window
        self.mult = ClickMultiplier(cfg, self.click, is_own_window=lambda x, y: self.own_window,
                                    sleep=self.waits.append)
        self.mult.enabled = True

    def click(self, button):
        self.clicks.append(button)

    def real_click(self, button="left"):
        self.mult.on_click(5, 5, button, True)
        self.mult.on_click(5, 5, button, False)

    def run(self):
        while not self.mult._jobs.empty():
            self.mult._replay(*self.mult._jobs.get())


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


def test_switching_off_mid_burst_stops_at_once():
    rig = Rig()
    rig.real_click()

    def off_after_two(button):
        rig.clicks.append(button)
        if len(rig.clicks) == 2:
            rig.mult.enabled = False

    rig.mult._click = off_after_two
    rig.run()
    assert len(rig.clicks) == 2


def test_a_click_in_flight_is_dropped_if_off_lands_during_the_wait():
    rig = Rig()
    rig.real_click()
    rig.mult._sleep = lambda seconds: setattr(rig.mult, "enabled", False)
    rig.run()
    assert rig.clicks == []  # used to send one more after the switch-off


def test_off_then_on_does_not_replay_old_clicks():
    rig = Rig()
    for _ in range(5):
        rig.real_click()
    rig.mult.enabled = False
    rig.mult.enabled = True  # a quick double toggle
    rig.run()
    assert rig.clicks == []  # used to fire every queued burst


def test_new_clicks_after_a_toggle_still_work():
    rig = Rig()
    rig.mult.enabled = False
    rig.mult.enabled = True
    rig.real_click()
    rig.run()
    assert rig.clicks == ["left"] * 3


def test_burst_stops_when_the_cursor_reaches_our_window():
    rig = Rig()
    rig.real_click()

    def moved_after_one(button):
        rig.clicks.append(button)
        rig.own_window = True  # the user went for the OFF button while the burst ran

    rig.mult._click = moved_after_one
    rig.run()
    assert len(rig.clicks) == 1  # the rest used to land on the button and flip it back and forth


def test_a_failed_click_does_not_kill_the_worker():
    rig = Rig()
    calls = []

    def flaky(button):
        calls.append(button)
        if len(calls) == 1:
            raise OSError("SendInput failed")

    rig.mult._click = flaky
    rig.mult._sleep = lambda seconds: None
    rig.mult.start_worker()
    rig.real_click()
    deadline = time.monotonic() + 5
    while len(calls) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    rig.real_click()
    while len(calls) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    rig.mult.stop_worker()
    assert len(calls) == 4  # one failed, then the next real click's three


def test_stop_worker_ends_the_thread_even_with_a_full_queue():
    rig = Rig()
    release = threading.Event()
    rig.mult._sleep = lambda seconds: release.wait(5)
    rig.mult.start_worker()
    for _ in range(70):
        rig.real_click()
    release.set()
    rig.mult.stop_worker()
    assert not rig.mult._thread.is_alive()
    assert not rig.mult.enabled


def test_one_failing_ui_job_does_not_stop_the_others():
    jobs = queue.Queue()
    ran = []

    def broken():
        raise ValueError("bad widget")

    jobs.put(lambda: ran.append(1))
    jobs.put(broken)
    jobs.put(lambda: ran.append(2))
    run_pending(jobs)  # the exception used to end the UI loop, and with it every hotkey
    assert ran == [1, 2]


def test_shutdown_finishes_after_a_failing_step():
    ran = []

    def broken():
        raise OSError("LED busy")

    run_all([lambda: ran.append("hooks"), broken, lambda: ran.append("server")])
    assert ran == ["hooks", "server"]  # one failure used to leave the window open and hooks on


def test_old_bursts_are_dropped_without_waiting_out_their_delay():
    rig = Rig({**CFG, "delay_ms": 200})
    for _ in range(64):
        rig.real_click()
    rig.mult.enabled = False
    rig.mult.enabled = True
    rig.run()
    # Each one used to sleep 200 ms first, so a fresh click after a quick off/on sat behind
    # almost 13 seconds of dead bursts.
    assert rig.waits == []
