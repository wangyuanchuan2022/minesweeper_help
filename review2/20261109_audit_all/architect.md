# 全量只读架构审计报告（scan --all）

## 0. 评审身份块

| 项 | 值 |
|---|---|
| 角色 / 席位 | Architect（只读架构审计，不实现变更） |
| 会话 ID | session-51a65504-719b-4a82-a0ea-72984604dbe3 |
| 仓库 / 分支 | `D:\python\projects\minesweeper_help` / `main` |
| head_sha | `afa3f4c4f350e8057ab4440406148018c8b6f2bb`（`git log -1 --format=%H` 实测） |
| 规格信封 | `review2/review-spec.json`（本报告与另一席引用同一路径） |
| 语言清单 | Python 3.10、C++17（pybind11）、Qt `.ui` XML、Batch、Markdown/JSON |
| 模型 | 当前执行模型 `deepseek-official/deepseek-flash`；任务书原档位为 `buddy/kimi-k3.1-pro`，会话中途发生模型切换，此处不冒充原模型运行 |
| 实际达成模式 | **仅代码**（三值枚举中的“仅代码”；未做实机、未做截图） |
| 降级理由 | DSH 沙箱为低完整性级别，无法向 Medium IL 的 Arbiter 发送输入；本席为只读白名单席位，不运行会写 `state/`、`__pycache__`、截图或 GUI 的生产测试；无真实屏幕、真实 GUI 事件循环和已安装 pyd 的可执行核验。故不以实机证据自报。 |
| spec 分母 | 22 个 Python 模块 / 5287 可执行行（`review-spec.json:47-53`）；另审计 `cpp/mscore.cpp` 3766 行、`cpp/build.bat`、tests 目录与指定 bench。 |
| 只读边界 | 未修改任何生产/测试/配置文件；本报告是唯一写盘产物。 |

### 0.1 实际达成模式与工具边界

- **实机**：未达成。真实 E2E 必须由用户在自终端运行（README:108-110、docs/现行架构与测量.md:236-238）。
- **截图**：未达成。没有可注入的 Arbiter 窗口，不能用截图替代运行证据。
- **仅代码**：已达成。所有结论来自逐文件阅读、`grep` 交叉检索、`git log`/`git diff` 只读命令；运行时行为影响均标注为静态推断。

### 0.2 断言鉴别力自查（R5 精神）

1. **能锚定的断言**：本报告 P0/P1/P2 每条均有 `文件:行号` 且为逐字命中；没有“某处可能有”的泛化结论。
2. **需要运行才能闭合的断言**：`counts[64][10]` 越界是否在真实盘面触发、帮助候选是否存在误点、GUI 悬停是否造成可感卡顿——本报告均未写 PASS，统一标注为 **NOT_RUN**，并给复跑方式。
3. **阳性对照**：grep 命中均以已读文件原文复核；未用零命中作为“不存在”的结论（唯一“不存在第二套生产决策调用”结论由调用链阅读支持，并在盲区说明其边界）。
4. **仪器可信度**：未运行 `tools/coverage_probe.py`，故不引用任何新覆盖率数字；仅引用 spec 中既有基线 5287 行/22 模块及其 caveat（`review-spec.json:47-53`）。
5. **形式化验证**：本任务不是并发协议定理证明，未启动 S1-S7；`thread_local` 缓存不共享属 C++ 语义可直接判定的性质，凡涉及“永不死锁/永不丢消息”的更强全称命题均 **NOT_RUN**，不冒充已证。

---

## 1. Summary（摘要）

三分钟结论：主决策链已经收口为 `validate_board_consistency → native.decide → C++ OPTIMAL/pbs+PSEQ`，没有发现自动模式调用第二套 Python 决策实现；真正的高风险不在算法分支，而在**执行侧的语义污染和边界失守**。最严重的是帮助模式把“最佳但不确定”的格子并入“确定安全”候选，导致“点击所有确定方格”可能点击非确定格（P0-1）；其次是 C++ `counts[64][10]` 对无界 `nTied` 的栈写入（P0-2）与 GUI 悬停线程重建世界集（P0-3）。1.5GB 内存闸门、取消信号、决策 trace 和 UI 数值语义都存在“看起来有、实际不覆盖全链”的结构性缺口，建议按“C++ 固定上界 → 单次 decide 适配器 → GUI 异步预览 → 测试/构建 provenance”的顺序修复。

## 2. Analysis（分层、依赖与逐项发现）

### 2.1 实际依赖图（静态阅读）

```text
main.py (GUI/主线程)
  ├─ ui/__init__.py → ui/window.py, ui/edit_setting.py, ui/screenshot.py
  │    └─ ui/window.py:35 运行期 `from main import oklch_grad`（反向依赖，见 P2-3）
  ├─ utils/__init__.py → mm0(ClientToScreen/minesweeper_run) + solver.Solver
  │    └─ solver.py (AutoPlayThread = QThread)
  │         ├─ vision.BoardVisionMixin（截图/模板匹配，pyautogui/PIL/win32）
  │         ├─ deduction.DeductionMixin（残留兼容推理，不在 play/help 主链）
  │         └─ probability.ProbabilityMixin（校验 + decide 调用 + 点击/重扫）
  │              ├─ native.py → mscore.cp310-win_amd64.pyd
  │              │    └─ cpp/mscore.cpp（py_decide → decide_impl）
  │              └─ e2e_probe / decide_trace（观测，失败吞掉）
  └─ ui/preview_popup.py（纯展示；但数据由 main._transitions_for_cell 在主线程取）

setting.py（import 时 chdir，模块副作用）→ logger/color 为底层叶子。
```

**方向判断**：生产决策方向的 `GUI → Solver → probability → native → pyd` 没有发现反向调用；`ui/window.py:35` 的 `main` 反向 import 是显示资源的运行期回边，不参与决策但破坏 UI 叶层契约。`utils/__init__.py:1-18` 在包导入时加载 pyautogui 并连带导入 Solver，导致“导入工具函数”也拉入 Qt/pyautogui 副作用，是测试隔离成本高的结构性原因之一。

### 2.2 决策链路完整性

- 入口：`utils/probability.py:830-879` 在每次 decide 前校验盘面；`utils/probability.py:901-922` 只在 `native.has_decide()` 时调用 C++；失败统一走 `_decide_unavailable`（`:612-679`），无 Python 回退。**这符合设计目标，不是缺陷。**
- C++ 决策：`cpp/mscore.cpp:3080-3094` 的 `py_decide` 调 `decide_impl`，`decide_impl:1852-1888` 先切前沿/内部格；`:2130-2325` 为 OPTIMAL，`:2372-2648` 为 pbs+PSEQ。结果坐标由 Python 点击（`probability.py:1141-1206`），帮助模式只收集不点击（`:954-1102`）。
- 批点击取消：自动 `certain_safe` 批量内部有取消检查（`probability.py:1148-1155`），自动 decide 循环入口也有（`:1109-1115`）。但一旦进入 C++ 的 `ExactEngine::after_click` 循环（`cpp/mscore.cpp:2248-2252`）没有取消回调，取消只能等 C++ 返回，见 P1-2。
- 旁路：`deduction.DeductionMixin` 仍是 `Solver` 的 MRO 成员（`utils/solver.py:248`），但 `play()/help()` 的生产分支不再调用 `mine_clear1/mine_clear3_1`（`utils/solver.py:891-902` 明确删除）；残留方法仍可直接点击（`utils/deduction.py:74-104,154-160,207-213`），属于“可被外部/历史脚本误调用的第二实现残骸”，不是当前主链，见 P2-1。

