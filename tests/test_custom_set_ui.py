import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CustomSetUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "electron", "renderer", "index.html")) as file:
            cls.html = file.read()
        with open(os.path.join(ROOT, "electron", "renderer", "app.js")) as file:
            cls.js = file.read()

    def test_custom_set_creation_is_hidden_but_editor_fields_remain(self):
        self.assertNotIn("new-custom", self.js)
        self.assertNotIn('data-set="new-custom"', self.js)
        self.assertNotIn("＋ Custom Set", self.js)
        for token in ("set-header", "set-api-key", "set-callback"):
            with self.subTest(token=token):
                self.assertIn(token, self.html + self.js)

    def test_custom_save_posts_custom_type_and_returns_before_webhook(self):
        save_handler = self.js.split('$("#set-save").onclick', 1)[1].split(
            '$("#set-delete").onclick', 1
        )[0]
        self.assertIn('type: setType', save_handler)
        self.assertIn('if (setType === "custom")', save_handler)
        custom_branch = save_handler.split('if (setType === "custom")', 1)[1]
        self.assertIn("return;", custom_branch)
        self.assertLess(save_handler.index('if (setType === "custom")'),
                        save_handler.index('/webhook'))

    def test_gateway_save_still_verifies_credentials(self):
        self.assertIn('post(`/api/sets/${activeSetId}/webhook`)', self.js)
        self.assertIn("credentials verified", self.js)


if __name__ == "__main__":
    unittest.main()
