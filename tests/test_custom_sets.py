import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

import engine


class CustomSetTests(unittest.TestCase):
    def make_engine(self, settings=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings_file = os.path.join(tmp.name, "settings.json")
        transactions_file = os.path.join(tmp.name, "transactions.json")
        with open(settings_file, "w") as file:
            json.dump(settings or {}, file)
        with open(transactions_file, "w") as file:
            json.dump([], file)

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(engine, "SETTINGS_FILE", settings_file))
        stack.enter_context(mock.patch.object(engine, "TRANSACTIONS_FILE", transactions_file))
        stack.enter_context(mock.patch.object(engine.threading.Thread, "start"))
        return engine.Engine()

    def test_legacy_gateway_set_gets_type_and_redacts_secret(self):
        eng = self.make_engine({
            "sets": {
                "legacy": {
                    "name": "CSLDOME",
                    "client_id": "client",
                    "api_key": "secret",
                    "api_url": "",
                }
            }
        })

        result = eng.sets()[0]

        self.assertEqual(result["type"], "gateway")
        self.assertTrue(result["has_secret"])
        self.assertNotIn("api_key", result)
        self.assertEqual(result["client_id"], "client")

    def test_custom_set_is_redacted_and_blank_secret_is_preserved(self):
        eng = self.make_engine()
        set_id = eng.save_set(
            "", "Partner", "", "first-key", "",
            set_type="custom", header="X-API-Key",
            callback_url="https://partner.example/transactions",
        )

        result = eng.sets()[0]
        self.assertEqual(result, {
            "id": set_id,
            "name": "Partner",
            "type": "custom",
            "header": "X-API-Key",
            "callback_url": "https://partner.example/transactions",
            "has_secret": True,
        })
        self.assertNotIn("api_key", result)

        eng.save_set(
            set_id, "Partner renamed", "", "", "",
            set_type="custom", header="Authorization",
            callback_url="https://partner.example/v2/transactions",
        )
        self.assertEqual(eng.settings["sets"][set_id]["api_key"], "first-key")
        self.assertEqual(eng.settings["sets"][set_id]["name"], "Partner renamed")

    def test_custom_set_validation_is_local_and_requires_valid_fields(self):
        eng = self.make_engine()
        invalid = [
            {"name": "", "header": "X-Key", "url": "https://partner.example/cb", "key": "k"},
            {"name": "Partner", "header": "Bad Header", "url": "https://partner.example/cb", "key": "k"},
            {"name": "Partner", "header": "X-Key", "url": "ftp://partner.example/cb", "key": "k"},
            {"name": "Partner", "header": "X-Key", "url": "https:///missing-host", "key": "k"},
            {"name": "Partner", "header": "X-Key", "url": "https://partner.example/cb", "key": ""},
        ]
        with mock.patch.object(engine.csl_client, "setup_webhook") as setup:
            for item in invalid:
                with self.subTest(item=item), self.assertRaises(ValueError):
                    eng.save_set(
                        "", item["name"], "", item["key"], "",
                        set_type="custom", header=item["header"],
                        callback_url=item["url"],
                    )
            setup.assert_not_called()

    def test_custom_set_never_registers_gateway_webhook(self):
        eng = self.make_engine({
            "sets": {
                "custom": {
                    "name": "Partner",
                    "type": "custom",
                    "header": "X-Key",
                    "api_key": "secret",
                    "callback_url": "https://partner.example/cb",
                }
            }
        })

        with mock.patch.object(engine.csl_client, "setup_webhook") as setup:
            result = eng.setup_set_webhook("custom")

        self.assertFalse(result["ok"])
        self.assertIn("do not use", result["message"])
        setup.assert_not_called()

    def test_sets_api_returns_validation_error_without_verification(self):
        eng = self.make_engine()
        import server

        with mock.patch.object(server, "eng", eng), mock.patch.object(
            engine.csl_client, "setup_webhook"
        ) as setup:
            response = server.app.test_client().post("/api/sets", json={
                "type": "custom",
                "name": "Partner",
                "header": "Bad Header",
                "secret_key": "secret",
                "callback_url": "https://partner.example/cb",
            })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])
        setup.assert_not_called()

    def test_send_dispatches_custom_payload_without_gateway_signing(self):
        eng = self.make_engine({
            "sets": {
                "custom": {
                    "name": "Partner",
                    "type": "custom",
                    "header": "X-Partner-Key",
                    "api_key": "secret",
                    "callback_url": "https://partner.example/cb",
                }
            },
            "devices": {"SERIAL-1": {"set": "custom"}},
        })
        transaction = {
            "type": "TRI",
            "kind": "transfer",
            "from_account": "111222333",
            "from_name": "PHON",
            "account": "999888777",
            "details": "Transfer in",
            "ref": "20260717002",
            "amount_in": "50,000 LAK",
            "time": "17/07/2026 09:45:00",
        }

        with mock.patch.object(
            engine.custom_client, "post_transactions",
            return_value=(True, "accepted", False),
        ) as custom_post, mock.patch.object(
            engine.csl_client, "post_transactions"
        ) as gateway_post:
            result = eng._send("SERIAL-1", [transaction])

        self.assertTrue(result)
        gateway_post.assert_not_called()
        custom_post.assert_called_once()
        args, kwargs = custom_post.call_args
        self.assertEqual(args[:3], (
            "https://partner.example/cb", "X-Partner-Key", "secret"
        ))
        sent = args[3][0]
        self.assertEqual(sent["serial"], "SERIAL-1")
        self.assertEqual(sent["from_account"], "111222333")
        self.assertEqual(sent["from_name"], "PHON")
        self.assertEqual(sent["to_account"], "999888777")
        self.assertEqual(kwargs["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
