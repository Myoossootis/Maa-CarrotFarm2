"""Compatibility Maa entry that opens the independent automation interface."""

from pathlib import Path
import sys

from maa.custom_action import CustomAction


class OpenFarmHud(CustomAction):
    def run(self, context, argv) -> bool:
        if context.tasker.stopping:
            return False
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.farm_hud import launch_hud
        try:
            pid = launch_hud()
        except OSError as error:
            print(f'[操作台] 启动失败：{error}', flush=True)
            return False
        print(f'[自动化] 已打开主界面 PID={pid}；选择模板后点击开始运行。', flush=True)
        return True
