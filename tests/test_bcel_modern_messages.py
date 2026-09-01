import unittest
import re
from unittest import mock

import bcel


# Synthetic values, with the nesting/attributes observed on the updated phone.
LIST = '''<node resource-id="messageframe" bounds="[0,222][1080,2114]">
  <node resource-id="pagecontainer"><node><node>
    <node class="com.google.android.material.navigation.NavigationView">
      <node text="ຂໍ້ຄວາມ"/><node content-desc="Context Icon"/>
    </node>
    <node scrollable="true" bounds="[0,376][1080,2114]">
      <node clickable="false" bounds="[0,376][1080,750]">
        <node text="TRI"/><node text="ໄດ້ຮັບເງິນໂອນ"/>
        <node text="50,000.00 LAK"/><node text="Account 010-12-xxxxx123"/>
        <node text="ຈາກບັນຊີ: PHON - 02012345678"/>
        <node text="31/08/2026"/><node text="10:15:22"/>
      </node>
      <node clickable="false" bounds="[0,748][1080,1119]">
        <node text="TRI"/><node text="ໄດ້ຮັບເງິນໂອນ"/>
        <node text="100,000.00 LAK"/><node text="Account 010-12-xxxxx123"/>
        <node text="ຈາກບັນຊີ: PHOU - 02099999999"/>
        <node text="31/08/2026"/><node text="10:14:22"/>
      </node>
    </node>
  </node>{detail}</node></node>
</node>'''
DETAIL = '''<node><node>
  <node class="com.google.android.material.navigation.NavigationView">
    <node content-desc="Close"/><node text="ໄດ້ຮັບເງິນໂອນ"/>
  </node>
  <node text="TRI"/><node text="Account 010-12-xxxxx123"/>
  <node text="31/08/2026 10:15:22"/>
  <node text="ຈາກບັນຊີ"/><node text="PHON&#10;02012345678"/>
  <node text="ລາຍລະອຽດ"/><node text="Transfer"/>
  <node text="ເລກໃບບິນ"/><node text="20260831000125"/>
  <node text="ເງິນເຂົ້າ"/><node text="50,000.00 LAK"/>
</node></node>'''
PIPE = 'LMPS QR TRANSFER IN|001|02012345678|BCEL|0101234567890|PHON|QR PAYMENT|31/08/2026 10:15:22'
QR_DETAIL = DETAIL.replace('TRI', 'ACC').replace('ໄດ້ຮັບເງິນໂອນ', 'ໄດ້ຮັບເງິນ').replace(
    '<node text="ຈາກບັນຊີ"/><node text="PHON&#10;02012345678"/>\n'
    '  <node text="ລາຍລະອຽດ"/><node text="Transfer"/>',
    '<node text="' + PIPE + '"/>')


def device(detail=""):
    d = mock.Mock()
    d.info = {"displayWidth": 1080, "displayHeight": 2408}
    d.dump_hierarchy.return_value = '<hierarchy>' + LIST.format(detail=detail) + '</hierarchy>'
    d.xpath.return_value.exists = False
    d.xpath.return_value.all.return_value = []
    d.app_current.return_value = {"package": bcel.PKG}
    return d


