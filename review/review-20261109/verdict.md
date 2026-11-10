# 评审裁决（verdict）— review-20261109

> ⚠️ **本 verdict 由组织者（Lead）直接撰写，不是独立 Critic 席产出。**
> 原因如实记录：Critic 角色先后派出 **2 个会话**（`session-1fc87479`（agent_spawn）、
> `session-75ecf460`（de_session spawn）），两次均**转为 idle 且零文件产出**
> （`review/review-20261109/verdict.md` 未生成、`agent-out/critic-*` 为空）。
> 按重试纪律（同一目标 2 次失败即跳过，不再重试），改由组织者汇总。
> **该席位级失败按流程 fail-closed 处理：本次不给 clean APPROVE**，
> 交付等级记为 `partial`（理由=Critic 席未产出，见下「席位级失败」节）。
> 组织者已完成 Critic 的核心职责（去重、裁定、缺口检查、汇总），
> 但**不具备 Critic 的独立性**——这是本 verdict 的结构性局限，已显式申报。

## 一、评审身份块

| 项 | 值 |
|---|---|
| 批次 | `review-20261109`（全仓库核心代码审计） |
| 被审对象 | 工作区当前内容（HEAD `afa3f4c4f350e8057ab4440406148018c8b6f2bb` + 未提交改动） |
| 冻结覆盖分母 | `review-20261109/review-spec.json` |
| `spec_sha` | `48d5e2a5331a8b2b` |
| `selection_hash` | `2303a1c35ffd7f7f` |
| reviewable | **23 文件 / 15,045 行** |
| 语言清单 | C++（`cpp/mscore.cpp` 4,732 行、`cpp/ab_native.cpp` 861 行）、Python（21 文件） |
| 实际达成模式 | **仅代码**（纯静态审阅 + 本机导出探针；未操作扫雷窗口、未跑 GUI 实机） |
| 组织者模型 | deepseek-v4.1-flash（Lead）；评审席模型见各席报告 |
| 席位多样性 | code-reviewer / security-reviewer HIGH（buddy·kimi-k3.1-pro）；silent-failure-hunter MEDIUM（deepseek-official·deepseek-flash）⇒ **跨模型家族已达成** |
| 形式化验证 | **NOT_RUN**（本批未要求，未跑求解器/模型检测器） |

### 席位级失败（fail-closed 申报）

| 席位 | 状态 | 后果 |
|---|---|---|
| code-reviewer | ✅ 完成（173 行，P0=3/P1=3/P2=6） | — |
| security-reviewer | ✅ 完成（322 行，P0=1/P1=4/P2=3） | — |
| silent-failure-hunter | ✅ 完成（300 行，P0=0/P1=7/P2=15） | — |
| **critic（独立汇总席）** | ❌ **两会话均零产出** | **本次交付等级 = partial；不给 clean APPROVE** |

## 二、覆盖状态（两轨分列）

### 交付等级轨 = `partial`

- **理由**：Critic 席未产出（席位级失败）；另三席覆盖账口径不一致（见下）。
- 已 reviewed 的 23 项分母**未伪装通过**。

**三席覆盖账不一致（如实记录，不抹平）**：

| 席位 | 声明覆盖 | 与冻结分母的差异 |
|---|---|---|
| code-reviewer | 23 项 reviewed | 但其报告内的「规格哈希」`17e7133f…` 与冻结 spec_sha `48d5e2a5…` **不同** ⇒ 该席按自己补读后的分母闭合，未完全对齐冻结版 |
| security-reviewer | **21 项** reviewed | 缺 `ui/__init__.py`、`utils/__init__.py` 两项（冻结版含） |
| silent-failure-hunter | 23 项 reviewed | 已按更正后 spec 重写覆盖账，`reviewed 23 / skipped 0 / blocked 0 / waived 0` |

⇒ 严格口径：**仅 silent-failure-hunter 的覆盖账与冻结分母完全一致**。另两席的 21/23 差异属覆盖分母未完全闭合，按流程应退回补评；本次因 Critic 席缺失，由组织者在此显式记账，**不作为「已完整覆盖」宣称**。

### 合并阻塞轨 = `BLOCKED`

- **P0 未清零**（D1 栈越界仍在代码中，未修）⇒ 合并阻塞。
- P1 全部已有处置意见（见分流清单）。

