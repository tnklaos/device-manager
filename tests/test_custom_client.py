import unittest
from unittest import mock

import requests

import custom_client


class CustomClientTests(unittest.TestCase):
    def transaction(self):
        return {
            "serial": "R8YL10AHMTK",
            "type": "TRI",
            "kind": "transfer",
            "from_account": "123456789",
            "from_name": "PHON",
            "account": "987654321",
            "details": "Transfer received",
            "bill_no": "20260717001",
            "amount": "50,000 LAK",
            "time": "17/07/2026 09:30:00",
            "raw": ["must", "not", "leak"],
        }

    @mock.patch("custom_client.requests.post")
    def test_posts_only_normalized_fields_with_configured_header(self, post):
        post.return_value.status_code = 201
        post.return_value.text = "created"

        result = custom_client.post_transactions(
            "https://client.example/callback",
            "X-API-Key",
            "secret-value",
            [self.transaction()],
        )

        self.assertEqual(result, (True, "Custom callback accepted [201]", False))
        post.assert_called_once_with(
            "https://client.example/callback",
            json={
                "transactions": [{
                    "serial": "R8YL10AHMTK",
                    "type": "TRI",
                    "kind": "transfer",
                    "from_account": "123456789",
                    "from_name": "PHON",
                    "to_account": "987654321",
                    "details": "Transfer received",
                    "ref": "20260717001",
                    "amount_in": "50,000 LAK",
                    "time": "17/07/2026 09:30:00",
                }]
            },
            headers={"Content-Type": "application/json", "X-API-Key": "secret-value"},
            timeout=15,
        )
        headers = post.call_args.kwargs["headers"]
        self.assertNotIn("client-id", headers)
        self.assertNotIn("hash-signature", headers)

    @mock.patch("custom_client.requests.post")
    def test_4xx_is_permanent(self, post):
        post.return_value.status_code = 401
        post.return_value.text = "invalid api key"

        ok, message, transient = custom_client.post_transactions(
            "https://client.example/callback", "Authorization", "key", []
        )

        self.assertFalse(ok)
        self.assertIn("[401]", message)
        self.assertFalse(transient)

    @mock.patch("custom_client.requests.post")
    def test_5xx_is_transient(self, post):
        post.return_value.status_code = 503
        post.return_value.text = "unavailable"

        ok, message, transient = custom_client.post_transactions(
            "https://client.example/callback", "Authorization", "key", []
        )

        self.assertFalse(ok)
        self.assertIn("[503]", message)
        self.assertTrue(transient)

    @mock.patch("custom_client.requests.post")
    def test_request_error_is_transient(self, post):
        post.side_effect = requests.exceptions.Timeout("slow")

        ok, message, transient = custom_client.post_transactions(
            "https://client.example/callback", "Authorization", "key", []
        )

        self.assertFalse(ok)
        self.assertIn("timed out", message.lower())
        self.assertTrue(transient)


if __name__ == "__main__":
    unittest.main()
