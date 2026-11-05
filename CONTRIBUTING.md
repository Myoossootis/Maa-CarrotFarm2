# 开发与版本流程

Karpathy Guidelines 不再自动调用；仅在用户明确要求时使用。项目自身的审计、测试与版本流程继续执行。

`main` 保留稳定历史；开发使用 `codex/<主题>` 分支，通过 PR 合入，不强推或重写历史。一次提交只处理一个问题，使用 `fix(decoder): ...`、`feat(orders): ...`、`test:`、`docs:`、`ci:` 等约定式说明。

应用版本以 `assets/interface.json` 的 `version` 为准，遵循 `主版本.次版本.修订号`：兼容修复递增修订号，兼容功能递增次版本，不兼容变更递增主版本。客户端包号（87、90）单独记录。

每项交付更新 `CHANGELOG.md`，写明行为变化、验证与限制。提交、PR 和 CI 保存实际修改记录；实机报告注明日期、范围、证据位置。发布标签 `vX.Y.Z` 必须与应用版本和 changelog 一致，未经验证的开发快照不打正式发布标签。

## 开发和检查

先明确复现步骤与验收条件，再补有意义的回归并做最小修改。先跑相关测试，再跑全套离线检查；实机验证单独进行。审查 `git diff`，按指定文件暂存，避免 `git add .` 混入本地资料。

Windows x64，Python 3.10、Node.js 22 或以上；MaaFw 固定 5.13.1：

```powershell
git submodule update --init --recursive
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r tools/requirements.txt
.venv/Scripts/python.exe tools/configure.py
.venv/Scripts/python.exe tools/check_version.py
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
node --test tools/read_only_game_state.test.cjs tools/read_only_production.test.cjs
git diff --check
```

截图回放依赖固定提交的 MaaCommonAssets OCR。`configure.py` 保留已有模型，模型差异须记录。CI 从干净检出初始化资源，不连接真实游戏或发送输入。

## 复用与健壮性

本地读取、纯决策、Maa 视觉输入保持分离，优先复用现有模块。一次决策共享快照，动作前后刷新；未知版本、损坏或过期状态不能伪造为空位或成功。游戏 UI 只允许一个写入者，先保存意图，再按订单／任务 UID、计划、材料及进度确认；结果不明先回读，不重复点击，不覆盖已有计划。

离线测试、当前只读验证、实际业务操作验收分别记录。Cocos 实验保持封存。狮王一期目标固定26，UP优先；指定员工按任务要求上阵，不必本人执行生产。普通订单补料员工优先，UP收获任务优先手动一键种植／收获。具体交付顺序见 [交付路线](docs/DELIVERY_ROADMAP.md)。

不上传 `.tmp/`、`.archive/`、`.venv/`、`debug/`、`config/`、运行态 `latest_*.json`、账户缓存、客户端包及解包源码、进程转储、凭证。报告仅保留必要结论，原始快照留本地。

回退使用 `git revert <提交>` 并重新验证。代码回退不能撤销已经发出的游戏操作，必须保留日志并先对账。

会话因额度或其他原因中断后，先读[开发交接](docs/SESSION_HANDOFF_2026-10-25.md)，核对真实Git／PR状态和未决操作再续做；不能仅凭旧latest恢复业务输入。
