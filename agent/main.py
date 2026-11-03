import sys

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker

import production_actions
from decoded_dashboard_action import OpenDecodedDashboard
from farm_hud_action import OpenFarmHud
from manual_planting_action import ManualPlantingPreflight, ManualPlantingOnce


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)
        
    socket_id = sys.argv[-1]

    actions = {
        'OpenDecodedDashboard': OpenDecodedDashboard(),
        'OpenFarmHud': OpenFarmHud(),
        'ManualPlantingPreflight': ManualPlantingPreflight(),
        'ManualPlantingOnce': ManualPlantingOnce(),
        'ConfirmProductionCycleStart': production_actions.ConfirmProductionCycleStart(),
        'RuntimePreflight': production_actions.RuntimePreflight(),
        'CloseOfflineIncome': production_actions.CloseOfflineIncome(),
        'EnterRestaurant': production_actions.EnterRestaurant(),
        'EnterFarm': production_actions.EnterFarm(),
        'OpenPhone': production_actions.OpenPhone(),
        'OpenEmployeeConfig': production_actions.OpenEmployeeConfig(),
        'SelectFarmEmployment': production_actions.SelectFarmEmployment(),
        'OpenFieldDetail': production_actions.OpenFieldDetail(),
        'ReturnEmployeeList': production_actions.ReturnEmployeeList(),
        'LocateField2': production_actions.LocateField2(),
        'OpenWorkPlan': production_actions.OpenWorkPlan(),
        'CloseWorkPlan': production_actions.CloseWorkPlan(),
        'VerifySlotWorkPlan': production_actions.VerifySlotWorkPlan(),
        'RunShortCycle': production_actions.RunShortCycle(),
        'RunDailyOrders': production_actions.RunDailyOrders(),
        'RefreshOrder': production_actions.RefreshOrder(),
        'RunFeedProduction': production_actions.RunFeedProduction(),
    }
    for name, action in actions.items():
        if not AgentServer.register_custom_action(name, action):
            raise RuntimeError(f'Failed to register {name}')
    Tasker.set_log_dir('')
    Tasker.set_stdout_level(2)
    if not AgentServer.start_up(socket_id):
        raise RuntimeError('Maa Agent connection failed')
    try:
        AgentServer.join()
    finally:
        AgentServer.shut_down()


if __name__ == "__main__":
    main()
