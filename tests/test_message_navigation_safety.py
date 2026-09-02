import itertools
import threading
import unittest
from unittest import mock

import bcel
import engine


class MessageNavigationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.device = mock.Mock()
        self.device.app_current.return_value = {"package": bcel.PKG}
        self.device.return_value.exists = False
        self.device.return_value.wait.return_value = True
        self.device.xpath.return_value.exists = False
        for name in ("time", "monotonic"):
            patcher = mock.patch.object(
                bcel.time, name, side_effect=itertools.count(0, 0.5)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(bcel.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_slow_list_does_not_trigger_detail_close_or_back(self):
        with (mock.patch.object(bcel, "go_home"),
              mock.patch.object(bcel, "_message_list_visible",
                                side_effect=[False, False, True]),
              mock.patch.object(bcel, "close_message_detail") as close):
            bcel.open_messages_tab(self.device)
        close.assert_not_called()
        self.device.press.assert_not_called()

    def test_unknown_list_stops_without_back_or_refresh(self):
        with (mock.patch.object(bcel, "connect", return_value=self.device),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "go_home"),
              mock.patch.object(bcel, "_message_list_visible", return_value=False),
              mock.patch.object(bcel, "refresh_messages") as refresh):
            with self.assertRaisesRegex(RuntimeError, "Messages list"):
                bcel.poll_messages("test-device", last_ref="existing-ref")
        self.device.press.assert_not_called()
        refresh.assert_not_called()

    def test_unknown_page_is_not_closed(self):
        with mock.patch.object(bcel, "_message_list_visible", return_value=False):
            self.assertFalse(bcel.close_message_detail(self.device))
        self.device.press.assert_not_called()
        self.device.xpath.return_value.click.assert_not_called()
        self.device.return_value.click.assert_not_called()

    def test_missing_tab_does_not_attempt_go_home_back_navigation(self):
        self.device.return_value.wait.return_value = False
        with mock.patch.object(bcel, "go_home") as go_home:
            with self.assertRaisesRegex(RuntimeError, "Messages tab"):
                bcel.open_messages_tab(self.device)
        go_home.assert_not_called()
        self.device.press.assert_not_called()

    def test_loading_after_detail_close_does_not_press_back_again(self):
        self.device.xpath.return_value.exists = True
        with mock.patch.object(bcel, "_message_list_visible", return_value=False):
            self.assertFalse(bcel.close_message_detail(self.device))
        self.device.xpath.return_value.click.assert_called_once_with()
        self.device.press.assert_not_called()

    def test_refresh_does_not_scroll_when_reload_never_settles(self):
        with (mock.patch.object(bcel, "_message_list_visible", return_value=True),
              mock.patch.object(bcel, "_wait_list_settled", return_value=False),
              mock.patch.object(bcel, "_scroll_messages_to_top") as scroll):
            with self.assertRaisesRegex(RuntimeError, "Messages list"):
                bcel.refresh_messages(self.device)
        scroll.assert_not_called()

    def test_unknown_screen_is_not_refreshed(self):
        with mock.patch.object(bcel, "_message_list_visible", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Messages list"):
                bcel.refresh_messages(self.device)
        self.device.xpath.return_value.click.assert_not_called()

    def test_lost_rows_do_not_cause_blind_swipes(self):
        with mock.patch.object(bcel, "_list_rows", return_value=[]):
            self.assertFalse(bcel._scroll_messages_to_top(self.device))
        self.device.swipe_ext.assert_not_called()

    def test_failed_detail_open_never_backs_out_of_an_unknown_screen(self):
        row = {"key": "row", "center": (200, 400), "sig": "row"}
        with (mock.patch.object(bcel, "_list_rows",
                                side_effect=itertools.chain([[row]], itertools.repeat([]))),
              mock.patch.object(bcel, "_extract_message_detail", return_value={})):
            with self.assertRaisesRegex(RuntimeError, "Messages list"):
                bcel._read_verified_detail(self.device, row, log=lambda _: None)
        self.device.press.assert_not_called()

    def test_lost_list_during_poll_stops_without_swiping(self):
        with (mock.patch.object(bcel, "connect", return_value=self.device),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "open_messages_tab"),
              mock.patch.object(bcel, "refresh_messages"),
              mock.patch.object(bcel, "_list_rows", return_value=[])):
            with self.assertRaisesRegex(RuntimeError, "Messages list"):
                bcel.poll_messages("test-device", last_ref="existing-ref")
        self.device.swipe_ext.assert_not_called()


class MonitorRestartSafetyTests(unittest.TestCase):
    def make_engine(self, cycle=0):
        eng = engine.Engine.__new__(engine.Engine)
        eng._cycles = {"test-device": cycle}
        eng._slock = threading.RLock()
        eng.settings = {"devices": {"test-device": {"last_ref": "existing-ref"}}}
        patcher = mock.patch.object(engine, "save_settings")
        patcher.start()
        self.addCleanup(patcher.stop)
        eng.log = mock.Mock()
        eng._set_status = mock.Mock()
        eng.set_last_ref = mock.Mock()
        eng._send = mock.Mock(return_value=False)
        return eng

    def run_polls(self, eng, outcomes):
        state = {"active": True}
        flags = []

        def poll(*args, **kwargs):
            flags.append(kwargs["fresh"])
            outcome = outcomes[len(flags) - 1]
            if len(flags) == len(outcomes):
                state["active"] = False
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (mock.patch.object(engine, "device_state", return_value="device"),
              mock.patch.object(engine.bcel, "poll_messages", side_effect=poll),
              mock.patch.object(engine.time, "sleep")):
            eng._loop("test-device", state)
        return flags

    def test_failed_poll_forces_a_clean_restart_next_poll(self):
        eng = self.make_engine()
        flags = self.run_polls(eng, [RuntimeError("failed"), RuntimeError("failed")])
        self.assertEqual(flags, [True, True])
        eng.set_last_ref.assert_not_called()
        eng._send.assert_not_called()

    def test_opaque_message_poll_failure_reopens_immediately(self):
        eng = self.make_engine(cycle=1)
        flags = self.run_polls(
            eng,
            [RuntimeError("WebView failed"), {"last_ref": "existing-ref", "new": []}],
        )
        self.assertEqual(flags, [False, True])
        self.assertIn("closing and reopening BCEL", str(eng.log.call_args_list))

    def test_restart_schedule_still_wraps_after_thirty_attempts(self):
        eng = self.make_engine(cycle=29)
        self.assertEqual(self.run_polls(eng, [RuntimeError("failed")] * 3),
                         [False, True, True])

    def test_transient_send_failure_still_holds_watermark(self):
        eng = self.make_engine()
        self.run_polls(eng, [{"last_ref": "new-ref", "new": [{"ref": "new-ref"}]}])
        eng._send.assert_called_once()
        eng.set_last_ref.assert_not_called()
        self.assertEqual(eng.device_creds("test-device")["last_ref"], "existing-ref")
        self.assertEqual(eng._scan_state("test-device")["scan_ref"], "new-ref")

    def test_successful_send_still_advances_watermark(self):
        eng = self.make_engine()
        eng._send.return_value = True
        self.run_polls(eng, [{"last_ref": "new-ref", "new": [{"ref": "new-ref"}]}])
        self.assertEqual(eng.device_creds("test-device")["last_ref"], "new-ref")

    def test_messages_failure_reopens_once_without_sending_or_advancing(self):
        eng = self.make_engine(cycle=1)
        state = {"active": True}
        error = bcel.MessagesUIError("Messages list unavailable")
        flags = []

        def poll(*args, **kwargs):
            flags.append(kwargs["fresh"])
            if len(flags) == 1:
                raise error
            state["active"] = False
            return {"last_ref": "existing-ref", "new": []}

        with (mock.patch.object(engine, "device_state", return_value="device"),
              mock.patch.object(engine.bcel, "poll_messages", side_effect=poll),
              mock.patch.object(engine.time, "sleep")):
            eng._loop("test-device", state)
        self.assertEqual(flags, [False, True])
        eng._send.assert_not_called()
        eng.set_last_ref.assert_not_called()
        self.assertIn("closing and reopening BCEL", str(eng.log.call_args_list))

    def test_reopened_messages_failure_keeps_monitor_recovering(self):
        eng = self.make_engine()
        state = {"active": True}

        def poll(*args, **kwargs):
            state["active"] = False
            raise bcel.MessagesUIError("still unavailable")

        with (mock.patch.object(engine, "device_state", return_value="device"),
              mock.patch.object(engine.bcel, "poll_messages", side_effect=poll),
              mock.patch.object(engine.time, "sleep")):
            eng._loop("test-device", state)
        eng._set_status.assert_any_call("test-device", "reconnecting")
        self.assertIn("monitor remains active", str(eng.log.call_args_list))
        eng._send.assert_not_called()
        eng.set_last_ref.assert_not_called()


if __name__ == "__main__":
    unittest.main()
