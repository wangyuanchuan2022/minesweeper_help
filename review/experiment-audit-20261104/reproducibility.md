# 全部实验系列正确性与可复现性独立审计

【评审身份】对象快照：HEAD `84fa7ce312b4a31ba64c0962dacc9d9f87ab513c` + 2026-11-04 19:13 工作区（含大量未跟踪脚本/数据）；模型 kimi-k3.1-pro；通用清单 + 科学审计专项；按实验家族冻结，不执行全仓 crgate 机检。代码图索引缺失，使用 read/grep 替代。
【对象漂移】审计期间 `state/ab` 仍有后台产物更新（P1 journal、F 重试等），结论只针对上述对象及明确读取时点；当前工作区不能由 HEAD 单独重现。
【覆盖状态】partial（实验家族覆盖，非逐行审 180 个脚本；无生产合并批准含义）。
【达成模式】实现/产物审计 + 小规模只读重算（`python -B`，不落实验输出）；无真实 Arbiter 实机、无大规模实验、无重新编译。

## 结论摘要

**已证实的最重要缺陷在 F/regret 评估器，而不是生产求解器。** `dump_metric_matrix_ext.py` 只取饱和后的一个终端分支，会删除其他合法世界及其决策；q 又没有对齐到 Python 选中的饱和状态。因此当前 `mm_discovery/mm_full` 的 regret、子集、LOO 数值不能称为所声明条件下的完整递归值，必须修复后重算。该项不直接证明生产胜率结论有方向性偏差。

第二根已证实的支柱是身份/复现链：discovery/ext/ext2 三批共 2286 个 case 快照均无 seed/game_id/step 身份字段；四处脚本共用的去重键 case_key 不含 cell_value/groups/ck，实测 51 个重复键组中 18 组同键不同内容、16 组跨文件（含 discovery 与冻结验证集 ext2 之间），同键写入静默覆盖——以上均为本次审计独立实测，与组织者证据一致。

## 实验家族覆盖账

| 家族 | 已追踪实现与结果 | 结论 | 边界/未覆盖 |
|---|---|---|---|
| A 仿真、seed、首点 | `sim/minesweeper_sim.py`、`sim/eval_harness.py`、`bench/ab_policy.py`、`ab_first_click.py`、`ab_first_click_corner.py`；P2 正式/复现 JSON 与 UTF-16 日志 | reviewed | 仅策略级同 seed 配对，非相同雷位配对；未重跑 4000×2 局 |
| B 世界枚举、V*、flood、C++ | `arms_lookahead.py`、`compare_2ply_vs_winrate.py`、`utils/probability.py`、`cpp/mscore.cpp`；`verify_exact_cpp.py`、`verify_after_click_semantics.py` | reviewed | Python/C++ 逐位相等不是独立正确性；完整 V* 已知答案仍缺；未重编二进制 |
| C F、regret、min-max、LOO | `dump_metric_matrix_ext.py`、`merge_and_eval_cases.py`、`eval_heuristic_metrics.py`、`optimize_weights.py`、`fast_loo.py`、`search_metric_subsets.py`；mm_discovery/mm_full | reviewed | 发现 P0/P1 状态错配；不读取未评估的 ext2 验证集，避免污染冻结验证 |
| D 参数、hook、预算、计时 | `sweep_cap.py`、`eval_depth_policy_games.py`、`timing_protocol.py`、`report_repeat_slowdown.py`、`verify_sampler_fix.py`；cap/B 线报告 | reviewed | 未复现耗时漂移根因；只做静态与既有结果审计 |
| E 原生加速与性能 | `compare_native.py`、`bench_wr_speed.py`、`instrument_walltime.py`、`cpp/build.bat`；14.12x 与 2000 局报告 | reviewed | 未在统一新进程协议下复测 14.12x；视觉时序另有合成证据 |
| F OCR、视觉、UI 像素 | `utils/vision.py`、`verify_vision_*`、`verify_ui_*`；UI/视觉报告 | reviewed | 模板贴回/随机板/offscreen 并非真实截图或实机；未实机渲染 |
| G 启动、完整性、续跑、复现 | `run_winrate_suite.cmd`、`verify_stage_output.py`、`verify_launcher_three_states.py`、`verify_journal_resume.py`、`record_run_manifest.py`、state/ab 与日志 | reviewed | 不运行会写文件的启动/续跑脚本；检查实现与已存结果 |

## 发现（P0/P1/P2）

### P0 — F/q 不是同一状态，且导出器系统性丢弃合法分支【bench/实验器真实缺陷】