## 三、去重后的分流清单

> 去重键 = 文件 + 归一化证据片段（不用标题措辞/行号）。
> 三席独立命中同一条的，合并并标注「三席命中」。

### P0 — 必修（阻塞合并）

#### **D1. PSEQ 并列候选超过 64 时栈越界写**（三席命中）

- **代码位置**：`cpp/mscore.cpp:3113` `int counts[64][10]` → 写点 `:3164` `counts[ti][mn]++`；
  同型 `:3455` `int cnts[64][10]` → 写点 `:3499` `cnts[ti][mn2]++`
- **无界下标来源**：`:3111` `const size_t nTied = tied.size();`（**无 `nTied<=64` 检查**）、`:3454` `const size_t nT = tied.size();`
- **三席命中**：code-reviewer（P0×2，拆条）／security-reviewer（P0×1，合并）／silent-failure-hunter（S-19 P1）
- **严重度裁定 = P0**。理由：内存安全（栈破坏），属「保护类主体否决」类别，按流程不得删减、不得降级。
  silent-failure-hunter 定 P1 的理由是「无法测真实触发率」——**触发率低不等于可降级**，且本仓历史崩溃形态（0xC0000005）同族。
- **组织者独立核验**：
  - `git show HEAD:cpp/mscore.cpp` 确认两处 `[64][10]` **在 HEAD 已存在** ⇒ **非本轮改动引入的既有缺陷**
  - 真实 9 局 / 367 决策点：`tied_len` **max = 36**，从未 >64 ⇒ **当前生产未触发**
  - 静态上界 = 候选格数（1 + nF + n9，可达 480）⇒ **可达性成立**
  ⇒ 结论：**真实存在、可达但低概率、既有缺陷**。P0 定级以「内存安全 + 不可预测的栈破坏」为据。
- **修法**（三席一致方向）：两处计数器改为按 `nTied` 动态分配（`std::vector<std::array<int,10>>`）；
  `row_vals[256]` / `rvals[256]` 同型无界（frontier > 256 时整批统计行被跳过 ⇒ 退随机抽签，见 S-19）一并改为动态容器；
  加 ASan/受控 >64 并列回归用例。

### P1 — 建议修（给具体修法）

