import os
import unittest

from ferry.config import Settings


class ExtraModelsTests(unittest.TestCase):
    def test_default_exposes_ferry_then_local_model(self):
        os.environ.pop("EXTRA_LOCAL_MODELS", None)
        s = Settings()
        self.assertEqual(s.service_models, ["ferry", s.local_model])

    def test_extra_models_join_the_picker(self):
        os.environ["EXTRA_LOCAL_MODELS"] = "nemotron-3-nano:4b, another:1b"
        try:
            s = Settings()
            self.assertEqual(
                s.service_models,
                ["ferry", s.local_model, "nemotron-3-nano:4b", "another:1b"],
            )
        finally:
            del os.environ["EXTRA_LOCAL_MODELS"]

    def test_picker_deduplicates(self):
        os.environ["EXTRA_LOCAL_MODELS"] = "nemotron-3-nano:4b,nemotron-3-nano:4b"
        try:
            s = Settings()
            self.assertEqual(
                s.service_models, ["ferry", s.local_model, "nemotron-3-nano:4b"]
            )
        finally:
            del os.environ["EXTRA_LOCAL_MODELS"]

    def test_blank_entries_are_dropped(self):
        os.environ["EXTRA_LOCAL_MODELS"] = " ,,nemotron-3-nano:4b,"
        try:
            self.assertEqual(
                Settings().extra_local_models, ["nemotron-3-nano:4b"]
            )
        finally:
            del os.environ["EXTRA_LOCAL_MODELS"]
