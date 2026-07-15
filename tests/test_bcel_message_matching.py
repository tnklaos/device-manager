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


class _DateOnlyHierarchyDevice:
    info = {"displayWidth": 1080, "displayHeight": 2400}

    def dump_hierarchy(self):
        return """<hierarchy>
          <node scrollable="true" bounds="[0,200][1080,2100]" />
          <node clickable="true" bounds="[0,300][1080,700]" />
          <node text="TRI" bounds="[20,320][120,380]" />
          <node text="01/07/2026" bounds="[150,320][350,380]" />
          <node text="ຈາກບັນຊີ: ANANH - 02012345678" bounds="[20,430][850,500]" />
          <node text="4,600,000 LAK" bounds="[650,580][1040,650]" />
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


class IncomingClassificationTests(unittest.TestCase):
    def test_sal_card_notice_is_skipped_despite_positive_amount(self):
        sig = "SAL\nMastercard Virtual 5496 28xx xxxx xx93\n49.22 USD"
        self.assertFalse(bcel._row_is_incoming("SAL", sig, positive_amount=True))

    def test_mastercard_notice_is_skipped_even_if_kind_changes(self):
        sig = "ACC\nMastercard Virtual 5496 28xx xxxx xx93\n49.22 USD"
        self.assertFalse(bcel._row_is_incoming("ACC", sig, positive_amount=True))

    def test_regular_positive_acc_remains_incoming(self):
        sig = "ACC\nໄດ້ຮັບເງິນ\n50,000 LAK"
        self.assertTrue(bcel._row_is_incoming("ACC", sig, positive_amount=True))


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

    def test_date_only_row_matches_detail_on_same_date(self):
        date_only_row = self.row.replace("10:15:22", "01/07/2026")
        same_date = {**self.rec, "time": "01/07/2026 00:37:53"}
        self.assertTrue(bcel.detail_matches_row(date_only_row, same_date))

    def test_date_only_row_rejects_detail_on_different_date(self):
        date_only_row = self.row.replace("10:15:22", "01/07/2026")
        different_date = {**self.rec, "time": "02/07/2026 00:37:53"}
        self.assertFalse(bcel.detail_matches_row(date_only_row, different_date))

    def test_row_without_time_or_date_is_rejected(self):
        no_timestamp = self.row.replace("10:15:22\n", "")
        self.assertFalse(bcel.detail_matches_row(no_timestamp, self.rec))

    def test_date_only_row_key_uses_date(self):
        rows = bcel._list_rows(_DateOnlyHierarchyDevice())
        self.assertEqual(rows[0]["key"], "TRI|01/07/2026|4,600,000 LAK")


class MessageDetailNavigationTests(unittest.TestCase):
    def test_titleprev_restores_message_list(self):
        device = mock.Mock()
        state = {"list": False}
        titleprev = mock.Mock()
        titleprev.exists = True
        titleprev.click.side_effect = lambda: state.update(list=True)
        titlecontext = mock.Mock()
        type(titlecontext).exists = mock.PropertyMock(
            side_effect=lambda: state["list"]
        )
        device.xpath.side_effect = lambda query: (
            titleprev if "titleprev" in query else titlecontext
        )

        with (mock.patch.object(
                  bcel, "_list_rows",
                  side_effect=lambda _: [{}] if state["list"] else []),
              mock.patch.object(bcel.time, "sleep")):
            self.assertTrue(bcel.close_message_detail(device))

        titleprev.click.assert_called_once_with()

    def test_verified_detail_is_rejected_when_list_cannot_be_restored(self):
        row = {
            "key": "TRI|10:15:22|50,000 LAK",
            "center": (360, 500),
            "sig": "TRI\n10:15:22\n50,000 LAK",
        }
        rec = {
            "type": "ໄດ້ຮັບເງິນໂອນ",
            "time": "15/07/2026 10:15:22",
            "amount_in": "50,000.00 LAK",
            "ref": "125",
        }
        device = mock.MagicMock()

        with (mock.patch.object(bcel, "_list_rows", return_value=[row]),
              mock.patch.object(
                  bcel, "_extract_message_detail", return_value=rec),
              mock.patch.object(
                  bcel, "detail_matches_row", return_value=True),
              mock.patch.object(
                  bcel, "close_message_detail", return_value=False),
              mock.patch.object(
                  bcel.time, "time", side_effect=[0, 0, 0, 0]),
              mock.patch.object(bcel.time, "sleep")):
            self.assertIsNone(
                bcel._read_verified_detail(device, row, log=lambda _: None)
            )


class MessageListNavigationTests(unittest.TestCase):
    def test_scroll_to_top_is_not_limited_to_three_swipes(self):
        class Device:
            position = 6
            swipes = 0

            def swipe_ext(self, direction, scale=None):
                self.swipes += 1
                self.position = max(0, self.position - 1)

        device = Device()

        def rows(current):
            return [{"key": f"row-{current.position}"}]

        with (mock.patch.object(bcel, "_list_rows", side_effect=rows),
              mock.patch.object(bcel.time, "sleep")):
            self.assertTrue(bcel._scroll_messages_to_top(device))

        self.assertEqual(device.position, 0)
        self.assertGreater(device.swipes, 3)


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

    def test_verified_candidates_send_when_manual_watermark_is_not_found(self):
        row = {
            "key": "TRI|01/07/2026|4,600,000 LAK",
            "sig": "TRI\n01/07/2026\n4,600,000 LAK",
            "incoming": True,
        }
        historical = {
            "type": "ໄດ້ຮັບເງິນໂອນ",
            "ref": "202607011308546",
            "bill_no": "202607011308546",
            "amount_in": "4,600,000.00 LAK",
            "time": "01/07/2026 00:37:53",
            "raw": ["ຈາກບັນຊີ", "ANANH\n02012345678"],
        }
        logs = []

        with (mock.patch.object(bcel, "connect", return_value=object()),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "open_messages_tab"),
              mock.patch.object(bcel, "refresh_messages"),
              mock.patch.object(bcel, "_list_rows", return_value=[row]),
              mock.patch.object(
                  bcel, "_read_verified_detail",
                  return_value=(historical, row["sig"]))):
            result = bcel.poll_messages(
                "device", last_ref="2026071539269477", max_scrolls=0,
                log=logs.append,
            )

        self.assertEqual(
            [transaction["ref"] for transaction in result["new"]],
            ["202607011308546"],
        )
        self.assertEqual(result["last_ref"], "202607011308546")

    def test_first_run_scrolls_past_card_rows_to_find_incoming_baseline(self):
        class Device:
            scrolled = False

            def swipe_ext(self, direction, scale=None):
                self.scrolled = True

        device = Device()
        card = {"key": "SAL||49.22 USD", "sig": "Mastercard", "incoming": False}
        incoming = {
            "key": "TRI|10:15:22|50,000 LAK",
            "sig": "incoming",
            "incoming": True,
        }
        verified = {
            "type": "ໄດ້ຮັບເງິນໂອນ", "ref": "125", "bill_no": "125",
            "amount_in": "50,000.00 LAK", "time": "15/07/2026 10:15:22",
            "raw": ["ຈາກບັນຊີ", "SENDER\n02012345678"],
        }

        def rows(current):
            return [incoming] if current.scrolled else [card]

        with (mock.patch.object(bcel, "connect", return_value=device),
              mock.patch.object(bcel, "by_pass_popup_network_failure"),
              mock.patch.object(bcel, "open_messages_tab"),
              mock.patch.object(bcel, "refresh_messages"),
              mock.patch.object(bcel, "_list_rows", side_effect=rows),
              mock.patch.object(bcel.time, "sleep"),
              mock.patch.object(
                  bcel, "_read_verified_detail",
                  return_value=(verified, incoming["sig"]))):
            result = bcel.poll_messages(
                "device", last_ref=None, max_scrolls=1, log=lambda _: None,
            )

        self.assertTrue(device.scrolled)
        self.assertEqual(result["last_ref"], "125")
        self.assertEqual(result["new"], [])

    def test_first_run_gets_deeper_card_only_search_limit(self):
        self.assertEqual(bcel._poll_scroll_limit(None, 6), 30)
        self.assertEqual(bcel._poll_scroll_limit("existing-ref", 6), 6)


if __name__ == "__main__":
    unittest.main()
