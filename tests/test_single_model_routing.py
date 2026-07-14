import unittest

from ferry import router
from ferry.config import settings
from ferry.drainer import _cloud_mode


class DummyClients:
    async def ollama_complete(self, messages, model):
        return "SINGLE_AGENT"


class SingleModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clients = DummyClients()

    def messages(self, prompt):
        return [{"role": "user", "content": prompt}]

    def test_openai_surface_matches_configuration(self):
        # "ferry" (auto-routing) leads when exposed; the local model and
        # passthrough extras follow as direct picker entries (deduplicated).
        expected = (["ferry"] if settings.expose_router_model else []) + [
            settings.local_model,
            *settings.extra_local_models,
        ]
        self.assertEqual(settings.service_models, list(dict.fromkeys(expected)))

    async def test_short_prompt_routes_local(self):
        route, reason = await router.decide(
            self.clients,
            self.messages("What is 17 times 4?"),
        )

        self.assertEqual(route, "local")
        self.assertIn("short/simple", reason)

    async def test_long_output_prompt_routes_cloud(self):
        route, reason = await router.decide(
            self.clients,
            self.messages("Say the word hi exactly 200 times."),
        )

        self.assertEqual(route, "cloud")
        self.assertIn("repetition", reason)

    async def test_search_prompt_routes_single_agent(self):
        messages = self.messages("Search the web for current Cerebras Gemma 4 info")

        route, _ = await router.decide(self.clients, messages)
        mode, reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        self.assertEqual(mode, "single_agent")
        self.assertIn("search", reason)

    async def test_pptx_prompt_routes_single_agent(self):
        messages = self.messages("Create a PowerPoint profile deck as a PPTX file.")

        route, reason = await router.decide(self.clients, messages)
        mode, mode_reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        self.assertIn("pptx", reason)
        self.assertEqual(mode, "single_agent")
        self.assertIn("pptx", mode_reason)

    async def test_gif_prompt_routes_single_agent(self):
        messages = self.messages("Make a GIF of a neural network.")

        route, reason = await router.decide(self.clients, messages)
        mode, mode_reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        self.assertIn("gif", reason)
        self.assertEqual(mode, "single_agent")
        self.assertIn("gif", mode_reason)

    async def test_cerebras_prompt_routes_single_agent(self):
        messages = self.messages("Use Cerebras to answer this in one sentence.")

        route, reason = await router.decide(self.clients, messages)
        mode, mode_reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        # "Use Cerebras" is now an explicit user route override.
        self.assertIn("override", reason)
        self.assertEqual(mode, "single_agent")
        self.assertIn("cerebras", mode_reason)

    async def test_broad_prompt_routes_multi_agent(self):
        messages = self.messages(
            "Research, compare, compute, and recommend an architecture for a "
            "flaky-network AI gateway"
        )

        route, _ = await router.decide(self.clients, messages)
        mode, reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        self.assertEqual(mode, "multi_agent")
        self.assertIn("research", reason)

    async def test_analysis_comparison_routes_multi_agent(self):
        messages = self.messages(
            "Write a detailed analysis comparing Raft and Paxos consensus."
        )

        route, _ = await router.decide(self.clients, messages)
        mode, reason = await router.decide_cloud_mode(self.clients, messages)

        self.assertEqual(route, "cloud")
        self.assertEqual(mode, "multi_agent")
        self.assertIn("comparing", reason)

    def test_drainer_uses_stored_route_for_agent_mode(self):
        self.assertEqual(
            _cloud_mode({"route": "multi_agent: broad research"}),
            "multi_agent",
        )
        self.assertEqual(
            _cloud_mode({"route": "single_agent: current facts"}),
            "single_agent",
        )
        self.assertEqual(_cloud_mode({"route": ""}), "single_agent")


if __name__ == "__main__":
    unittest.main()
