import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from opencode_service import panel_launcher


class LauncherTests(unittest.TestCase):
    def test_selection_order_and_fallback(self):
        override = "TEST_NODE"
        advertised = os.path.abspath("opencode")
        accepted = {"outcome": "accepted", "panel_runtime": advertised}
        with patch.dict(os.environ, {override: "custom"}), patch("shutil.which") as which:
            which.side_effect = lambda candidate: candidate
            self.assertEqual(panel_launcher([accepted], override), (advertised, True))
            which.assert_called_once_with(advertised)
            self.assertEqual(panel_launcher([], override), ("custom", False))
            which.side_effect = lambda candidate: "node" if candidate == "node" else None
            self.assertEqual(panel_launcher([accepted], override), ("node", False))
            which.side_effect = lambda _: None
            self.assertEqual(panel_launcher([accepted], override), (None, False))


if __name__ == "__main__":
    unittest.main()