- **位置**：`bench/dump_metric_matrix_ext.py:301-318,320-327,358-370`；同类 `bench/eval_heuristic_metrics.py:162-175`、`bench/merge_and_eval_cases.py:254-260`、`bench/arms_lookahead.py:368-399`。
- **问题**：Python 先 `saturate`，再只保留非胜、有候选的终端，按权重取一个；F 在该终端计算，而 C++ q 从 `opened0` 独立饱和。C++ 根评估还会对所有终端加权（`cpp/mscore.cpp:1980-1999`），不是 Python 的最大终端。`merge_and_eval_cases` 甚至完全不先饱和 Python 状态。
- **证据**：上述行号逐字读取。最小 6×2、2 雷、两个已开数字 1 的合成局面产生 4 个终端，权重 `4/9,2/9,2/9,1/9`；现有选择只保留 `4/9`，丢弃 `5/9` 合法概率质量。采用全 `clicks` 的真实矩阵行可直接进入单终端路径，故 `compute_metrics` 的预算异常吞噬（150-151）通常不会在 F 计算时触发，175 个 err 主要来自 C++ q 超时/节点上限（328-333）。
- **影响面**：`mm_discovery`（77 行）、`mm_full`（287 行）、`merged_regret`（300 行）、F 矩阵说明、子集与 LOO 的输入不是所声称的完整 V*。F 还可能对应饱和后新候选，而 q 是原状态/全分支值。当前 HANDOFF 的连续权重、分层权重、最佳 LOO、3-ply/regret 等数值应标记**待重算**；不可把该缺陷直接外推为“生产算法错”或预判数值偏差方向。
- **置信度**：高（代码 + 受控探针）。
- **【改进点】**：统一状态转移接口，一次饱和得到带权终端列表；对每个终端计算 F/q，按权重聚合 regret，不能只聚合标量 V* 后再挑分支；q 与 F 必须传同一个 `(world_subset, opened)`。C++ 接口增加饱和终端导出或接受该状态，禁止自己重新选择不同终端。任一候选/终端 Budget 整点标未覆盖，所有异常保留类型。
- **最小验证**：上述 6×2 用例必须保留 4 终端、总权重 1；手算各分支 regret，Python/C++ 一致；注入只留最大分支必须必红；真实矩阵再逐行核对坐标和权重守恒。

### P1 — 通用胜率与若干实验会静默缩小异常样本分母【bench 完整性缺陷】

- **位置**：`sim/eval_harness.py:407-411,481-482,555-559`；`bench/ab_policy.py:449-454,606-610`；`bench/ab_first_click.py:97-103,177-190,208-212,256-259`；`bench/pool_first_click.py:46-50`；`bench/eval_depth_policy_games.py:142-151,158-166`。
- **问题**：异常记录被排除出胜率；首点旧实验仍写 `complete:true`，池化器再跳过异常对；B 线甚至 `except Exception: continue`，且不检查 `r["error"]`。
- **影响面**：历史通用胜率、早期首点合并与 B 线完成数不是请求样本的完整结果，不能与 P2 新版完整门禁等价比较。P2 新版 `ab_first_click_corner.py:121-145,208-210,225-263` 已修复该问题。
- **置信度**：高。**【改进点】**：按请求 seed 建完整配对键，异常单列未覆盖；非完整禁止 complete/正式胜率，退出非零。最小验证：连续 3 个 seed 中第 2 个异常，输出必须 requested=3/completed=2/complete=false 且不得给正式 p 值。

### P1 — P2 配对检验不能从正式 JSON 独立重算【可复现性缺陷】

- **位置**：`bench/ab_first_click_corner.py:139-142,321-325`；`bench/judge_p2_replication.py:90-106,148-159,199-212`；`state/ab/firstclick_p2_formal.json:1-66`、复现同结构。
- **问题**：实验只保存 summaries，未保存 results；判定器兼容逐局但主结果没有，故 verdict 的 `mcnemar` 为 null。配对计数只存在日志。
- **实算**：用日志的 382/317、379/357 重算得 p=`0.015430731751397853`、`0.43891011179382655`，与报告一致；首格 0 理论值为 corner `0.4984628742052113`、center `0.15448690437861692`。
- **影响面**：结论数值目前可交叉核对日志，但单凭主 JSON 不可复算配对显著性，也不能做 seed 完整性/棋盘配对审计。**【改进点】**：原子保存每 seed/臂结果、首格、棋盘哈希、配置与代码/二进制哈希，判定器缺逐局必须拒绝配对裁定。最小验证：从结果文件独立得到 2000 对、四个不一致计数与 p，无需日志。

### P1 — 计时协议把部分失败当成功测量；速度结论缺完整身份链【测量/复现缺陷】

