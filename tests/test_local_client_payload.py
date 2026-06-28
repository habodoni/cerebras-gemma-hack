import unittest

from ferry.clients import _compact_local_messages, _local_payload
from ferry.config import settings


class LocalClientPayloadTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "local_context_chars": settings.local_context_chars,
            "local_context_messages": settings.local_context_messages,
            "local_max_tokens": settings.local_max_tokens,
            "local_temperature": settings.local_temperature,
        }

    def tearDown(self):
        for key, value in self.original.items():
            setattr(settings, key, value)

    def test_local_payload_caps_generation(self):
        settings.local_max_tokens = 32
        settings.local_temperature = 0.1

        payload = _local_payload(
            [{"role": "user", "content": "What is 2 plus 2?"}],
            "LiquidAI/lfm2.5-1.2b-instruct",
            stream=True,
        )

        self.assertEqual(payload["model"], "LiquidAI/lfm2.5-1.2b-instruct")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_tokens"], 32)
        self.assertEqual(payload["temperature"], 0.1)

    def test_local_context_keeps_system_and_recent_turns(self):
        settings.local_context_messages = 2
        messages = [
            {"role": "system", "content": "Use concise answers."},
            {"role": "user", "content": "Old question that should be dropped"},
            {"role": "assistant", "content": "Old answer that should stay"},
            {"role": "user", "content": "Current question"},
        ]

        compacted = _compact_local_messages(messages)

        self.assertEqual(
            [msg["role"] for msg in compacted],
            ["system", "assistant", "user"],
        )
        all_text = "\n".join(msg["content"] for msg in compacted)
        self.assertIn("Use concise answers.", all_text)
        self.assertIn("Old answer that should stay", all_text)
        self.assertIn("Current question", all_text)
        self.assertNotIn("Old question that should be dropped", all_text)

    def test_local_context_trims_long_content(self):
        settings.local_context_messages = 1
        settings.local_context_chars = 200
        prompt = ("a" * 250) + "tail"

        compacted = _compact_local_messages([{"role": "user", "content": prompt}])

        self.assertEqual(len(compacted), 1)
        self.assertTrue(compacted[0]["content"].startswith("[truncated]\n"))
        self.assertTrue(compacted[0]["content"].endswith("tail"))
        self.assertLess(len(compacted[0]["content"]), len(prompt))


if __name__ == "__main__":
    unittest.main()
