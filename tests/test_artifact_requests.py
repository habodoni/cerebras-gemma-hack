import unittest

from ferry.clients import _requests_web_search, _requires_code_artifact


class ArtifactRequestTests(unittest.TestCase):
    def messages(self, prompt):
        return [{"role": "user", "content": prompt}]

    def test_pptx_request_requires_run_code(self):
        self.assertTrue(
            _requires_code_artifact(
                self.messages("Create a PowerPoint profile deck as a PPTX file.")
            )
        )

    def test_downloadable_csv_request_requires_run_code(self):
        self.assertTrue(
            _requires_code_artifact(
                self.messages("Generate a downloadable CSV of the benchmark results.")
            )
        )

    def test_gif_request_requires_run_code(self):
        self.assertTrue(
            _requires_code_artifact(
                self.messages("Make a GIF of a neural network.")
            )
        )

    def test_png_request_requires_run_code(self):
        self.assertTrue(
            _requires_code_artifact(
                self.messages("Generate a PNG visualization of model routing.")
            )
        )

    def test_search_plus_gif_requests_search_and_run_code(self):
        messages = self.messages(
            "Make an extensive GIF of a neural network and search the web."
        )

        self.assertTrue(_requests_web_search(messages))
        self.assertTrue(_requires_code_artifact(messages))

    def test_generic_presentation_advice_does_not_force_run_code(self):
        self.assertFalse(
            _requires_code_artifact(
                self.messages("Give me tips for a better presentation.")
            )
        )


if __name__ == "__main__":
    unittest.main()
