import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest import mock

import engine


class LogRetentionTests(unittest.TestCase):
    def make_engine(self, retention=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings_file = os.path.join(self.tmp.name, "settings.json")
        transactions_file = os.path.join(self.tmp.name, "transactions.json")
        settings = {} if retention is None else {"log_retention": retention}
        with open(settings_file, "w") as f:
            json.dump(settings, f)
        now = time.time()
        with open(transactions_file, "w") as f:
            json.dump([
                {"ref": "old", "synced_at": now - 2 * 86400},
                {"ref": "new", "synced_at": now - 60},
            ], f)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(engine, "SETTINGS_FILE", settings_file))
        stack.enter_context(mock.patch.object(engine, "TRANSACTIONS_FILE", transactions_file))
        stack.enter_context(mock.patch.object(engine.threading.Thread, "start"))
        return engine.Engine(), settings_file, transactions_file

    def test_default_is_one_day_and_old_logs_are_pruned(self):
        eng, settings_file, _ = self.make_engine()
        self.assertEqual(eng.get_settings()["log_retention"], "1_day")
        self.assertEqual([t["ref"] for t in eng.transactions_list()], ["new"])
        with open(settings_file) as f:
            self.assertEqual(json.load(f)["log_retention"], "1_day")

    def test_continue_keeps_old_logs(self):
        eng, _, _ = self.make_engine("continue")
        self.assertEqual([t["ref"] for t in eng.transactions_list()], ["old", "new"])

    def test_shortening_retention_prunes_and_persists_immediately(self):
        eng, _, transactions_file = self.make_engine("continue")
        result = eng.set_log_retention("1_day")
        self.assertEqual(result["removed"], 1)
        with open(transactions_file) as f:
            self.assertEqual([t["ref"] for t in json.load(f)], ["new"])

    def test_legacy_bank_time_is_used_when_synced_at_is_missing(self):
        old = {"time": "13/06/2026 00:12:49"}
        self.assertLess(engine.transaction_log_timestamp(old), time.time() - 86400)

    def test_bounded_retention_removes_log_with_unknown_age(self):
        eng, _, transactions_file = self.make_engine("continue")
        eng.transactions.append({"ref": "unknown", "time": ""})
        result = eng.set_log_retention("1_day")
        self.assertEqual(result["removed"], 2)
        with open(transactions_file) as f:
            self.assertEqual([t["ref"] for t in json.load(f)], ["new"])


if __name__ == "__main__":
    unittest.main()
