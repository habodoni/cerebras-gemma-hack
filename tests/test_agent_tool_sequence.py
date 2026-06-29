import asyncio
import unittest

from ferry import tools
from ferry.clients import Clients


class FakeClients(Clients):
    def __init__(self):
        self.tool_choices = []
        self.posts = 0

    @property
    def has_cerebras_key(self):
        return True

    def _cerebras_payload(self, messages, **extra):
        self.tool_choices.append(extra.get("tool_choice"))
        return {"messages": messages, **extra}

    async def _post_cerebras_completion(self, payload):
        self.posts += 1
        if self.posts == 1:
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "search-1",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"neural network visual style"}',
                            },
                        }],
                    },
                }],
            }
        if self.posts == 2:
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "code-1",
                            "function": {
                                "name": "run_code",
                                "arguments": '{"code":"print(\\"created gif\\")"}',
                            },
                        }],
                    },
                }],
            }
        return {"choices": [{"message": {"content": "done"}}]}


class ParallelToolClients(Clients):
    def __init__(self):
        self.posts = 0

    @property
    def has_cerebras_key(self):
        return True

    def _cerebras_payload(self, messages, **extra):
        return {"messages": messages, **extra}

    async def _post_cerebras_completion(self, payload):
        self.posts += 1
        if self.posts == 1:
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "search-a",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"topic a"}',
                                },
                            },
                            {
                                "id": "search-b",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"topic b"}',
                                },
                            },
                        ],
                    },
                }],
            }
        return {"choices": [{"message": {"content": "done"}}]}


class AgentToolSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_plus_artifact_forces_search_then_run_code(self):
        original_call_tool = tools.call_tool
        calls = []

        async def fake_call_tool(name, arguments, session=None):
            calls.append(name)
            return "ok"

        tools.call_tool = fake_call_tool
        try:
            client = FakeClients()
            messages = [{
                "role": "user",
                "content": "Search the web, then make a GIF of a neural network.",
            }]

            events = [
                event async for event in client.cerebras_agent(messages)
            ]
        finally:
            tools.call_tool = original_call_tool

        self.assertEqual(calls, ["web_search", "run_code"])
        self.assertEqual(
            [choice["function"]["name"] for choice in client.tool_choices[:2]],
            ["web_search", "run_code"],
        )
        self.assertIn(("status", "🔍 searching: neural network visual style"), events)
        self.assertIn(("status", "🐍 running code"), events)
        self.assertIn(("token", "done"), events)

    async def test_single_agent_runs_same_turn_tool_calls_in_parallel(self):
        original_call_tool = tools.call_tool
        active = 0
        max_active = 0
        calls = []

        async def fake_call_tool(name, arguments, session=None):
            nonlocal active, max_active
            calls.append(arguments)
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return name

        tools.call_tool = fake_call_tool
        try:
            client = ParallelToolClients()
            messages = [{
                "role": "user",
                "content": "Search for topic a and topic b.",
            }]

            events = [
                event async for event in client.cerebras_agent(messages)
            ]
        finally:
            tools.call_tool = original_call_tool

        self.assertEqual(len(calls), 2)
        self.assertEqual(max_active, 2)
        self.assertIn(("status", "🔍 searching: topic a"), events)
        self.assertIn(("status", "🔍 searching: topic b"), events)
        self.assertIn(("token", "done"), events)


if __name__ == "__main__":
    unittest.main()
