import os
import unittest

from ferry.config import Settings


def _set(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class ExtraModelsTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("EXTRA_LOCAL_MODELS", "EXPOSE_ROUTER_MODEL")
        }

    def tearDown(self):
        _set(**self._saved)

    def test_default_exposes_ferry_then_local_model(self):
        _set(EXTRA_LOCAL_MODELS=None, EXPOSE_ROUTER_MODEL="true")
        s = Settings()
        self.assertEqual(s.service_models, ["ferry", s.local_model])

    def test_extra_models_join_the_picker(self):
        _set(
            EXTRA_LOCAL_MODELS="nemotron-3-nano:4b, another:1b",
            EXPOSE_ROUTER_MODEL="true",
        )
        s = Settings()
        self.assertEqual(
            s.service_models,
            ["ferry", s.local_model, "nemotron-3-nano:4b", "another:1b"],
        )

    def test_picker_deduplicates(self):
        _set(
            EXTRA_LOCAL_MODELS="nemotron-3-nano:4b,nemotron-3-nano:4b",
            EXPOSE_ROUTER_MODEL="true",
        )
        s = Settings()
        self.assertEqual(
            s.service_models, ["ferry", s.local_model, "nemotron-3-nano:4b"]
        )

    def test_router_model_can_be_hidden(self):
        _set(
            EXTRA_LOCAL_MODELS="nemotron-3-nano:4b",
            EXPOSE_ROUTER_MODEL="false",
        )
        s = Settings()
        self.assertEqual(s.service_models, [s.local_model, "nemotron-3-nano:4b"])

    def test_blank_entries_are_dropped(self):
        _set(EXTRA_LOCAL_MODELS=" ,,nemotron-3-nano:4b,")
        self.assertEqual(Settings().extra_local_models, ["nemotron-3-nano:4b"])