### 2.3 P0 发现

#### P0-1 帮助模式把“最佳但不确定”并入“确定安全”，批量执行器会点击非确定格

**证据链（逐字锚定）**：
- `utils/probability.py:1018-1021` 只把 `prob>=0.999999` 放入 `_safe_cells`，语义正确。
- 但 `utils/probability.py:1026-1028` 随即无条件执行 `if rx >= 1 and ry >= 1: _safe_cells.add((rx, ry))`；`rx, ry` 来自 decide 的 best，不要求 `prob==1`。
- `utils/probability.py:1044-1056` 给该候选写 `confidence = float(prob.get((bx,by), 1.0))`（默认 **1.0**）、`is_best=True`、`is_recommend=True`，没有“是否已证明安全”字段。
- `main.py:2052-2064` 的 `_help_action_targets()` 对候选表执行：`elif item.get("is_best") or conf >= 0.99: clicks.add((x,y))`，因此 `is_best=True` 的非确定格进入安全点击集合。
- `main.py:2094-2101` 对 `clicks` 逐个 `pyautogui.click(...)`；帮助模式“点击所有确定方格”没有二次概率复核。

**根因**：候选数据结构只有一个无来源的 `confidence` + 两个展示语义位（`is_best/is_recommend`），把“推荐探索”与“已证明安全”压成同一可执行类别；C++ 的 `certain_safe` 证据（`cpp/mscore.cpp:2400-2413` 返回整批确定格）没有在跨层契约中保留为独立的 `proven_safe` 位。

**运行时状态**：**NOT_RUN**。本席不能点击真实 Arbiter；但从上述静态控制流可确定，只要某轮 C++ 返回 `reason` 非 `certain_safe` 且 `x/y` 有效，该 best 格就会进入“安全格”集合。复跑方法见 §5。

**【改进点】**：在 C++ 返回和候选字典中增加显式 `safety_proof: "certain_safe" | "prob_1" | "none"`（或复用 `reason` 做受控映射），只允许 `safety_proof != none` 或 `prob >= 1-eps` 进入 `clicks`；最佳不确定格单独进入“推荐”列表且不参与批量点击。新增负向测试：`confidence=0.5, is_best=True` 必须不进入 `clicks`，并构造 `clicks` 不被调用的断言。

#### P0-2 C++ `counts[64][10]` 与无界 `nTied`：固定栈数组写越界

**证据链**：
- `cpp/mscore.cpp:2525-2528` 定义 `const size_t nTied = tied.size(); int counts[64][10];`，没有 `nTied <= 64` 的断言或截断。
- `cpp/mscore.cpp:2552` 外层循环 `for (size_t ti = 0; ti < nTied; ++ti)`，`:2578` 写 `counts[ti][mn]++`；当 `nTied > 64` 即写越界。
- `tied` 来自 pbs/PSEQ 的候选集合（`:2427-2496`，`tied` 可包含所有与 best 在 `0.0005f` 内的前沿候选；生产盘 480 格，候选上界远高于 64）。
- 另一个固定缓冲 `row_vals[256]`（`:2535`）因 `:2542` 的 `flat < 256` 与 `:2548` 的 `flat != ordered_clicks.size()` 守卫而不会越界，但它导致更大前沿被静默跳过，和 `counts` 风险是两个不同问题。
- `README.md:185-186` 已把“`counts[64][10]` 固定数组与无界 `nTied`”列为遗留 P0；本次代码审计确认该描述仍成立，不是已修项。

**根因**：C++ 用“经验最大观测 35/300 局”当作容量上界，但函数契约没有把该经验上界编码成输入断言；PSEQ 的 tie 集合由运行时数据决定，存在结构性无界。

**运行时状态**：**NOT_RUN**（未编译/未注入超过 64 的 tie）。这是 UB 级风险，不应等真实盘面采样来证明是否存在。

**【改进点】**：把 `counts` 改为 `std::vector<std::array<int,10>>` 按 `nTied` 分配，或显式 `if (nTied > CAP) return fallback`（fail-closed 到确定性 tie-break）；同时把 `row_vals` 改为动态容器，避免“大前沿被静默丢弃”。加入 C++ 单测/诊断导出，直接以 65/256/480 个 tie 注入验证不越界、不崩溃。

#### P0-3 GUI 悬停会在主线程重建世界集；C++ `thread_local` 缓存跨线程不命中

**证据链**：
- `cpp/mscore.cpp:2690` 的 `static thread_local TransitionEngineCache g_tr_cache;`，注释明确“帮助线程与 GUI 线程各自独立”。
- 帮助线程在 `cpp/mscore.cpp:3043-3054` 只填充**当前线程**的缓存；`:3068-3077` 命中要求同一线程的 `g_tr_cache.valid`。
- GUI 侧 `main.py:895` 直接同步调用 `_native_mod.transitions_for(...)`；缓存未命中时 `cpp/mscore.cpp:3075` 走 `help_exact_transitions`，其物化上限 `:2674` 为 200,000 世界，循环 `:2962-3003` 在 GUI 线程执行。
- `main.py:934-950` 的自动页 hover 也走同一条 `_transitions_for_cell`；而 `main.py:1966-1977` 在第帧给所有有概率的热力图格挂上 `_preview_payload_for_heatmap_cell`，实际让“Auto 页不接状态转移”的设计承诺失效。文档 `docs/现行架构与测量.md:104`、README:28-29 声明 Auto 不接。
- 即使同一线程，第一次悬停若缓存指纹不匹配，也会同步重建；这不是仅“跨线程一次 miss”，而是 UI 线程承担最重枚举的结构性路径。

**根因**：缓存按线程隔离，但“计算的生产者”和“消费的 GUI”分属不同线程；没有把转换计算作为服务层异步化，也没有在 Auto 页禁用该路径。

**运行时状态**：**NOT_RUN**（未做 UI 帧耗时测量）。结论是调用关系与线程归属的静态事实；性能影响的数值需真机验证。

