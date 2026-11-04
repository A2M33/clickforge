from clickforge_core import LockLed


class Keyboard:
    def __init__(self, scroll=False, num=False):
        self.locks = {"scroll": scroll, "num": num}
        self.taps = []

    def read(self, mode):
        return self.locks[mode]

    def tap(self, mode):
        self.taps.append(mode)
        self.locks[mode] = not self.locks[mode]


def test_led_follows_the_state():
    kb = Keyboard()
    led = LockLed(kb.read, kb.tap)
    led.use("scroll")
    led.show(True)
    assert kb.locks["scroll"] is True
    led.show(True)
    assert kb.taps == ["scroll"]  # already on: no second tap
    led.show(False)
    assert kb.locks["scroll"] is False


def test_restore_gives_the_user_their_lock_state_back():
    kb = Keyboard(num=True)
    led = LockLed(kb.read, kb.tap)
    led.use("num")
    led.show(False)
    assert kb.locks["num"] is False
    led.restore()
    assert kb.locks["num"] is True
    led.restore()  # safe to call twice
    assert kb.locks["num"] is True


def test_off_mode_leaves_the_keyboard_alone():
    kb = Keyboard()
    led = LockLed(kb.read, kb.tap)
    led.use("off")
    led.show(True)
    led.restore()
    assert kb.taps == []