- **位置**：`bench/timing_protocol.py:51-54,80-105,133-153`；`bench/bench_wr_speed.py:70-85,109-147`；`bench/record_run_manifest.py:49-85`。
- **问题**：新进程函数不检查 `returncode`，只要 stderr 出现计时便纳入；交错函数无输出返回 `{}`，新进程无输出返回 `None`，无显式 skipped 原因。`bench_wr_speed` 对全部 33 用例不检查集合相等，只检查交集；旧 14.12x 是同进程测量，与后制定协议冲突。manifest 无编译器、构建命令、所有 bench 脚本和输入数据哈希。
- **影响面**：14.12x 不应按当前强制协议作为可复现性能结论；2000 局没有改前二进制的同协议对照，报告本身也正确保留此边界。部分子进程失败可产生看似有效的性能数字。
- **【改进点】**：returncode/输出/请求重复数/用例集合硬门；每配置独立进程并保存失败项；编译器版本、编译参数、依赖、脚本和输入全量哈希入 manifest。最小验证：注入 exit 3 且带 `__ELAPSED__` 的子进程必须整项 unreliable，不允许出中位数。

### P1 — 续跑身份过弱，可能复用不同局面/不同实现的结果【续跑真实风险】

- **位置**：`bench/dump_metric_matrix_ext.py:72-76,220,250-263,276-283`；`bench/merge_and_eval_cases.py:65-72,164,200-239`；`bench/classify_p1_coverage.py:61-65,157,181-204`；`bench/verify_vstar_precision.py:67`。四处 case_key 实现逐字一致，且 `merged[case_key(c)] = c` 同键静默覆盖。
- **问题**：key 不含 `cell_value/groups/ck`，也无 seed/game_id/step（实测三批 2286 个快照全部没有这些字段）；同候选坐标但不同数字或约束可碰撞。journal 也无输入、代码、二进制、cap 的身份；成功/失败均按 key 永久复用。JSON 解析失败被静默忽略。
- **实测复核（与组织者证据独立一致）**：三批文件按 case_key 去重得 51 个重复键组，其中 18 组 cell_value/groups 哈希不同、16 组跨文件（含 discovery↔ext2、discovery↔ext）——『同键不同盘』不是理论风险而是已存在数据。
- **影响面**：换实现/cap 后续跑可能把旧值混入新矩阵；更关键的是发现集与冻结验证集 ext2 之间也存在同键组，合并时该键保留哪份内容取决于遍历顺序——ext2 的『只评一次』点既可能被 discovery 内容顶替、也可能被静默丢弃，两个方向都破坏验证集独立性。P1-c 在审计时仍 incomplete，不能把中途快照当全样本。**【改进点】**：完整 case 规范化哈希（含 cell_value/groups/ck）+ 输入/实现/二进制/配置哈希；journal 坏行 fail-closed；合并遇同键不同内容必须报错而非静默取一；skip 按策略决定是否可复用。最小验证：同 clicks/clicks9、改一个已开数字，续跑必须重算而非 resumed；注入同键不同 cell_value 的两快照，合并必须失败。

### P1 — OCR 没有真实标注闭环，UI 像素探针部分只是重建样式【证据缺口，非已证实生产识别错误】

- **位置**：`bench/verify_vision_accuracy.py:51-68,115-118`；`utils/vision.py:120-139,157-216`；`bench/verify_ui_p0.py:81-102,135-160`；`bench/verify_ui_legend.py:65-80`。
- **问题**：OCR 是模板贴回与扰动，没有真实游戏人工标注；随机棋盘等价只证明批量≈旧算法，不证明旧算法正确。UI P0 的按钮/进度条/frame 是脚本内重建，未 grab 真实主窗口；frame 与叠加色是几何/公式检查。图例只验采样点，不验真实图像映射。
- **影响面**：不能声称真实 OCR 准确率或真实窗口 AA 已验证。当前没有足够证据把模板行为定级为生产 P0/P1。
- **【改进点】**：采集覆盖 DPI/主题/缩放的实机截图人工标注集；保存金标准哈希与混淆矩阵；直接 grab `Ui_MainWindow` 真实窗口并做像素 ROI/差分。最小验证：真实集中每一数字至少一个难例，批量与参考同时接受独立标注评估。

### P2 — 文档/输出存在误导性字段与过期文案【 bench/交付质量】

- **位置**：`sim/eval_harness.py:527-528` 仍打印“首点 3x3 安全”，实际 `sim/minesweeper_sim.py:124-132` 仅首格安全；`sim/eval_harness.py:327-351` 用累计 `decided` 造成后续纯推理步重复记录置信度（注释与代码不一致）；`bench/ab_first_click.py:242-247` 用局末 `opened==1` 解释首点展开，不是首点即时值；`sim/first_click_rules.py:11` 文档串把『首点+8 邻域无雷』称为『Arbiter/WinMines 常规做法』——本项目仿真与重算首格 0 率均只对应『仅首格安全』，该行把未经核实的逆向推断写成了定论。
- **影响面**：不直接改胜率，但污染日志解读与校准/诊断。**【改进点】**：改文案；用本步决策标志；首点快照记录 `first_opened`。最小验证：一次概率决策后接纯推理步，置信度记录数不增加。

