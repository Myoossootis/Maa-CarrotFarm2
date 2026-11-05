"""Maa cycle entry guard; no production actions run from this node alone."""

from maa.custom_action import CustomAction
import importlib.metadata
import json

try:
    from .production_start import confirm_cycle_start, wait_for_background_ready
    from .cycle_bridge import run_cycle_worker
    from .order_preferences import allowed_products_from_node
    from .task_settings import action_values
except ImportError:
    from production_start import confirm_cycle_start, wait_for_background_ready
    from cycle_bridge import run_cycle_worker
    from order_preferences import allowed_products_from_node
    from task_settings import action_values


class ConfirmProductionCycleStart(CustomAction):
    def run(self, context, argv) -> bool:
        return confirm_cycle_start(is_stopping=lambda: context.tasker.stopping)


class RuntimePreflight(CustomAction):
    def run(self, context, argv) -> bool:
        version = importlib.metadata.version('MaaFw')
        ready = version == '5.13.1' and not context.tasker.stopping
        print(json.dumps({'task': 'runtime_preflight', 'MaaFw': version, 'ready': ready,
                          'game_input': False}, ensure_ascii=False), flush=True)
        return ready


class CloseOfflineIncome(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='dismiss_offline_income.py')


class EnterRestaurant(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='enter_restaurant.py')


class RefreshOrder(CustomAction):
    def run(self, context, argv) -> bool:
        try:
            parameters, node = action_values(context, argv)
            order_type = node.get('attach', {}).get('refresh_order_type', parameters.get('order_type', 'party'))
            number = parameters.get('order_number', 1)
            if order_type not in {'party', 'takeout', 'normal'} or type(number) is not int or not 1 <= number <= 9:
                raise ValueError('订单类型须为 party/takeout/normal，订单位置须为 1–9')
        except (ValueError, TypeError, AttributeError) as error:
            print(f'[错误] 刷新订单参数不合法：{error}', flush=True)
            return False
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker(['--order-type', order_type, '--order-number', str(number)],
                                is_stopping=stopping, script='refresh_order.py')


class EnterFarm(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='enter_farm.py')


class OpenPhone(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='open_phone.py')


class OpenEmployeeConfig(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='open_employee_config.py')


class SelectFarmEmployment(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='select_farm_employment.py')


class OpenFieldDetail(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='open_field_detail.py')


class ReturnEmployeeList(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='return_employee_list.py')


class LocateField2(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='locate_field_2.py')


class OpenWorkPlan(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='open_work_plan.py')


class CloseWorkPlan(CustomAction):
    def run(self, context, argv) -> bool:
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker([], is_stopping=stopping, script='close_work_plan.py')


class VerifySlotWorkPlan(CustomAction):
    def run(self, context, argv) -> bool:
        parameters = json.loads(argv.custom_action_param or '{}')
        slot_id = parameters.get('slot_id')
        if slot_id not in {'field_2', 'field_3', 'ranch_1', 'ranch_2', 'orchard_1', 'orchard_2'}:
            print('岗位参数不合法，未执行。', flush=True)
            return False
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker(['--slot', slot_id], is_stopping=stopping,
                                script='verify_slot_work_plan.py')


class RunShortCycle(CustomAction):
    def run(self, context, argv) -> bool:
        try:
            parameters, node = action_values(context, argv)
        except (ValueError, TypeError, OSError) as error:
            print(f'[错误] {error}；未启动。', flush=True)
            return False
        rounds = parameters.get('rounds', 1)
        poll = parameters.get('poll_seconds', 20)
        timeout = parameters.get('timeout_seconds', 900)
        # Maa replaces custom_action_param as one object. Keep the UI mode in
        # independent node metadata so numeric input overrides cannot erase it.
        apply = node.get('attach', {}).get('ui_apply', False)
        if (type(rounds) is not int or not 1 <= rounds <= 20 or type(poll) is not int
                or not 5 <= poll <= 300 or type(timeout) is not int or not poll <= timeout <= 7200
                or type(apply) is not bool):
            print('周期参数不合法，未启动。', flush=True)
            return False
        stopping = lambda: context.tasker.stopping
        if stopping():
            print("[停止] 短循环启动前已取消。", flush=True)
            return False
        if apply and getattr(argv, 'ui_start_confirmed', False) is not True:
            print("[日常] 短循环将弹出清地确认（60秒超时，日志会心跳）。", flush=True)
            if not confirm_cycle_start(is_stopping=stopping):
                print("[错误] 未确认清地或超时，短循环取消。", flush=True)
                return False
        if apply and not wait_for_background_ready(is_stopping=stopping):
            return False
        arguments = ['--rounds', str(rounds), '--poll-seconds', str(poll),
                     '--timeout-seconds', str(timeout), '--skip-checklist']
        if apply:
            arguments.append('--apply')
        return run_cycle_worker(arguments, is_stopping=stopping)