**【改进点】**：新增 `TransitionService(QObject)` 在独立 QThread 计算，GUI 只提交请求并按 `(board_fp,x,y)` 接收结果；缓存归 Service 单线程所有；自动页不挂预览，帮助页悬停显示 loading 并合并同格重复请求。收益是主线程不再承担枚举，代价是多一层异步状态与“结果到达时盘面已变”的失效处理（必须用指纹丢弃过期结果）。

### 2.4 P1 发现

#### P1-1 “1.5GB 硬闸门”只在 OPTIMAL 分支计账，且 pybind 参数可覆盖

**证据链**：
- `cpp/mscore.cpp:1447` 定义 `DEFAULT_MEM_BUDGET=1536MiB`；`:1674-1685` 的 `estimate_mem_impl` 只计算 `worlds*candidates*WORLD_BYTES`（2304）并返回 `allowed`。
- `decide_impl` 的 `mem` 计算在 `:1993-2009` 使用 `bits_bytes=88`，与 `estimate_mem_impl` 的 2304B 口径不同；`:2130` 只有 OPTIMAL 受 `mem.allowed` 约束。
- pbs 的调用在 `:2372-2376`，其 `py_pbs_compute` 在 `:2509-2519` 复制 `gmats` 后才继续；`:2646` 即使 `!mem.allowed` 也只把 reason 改成 `memory_gate`，仍然计算并返回 pbs/PSEQ 结果。也就是说，超大局面并不会被内存闸门阻止，只是被贴上标签。
- `py_decide` 的导出签名暴露 `mem_budget` 参数（`:3082`、`:3202`），Python `native.decide` 不转发它（`utils/native.py:99-135`），但任意直接调用 `mscore.decide(..., mem_budget=...)` 的 bench/未来代码可把上限调高；“用户铁律不可调”在当前 C++ 导出面上不是硬约束。
- 此外 `help_exact_transitions`（`:2957-3003`）自行以 `HELP_TRANSITION_WORLD_CAP=200000` 物化世界，没有经过 `mem.allowed`，其内存约为 200k×Bits 级，独立于 1.5GB 闸门。

**根因**：内存“估算函数”被当成“资源边界”，但真正的大内存分配发生在 `py_part_solve`、pbs gmats 复制和帮助转移枚举中；闸门位置在计算之后、覆盖仅一个分支，且预算是可变参数。

**运行时状态**：**NOT_RUN**（未进行 RSS 压测）；这是控制流与参数面的事实。

**【改进点】**：把 `mem_budget` 从生产 `decide` 导出签名移除或 clamp 到 `min(provided, DEFAULT_MEM_BUDGET)`；将 `estimate_mem_impl` 的字节口径统一（worlds×candidates×实际对象大小，并在文档标明估算 vs 实测）；在 `py_part_solve`/pbs 物化前做 fail-closed 预检；帮助转移枚举复用同一预算。权衡：更早拒绝可能让更多局面走 pbs，需用同 seed 胜率/延迟对拍确认质量不退化。

#### P1-2 取消信号无法穿透 C++ 核心，3 秒后 `QThread.terminate()` 绕过 finally

**证据链**：
- `main.py:63-65` 说明 3 秒超时后强杀；`main.py:1072-1093` 先 `request_cancel()`，`wait(3000)` 后 `thread.terminate(); thread.wait(1000)`。
- 协作安全点覆盖 Python：`utils/solver.py:628-630,726-728`、`utils/probability.py:1109-1115,1148-1155`；但 C++ `ExactEngine` 的候选循环 `cpp/mscore.cpp:2248-2252` 与 pbs/枚举路径没有接收任何取消 token。
- `AutoPlayThread.run` 的 finally 只停止热键（`utils/solver.py:329-332`），`help()` 的 finally 恢复模块预算乘数（`:958-960`）；`terminate()` 会绕过它们，可能把 `_DECISION_BUDGET_MULT` 留在 3.0、跳过统计/正常清理。
- `terminate()` 在 C++ 栈帧中强杀还可能遗留未释放的 STL/向量与该线程持有的资源；Qt 文档不保证安全性。该路径只在超时故障时进入，但“用户按 ESC 一定可停”的全称承诺不成立。

**根因**：取消是 Python 协作式 Event，而最长的计算段在 C++ 且不轮询；GUI 用“超时强杀”弥补，导致清理契约断裂。

**运行时状态**：**NOT_RUN**（未做真实 ESC 触发计时）。静态事实是：任何单次 decide 超过 3 秒，用户命令无法在安全点完成。

**【改进点】**：C++ 决策入口接收 `std::atomic_bool*`/回调取消 token，在 `ExactEngine` 每次候选/每 1024 节点和 pbs 枚举中轮询；GUI 超时后先取消并等待，必要时提示用户重启应用，不把 `terminate()` 作为常规兜底。权衡：C++ 增加轮询会带来极小常数开销，换取可预测终止；对性能敏感的批处理可作为可编译选项关闭。

#### P1-3 决策 trace / UI 数值语义没有“来源字段”，精确安全与启发式胜率混用

**证据链**：
- `main.py:88-99` 的 `_EXP_TO_KIND` 把任意包含“C++ 三层决策”的候选映射为 `exact_safety`；但 C++ 的 pbs 路径并非精确安全概率（`cpp/mscore.cpp:2372-2426` 的 PSEQ 启发式），`reason` 可包含 `pbs_n...`。
- C++ OPTIMAL 的 `confidence` 是 `v_opt`（策略值，`:2314-2316`），pbs 的 `bestv` 是 PSEQ 的最大安全近似（`:2427-2432,2645-2648`）；Python 候选 `exp` 统一写 `f"C++ 三层决策 ({reason})"`（`utils/probability.py:1055`），未标注数值语义。
- `main.py:1456-1468` 帮助统计栏固定显示“当前格非雷的概率: {best_conf}”，`:1926-1944` 自动页同样；而 `best_conf` 可能是策略值/近似值。`main.py:165-201` 的类型推断只在 `best_conf` 与 `prob[best]` 同值时判 `exact_safety`，无法识别 OPTIMAL 的 `v_opt` 与 pbs 的启发式。
- `utils/native.py:99-111` 的 docstring 说返回 `confidence/path/reason/prob/tied/weighted`，没有定义 confidence 的口径；跨层契约因此只能靠注释推断。

**根因**：C++ 返回面是“数值 + 自由文本 reason”，没有机器可判定的 `metric_kind`/`probability_basis`；Python UI 以字符串前缀猜类型，导致“精确解”标签覆盖启发式结果。

**运行时状态**：**NOT_RUN**（未对真实 payload 做逐帧断言）。静态映射链完整。

**【改进点】**：C++ `make_response` 增加结构化字段 `metric_kind: "safety_probability"|"strategy_value"`、`probability_basis: "exact_worlds"|"pseq"`、`proven_safe`；Python 直接透传并按字段显示，不再从中文文案分类。补两条金样本：pbs reason 必须显示“启发式”，OPTIMAL 的 v_opt 不得显示成“非雷概率”。权衡：C++ 返回 JSON 略增字段/序列化成本，换取 UI 可验证语义。

