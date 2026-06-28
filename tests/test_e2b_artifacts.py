import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ferry.config import settings
from ferry.main import _generated_file_path
from ferry.tools import _artifact_relpath, _created_files, _save_sandbox_files


class FakeFiles:
    def __init__(self, contents):
        self.contents = contents

    def read(self, path, format="bytes"):
        return self.contents[path]


class FakeSandbox:
    def __init__(self, contents):
        self.files = FakeFiles(contents)


def entry(path, size=5, modified_time=1):
    return SimpleNamespace(
        name=Path(path).name,
        path=path,
        size=size,
        modified_time=modified_time,
        type=SimpleNamespace(value="file"),
    )


class E2BArtifactTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "e2b_file_roots": settings.e2b_file_roots,
            "e2b_max_file_bytes": settings.e2b_max_file_bytes,
            "generated_files_dir": settings.generated_files_dir,
            "public_base_url": settings.public_base_url,
        }
        self.tmp = tempfile.TemporaryDirectory()
        settings.e2b_file_roots = ["/home/user", "/mnt/data"]
        settings.e2b_max_file_bytes = 1000
        settings.generated_files_dir = self.tmp.name
        settings.public_base_url = "http://ferry.test"

    def tearDown(self):
        self.tmp.cleanup()
        for key, value in self.original.items():
            setattr(settings, key, value)

    def test_artifact_relpath_handles_supported_roots(self):
        self.assertEqual(_artifact_relpath("/home/user/report.csv"), "report.csv")
        self.assertEqual(_artifact_relpath("/mnt/data/nested/out.txt"), "nested/out.txt")
        self.assertIsNone(_artifact_relpath("/tmp/out.txt"))
        self.assertIsNone(_artifact_relpath("/home/user/.secret"))

    def test_created_files_includes_new_and_modified_files(self):
        before = {
            "/home/user/old.txt": entry("/home/user/old.txt", size=3, modified_time=1),
        }
        after = {
            "/home/user/old.txt": entry("/home/user/old.txt", size=4, modified_time=2),
            "/home/user/new.txt": entry("/home/user/new.txt", size=3, modified_time=1),
        }

        created = _created_files(before, after)

        self.assertEqual([item.path for item in created], ["/home/user/new.txt", "/home/user/old.txt"])

    def test_save_sandbox_files_persists_files_and_returns_links(self):
        sandbox = FakeSandbox({
            "/home/user/report.csv": b"a,b\n1,2\n",
            "/mnt/data/nested/out.txt": "hello",
        })
        entries = [
            entry("/home/user/report.csv", size=8),
            entry("/mnt/data/nested/out.txt", size=5),
        ]

        lines = _save_sandbox_files(sandbox, entries)

        self.assertEqual(len(lines), 2)
        self.assertIn("http://ferry.test/api/files/", "\n".join(lines))
        generated = sorted(
            path.relative_to(self.tmp.name).as_posix()
            for path in Path(self.tmp.name).rglob("*")
            if path.is_file()
        )
        self.assertEqual(
            ["/".join(path.split("/")[1:]) for path in generated],
            ["nested/out.txt", "report.csv"],
        )

    def test_generated_file_path_rejects_traversal(self):
        run_id = "a" * 32
        target = Path(self.tmp.name) / run_id / "safe.txt"
        target.parent.mkdir(parents=True)
        target.write_text("safe")

        self.assertEqual(_generated_file_path(run_id, "safe.txt"), target.resolve())
        self.assertIsNone(_generated_file_path(run_id, "../safe.txt"))
        self.assertIsNone(_generated_file_path("not-valid", "safe.txt"))


if __name__ == "__main__":
    unittest.main()