| ID | 发现 | 位置 | 裁决 | 修法 | 组织者核验 |
|---|---|---|---|---|---|
| **D2** | 内存门 `estimate_mem_impl(world_upper, …)` 漏乘 `C(n9,rem)`，与前瞻 `log2_total_est` 口径不一致 | `cpp/mscore.cpp:2536`、`:2570-2584` | **P1（下调，原席报 P0）** | 先算完整世界上界 `world_upper × C(n9,rem_max)`（饱和转 int64）再传给 `estimate_mem_impl`；`py_win_rate` 物化循环加中途硬停止 | **已核**：旧门确低估（可构造 34,680 组反例；`wu=401,cand=100,n9=60,rem=2` 旧门 92.4MB / 真实 1635MB）。**但 OPTIMAL 层 PRE-GUARD 已用 `C(n9,rem_max)` 卡 `full_est`，win_rate 层新增 `COST_CAP_LOG2` 也含该项** ⇒ 其它门兜住了主要风险。真实 trace 5/155 命中该特征，全为 `certain_safe`（pbs 分支不物化世界，0.1ms）。**剩余真缺口 = PRE-GUARD 的 4096 行截断近似**，故定 P1 非 P0 |
| **D3** | 合并构建 `decide_impl` 调用漏传 `is_play`（7 参 vs 8 参） | `cpp/mscore.cpp:4575-4578`、`:4707-4709` | **P1（已修）** | 两处补 `/*is_play*/ true` | **已修并验证**：`AB_NATIVE_MERGED + MSW_TRIAL_DECIDE` 只编译不链接 `exit=0`（原会编译失败）。生产 `build.bat` 不含该宏故未暴露 |
| **D4** | `wr_threshold` 全链空转（写端活跃、读端已断） | 读端 `cpp/mscore.cpp:2432/3992/3998`（仅转发，`decide_impl` 体内**零读取**）；写端 `utils/probability.py:1048/1058/1068/1334/1339` + `_wr_update:1076/1356` | **P1** | 删除假反馈链，或把预算反馈接到真实 `cost_log2`/深度；不得恢复已知与成本反向的 `limitation` 门 | **已证**：反证测试 7 盘面 × 阈值{0,8,18,100} × 每组合 8 次 × 2 pyd ⇒ 跨阈值 `reason` 集合**全同**。边界：同 reason 内坐标因 `thread_local` PRNG 抖动（既有设计，非本参数造成） |
| **D5** | 配置允许 0..10000 且**非原子落盘** | `main.py`（原 `open(CFG_PATH,"w")`，**已改**）、`setting.py:68-74`（原只 `json.load`，**已改**） | **P1（已修）** | `validate_cfg` 集中校验 + `save_cfg` 临时文件 + `os.replace` | **已修并验证**：14 个非法输入全部 fail-closed（w/h=0、=10000、a=-1、a=w*h、类型错、缺字段、空 win_name 等）；合法 `cfg.json` 通过；写中途失败原文件完好、tmp 清理 |
| **D6** | `transitions_for` 原生入口未校验形状/尺寸，负尺寸可越界读 | `cpp/mscore.cpp:3971-3976`、`:3745-3764` | **P1** | 入口校验 `ndim==2`、`shape==(h+2,w+2)`、`w>0&&h>0`，fail-closed 抛 `invalid_argument` | 未独立复现（read 确认下游缺校验）；建议随 D1 一并修 |
| **D7** | `cost_log2` 未接观测面（本轮改动疏漏） | `utils/decide_trace.py`（白名单）、`utils/analyze_decide_trace.py`（Q1/Q2 用 `limitation`） | **P1（已修）** | trace 白名单加 `cost_log2` + `schema=2`；分析器加 Q1b（`cost_log2` vs 耗时）/ Q2b（三门槛分解） | **已修并验证**：新 trace 正确记录 `cost_log2`（如 `win_rate` 点 `cost_log2=10.0`），分析器正确输出覆盖比例与三门槛分解；旧 367 条记录报「无 cost_log2（schema=1）」不再误用 `limitation` |
| **D8** | 决策确定性契约被 `thread_local` PRNG 破坏 | `cpp/mscore.cpp` 的 `rs3/rs4/rs6/rs7`（PSEQ 残差抽签）；对照 `:2406-2414/3994-3997` 自述「PURE/无隐藏状态」 | **P1** | 二选一：①去掉 PRNG 改用确定性 tie-break；②把「存在随机抽签」显式化（返回字段 + trace 标记 + 文档改口径） | 组织者实测确认「同盘面连续两次 decide 坐标不同」（`memory_gate`/`limitation` 分支），证实**非确定性真实存在** |
| **D9** | 动态深度反解公式数学缺陷（**本轮新增代码**） | `cpp/mscore.cpp:3275-3289` | **P2（下调，因已修）** | 判据由 `base<=BUDGET` 改为 `2×base<=BUDGET`（因 `cost(d)=base×(1+2^(d-1))`，d=1 即 2×base） | **已修并验证**：修正前 22,836 组合中 1,670 违反预算；修正后 **0 违反**。性能 523ms→**490ms**，`win_rate` 仍触发 12 次 |
| **D10** | 非 MSVC 分支内建函数**自我递归** | `cpp/mscore.cpp:59-62` | **P2（已修）** | 调用真内建 `__builtin_ctzll` / `__builtin_popcountll` | **已修**：原 `return msw_ctzll(x)` 是调自身 ⇒ 无限递归。当前只用 MSVC 故不触发 |
| **D11** | 自动模式主循环无通用异常兜底，线程死亡被 UI 播报成「自动运行完成」 | `utils/solver.py`（S-01） | **P1** | 主循环外层加 `try/except`，异常时发 `warning_signal` 并置显式失败态；UI 区分「完成」与「异常终止」 | 未独立复现；PyQt5 `QThread.run` 未捕获异常行为需真机窗口模式实测（原席已申报 NOT_RUN） |
| **D12** | 悬停「按格状态转移」失败时静默回退成**最优格**数据（错格当该格显示） | `main.py:880` 附近（S-03） | **P1** | 非当前格结果**禁止回退**；显式显示「当前格转移不可用」 | 未独立复现；与 `main.py:880` P2（同源，code-reviewer 报 MEDIUM）**去重合并** |
| **D13** | 日志设施可静默降级为**零日志**（相对路径 + `except OSError: pass` + 无 stderr） | `logger.py`（S-04） | **P1** | 路径走 `setting.project_path()`；初始化失败显式告警（至少 stderr/弹窗其一） | 未独立复现 |
| **D14** | 帮助候选无条件 `is_best=True`，UI 渲染当「确定安全」，覆盖真实非 1 概率 | `main.py:1694`、`utils/probability.py:1170-1200` | **P1** | 区分 `is_recommend`（当前最优）与 `is_proven_safe`（`prob>=0.999999`）；绿底/确定文案/`clicks` 只由 prob 驱动 | 未独立复现；但 `_help_action_targets` 已用 prob 判据（部分缓解） |