#### P1-4 观测链有“独立链路”名义，但上限、字段和覆盖点与文档不一致

**证据链**：
- `utils/decide_trace.py:38` 设 `_MAX_BYTES=20MB`，超过停止；而 `docs/现行架构与测量.md:160-162` 说该文件“无文件上限，是探针截断时唯一完整的决策链证据”，`README.md:170-171` 又说 20MB 上限截断的探针已删。两者对同一核心证据源的承诺不一致。
- `decide_trace.record` 的字段白名单 `utils/decide_trace.py:118-137` 不含 `situations`、`dstar`；C++ 在 `cpp/mscore.cpp:3100-3101` 返回这两个字段。于是 trace 无法独立复算 OPTIMAL 准入哨兵，分析器只能依赖 probe。
- 帮助模式的后续 decide 在 `utils/probability.py:1084-1089` 调用 native，但未调用 `decide_trace.record`/`probe.decision`；同文件自动循环的后续 decide 则在 `:1207-1224` 有记录。帮助多轮决策链不是完整 trace。
- `utils/analyze_decide_trace.py:138-164` 仍在讨论 `COST_CAP=20` 与 LAYER 2 门；`utils/analyze_decide_trace.py:198-205` 的说明仍按 `world_upper × candidates × 2304B`，而 decide 的 mem 已使用 88B（`cpp/mscore.cpp:1993-2009`）。分析器与生产口径不同步。

**根因**：三个观测入口（probe、trace、分析器）各自手写字段与上限，没有唯一的 `decide invocation` 适配器在调用边界统一采集；决策调用点分散在 `probability.py` 两处循环。

**运行时状态**：**NOT_RUN**（未生成/未解析新 trace）；字段与上限是静态事实。

**【改进点】**：建立唯一 `invoke_decide(cell_value, a, w, h, **kw) -> dict`，内部完成 native 调用、耗时、trace/probe 记录、异常归一化；所有调用点（first/auto loop/help loop）只走该入口。trace schema 补 `situations/dstar/metric_kind`，上限用滚动文件或显式 `probe_stopped`，分析器按 schema 版本分支。权衡：适配器增加一层函数调用与字段耦合，但换来“每个决策都有同等证据”的可验证性。

#### P1-5 `game_stats` 是累计真值源，但写入没有进程/运行域隔离

**证据链**：
- `utils/solver.py:41-60` 的 `_gs_load/_gs_save` 读写 `state/game_stats.json`；`:617-620` 在每轮 play 中读取累计值，`:706-709` 胜负分支先累加再 `_gs_save`。
- `utils/e2e_probe.py:224-262` 的局事件不携带运行域；`bench/e2e_900.py:203-216` 只在 runner 内读起始累计，`:418-421` 按进程 `t_run_start` 切 probe，`:429-445` 再用累计差校正。`game_stats` 本身没有 run_id，无法在文件层区分并发进程、用户手动运行和 runner。
- `_gs_save` 使用固定 `path + ".tmp"` 原子替换（`utils/solver.py:53-58`）；两个进程同时写会互相覆盖 `.tmp`，最后写者胜，累计差不再等于真实本局增量。GUI 与 bench 同时驱动同一 Arbiter 时，runner 的 `t_run_start` 过滤和 `game_stats` 差值的交叉校验会失效。

**根因**：把“跨进程累计统计”与“单次实验计数”共用同一个可变 JSON，没有锁/run_id/单调日志；e2e runner 的真值源依赖一个无所有者的文件。

**运行时状态**：**NOT_RUN**（未并发运行 GUI 与 runner；沙箱无法实机）。静态看，固定 `.tmp` 与无锁读改写已足以构成竞态。

**【改进点】**：`game_stats.json` 只作为展示缓存，实验真值改为每进程带 `run_id` 的 append-only 事件（或对 `game_stats` 加文件锁并写入 `last_run`/`pid`）；e2e runner 通过 run_id 过滤并在 summary 中带 `run_id/pyd_sha8/t_run_start`。权衡：多一个文件/锁，换可复现的测量底账。

### 2.5 P2 发现

#### P2-1 旧推理残骸与旧进度/预算代码仍在生产类上

- `utils/solver.py:248` 的 `Solver(BoardVisionMixin, DeductionMixin, ProbabilityMixin, AutoPlayThread)` 仍把 `mine_clear1/mine_clear3_1` 暴露为实例方法；`utils/deduction.py:26-106,244-256` 直接调用 `pyautogui.click`。
- `README.md:141` 说 `utils/deduction.py` 是“兼容层推理（生产已移除）”，`utils/native.py:19` 又说“帮助模式保留的 mine_clear1/mine_clear3_1 属推理提示”，两处互相矛盾；`utils/native.py:8-19` 顶部还保留“热点方法纯 Python 回退”的旧语义，而决策层已明确不回退。
- `utils/probability.py:299-532` 的 `_ps_*` / `_tb_*` / win_rate 历史逻辑仍被 `_number5_1_core:758-769` 初始化与 `:600` 的 `native.tuned()` 间接消费；`utils/native.py:167-181` 的 `tuned/MSW_NATIVE_TUNE` 与 docs/现行架构与测量.md:79 的“MSW_NATIVE_TUNE 全删”不一致。
- **影响**：当前 `play/help` 不调用这些方法，不改变决策；但它们是可以被外部脚本、测试或未来编辑误接的第二套逻辑，增加“唯一决策路径”的维护成本。**【改进点】**：把 `DeductionMixin` 从 `Solver` MRO 拆出为 `LegacyDeduction`（或删除并保留明确的 bench 迁移），进度/预算残留按引用计数清理；保留旧 API 需在 README/注释中标注“非生产、无回退语义”。权衡：删除会影响历史 bench，需先做 `bench/audit_dead_code.py` 引用审计。

#### P2-2 构建不可复现性与安装脚本没有 provenance

- `cpp/build.bat:31-38` 使用 `/O2 /std:c++17 /D MSW_TRIAL_DECIDE`，没有 `/Brepro`、`/GL` 说明或源文件哈希；MSVC 构建产物可能因路径/时间/PE 元数据变化而哈希不同。`docs/现行架构与测量.md:232-234` 却用 pyd sha8 作为版本标识，`README.md:47-49` 的安装流程只是复制。
- `.gitignore:24-25` 忽略 `utils/mscore*.pyd` 与 C++ 中间物，仓库 head_sha 无法唯一确定运行二进制；`apply_native_fix.cmd:54-81` 只备份/覆盖，不验证 `MSW_TRIAL_DECIDE` 导出、`is_play` 签名或源哈希。
- 本报告**未核验** pyd 当前 sha8 45C18B2F：没有在只读席执行哈希工具，也不把用户提供的值当实测证据。
- **改进点**：构建后生成 `build_manifest.json`（compiler 版本、源码 hash、`MSW_TRIAL_DECIDE`、导出列表、pyd sha256），安装脚本校验 manifest 与目标导出；若要求逻辑等价，用“同源输入 + 输出 dict 对拍”替代二进制哈希。权衡：构建与安装多一步校验，换来可追溯性。

