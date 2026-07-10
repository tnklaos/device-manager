import unittest
from unittest import mock

import bcel


class _HierarchyDevice:
    info = {"displayWidth": 1080, "displayHeight": 2400}

    def dump_hierarchy(self):
        return """<hierarchy>
          <node scrollable="true" bounds="[0,200][1080,2100]" />
          <node clickable="true" bounds="[0,300][1080,700]" />
          <node text="TRI" bounds="[20,320][120,380]" />
          <node text="10:15:22" bounds="[150,320][350,380]" />
          <node text="ຈາກບັນຊີ: PHON - 02012345678" bounds="[20,430][850,500]" />
          <node text="50,000 LAK" bounds="[700,580][1040,650]" />
        </hierarchy>"""


class DetailSourceTests(unittest.TestCase):
    def test_list_snapshot_does_not_invent_qr_pipe_delimiters(self):
        rows = bcel._list_rows(_HierarchyDevice())
        self.assertEqual(len(rows), 1)
        self.assertNotIn("|", rows[0]["sig"])
        self.assertEqual(bcel.row_source(rows[0]["sig"]), ("02012345678", "PHON"))

    def test_regular_list_row_is_not_misread_as_pipe_statement(self):
        row = (
            "TRI\n10:15:22\nໄດ້ຮັບເງິນໂອນ\n"
            "ຈາກບັນຊີ: PHON - 02012345678\n50,000 LAK"
        )
        self.assertEqual(bcel.row_source(row), ("02012345678", "PHON"))

    def test_regular_transfer_source_is_label_anchored(self):
        rec = {"raw": [
            "MAIN", "BCEL One", "OneBank", "MESSAGE",
            "ຈາກບັນຊີ", "PHON\n02012345678", "ຈຳນວນເງິນ", "50,000.00 LAK",
        ]}
        self.assertEqual(bcel.detail_source(rec), ("02012345678", "PHON"))

    def test_pipe_transfer_source(self):
        rec = {"raw": [
            "LMPS QR TRANSFER IN|BANK|02012345678|BCEL|OWN|PHON|extra",
        ]}
        self.assertEqual(bcel.detail_source(rec), ("02012345678", "PHON"))


class DetailMatchTests(unittest.TestCase):
    def setUp(self):
        self.row = (
            "TRI\n10:15:22\nຈາກບັນຊີ: PHON - 02012345678\n50,000 LAK"
        )
        self.rec = {
            "type": "ໄດ້ຮັບເງິນໂອນ",
            "time": "10/07/2026 10:15:22",
            "bill_no": "125",
            "amount_in": "50,000.00 LAK",
            "raw": ["ຈາກບັນຊີ", "PHON\n02012345678"],
        }

    def test_matching_transaction_is_accepted(self):
        self.assertTrue(bcel.detail_matches_row(self.row, self.rec))

    def test_previous_sender_name_is_rejected(self):
        mixed = {**self.rec, "raw": ["ຈາກບັນຊີ", "PHOU\n02099999999"]}
        self.assertFalse(bcel.detail_matches_row(self.row, mixed))

    def test_previous_sender_name_is_rejected_even_for_same_account(self):
        mixed = {**self.rec, "raw": ["ຈາກບັນຊີ", "PHOU\n02012345678"]}
        self.assertFalse(bcel.detail_matches_row(self.row, mixed))

    def test_missing_detail_time_is_rejected(self):
        incomplete = {**self.rec, "time": ""}
        self.assertFalse(bcel.detail_matches_row(self.row, incomplete))

    def test_different_amount_is_rejected(self):
        wrong = {**self.rec, "amount_in": "100,000.00 LAK"}
        self.assertFalse(bcel.detail_matches_row(self.row, wrong))

    def test_masked_account_does_not_create_false_conflict(self):
        masked_row = self.row.replace("02012345678", "xxx-x-5678")
        self.assertTrue(bcel.detail_matches_row(masked_row, self.rec))


class WatermarkSafetyTests(unittest.TestCase):
    def test_unverified_incoming_row_holds_old_watermark(self):
        rows = [
            {"key": "TRI|10:15:23|60,000 LAK", "sig": "newest", "incoming": True},
            {"key": "TRI|10:15:22|50,000 LAK", "sig": "blocked", "incoming": True},
        ]
        verified = {
            "type": "ໄດ້ຮັບເງິນໂອນ", "ref": "126", "bill_no": "126",
            "amount_in": "60,000.00 LAK", "time": "10/07/2026 10:15:23",
            "raw": ["ຈາກບັນຊີ", "SENDER\n02012345678"],
        }

        with (mock.patch.object(bcel, "connect", return_value=object()),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "open_messages_tab"),
              mock.patch.object(bcel, "refresh_messages"),
              mock.patch.object(bcel, "_list_rows", return_value=rows),
              mock.patch.object(
                  bcel, "_read_verified_detail",
                  side_effect=[(verified, rows[0]["sig"]), None])):
            result = bcel.poll_messages("device", last_ref="125", log=lambda _: None)

        self.assertEqual([t["ref"] for t in result["new"]], ["126"])
        self.assertEqual(result["last_ref"], "125")


if __name__ == "__main__":
    unittest.main()
