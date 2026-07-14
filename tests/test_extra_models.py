import os
import unittest

from ferry.config import Settings


class ExtraModelsTests(unittest.TestCase):
    def test_default_exposes_only_ferry(self):
        os.environ.pop("EXTRA_LOCAL_MODELS", None)
        self.assertEqual(Settings().service_models, ["ferry"])

    def test_extra_models_join_the_picker(self):
        os.environ["EXTRA_LOCAL_MODELS"] = "nemotron-3-nano:4b, another:1b"
        try:
            s = Settings()
            self.assertEqual(
                s.service_models, ["ferry", "nemotron-3-nano:4b", "another:1b"]
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