#### P2-3 `ui/window.py` 反向 import `main`，且生成文件被手工改写

- `main.py:33` `from ui import ...`，而 `ui/window.py:35` 在 `_make_gradient_qss` 内 `from main import oklch_grad`；这是运行期延迟回边，失败时 fallback 到 sRGB 线性渐变（`:36-41`），会导致图例与现场色带不一致但**静默**沿用。
- `ui/window.py:30-32` 明确注释该文件是 pyuic 生成物但含手工改动；`README.md:132` 也要求“手工同步，勿整体 pyuic5 重生成”。生成物与手工改动混合会提高后续 UI 迁移的回归风险。
- **改进点**：把颜色渐变提取到 `color.py` 或 `ui/theme.py`（无 GUI 依赖），`ui/window.py` 只引用；为手工改动建立小型 patch 脚本或把改动搬回 `main.py` 运行时覆盖。权衡：一次移动会触碰生成文件，但消除循环 import 和再生成丢失。

#### P2-4 测试/覆盖率结构性障碍：断言容易变成“为绿而测”

- `tools/coverage_probe.py:30-45` 的分母只含 Python 函数体，明确排除 C++/真实 GUI/Arbiter；`:91-134` 用 `sys.settrace` 跑 unittest，无分支覆盖与 C++ 覆盖。`review-spec.json:47-53` 自己声明“与 coverage.py 近似不等价、不含分支/GUI/Arbiter”。
- `tests/test_cpp_decide_wiring.py:1163-1178` 在 native 不可用、盘面异常或走 OPTIMAL 时 `skipTest`，因此 transitions 的“非 OPTIMAL 真实转移”可能长期无样本；`:1201-1203` 类似。`tests/test_ui_migration.py:1097-1108` 的正向用例把 `is_best=True` 候选当作安全格，**恰好巩固了 P0-1 的错误语义**，没有“非确定 best 不得点击”的负向断言。
- `tests/test_ui_migration.py:1065-1069` 也用 `confidence=1.0, is_best=True`，没有 `confidence=0.5, is_best=True` 对照；`tests/test_cpp_decide_wiring.py:215-246` 只验证帮助不点击，未验证候选可执行性边界。
- 风险面：若以“覆盖率 100%”为目标，最省力的做法是继续 mock 并扩写正向断言，可能把 P0-1 的错误行为固化为“预期”；反之，应把关键 P0 转为可注入的纯函数/负向测试。
- **改进点**：新增 seam 而非围绕 mock 加断言：① `DecideInvoker`（纯输入/输出 + trace）；② `CandidateClassifier`（输入 C++ dict，输出 `proven_safe/recommended`，可单测）；③ `TransitionService`（无 Qt 核心 + 线程边界）；④ `ClickSink`（注入 `pyautogui` 替身，验证 clicks 是否允许）。权衡：抽象层略增代码，但把“真实屏幕/Arbiter/Qt”从判据中剥离。

#### P2-5 E2E runner 的目标局数用 append-only 历史文件计数，可能提前停止/重复计数

- `bench/e2e_900.py:119-133` 的 `_count_game_ends` 统计整个 `probe.jsonl` 的历史 `game_end`，不按 `t_run_start`/run_id 过滤；`:355-357` 与 `:374-377` 用该计数判断“已达标”并停止/重启。
- 同文件 `:418-421` 在最终汇总时又按 `t_run_start` 过滤，导致**运行期判定**与**汇总口径**不一致；若 `probe.jsonl` 有上一轮记录，runner 可能在启动后立即认为达标，或在重启时传错剩余局数（`:367` 以 `args.games - n_done`，其中 `n_done` 是历史累计）。
- `--resume` 分支 `:423-427` 把 `done + games` 合并，但 `:291` 启动线程仍传 `target=args.games`，未扣减已有完成局数；断点续跑可能超出目标。此路径影响测量证据而非生产，但会污染“900 局”可信度。
- **改进点**：runner 启动时生成 `run_id`，只统计本 run 事件；resume 时按已完成局数设置剩余 `target`；`probe_summary`/summary 统一带 `run_id/t_run_start`。权衡：改动 runner 与 summary schema，需重跑冒烟验证。

#### P2-6 文档“未闭合项”与代码现状有 2 处明确漂移，其余多为未验证

- `docs/现行架构与测量.md:253-264` 的 #2 写“`coarse_cap = DSTAR` 提案未验证”，但当前 C++ 已是 `coarse_cap = DSTAR * 4`（`cpp/mscore.cpp:2075`，`docs/现行架构与测量.md:55` 也写 `DSTAR×4`）；README:183-184 又写“`coarse_cap = DSTAR*4` 收紧为 `DSTAR` 的提案未验证”。同一文件内口径不一致，容易让后续优化基于错误前提。
- `docs/现行架构与测量.md:79` 说 `MSW_NATIVE_TUNE` 全删，但 `utils/native.py:179-181` 仍定义 `tuned()` 并在 `utils/probability.py:600` 使用；该环境变量已不影响 C++ 决策，但仍影响进度心跳/`_ps_refit_k`，文档应改为“仅影响历史 Python 进度/拟合，不参与决策”。
- #4 `counts[64][10]`：本报告 P0-2 已实证代码仍未闭合；#7 黄金集：`tests/test_scan.py` 只读真实模板，没有 DPI/主题/缩放/遮挡的标注黄金集，结构上仍缺失；#10 完整 900 局：README:178 明确未验证。其余 #1/#3/#5/#6/#8/#9 本报告未运行，不能宣称“与代码一致”，列为 **NOT_RUN/未验证**。
- **改进点**：把 docs 的未闭合项逐条改为 `{code_anchor, status, rerun_command}`，并把 `coarse_cap` 提案统一到当前实现；需要用户裁定的 #8 单独标记 owner。

---

## 3. Root Cause（根因）

1. **跨层语义被压扁**：C++ 只返回“数字 + reason 文本”，Python 侧再用 `confidence/is_best/exp` 猜安全性与指标类型。P0-1（最佳当安全）和 P1-3（精确/启发式混用）是同一个根：**没有类型化 provenance 字段**。
2. **容量边界按经验值实现，没有契约化**：`counts[64][10]`、`row_vals[256]`、`nTied`、`mem_budget` 都是“实测通常不超过”的经验上界，没有输入断言/动态容器/fail-closed；P0-2 与 P1-1 是同一根：**资源/容量约束停留在注释，没有成为接口契约**。
3. **线程边界与工作职责不匹配**：计算在 QThread，但世界集缓存是 `thread_local`，而预览消费在 GUI；取消事件在 Python，而长循环在 C++。P0-3、P1-2 是同一根：**没有跨语言/跨线程的单一调度与取消协议**。
4. **观测是“多处手写”而不是“调用边界统一”**：probe、trace、analyzer、UI 各自决定记录/显示什么，导致上限、字段、语义漂移（P1-4、P1-3）。
5. **测试围绕 mock 组装正向结果**：没有用 P0 的负向输入反向证明“不得点击/不得越界”，覆盖率工具又排除 C++/GUI；这使结构缺陷可以被绿色测试掩盖（P2-4）。

