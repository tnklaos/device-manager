import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

import bcel
import engine


def row(ref):
    return {"key": f"row-{ref}", "sig": f"transaction {ref}", "incoming": True}


def record(ref):
    return {"ref": str(ref), "bill_no": str(ref), "amount_in": "50,000 LAK"}


class ScanRangeTests(unittest.TestCase):
    def scan(self, refs, stop, failures=(), start=None, max_scrolls=0):
        rows = [row(ref) for ref in refs]
        reads = []

        def read(d, selected, **kwargs):
            ref = int(selected["sig"].split()[-1])
            reads.append(ref)
            return None if ref in failures else (record(ref), selected["sig"])

        device = mock.Mock()
        with (mock.patch.object(bcel, "_list_rows", return_value=rows),
              mock.patch.object(bcel, "_read_verified_detail", side_effect=read),
              mock.patch.object(bcel.time, "sleep")):
            result = bcel._scan_messages(device, stop, max_scrolls, lambda _: None, start)
        return result, reads, device

    def test_failure_keeps_delivery_boundary_but_records_new_scan_head(self):
        result, reads, _ = self.scan([130, 129, 128, 127], "127", failures=[129])
        self.assertEqual(reads, [130, 129])
        self.assertEqual(result["scan_ref"], "130")
        self.assertEqual(result["last_ref"], "127")
        self.assertEqual(result["pending_scan"], {"last_ref": "127", "start_row": {
            "key": "row-129", "sig": "transaction 129"}})

    def test_next_main_cycle_stops_at_previous_head_without_scrolling(self):
        result, reads, device = self.scan([130, 129, 128, 127], "130")
        self.assertEqual(reads, [130])
        self.assertEqual(result["new"], [])
        device.swipe_ext.assert_not_called()

    def test_retry_skips_verified_prefix_and_reads_unfinished_range_only(self):
        result, reads, _ = self.scan([132, 131, 130, 129, 128, 127], "127", start=row(129))
        self.assertEqual(reads, [129, 128, 127])
        self.assertEqual([r["ref"] for r in result["new"]], ["129", "128"])
        self.assertIsNone(result["pending_scan"])

    def test_retry_failure_does_not_reopen_verified_prefix(self):
        result, reads, _ = self.scan([130, 129, 128, 127], "127", failures=[129], start=row(129))
        self.assertEqual(reads, [129])
        self.assertEqual(result["pending_scan"]["start_row"]["key"], "row-129")

    def test_retry_progress_moves_to_next_failed_row(self):
        result, reads, _ = self.scan([130, 129, 128, 127], "127", failures=[128], start=row(129))
        self.assertEqual(reads, [129, 128])
        self.assertEqual(result["pending_scan"]["start_row"]["key"], "row-128")
        again, reads, _ = self.scan([130, 129, 128, 127], "127", start=result["pending_scan"]["start_row"])
        self.assertEqual(reads, [128, 127])
        self.assertIsNone(again["pending_scan"])

    def test_missing_or_changed_retry_row_is_retained_without_reading_other_details(self):
        for start in (row(999), {**row(129), "sig": "different sender"}):
            with self.subTest(start=start):
                result, reads, _ = self.scan([130, 129, 128], "127", start=start)
                self.assertEqual(reads, [])
                self.assertEqual(result["pending_scan"]["start_row"], start)

    def test_long_retry_range_resumes_after_last_verified_row(self):
        result, reads, _ = self.scan([130, 129, 128], "127", start=row(129))
        self.assertEqual(reads, [129, 128])
        start = result["pending_scan"]["start_row"]
        self.assertEqual(start["key"], "row-128")
        self.assertTrue(start["after"])
        again, reads, _ = self.scan([129, 128, 127], "127", start=start)
        self.assertEqual(reads, [127])
        self.assertIsNone(again["pending_scan"])

    def test_retry_after_anchor_at_scroll_limit_is_not_discarded(self):
        start = {**row(128), "after": True}
        result, reads, _ = self.scan([130, 129, 128], "127", start=start)
        self.assertEqual(reads, [])
        self.assertEqual(result["pending_scan"]["start_row"], start)

    def test_clipped_retry_row_is_not_marked_processed_while_seeking(self):
        pages = [[row(130), {**row(129), "sig": "clipped"}], [row(129), row(128), row(127)]]
        page = [0]
        device = mock.Mock()
        device.swipe_ext.side_effect = lambda *a, **kw: page.__setitem__(0, 1)
        reads = []

        def read(d, selected, **kwargs):
            ref = int(selected["sig"].split()[-1])
            reads.append(ref)
            return record(ref), selected["sig"]

        with (mock.patch.object(bcel, "_list_rows", side_effect=lambda d: pages[page[0]]),
              mock.patch.object(bcel, "_read_verified_detail", side_effect=read),
              mock.patch.object(bcel.time, "sleep")):
            result = bcel._scan_messages(device, "127", 6, lambda _: None, row(129))
        self.assertEqual(reads, [129, 128, 127])
        self.assertIsNone(result["pending_scan"])

    def test_failed_first_baseline_still_sends_nothing(self):
        result, reads, _ = self.scan([130, 129], None, failures=[130])
        self.assertEqual(reads, [130])
        self.assertEqual(result["new"], [])
        self.assertIsNone(result["scan_ref"])

    def test_poll_combines_new_scan_and_pending_retry_without_moving_head_backwards(self):
        task = {"last_ref": "127", "start_row": row(129)}
        main = {"new": [record(131)], "scan_ref": "131", "last_ref": "131", "pending_scan": None}
        retry = {"new": [record(129), record(128)], "scan_ref": "129", "pending_scan": None}
        with (mock.patch.object(bcel, "connect"),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "open_messages_tab"),
              mock.patch.object(bcel, "refresh_messages"),
              mock.patch.object(bcel, "_scroll_messages_to_top", return_value=True),
              mock.patch.object(bcel, "_wait_list_settled", return_value=True),
              mock.patch.object(bcel, "_scan_messages", side_effect=[main, retry])):
            result = bcel.poll_messages("test", "130", pending_scans=[task])
        self.assertEqual(result["scan_ref"], "131")
        self.assertEqual(result["pending_scans"], [])
        self.assertEqual([r["ref"] for r in result["new"]], ["131", "129", "128"])


class DurableScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = os.path.join(temp.name, "settings.json")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(engine, "SETTINGS_FILE", self.path))
        self.stack.enter_context(mock.patch.object(engine, "TRANSACTIONS_FILE", os.path.join(temp.name, "tx.json")))
        self.stack.enter_context(mock.patch.object(engine.threading.Thread, "start"))
        engine.save_settings({"devices": {"test": {"last_ref": "127"}}, "log_retention": "7_days"})
        self.eng = engine.Engine()
        self.eng.log = mock.Mock()
        self.eng._send = mock.Mock(return_value=False)

    def stage(self, head="130", refs=(130, 129, 128), pending=()):
        result = {"scan_ref": head, "last_ref": head, "new": [record(r) for r in refs],
                  "pending_scans": list(pending)}
        self.eng._stage_scan("test", result)

    def test_failed_delivery_advances_only_scan_and_survives_restart(self):
        self.stage()
        self.assertFalse(self.eng._deliver_pending("test"))
        restarted = engine.Engine()
        self.assertEqual(restarted._scan_state("test")["scan_ref"], "130")
        self.assertEqual(restarted.device_creds("test")["last_ref"], "127")
        self.assertEqual([r["ref"] for r in restarted._scan_state("test")["outbox"]], ["130", "129", "128"])
        restarted._send = mock.Mock(return_value=True)
        restarted._deliver_pending("test")
        restarted._send.assert_called_once()
        self.assertEqual(restarted._scan_state("test")["outbox"], [])
        self.assertEqual(restarted.device_creds("test")["last_ref"], "130")

    def test_later_cycle_does_not_lose_or_duplicate_unsent_records(self):
        self.stage()
        self.eng._deliver_pending("test")
        self.stage("131", (131, 130))
        self.assertEqual([r["ref"] for r in self.eng._scan_state("test")["outbox"]], ["130", "129", "128", "131"])

    def test_unverified_range_survives_successful_delivery_and_restart(self):
        task = {"last_ref": "127", "start_row": row(129)}
        self.stage(refs=(130,), pending=[task])
        self.eng._send.return_value = True
        self.eng._deliver_pending("test")
        restarted = engine.Engine()
        self.assertEqual(restarted._scan_state("test")["scan_ref"], "130")
        self.assertEqual(restarted._scan_state("test")["pending_scans"], [task])
        self.assertEqual(restarted.device_creds("test")["last_ref"], "127")
        self.stage(refs=(129, 128))
        self.eng._deliver_pending("test")
        self.assertEqual(self.eng.device_creds("test")["last_ref"], "130")

    def test_checkpoint_write_failure_does_not_advance_memory_or_disk(self):
        before = copy.deepcopy(self.eng.settings)
        with mock.patch.object(engine.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.stage()
        self.assertEqual(self.eng.settings, before)
        self.assertEqual(engine.load_settings(), before)
        self.eng._send.assert_not_called()

    def test_manual_boundary_change_preserves_retries_and_wins_over_inflight_scan(self):
        task = {"last_ref": "127", "start_row": row(129)}
        self.stage(refs=(130,), pending=[task])
        self.eng.set_last_ref("test", "manual")
        self.stage("131", (131,))  # still carries generation 0 from before edit
        state = self.eng._scan_state("test")
        self.assertEqual(state["scan_ref"], "manual")
        self.assertEqual(state["pending_scans"], [task])
        self.assertEqual([r["ref"] for r in state["outbox"]], ["130", "131"])

    def test_baseline_is_persisted_without_delivery(self):
        self.stage(refs=())
        self.eng._deliver_pending("test")
        self.eng._send.assert_not_called()
        self.assertEqual(self.eng.device_creds("test")["last_ref"], "130")

    def test_devices_api_reports_only_safe_checkpoint_summary(self):
        self.stage(pending=[{"last_ref": "127", "start_row": row(129)}])
        self.eng.device_creds("test")["password"] = "private-password"
        with (mock.patch.object(engine, "list_devices", return_value=[{"serial": "test", "state": "device"}]),
              mock.patch.object(self.eng, "_device_connection_status", return_value={})):
            public = self.eng.devices()[0]
        self.assertEqual(public["scan_ref"], "130")
        self.assertEqual(public["last_ref"], "127")
        self.assertEqual(public["pending_delivery"], 3)
        self.assertEqual(public["pending_scans"], 1)
        self.assertNotIn("scan_state", public)
        self.assertNotIn("outbox", public)
        self.assertNotIn("private-password", json.dumps(public))

    def test_main_loop_uses_new_scan_boundary_even_after_failed_send(self):
        monitor = {"active": True}
        calls = []

        def poll(serial, stop, *args, **kwargs):
            calls.append(stop)
            if len(calls) == 2:
                monitor["active"] = False
            return {"scan_ref": "130", "new": [record(130)] if len(calls) == 1 else [], "pending_scans": []}

        with (mock.patch.object(engine, "device_state", return_value="device"),
              mock.patch.object(engine.bcel, "poll_messages", side_effect=poll),
              mock.patch.object(engine.time, "sleep")):
            self.eng._loop("test", monitor)
        self.assertEqual(calls, ["127", "130"])
        self.assertEqual(self.eng._send.call_count, 2)
        self.assertEqual(self.eng.device_creds("test")["last_ref"], "127")

    def test_saved_delivery_retries_even_if_phone_ui_is_unavailable(self):
        self.stage()
        monitor = {"active": True}
        with (mock.patch.object(engine, "device_state", return_value="device"),
              mock.patch.object(engine.bcel, "poll_messages", side_effect=bcel.MessagesUIError("unavailable"))):
            self.eng._loop("test", monitor)
        self.eng._send.assert_called_once()
        self.assertEqual(len(self.eng._scan_state("test")["outbox"]), 3)


if __name__ == "__main__":
    unittest.main()
