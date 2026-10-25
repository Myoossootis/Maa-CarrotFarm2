# 本地只读解码

入口：`tools/read_only_game_state.cjs`；内存读取桥接：`tools/read_only_memory.py`。
Windows、Node.js 和 Python 3（64 位）即可，不需要安装新的 Python/npm 包。
默认使用项目 `.venv/Scripts/python.exe`，可以用 `--python` 指定其他解释器。

保持微信小程序运行。读取共享任务前，先手动打开家族共享任务板，让客户端缓存完整的 90 个槽位。
脚本不会代为打开页面。

从项目根目录运行：

```powershell
node tools/read_only_game_state.cjs inventory --output .tmp/read_only_decoder/inventory.json
node tools/read_only_game_state.cjs orders --output .tmp/read_only_decoder/orders.json
node tools/read_only_game_state.cjs employees --output .tmp/read_only_decoder/employees.json
node tools/read_only_game_state.cjs production --output .tmp/read_only_decoder/production.json
node tools/read_only_game_state.cjs family --output .tmp/read_only_decoder/family.json
```

需要同时读取全部数据时，用一次 `all`，共享进程发现、配置解包和背包解码的开销：

```powershell
node tools/read_only_game_state.cjs all --output .tmp/read_only_decoder/current_state.json
```

正式 Maa 读取后端使用 `automation` 模式，一次返回库存、订单、员工计划及逐槽位生产数据，跳过 90 槽任务堆扫描，
不要求打开家族任务板。代码接入与只读验证见[正式 Maa 接入说明](DECODED_STATE_MAA.md)。

不传 `--output` 时，标准输出为 JSON。耗时摘要或错误信息写入标准错误；失败返回非零退出码，
不会用空数据覆盖已有输出。输出目录和 `.tmp/read_only_decoder/hints.json` 是脚本自行创建的分析文件。
若存在多个微信账号，脚本要求用 `--applet` 指定目标账号的 `.../radium/users/<账号目录>/applet`。

## JSON 内容

所有模式均包含 `schemaVersion`、`capturedAt`、`completedAt`、`storageSequence`、`packageVersion` 和 `timings`。

| 字段 | 内容 |
| --- | --- |
| `inventory.products` | 按物品 ID 汇总的农场产品库存；同名不同 ID 不合并 |
| `inventory.records` | 未过期的背包记录、数量和游戏附加字段 |
| `orders` | 普通、聚会、外卖各栏订单；材料、奖励、库存差额、状态和菜单 ID |
| `employees.plans` | 各建筑员工、等级、安排目标、累计产量、员工持有产品和原始计划字段 |
| `production.buildings[].slots` | 农田、牧场、果园逐槽位产品、成熟时间、派生状态、供料计时与生产轮次标记 |
| `family.tasks` | 90 个槽位：内容、条件、品质、接取成员、升级成员、进度、截止时间 |

`orders.materialsSufficient` 仅表示材料足够，不保证等级、菜单解锁等提交条件满足。
库存与员工持有产品分别输出，后者不会自动计入玩家背包。
逐槽位字段、成熟公式和时钟限制见[生产槽位解码说明](PRODUCTION_SLOT_DECODER.md)。
`production.plantingCatalog` 提供普通农田作物的等级与固定种植费用；手动安排见[手动种植只读预检](MANUAL_PLANTING_PREFLIGHT.md)。
`inventory.records` 包含背包中的其他游戏记录，不能将所有条目的数量相加当作产品库存。

共享任务有独立的状态和进度可用性：

- `status: "occupied"`：被成员占用，`acceptor` 给出成员。
- `status: "unclaimed"`：未被接取。
- `status: "cooling_down"`：未被接取且刷新冷却时间未到。
- `progress.current: null`：缓存没有提供可解码的单一进度值；不代表进度为零。
- 自己的任务优先关联背包任务记录，`progress.source: "personal_bag"`。
- `upgrader` 与 `acceptor` 分开；任务升级成员不一定是接取成员。
- `isRare` / `rarity` 按客户端 `quality > WHITE(0)` 判定，升级后的品质 2 任务也属于稀有。
- `isUpgraded` 根据任务配置 `mutation_ref_id` 判定，与稀有度分开记录。
- `bellReward.count` 是该条任务完成后的铃铛奖励（物品 3638），从 `reward_box_id` 对应奖励表读取；
  有个人任务或共享缓存奖励覆盖时优先使用覆盖值。`bellReward.source` 记录来源。
  这不是玩家已拥有的铃铛数量。当前 90 个槽位均有固定铃铛数量，未知或非固定配置会报错。
- `deadlineUnix` 为游戏缓存的 Unix 秒数，`deadline` 是带 `Z` 的 UTC 时间。
  北京时间加 8 小时。没有实际截止时间时输出 `null`；不会按当前时间推算一个截止时间。
- `durationSeconds` 是配置的接取后时限，与实际截止时间分别输出。

当前成功样本：槽位 22 被皮卡丘呢占用、冰淇淋萝卜升级，进度未提供；
槽位 67 被情衣占用并升级，个人背包提供 `42/172`。

## 只读范围和校验

进程句柄权限仅为 `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`。
脚本只使用查询和 `ReadProcessMemory`；不点击、不调用游戏接口、不发网络请求、不注入、不写进程内存，
不修改微信缓存。运行时解密密钥只在内存和父子进程管道内短暂使用，不写入 JSON、日志或地址提示文件。

读取 MMKV 前后比较元数据，并验证长度及 CRC；解密后验证背包结构。
自动选择已缓存的最新配置包，不固定进程 PID、V8 地址空间基址或成员名单。
共享任务必须得到 90 个槽位且所需成员姓名完整；缓存版本冲突或数量不足会报错。
这是客户端缓存快照，四类数据也不是原子快照；不保证代表服务器当前状态。
未打开的页面、新版本 V8 对象布局或尚未写入的缓存可能导致读取失败，应保留失败信号供自动化调用方处理。

## 效率与一次实测

2026-10-18 在当前机器完成一次成功的 `all` 读取（首次无地址提示）：

| 阶段 | 耗时 |
| --- | ---: |
| 进程发现 | 148 ms |
| MMKV 一致性快照 | 11 ms |
| 密钥定位及存储解码 | 372 ms |
| 配置解包 | 166 ms |
| 库存解析 | 2.34 ms |
| 订单解析 | 1.04 ms |
| 员工计划解析 | 0.55 ms |
| 狮王任务及成员扫描 | 4,279 ms |
| 脚本内部总计 | 4,982 ms |
| 含 Node 启动和文件输出的墙钟时间 | **5,248 ms** |

没有循环跑分；这些阶段数字不是四个独立子命令各自的完整耗时。
每次正常运行都会在结果 JSON 中记录实际 `timings`。

已使用的优化：任务与成员合并扫描、4 MB 分块、V8 字符串页缓存、一次加载共用配置、
优先上次验证过的游戏进程。密钥地址提示绑定进程创建时间，每次仍须成功解密并校验当前缓存；
失效后重新定位。共享任务每次重新扫描，避免仅凭仍可读取的旧地址返回过期对象。
后续运行速度取决于客户端内存规模和缓存情况，未额外测试热启动耗时。

解析回归测试（只用合成数据，不读取游戏）：

```powershell
node --test tools/read_only_game_state.test.cjs
```

测试覆盖 MMKV 覆盖/删除/截断、CRC、占用与进度缺失的区分、个人进度关联、
90 槽位完整性和冲突拒绝。实际目标验证另外完成：575 条背包、27 单、9 个计划、90 个共享任务，成员姓名无缺失。
