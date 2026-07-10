import unittest

import engine


class UsbConnectionStatusTests(unittest.TestCase):
    def test_file_transfer_mode_has_no_warning(self):
        status = engine.usb_connection_status("USB123", "device", "mtp,adb")
        self.assertTrue(status["data_transfer_mode"])
        self.assertEqual(status["connection_message"], "")

    def test_adb_only_mode_requests_file_transfer(self):
        status = engine.usb_connection_status("USB123", "device", "adb")
        self.assertFalse(status["data_transfer_mode"])
        self.assertIn("File Transfer mode is off", status["connection_message"])

    def test_unauthorized_device_has_actionable_message(self):
        status = engine.usb_connection_status("USB123", "unauthorized")
        self.assertIn("Select File Transfer", status["connection_message"])
        self.assertIn("Allow USB debugging", status["connection_message"])

    def test_wifi_device_never_gets_usb_warning(self):
        status = engine.usb_connection_status("192.168.1.10:5555", "device", "adb")
        self.assertIsNone(status["data_transfer_mode"])
        self.assertEqual(status["connection_message"], "")


if __name__ == "__main__":
    unittest.main()