## 4. Recommendations（按优先级、工作量、影响）

1. **P0-1 安全候选类型化（工作量 M，影响最高）**：新增 `safety_proof`/`metric_kind`，只让证明安全的格进入 `clicks`；把 `_help_action_targets` 改为纯 `CandidateClassifier` 并加负向测试。影响：直接消除误点风险，不动算法。
2. **P0-2 动态 counts / 上限 fail-closed（工作量 S-M，影响最高）**：`std::vector` 或 64 上限断言；把 `row_vals` 一并动态化。影响：消除 UB，允许验证 480 候选极值。
3. **P0-3 状态转移搬到 TransitionService（工作量 M，影响高）**：独立 QThread + 指纹请求、Auto 禁预览；影响主线程响应性，需处理过期结果。
4. **P1-1 统一内存预算（工作量 M，影响高）**：clamp `mem_budget`，统一 88B/2304B 口径，前置到 pbs/part_solve 分配；帮助转移也走预算。影响：让“1.5GB 硬闸”成为可检查的边界，可能增加 pbs 降级。
5. **P1-2 C++ 取消 token（工作量 M-L，影响高）**：在 ExactEngine/pbs 轮询，Qt 超时不再默认 terminate；影响终止语义，需 A/B 延迟验证。
6. **P1-4 单一 decide 适配器 + trace schema（工作量 M，影响中高）**：所有 decide 调用统一记录，补 `situations/dstar`；影响可观测性与测试复用。
7. **P1-3/P1-5 provenance 与 run_id（工作量 S-M，影响中高）**：UI 显示真实指标类型，统计文件带 run_id；避免用户把“策略值”误读为安全概率。
8. **P2 清理/构建/测试/文档（工作量分批 S-M，影响中）**：拆 LegacyDeduction、构建 manifest、补 3 个 seam、修 docs 的 coarse_cap 与 trace 口径。

### 4.1 构建顺序与依赖标注（多组件建议）

| 顺序 | 步骤 | 依赖先序产出 | 被后续消费 |
|---|---|---|---|
| 1 | C++ `counts`/`row_vals` 动态化 + 输入断言 | 无 | C++ 单测、P0-1 的候选验证、构建 |
| 2 | C++ `make_response` 增加 `safety_proof/metric_kind/probability_basis` | 1（返回结构稳定） | Python 契约、CandidateClassifier、UI |
| 3 | Python `CandidateClassifier` + 唯一 `invoke_decide` 适配器 | 1-2 | 帮助/自动循环、probe/trace、测试 |
| 4 | `TransitionService` 异步化 + Auto 禁预览 | 2（转移元数据字段） | UI 主线程、P0-3 回归测试 |
| 5 | 内存预算 clamp + 前置预检 | 2（统一度量字段） | pbs/OPTIMAL/帮助转移、内存测试 |
| 6 | UI 语义标签/统计栏切换 | 2-5 | UI 测试、用户可读性 |
| 7 | 测试 seam（DecideInvoker/Classifier/TransitionService/ClickSink）+ 负向用例 | 3-6 | CI、覆盖率口径、回归 |
| 8 | 构建 manifest / 安装校验 / docs 修订 | 1-7（需最终 API） | 交付审计、可复现性 |

依赖冲突：`TransitionService` 需要 C++ 的转移指标字段（2），否则 UI 仍只能猜；`内存预算` 若先于 2，可能与 `metric_kind` 的返回字段冲突。无环，可按 1→2→3→4/5 并行（4/5 互不依赖）→6→7→8。

## 5. Trade-offs（权衡）

| 选项 | 利 | 弊 |
|---|---|---|
| A：只修 P0-1 和 P0-2（最小安全补丁） | 风险最低、改动小、可立即验证 | 保留 GUI 卡顿、内存闸门假边界、trace 断层；P0-3 仍会在悬停时冻 UI |
| B：重构统一 decide/transition 服务（方案 A+B+C） | 同时消除 P0-1/3、P1-1/2/3 的根因，语义和取消可测 | 改动跨 C++/Python/UI，周期长；异步缓存有旧帧失效风险 |
| C：只加测试与文档，不改生产 | 零行为风险 | P0-1/P0-2 仍在，绿色测试可能掩盖真实缺陷，不推荐 |
| D：保留 Python `mem_budget` 供 bench 覆盖，生产 clamp | 保留实验弹性 | 需区分“bench-only”入口，否则仍是可调硬上限；需 API 分层 |
| E：删除 `DeductionMixin` 并清理 `_ps_*` | 结构清晰、减少第二实现 | 会破坏历史 bench/测试，需引用计数审计和迁移名单 |

**不可接受的权衡**：以“真实盘面通常 nTied<64”为由不修 P0-2；以“帮助模式只提示人类”为由不修 P0-1（按钮实际会点击）；把 `QThread.terminate()` 当作正常取消兜底。

## 6. 覆盖账（spec 每项四态之一）

封闭枚举：`reviewed` / `skipped` / `blocked` / `waived`。skipped 理由限定为 `HISTORICAL_DOC`、`GENERATED_MIRROR`、`OUT_OF_CURRENT_PATH`、`ENV_LIMITATION`、`COST_BOUND`。`复跑方式` 均为只读查看命令（不得由 AI 代跑生产测试）。

