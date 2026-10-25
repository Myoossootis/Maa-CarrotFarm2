import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import ui_worker as worker


class LoopLockTests(unittest.TestCase):
    def test_windows_venv_child_recognizes_locked_ancestor_and_rejects_other_owner(self):
        source="import sys;sys.path.insert(0,'tools');import ui_worker as w;from pathlib import Path;w.ROOT=Path(sys.argv[1]);print(w.inherits_cycle_lock());\ntry:\n with w.cycle_lock():print('unexpected lock')\nexcept RuntimeError:print('busy')"
        previous=os.environ.get('CARROT_UI_CYCLE_OWNER')
        with tempfile.TemporaryDirectory() as folder, patch.object(worker,'ROOT',Path(folder)), worker.cycle_lock():
            run=subprocess.run([sys.executable,'-c',source,folder],cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(run.stdout.splitlines(),['True','busy'])
            env={**os.environ,'CARROT_UI_CYCLE_OWNER':'4294967294'}
            run=subprocess.run([sys.executable,'-c',source,folder],cwd=ROOT,capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(run.stdout.splitlines(),['False','busy'])
        self.assertEqual(os.environ.get('CARROT_UI_CYCLE_OWNER'),previous)


if __name__=='__main__':unittest.main()
