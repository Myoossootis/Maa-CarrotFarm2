import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch

from tools import atom_cli_common as m


class AtomCliCommonTests(unittest.TestCase):
    def test_write_evidence_under_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ev"
            path = m.write_evidence("demo_task", {"status": "ok", "clicks": 0}, out)
            self.assertTrue(path.exists())
            self.assertEqual(path.parent, out)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["task"], "demo_task")
            self.assertEqual(body["status"], "ok")
            self.assertTrue(body["fg_safe_inputs"])
            latest = out / "latest.json"
            self.assertTrue(latest.exists())
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["status"], "ok")

    def test_install_safe_inputs_delegates(self):
        fake = ModuleType("maa_frame_normalize")
        fake.install_foreground_safe_inputs = MagicMock()
        with patch.dict(sys.modules, {"maa_frame_normalize": fake}):
            m.install_safe_inputs()
        fake.install_foreground_safe_inputs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
