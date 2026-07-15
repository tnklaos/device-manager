import unittest

import bcel


class _FakeXPath:
    def __init__(self, found):
        self.found = found

    def wait(self, timeout=None):
        return self.found


class _FakeDevice:
    def __init__(self, found=True):
        self.found = found
        self.selector = ""

    def xpath(self, selector):
        self.selector = selector
        return _FakeXPath(self.found)


class PasswordInputTests(unittest.TestCase):
    def test_selector_supports_missing_hint_via_password_attribute(self):
        device = _FakeDevice()
        field = bcel.password_input(device)
        self.assertIsInstance(field, _FakeXPath)
        self.assertIn('@password="true"', device.selector)
        self.assertIn('@hint="ລະຫັດຜ່ານ"', device.selector)

    def test_missing_accessible_password_field_has_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "not exposed to accessibility"):
            bcel.password_input(_FakeDevice(found=False), timeout=0.01)


if __name__ == "__main__":
    unittest.main()