### P2 — 记账

| ID | 发现 | 位置 | 处置 |
|---|---|---|---|
| D15 | `count` 单一计数混三类语义，统一播报「请检查总雷数」误导诊断 | `utils/solver.py`（S-14） | 拆分计数语义后分别播报 |
| D16 | `locate_exit()` 丢弃匹配分数直接返回坐标，`play()` 无条件点击 | `utils/vision.py`（S-05） | 保留分数，低置信不点 |
| D17 | 视觉「部分失败」（<25%）不传播：`UNKNOWN` 静默保持未开(9)，decide 照常出高置信建议 | `utils/vision.py`（S-06） | 边界判定项，需产品裁决 |
| D18 | `ab_native` 的 `arm` 对 safety/ply2/ply3/exact 静默等价 production；`fails` 恒 0 | `cpp/ab_native.cpp`（S-07/S-08/S-22） | A/B 实验可信度；随桥接修 |
| D19 | `analyze_decide_trace.py` 基于过期机制的结论、不校验 trace 覆盖的构建/配置 | `utils/analyze_decide_trace.py`（S-09） | **已缓解**：Q1 标 `[DEPRECATED]`、新增 Q1b/Q2b；建议再加 build/config 指纹校验 |
| D20 | trace 损坏行/缺失字段静默降级为假数字；记录失败全走 debug | `utils/decide_trace.py`（S-10/S-11） | 计数坏行并告警 |
| D21 | `cv2.imwrite` 返回值被忽略，写失败仍弹「保存成功」 | `main.py`（S-12） | 检查返回值 |
| D22 | `play()/help()` 不校验窗口句柄（`hwnd=0` 仍算坐标并点击） | `utils/solver.py`（S-13） | 句柄为 0 时中止 |
| D23 | 预览浮层边界 off-by-one（`cell_value` 的 0 哨兵被当已开格） | `ui/preview_popup.py:890` 附近（S-15） | 像素级回归验证 |
| D24 | `MSW_COST_CAP_LOG2` / `MSW_DSTAR` 非法值静默回落默认 | `cpp/mscore.cpp`（S-20） | 非法值告警 |
| D25 | `g_last_pbs_diag` 只写不导出（死信） | `cpp/mscore.cpp`（S-21） | 加 `m.def` 导出 |
| D26 | OPTIMAL 层失败静默落层（`optimal_skip`/nodecap/引擎异常）无 reason | `cpp/mscore.cpp:2799/2927/2931/3557`（S-18） | 加 `fallback_from` 诊断字段 |
| D27 | 所有 C++ 候选被标「安全概率（精确解）」，含 pbs/win_rate 启发式 | `main.py:88` | 带 `value_kind`/`reason` |
| D28 | 帮助统计栏把 `best_conf` 无条件写成「当前格非雷的概率」 | `main.py:1466` | 按 `value_kind` 分类措辞 |
| D29 | 自动/帮助内层 decide→点击循环不检查 `_stop_requested()` | `main.py:1305` | 每次 decide 后检查取消 |
| D30 | 默认配置含本机绝对安装路径 | `cfg.json` | 脱敏 + `.gitignore` |
| D31 | 旧 pyd 无 `transitions_for` 时 UI 回退 best_cell 但标题仍是悬停格 | `main.py:880`（与 D12 同源，P2 面） | 同 D12 |

## 四、盲区清单（三席共同未覆盖的面）

Critic 席缺失，本节由组织者以「第一手静态判断」列出。**以下各项都没有被任一席真正审过**（我 read 了代码但未做完整评审）：