class RunFeedProduction(CustomAction):
    def run(self, context, argv) -> bool:
        try:
            parameters, _node = action_values(context, argv)
        except (ValueError, TypeError, OSError) as error:
            print(f'[错误] {error}；未启动。', flush=True)
            return False
        maximum = parameters.get('max_feed', 120)
        poll = parameters.get('poll_seconds', 60)
        if (type(maximum) is not int or not 1 <= maximum <= 99999
                or type(poll) is not int or not 10 <= poll <= 3600):
            print('[饲料循环] 参数错误：上限 1–99999，间隔 10–3600 秒；未执行。', flush=True)
            return False
        stopping = lambda: context.tasker.stopping
        if stopping() or not wait_for_background_ready(is_stopping=stopping):
            return False
        return run_cycle_worker(['--max-feed', str(maximum), '--poll-seconds', str(poll)],
                                is_stopping=stopping, script='run_feed_production.py')


class RunDailyOrders(CustomAction):
    """Daily batch scheduler and individual order entries; normal has no quota."""

    def run(self, context, argv) -> bool:
        try:
            parameters, node = action_values(context, argv)
        except (ValueError, TypeError, OSError) as error:
            print(f'[错误] {error}；未启动。', flush=True)
            return False
        order_type = parameters.get("order_type") or "normal"
        continuous = parameters.get('continuous', False)
        maximum = parameters.get('max_orders', 0)
        poll = parameters.get("poll_seconds", 20)
        timeout = parameters.get("timeout_seconds", 900)
        apply = node.get("attach", {}).get("ui_apply", True)
        if (type(continuous) is not bool or type(maximum) is not int or not 0 <= maximum <= 999
                or continuous and order_type != 'normal'):
            print('[错误] 持续循环仅支持普通单，轮数须为0..999。', flush=True)
            return False
        try:
            allowed_products = allowed_products_from_node(node)
        except ValueError as error:
            print(f'[错误] {error}', flush=True)
            return False
        if order_type not in {"party", "takeout", "normal", "batch", "聚会", "外卖", "普通"}:
            print(
                f"[错误] 日常订单类型不合法：{order_type!r}，应为 party/takeout/normal/batch。",
                flush=True,
            )
            return False
        if (
            type(poll) is not int
            or not 5 <= poll <= 300
            or type(timeout) is not int
            or not poll <= timeout <= 7200
        ):
            print(
                "[错误] 日常轮询参数不合法（poll 5..300，timeout poll..7200），未启动。",
                flush=True,
            )
            return False
        if type(apply) is not bool:
            print("[错误] ui_apply 必须为布尔值。", flush=True)
            return False
        stopping = lambda: context.tasker.stopping
        if stopping():
            print("[停止] 任务在启动前被取消。", flush=True)
            return False
        # No MessageBox here: old dialog was not topmost and hung Maa logs silently.
        if apply and not continuous:
            print(
                "[日常] 跳过清地弹窗，直接开跑（请事先清残留作物/旧计划）。"
                "监控预览随当前布局；帧目录 .tmp/maa_monitor",
                flush=True,
            )
        if apply and not wait_for_background_ready(is_stopping=stopping):
            print("[错误] 任务已停止，未启动日常。", flush=True)
            return False
        arguments = [
            "--order-type",
            str(order_type),
            "--poll-seconds",
            str(poll),
            "--timeout-seconds",
            str(timeout),
        ]
        if order_type == "batch":
            arguments = ["--order-types", "party", "takeout", "--poll-seconds", str(poll),
                         "--timeout-seconds", str(timeout)]
        script = 'run_daily_orders.py'
        if continuous:
            script = 'run_normal_order_loop.py'
            arguments = ['--max-orders', str(maximum), '--poll-seconds', str(poll),
                         '--timeout-seconds', str(timeout)]
        arguments.extend(['--allowed-products', *sorted(allowed_products)])
        print(f'[一号位] 本次勾选允许品类：{"、".join(sorted(allowed_products))}', flush=True)
        if apply:
            arguments.append("--apply")
        print(
            f"[日常] 启动子进程 {script} type={order_type} apply={apply}",
            flush=True,
        )
        labels = {"party": "聚会订单", "takeout": "外卖订单", "normal": "普通订单",
                  "聚会": "聚会订单", "外卖": "外卖订单", "普通": "普通订单",
                  "batch": "聚会与外卖自动调度"}
        label = labels.get(str(order_type), str(order_type))
        if continuous:
            label = '普通订单3倍持续循环'
            print(f'[普通3倍] 本次完成上限={maximum or "持续"}单；缺料优先员工补产，停止保留已保存进度。', flush=True)
        print(
            f"[日常] 启动 {label}（type={order_type} apply={apply}）；"
            + ("日志将打印补产、收取与3倍交付结果。" if continuous else
               "日志将打印订单信息与员工安排。聚会/外卖自动切页签并走批量完成6次。"),
            flush=True,
        )
        ui_log_failed = False

        def publish(line):
            nonlocal ui_log_failed
            if ui_log_failed:
                return
            detail = context.run_task('UiProgressLog', {
                'UiProgressLog': {
                    'focus': {
                        'Node.Recognition.Succeeded': {
                            'content': line[:800],
                            'display': ['log'],
                        }
                    }
                }
            })
            if detail is None:
                ui_log_failed = True
                print('[日志] 界面进度日志节点没有执行，后续只写入工作进程日志。', flush=True)

        return run_cycle_worker(arguments, is_stopping=stopping, script=script, publish=publish)
