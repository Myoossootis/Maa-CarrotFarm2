"""Independent Maa entry that opens the read-only data viewer."""

from pathlib import Path
import sys

from maa.custom_action import CustomAction


class OpenDecodedDashboard(CustomAction):
    def run(self, context, argv) -> bool:
        if context.tasker.stopping:
            return False
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.decoded_dashboard import launch_dashboard
        try:
            pid = launch_dashboard()
        except OSError as error:
            print(f'[解码窗口] 启动失败：{error}', flush=True)
            return False
        print(f'[解码窗口] 已打开，只读数据窗口 PID={pid}；没有发送游戏输入。', flush=True)
        return True