1. **`EstimateMemImpl` PRE-GUARD 的 4096 行截断 + `rem_walk_truncated` 重走逻辑**（`cpp/mscore.cpp:2614-2690`）
   —— 该段自称「P1-3 fix」求精确 `rem_max`，但**正确性未被任何人验证**。这是 D2 剩余真实缺口的所在。
2. **`ExactEngine::after_click` 数值正确性**：`v_opt` / `confidence` 是否可能落在 `[0,1]` 外、是否可能 NaN（浮点）。
3. **Qt signal/slot 的默认参数陷阱**（`main.py` / `ui/preview_popup.py`）：本仓历史上出过「带默认参数的槽连 `clicked` 被 `bool` 覆盖」的 bug；三席均未专项扫这一类。
4. **帮助模式 `is_play=False` 与自动模式的分支差异对照**（`DSTAR×3`、`HELP_WR_BONUS=10`、`HELP_NODE_CAP=6e6`、`HELP_BUDGET_MULT=3`）——无任一席做逐项对照。
5. **本轮新增动态深度前瞻的完整正确性**：组织者已核数学反解（D9，已修）与性能（490ms），但**未核**「深度对最终选格质量的实际影响」（需 A/B 或 regret 评估）。
6. **`bench/` 255 个实验脚本**：按信封明确排除，未审（如有基于它们的结论引用，需另行审计）。
7. **`tests/` 12 文件 5,794 行**：未审（本批聚焦生产代码）。

## 五、组织者已独立完成的核验与修复（可直接采用）

| 项 | 内容 | 证据 |
|---|---|---|
| 核验 A | P0 栈越界是**既有缺陷**，非本轮引入 | `git show HEAD:cpp/mscore.cpp` 含两处 `[64][10]` |
| 核验 B | P0 当前生产**未触发** | 9 局/367 点 `tied_len` max=36 |
| 核验 C | D2 应定 **P1 非 P0** | 34,680 组可构造反例，但 PRE-GUARD + COST_CAP 兜住；trace 5/155 命中且均不物化 |
| **修复 D3** | 合并构建参数 arity | 编译-only `exit=0`（原失败） |
| **修复 D5** | 配置校验 + 原子落盘 | 14 负向用例全 fail-closed；中途失败原文件完好 |
| **修复 D7** | `cost_log2` 接观测面 | 新 trace 正确记录；分析器 Q1b/Q2b 正常 |
| **修复 D9** | 动态深度反解数学 | 22,836 组合 0 违反；性能 523→490ms |
| **修复 D10** | 非 MSVC 自递归 | 改真内建 |
| 全量回归 | 318 tests | **OK**（修复后复跑，无回归） |

## 六、人可复述的最小结论（5 条）

1. **有一个真实的栈越界 P0**：C++ 里 `counts[64][10]` 遇到超过 64 个并列候选会写坏内存——它从旧版本就存在，当前对局没触发（实测最大 36），但属于必须修的内存安全问题。
2. **`limitation` 已完全退出决策**，`wr_threshold` 也变成了空转旋钮：Python 一直在算并持久化它，C++ 已不读取——自适应调参是假的，已用反证测试证实。
3. **我本轮加的动态深度前瞻公式有数学错误**（`d=1` 实际成本是 `2×base`，却用 `base` 判门槛），已修正并验证 0 违反、性能还更好（490ms）。
4. **配置层原来既无校验也非原子写**：可输入 `10000×10000` 让 UI 创建一亿个按钮、或写盘中断后应用再也起不来——已加集中校验 + 原子保存，14 个非法输入全部拒绝。
5. **独立汇总席（Critic）两次都没产出**，所以本次只能给 `partial` 不给 `APPROVE`；三席覆盖账也不一致（21/22/23 项），其中只有 silent-failure-hunter 与冻结分母完全对齐。

## 七、证据边界

- **实际达成模式 = 仅代码**：无 GUI 实机操作、无真机点击、无真机窗口模式测试。
- 采样口径：真实运行数据来自 **9 局 / 367 决策点**（非 20 万局），所有「未观察到」表述均按此口径，不外推为「不存在」。
- 修复项均附「改前/改后」可复现读数；未修项标注为记账。
- 全量测试 318 项通过，但**不代表 P0 已被覆盖**（无 >64 并列的回归用例，需 ASan 构造）。