def scaled_device(width, height, detail=""):
    xml = LIST.format(detail=detail)

    def scale(match):
        x1, y1, x2, y2 = map(int, match.groups())
        return (f'[{round(x1 * width / 1080)},{round(y1 * height / 2408)}]'
                f'[{round(x2 * width / 1080)},{round(y2 * height / 2408)}]')

    xml = re.sub(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', scale, xml)
    d = device(detail)
    d.info = {"displayWidth": width, "displayHeight": height}
    d.dump_hierarchy.return_value = '<hierarchy>' + xml + '</hierarchy>'
    return d


class ModernMessagesTests(unittest.TestCase):
    def test_rows_and_semantic_targets_scale_across_phone_resolutions(self):
        for width, height in ((480, 800), (720, 1600), (1080, 2408),
                              (1440, 3200), (1600, 2560)):
            with self.subTest(size=(width, height)):
                rows = bcel._list_rows(scaled_device(width, height))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["key"], "TRI|10:15:22|50,000.00 LAK")
                self.assertEqual(rows[0]["xpath"],
                                 '(//*[@resource-id="messageframe"]//*[@scrollable="true"])[1]/*[1]')
                self.assertTrue(all(0 <= row["center"][0] <= width for row in rows))
                self.assertTrue(all(0 <= row["center"][1] <= height for row in rows))

    def test_detail_extraction_is_independent_of_screen_dimensions(self):
        for width, height in ((480, 800), (720, 1600), (1440, 3200)):
            with self.subTest(size=(width, height)):
                rec = bcel._extract_message_detail(scaled_device(width, height, DETAIL))
                self.assertEqual(rec["ref"], "20260831000125")
                self.assertEqual(rec["amount_in"], "50,000.00 LAK")

    def test_reads_semantic_rows_without_legacy_clickable_attribute(self):
        rows = bcel._list_rows(device())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["key"], "TRI|10:15:22|50,000.00 LAK")
        self.assertIn("xpath", rows[0])
        self.assertNotIn("PHOU", rows[0]["sig"])

    def test_rendered_list_is_visible_without_titlecontext(self):
        self.assertTrue(bcel._message_list_visible(device()))

    def test_background_list_is_not_visible_or_readable_behind_detail(self):
        d = device(DETAIL)
        self.assertFalse(bcel._message_list_visible(d))
        self.assertEqual(bcel._list_rows(d), [])
        self.assertTrue(bcel._message_detail_visible(d))

    def test_detail_fields_come_only_from_open_panel(self):
        rec = bcel._extract_message_detail(device(DETAIL))
        self.assertEqual(rec["kind"], "TRI")
        self.assertEqual(rec["type"], "ໄດ້ຮັບເງິນໂອນ")
        self.assertEqual(rec["ref"], "20260831000125")
        self.assertEqual(rec["time"], "31/08/2026 10:15:22")
        self.assertEqual(rec["amount_in"], "50,000.00 LAK")
        self.assertEqual(bcel.detail_source(rec), ("02012345678", "PHON"))
        self.assertNotIn("PHOU", str(rec))
        self.assertNotIn("100,000", str(rec))

    def test_gateway_raw_shape_is_preserved(self):
        rec = bcel._extract_message_detail(device(DETAIL))
        self.assertEqual(rec["raw"][2], "OneBank")
        self.assertEqual(rec["raw"][4], "ຈາກບັນຊີ")
        self.assertEqual(rec["raw"][5], "PHON\n02012345678")
        self.assertEqual(rec["raw"][9], "010-12-xxxxx123")

    def test_list_only_never_yields_transaction_detail(self):
        rec = bcel._extract_message_detail(device())
        self.assertFalse(rec.get("type"))
        self.assertFalse(rec.get("bill_no"))

    def test_incomplete_detail_never_gets_fallback_reference(self):
        partial = DETAIL.replace('<node text="20260831000125"/>', '')
        rec = bcel._extract_message_detail(device(partial))
        self.assertFalse(rec.get("ref"))
        self.assertFalse(rec.get("type"))

    def test_modern_detail_still_rejects_previous_sender(self):
        row = bcel._list_rows(device())[0]
        rec = bcel._extract_message_detail(device(DETAIL.replace('PHON', 'PHOU')))
        self.assertFalse(bcel.detail_matches_row(row["sig"], rec))

    def test_qr_credit_without_from_account_label_is_complete(self):
        rec = bcel._extract_message_detail(device(QR_DETAIL))
        self.assertEqual(rec['kind'], 'ACC')
        self.assertEqual(rec['ref'], '20260831000125')
        self.assertEqual(rec['raw'][4], PIPE)
        self.assertEqual(rec['raw'][9], '010-12-xxxxx123')
        self.assertEqual(bcel.detail_source(rec), ('02012345678', 'PHON'))
        self.assertEqual(rec['details'], PIPE)

    def test_short_qr_statement_is_rejected(self):
        rec = bcel._extract_message_detail(device(QR_DETAIL.replace(PIPE, 'LMPS QR TRANSFER IN|001')))
        self.assertFalse(rec.get('ref'))

    def test_usd_and_thb_details_keep_their_currency(self):
        for currency in ('USD', 'THB'):
            with self.subTest(currency=currency):
                d = device(DETAIL.replace('LAK', currency))
                rec = bcel._extract_message_detail(d)
                self.assertEqual(rec['amount_in'], '50,000.00 ' + currency)

    def test_named_close_returns_to_list_without_back(self):
        d = device(DETAIL)
        d.return_value.click.side_effect = lambda **kw: setattr(
            d.dump_hierarchy, 'return_value', device().dump_hierarchy.return_value)
        with mock.patch.object(bcel.time, 'sleep'):
            self.assertTrue(bcel.close_message_detail(d))
        d.assert_called_with(description="Close")
        d.press.assert_not_called()


if __name__ == '__main__':
    unittest.main()