## 可复现性清单与当前读数

| 项 | 核查结果 |
|---|---|
| HEAD/工作区 | HEAD=`84fa7ce`；P2 脚本有提交，但 P1/F/LOO 多个脚本与结果未跟踪；`HANDOFF.md:5` 的历史 HEAD `87976d3` 仅交接时点，不是全部产物版本凭证 |
| 当前二进制 | `utils/mscore.cp310-win_amd64.pyd` SHA256=`5b22ec2bde70acc8e64dad8a8cbf9305970df8eb228a744f06ed22aac50219cd`，与 HANDOFF 一致；bak/bak2 分别为 `8f172770…`、`3bf440ab…`，不能混用 |
| 源码身份 | 当前 `cpp/mscore.cpp`=`ae9d5ccaedf1d5459151c671952728fa0d8b733cc860b7d630427bf56d5c940a`；工作区编译器 cl 19.42.34435，但缺编译时间/命令与历史二进制一一对应凭证 |
| case 快照身份 | discovery/ext/ext2 共 2286 个快照均无 seed/game_id/step；case_key 四处实现一致且不含 cell_value/groups/ck（`dump_metric_matrix_ext.py:72-76` 等）；实测重复键组 51，其中 18 组同键不同内容、16 组跨文件（含 discovery↔ext2） |
| P2 结果 | 每臂 2000、失败 0；日志 `EXIT=0`；配对 p 已独立重算一致。正式 +3.25pp，复现 +1.10pp；主结论“方向一致、收益仍不确定”符合冻结判据 |
| P2 日志 | `p2_v2.log` 70 行；`p2_repl.log` 907051 行（约 112MB，含大量重复进度），UTF-16；不应误用按 UTF-8 读文件 |
| F 矩阵 | 读取时 `mm_full` 287/462，err 175；`mm_discovery` 77/119，err 42；受 P0 状态错配影响，且本身也不是完成的全覆盖 |
| P1 覆盖 | `p1_coverage_report.json` 明确 `is_snapshot:true`（299/462）；随后 journal 已变化，故没有最终全样本未覆盖率，禁止沿用“47%”或中途比例作最终结论 |
| V* 仪器 | 140 格 Python/C++ 对拍仅覆盖小世界、node_cap；C++ `tick` 只有节点上限（`cpp/mscore.cpp:1740-1743`），Python 另有 time cap（`arms_lookahead.py:280-286`），不能把 cap 稳定当作独立已知答案 |
| 世界等权 | 在无首轮条件偏置、固定总雷数、约束只决定哪些布局合法时，一致完整布局等权是合理的；首轮后验与 Arbiter 过程未独立核对。q 报告需保留此前提 |
| 原生加速 | 输出指纹只覆盖 res 数组，不含 clicks/coords/total；共同用例交集未做完备性门。性能结论需按新进程协议重跑 |
| 验证集 | 本审计未读取/评估 `midgame_cases_ext2.json` 的内容；不污染“只评估一次”纪律 |

## 正面观察

- 棋盘只排除首格、零 flood、视图无雷位字段，且每局重置 Python 与 NumPy seed（`sim/minesweeper_sim.py:107-132`、`sim/eval_harness.py:234-243`）。
- P2 首点不同导致棋盘不同已明确说明为策略级配对；新版完整性硬门禁有效，冻结复现判据且禁止将合并集作达标依据。
- `sweep_cap.py:51-82` 在 stub 前保存真实 sleep，避免采样线程忙等；生产 cap 用局前后差值（147-170），接线问题有受控对照。
- `fast_loo.py:87-96,219-237` 每折独立训练，向量化有标量对拍与阴性对照；但输入 q/F 的 P0 仍需先修。

## 未覆盖项与最终裁定

- 未逐行审全部约 180 个 bench 脚本；未重跑大规模整局、完整 V*、编译或真实 UI/OCR；未审冻结验证集内容。
- 历史主报告/架构报告中的过时结论只作背景，不视为当前事实；P0 修复前不得把现有 F/regret/LOO 数字用于采纳策略。
- 组织者补充证据（三批快照无身份字段、51 重复键组中 18 组同键不同内容、`sim/first_click_rules.py:11` 口径误称）已独立实测复核一致并计入上文发现；按要求未再开新审计面。
- **裁定：COMMENT（科学审计；不是 APPROVE）**；**覆盖状态：partial**。P0/P1 为实验器与可复现性问题，未发现据当前证据可定为“生产首点/生产枚举实现错误”的新缺陷。身份链缺陷（快照无身份、同键不同盘）已由实测升级为事实，修复前 mm/regret/LOO 与 P1 覆盖率数值不得引用为终值。
