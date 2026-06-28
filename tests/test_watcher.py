import unittest

from ferry.watcher import ConnectivityWatcher


class ConnectivityWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_open_still_requires_real_reachability_for_burst(self):
        watcher = ConnectivityWatcher()
        try:
            watcher.set_override(True)

            async def unreachable():
                return False

            watcher._probe = unreachable

            self.assertTrue(watcher.is_online())
            self.assertFalse(await watcher.can_burst_now())
            self.assertFalse(watcher.can_burst())
        finally:
            await watcher.aclose()

    async def test_forced_closed_never_probes_or_bursts(self):
        watcher = ConnectivityWatcher()
        try:
            watcher.set_override(False)

            async def should_not_run():
                raise AssertionError("forced closed should not probe")

            watcher._probe = should_not_run

            self.assertFalse(watcher.is_online())
            self.assertFalse(await watcher.can_burst_now())
            self.assertFalse(watcher.can_burst())
        finally:
            await watcher.aclose()

    async def test_auto_mode_uses_fresh_probe_for_burst(self):
        watcher = ConnectivityWatcher()
        try:
            async def reachable():
                return True

            watcher._probe = reachable

            self.assertTrue(await watcher.can_burst_now())
            self.assertTrue(watcher.can_burst())
        finally:
            await watcher.aclose()


if __name__ == "__main__":
    unittest.main()