| spec 项 | 状态 | 覆盖口径 / 理由 | 复跑方式 |
|---|---|---|---|
| main.py | reviewed | 主窗口、线程接线、点击 all、热力图/预览路径已读 | `Get-Content main.py` |
| setting.py | reviewed | 绝对路径、validate/save、chdir 副作用 | `Get-Content setting.py` |
| logger.py | reviewed | 日志 handler、失败降级 | `Get-Content logger.py` |
| color.py | reviewed | OKLCH 纯函数与示例路径 | `Get-Content color.py` |
| ui/window.py | reviewed | 生成 UI、图例渐变回边、滚动/按钮结构 | `Get-Content ui\window.py` |
| ui/window.ui | skipped | `GENERATED_MIRROR`：生成前 XML 与 py 镜像，已审生成物；未逐行读 XML | `Get-Content -Raw ui\window.ui` |
| ui/edit_setting.py | reviewed | 生成对话框、无决策逻辑 | `Get-Content ui\edit_setting.py` |
| ui/preview_popup.py | reviewed | 纯展示契约、无合成概率、Qt 事件 | `Get-Content ui\preview_popup.py` |
| ui/screenshot.py | reviewed | 生成截图对话框 | `Get-Content ui\screenshot.py` |
| utils/__init__.py | reviewed | 包级 pyautogui/setting/solver 副作用 | `Get-Content utils\__init__.py` |
| utils/solver.py | reviewed | QThread、play/help 主循环、局末、取消、统计 | `Get-Content utils\solver.py` |
| utils/probability.py | reviewed | 校验、decide 调用、自动/帮助循环、候选组装 | `Get-Content utils\probability.py` |
| utils/vision.py | reviewed | 模板、抓屏、置信度、重扫 | `Get-Content utils\vision.py` |
| utils/native.py | reviewed | pyd 加载、wrapper、旧签名 fallback、阈值残留 | `Get-Content utils\native.py` |
| utils/e2e_probe.py | reviewed | JSONL 事件、内存采样、上限 | `Get-Content utils\e2e_probe.py` |
| utils/decide_trace.py | reviewed | trace 字段/上限/观测点 | `Get-Content utils\decide_trace.py` |
| utils/deduction.py | reviewed | 残留推理与直接点击 | `Get-Content utils\deduction.py` |
| utils/combinatorics.py | reviewed | 组合枚举、get_list 修复 | `Get-Content utils\combinatorics.py` |
| utils/mm0.py | reviewed | 窗口/鼠标/QProcess 辅助 | `Get-Content utils\mm0.py` |
| utils/clicking.py | reviewed | MST 点击排序 | `Get-Content utils\clicking.py` |
| utils/misc.py | reviewed | JSON encoder/print | `Get-Content utils\misc.py` |
| utils/util.py | reviewed | re-export 兼容层 | `Get-Content utils\util.py` |
| utils/analyze_decide_trace.py | reviewed | 分析器口径与 COST_CAP 漂移 | `Get-Content utils\analyze_decide_trace.py` |
| cpp/mscore.cpp | reviewed | 决策层 1420-3239、导出/物化/边界已读；未逐行覆盖 330-1420、3240-3766（盲区已声明） | `Select-String -Path cpp\mscore.cpp -Pattern decide_impl` |
| cpp/build.bat | reviewed | MSVC flags、输出、无 provenance | `Get-Content cpp\build.bat` |
| tests/*.py | reviewed | 静态关键词/结构 + 关键 wiring/UI 用例抽读；未逐行全部 11 文件 | `Get-ChildItem tests\*.py` |
| tools/coverage_probe.py | reviewed | AST 分母、白名单、tracer、排除项 | `Get-Content tools\coverage_probe.py` |
| bench/e2e_900.py | reviewed | runner、切段、重启、resume 目标问题 | `Get-Content bench\e2e_900.py` |
| bench/analyze_e2e.py | reviewed | span/sentinel/内存口径 | `Get-Content bench\analyze_e2e.py` |
| bench/audit_dead_code.py | reviewed | AST 引用账、导出账、误判防护 | `Get-Content bench\audit_dead_code.py` |
| bench/verify_getlist_fix.py | reviewed | 恒等式 A/B 验收 | `Get-Content bench\verify_getlist_fix.py` |
| bench/verify_prob_truth.py | reviewed | 暴力真值、容差、仪器作废 | `Get-Content bench\verify_prob_truth.py` |
| bench/verify_paper_layers.py | reviewed | 层结构断言、pyd sha 打印 | `Get-Content bench\verify_paper_layers.py` |
| bench/repro_world_count_split.py | reviewed | off-by-one 复现/修复证据 | `Get-Content bench\repro_world_count_split.py` |
| bench/dstar_10k_serial.py | reviewed | 串行/同源布局/子进程 | `Get-Content bench\dstar_10k_serial.py` |
| bench/run_2000_after_cleanup.py | reviewed | 2000 局口径/内存/增量 | `Get-Content bench\run_2000_after_cleanup.py` |
| sim/*.py | skipped | `OUT_OF_CURRENT_PATH`：离线模拟非生产链路；仅抽读 eval_harness.py 的 stub seam，其余未审 | `Get-ChildItem sim\*.py` |
| README.md | reviewed | 架构承诺、命令、未闭合项 | `Get-Content README.md` |
| docs/现行架构与测量.md | reviewed | 权威架构、测量口径、未闭合项 | `Get-Content docs\现行架构与测量.md` |
| docs/实验总报告.md | skipped | `HISTORICAL_DOC`：历史实验编年，非现行代码契约 | `Get-Content docs\实验总报告.md` |
| docs/变量名与算法细节.md | skipped | `HISTORICAL_DOC`：历史命名陷阱 | `Get-Content docs\变量名与算法细节.md` |
| docs/胜率测试套件说明.md | skipped | `HISTORICAL_DOC`：历史 S0-S7 台架 | `Get-Content docs\胜率测试套件说明.md` |
| CHANGELOG.md | skipped | `HISTORICAL_DOC`：变更史料 | `Get-Content CHANGELOG.md` |
| HANDOFF.md | skipped | `HISTORICAL_DOC`：交接快照 | `Get-Content HANDOFF.md` |
| cfg.json | reviewed | 生产配置（30×16/99、24px、Arbiter 路径） | `Get-Content cfg.json` |
| requirements.txt | reviewed | 依赖钉版；未验证安装 | `Get-Content requirements.txt` |
| .gitignore | reviewed | pyd/state/历史数据排除规则 | `Get-Content .gitignore` |

**覆盖账结论**：所有 reviewable 项均落入四态，无 `failed`；skipped 均为“历史/生成/非现行路径”且给出复跑方式。未运行任何生产测试，故本账不把“代码已读”冒充实机通过。

## 7. 可测试性改进（不改生产语义的 seam）

1. **`CandidateClassifier`（纯函数）**：输入 `{confidence,is_best,is_recommend,reason,prob_best}`，输出 `{recommended, clickable, safety_proof}`。可在不启动 Qt 的情况下覆盖 P0-1 的正负例。
2. **`DecideInvoker`**：包装 native 调用 + trace/probe + 异常；传入 fake native 即可断言“每次 decide 都有 trace”，不需要真实 pyd。
3. **`TransitionService` + `TransitionBackend` 协议**：Qt 层只做请求/响应；核心 backend 用纯 Python 假对象测试指纹失效、重复悬停、Auto 禁入。
4. **`ClickSink` 注入**：把 `pyautogui.click/rightClick` 封装为可替换对象，测试 `_help_action_targets` 与 `click_all_func` 的“哪些格子会被点”；避免 mock GUI 全部路径。
5. **`ScanSource`**：沿用 `main.py:1371-1405` 的 `_OneShotScanner` 思路，抽为协议；视觉/模板测试使用固定 BGR fixture，不借用共享 `Solver.img`。
6. **C++ corpus replay**：把 `{cell_value,a,w,h,is_play}` 存为 JSON corpus，子进程调 pyd 输出 dict，断言 `situations/world_count/ΣP` 恒等式；这是覆盖 `counts>64`、DSTAR 哨兵、内存边界的最佳 seam，不依赖 GUI。权衡：需要固定 pyd sha + 运行环境，CI 成本高，但比 mock 更有证据。
7. **覆盖率口径修正**：不要在 C++/GUI/Arbiter 上强求 100%；把 `coverage_probe` 的 Python 分母与 C++ corpus replay 分开报告，禁止用 mock 正向断言刷绿。

## 8. 盲区说明（本报告漏了什么）

- 未运行 `python -m unittest discover -s tests -q`、未运行 `tools/coverage_probe.py`、未运行任何 bench；所有覆盖率/胜率/内存/延迟数字 **NOT_RUN**。
- 未实机驱动 Arbiter、未截图、未安装/替换 pyd、未核验 pyd sha8 45C18B2F；无法证明 P0-1 在真实局面的触发频率、P0-3 的实际卡顿秒数、P0-2 的现场 nTied 最大值。
- `cpp/mscore.cpp` 只逐段读了文件头/决策层/导出与物化边界；330-1420 的 part_solve/pbs 数值实现、3240-3766 的合并编译/其它入口未逐行审计，可能有本报告未覆盖的数值问题。
- 未逐行读 11 个 tests 文件全部内容；测试结构结论基于 grep、line count 与 3 个关键文件的抽读。未审 `docs/实验总报告.md` 等历史文档全文。
- 未审计 spec `excluded` 的 200 个 history bench、`ui/bg_rc.py`、二进制资源；不对其可运行性作结论。
- 无形式化验证工具输出；除 `thread_local` 的 C++ 语义外，涉及“永不/必然”的强断言均为 **NOT_RUN**。
- 未对 P2-5 runner 修复做实验；其结论基于代码控制流，`--resume` 目标超发风险需真实 runner 冒烟验证。

## 9. 人可复述的最小结论（≤5 条）

1. **最危险的不是算法，而是帮助模式的“确定方格”按钮**：C++ 不确定的 best 会被加入 `clicks` 并被批量点击（`probability.py:1026-1028` + `main.py:2052-2064,2094-2101`）。
2. **C++ PSEQ 的 `nTied` 无界，却写入固定 `counts[64][10]`**：超过 64 个并列候选即栈越界（`cpp/mscore.cpp:2525-2578`），现有文档已把它列为 P0。
3. **GUI 悬停不是纯展示**：跨线程 `thread_local` 转移缓存不命中，GUI 会同步重建最多 20 万世界的转移集（`cpp/mscore.cpp:2690,3068-3077` + `main.py:895,1966-1977`）。
4. **“1.5GB 硬闸门”不是全链硬边界**：只约束 OPTIMAL 计账，pbs/part_solve/帮助转移可绕过，且 `mem_budget` 仍在 pybind 签名可覆盖（`cpp/mscore.cpp:1993-2009,2372-2376,3082,3202`）。
5. **取消与证据都不完整**：C++ 长循环无取消 token，Qt 3 秒后 terminate 会绕过 finally（`main.py:1072-1093`）；decide trace 有 20MB 上限且漏记 `situations/dstar`（`utils/decide_trace.py:38,118-127`）。

## 10. References（引用）

- `review2/review-spec.json:9-56` — reviewable/excluded 与 5287 行/22 模块口径
- `main.py:88-99,165-201` — value_kind 文案映射与类型推断
- `main.py:864-912` — GUI 线程同步调用 transitions_for 与按格缓存
- `main.py:1072-1093` — 3 秒协作取消 + terminate 兜底
- `main.py:1966-1977` — Auto 页给概率格挂预览
- `main.py:2018-2075` — `_help_action_targets` 把 is_best 并入 clicks
- `main.py:2077-2122` — `click_all_func` 真实点击
- `utils/probability.py:612-679` — decide 不可用统一出口（不回退）
- `utils/probability.py:830-879` — decide 前盘面校验
- `utils/probability.py:901-922` — 唯一 C++ decide 调用
- `utils/probability.py:1010-1057` — 帮助候选组装（P0-1 根因）
- `utils/probability.py:1084-1102` — 帮助循环/候选返回
- `utils/probability.py:1109-1155` — 自动 decide 循环与批量取消检查
- `utils/native.py:45-55,116-135` — 加载/旧签名 fallback
- `utils/native.py:167-181` — tuned/MSW_NATIVE_TUNE 残留
- `utils/decide_trace.py:32-44,118-137` — trace 开关、20MB 上限、字段
- `utils/solver.py:63-245` — AutoPlayThread/取消/热键
- `utils/solver.py:320-332` — run finally
- `utils/solver.py:615-719` — game_stats 读写与累计
- `utils/solver.py:828-960` — help 预算乘数/异常/finally
- `utils/vision.py:45-73,588-744` — UNKNOWN/低置信度/扫描
- `utils/deduction.py:74-104,154-160,207-213` — 残留直接点击
- `cpp/mscore.cpp:1445-1448` — 内存预算常量
- `cpp/mscore.cpp:1674-1685` — estimate_mem
- `cpp/mscore.cpp:1993-2009` — decide 内 88B 内存口径
- `cpp/mscore.cpp:2075` — coarse_cap = DSTAR*4
- `cpp/mscore.cpp:2130` — OPTIMAL 的 mem.allowed
- `cpp/mscore.cpp:2248-2252` — C++ 候选循环无取消
- `cpp/mscore.cpp:2372-2376,2645-2648` — pbs 仍执行，memory_gate 仅改 reason
- `cpp/mscore.cpp:2525-2578` — counts[64][10] 写越界
- `cpp/mscore.cpp:2674,2957-3003` — 帮助转移 200k 物化
- `cpp/mscore.cpp:2690,3068-3077` — thread_local 转移缓存
- `cpp/mscore.cpp:3080-3101,3202` — py_decide 签名/诊断字段/mem_budget
- `cpp/build.bat:31-38` — MSVC flags / 无 provenance
- `apply_native_fix.cmd:54-81` — pyd 复制/备份/无校验
- `tools/coverage_probe.py:30-45,91-134` — Python-only 覆盖率口径
- `bench/e2e_900.py:119-133,291,355-377,418-427` — runner 历史计数/resume 目标
- `bench/analyze_e2e.py:66-107` — 跨轮切段口径
- `README.md:141,185-186` — deduction 兼容说法与 counts P0 自述
- `docs/现行架构与测量.md:55,79,160-162,232-264` — coarse_cap/trace/未闭合项
- `tests/test_cpp_decide_wiring.py:1163-1178` — transitions 跳过条件
- `tests/test_ui_migration.py:1065-1069,1097-1108` — 正向测试固化 is_best 可点击
