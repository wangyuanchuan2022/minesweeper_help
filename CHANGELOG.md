# CHANGELOG

本文件记录本项目的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循语义化版本。

---

## [Unreleased] - 2026-11-02

### 修复（正确性）

#### 1. 大情况近似分支的组间独立性假设错误 —— 影响胜率判定

- **文件**：`utils/probability.py`、`cpp/mscore.cpp`
- **问题**：`process_bigger_situation` 的近似分支（`total > 10000`）对每个分组独立归一化，
  丢掉了「总雷数」这个跨组耦合约束。正确的世界权重应为
  `W(m_1..m_G) = ∏H_g(m_g) · C(U, R−Σm_g)`，旧实现缺失 `C(U, R−Σm)` 项。
- **影响**：独立构造反例实测，安全率误差可达 **46.67 个百分点**（偏斜构造 100pp）。
  单组时误差为 0，因此该缺陷**只在多组大局面暴露**——这也解释了它为何长期未被发现。
- **修复**：改用**生成函数 DP**（雷数直方图 `H_g` + 卷积 `T(t)` + 组合数加权），
  Python 侧新增 `_pbs_compute_dp`，C++ 侧 `pbs_compute` 同步实现同一数学。
- **验证**：两条生产路径均与暴力枚举对拍，最大差 **0**（单组 2.55e-08 为 float32 精度）；
  `bench/compare_native.py` 输出 `ALL CONSISTENT`。

#### 2. `process_bigger_situation` 空集崩溃

- **文件**：`utils/probability.py`、`cpp/mscore.cpp`
- **问题**：当 `A(ck)` 的所有组合都不满足雷数上下界时，`mine_num` 为空，
  `min(mine_num)` 抛 `ValueError`；C++ 与 Python **双路径都崩**，不是优雅降级。
- **触发场景**：残局（雷大量被标记、内部未开格少）或设置中总雷数填错。
- **修复**：退回到均匀先验（`p_mine = clamp((a−num10)/未开格数, 0, 1)`），
  两侧同步实现。
- **验证**：原本崩溃的用例（`a=1`、`a=2`）现均正常返回。

#### 3. 批量识别补丁尺寸错位（本次性能改造引入的回归）

- **文件**：`utils/vision.py`
- **问题**：`_slice_cell` 实际取窗为 `2*(screenshot_h//2) × 2*(screenshot_w//2)`
  （`screenshot_w=13` 时实得 12），而新增的 `compare_cells_batch` 按配置值 13 建补丁，
  导致整体错位 1px。
- **发现方式**：子席视觉域评审指出，主会话用随机棋盘对抗测试复现。
- **修复**：批量路径改用与 `_slice_cell` 一致的实际尺寸。
- **验证**：40 张纯随机噪声棋盘、19200 格，**0 分歧**；变异体测试证明确实能检出该缺陷
  （错误尺寸 6 张里 1 张分歧，正确实现 0）。

### 修复（数据与并发）

#### 4. 耗时模型拟合未按注释截窗，导致「越玩越慢」

- **文件**：`utils/probability.py`
- **问题**：`_ps_refit_k` 注释声明「只取最近 200 条（`rows[-200:]`）」，
  实际传入全量 `rows[:]`，O(n²) 拟合随样本数恶化。
- **实测**：1777 条样本时全量拟合 25.5 ms / 最近 200 条 1.66 ms（**15.4x**）；3000 条时升至 1.5 s。
- **修复**：新增 `_PS_FIT_WINDOW = 200`，真正截窗。
- **效果**：`_ps_observe` 39.8 → **19.8 ms**；`_ps_refit_k` 49.2 → **5.6 ms**。

#### 5. 样本文件无限增长

- **文件**：`utils/probability.py`
- **问题**：`data_time.json` 只增不减，每次全量重写，随对局数无限变慢。
- **修复**：新增 `_PS_SAMPLES_KEEP = 1200` 滑窗上限，写成本有上界。

#### 6. 跨线程共享 `Solver.img`（GUI 与工作线程竞争）

- **文件**：`main.py`
- **问题**：帮助流程在 `help_thread.start()` 之后，GUI 线程又直接调用同一 Solver 的
  `complete_scan`，而该方法会写共享 `self.img` → 两线程交叉写，后台可能拿到错帧截图。
- **修复**：GUI 改用独立的一次性扫描器 `_gui_scan_once`，只读配置、独占 `img`，不触碰工作线程状态。

### 性能优化

#### 7. 视觉识别矢量化：完整决策 387 ms → 77.7 ms

- **文件**：`utils/vision.py`
- **改动**：把「480 格 × 25 模板 = 12000 次 `cv.matchTemplate`」替换为
  numpy 批量同语义计分（`TM_SQDIFF_NORMED` 定义逐位等价）。
- **实测**：逐格参考 1567 ms → 批量 **43.6 ms（35.9x）**；
  `number5_1` 端到端 **387 → 77.7 ms（5.0x）**；视觉段 309 → 48 ms。
- **等价性**：9 组测试（纯色/混合/噪声 × 3 seed）逐格 **0/480 不一致**；
  另加 40 张随机棋盘对抗测试 0 分歧。
- **保留**：`compare_img` 作为参考实现与批量失败时的回退路径。

### 新增

#### 8. 扫雷模拟环境与胜率评估（`sim/`）

- **`sim/minesweeper_sim.py`**：忠实复刻 Arbiter 规则的模拟引擎，含**信息隔离契约**——
  求解器只能拿到 `BoardView`（未开/已开数字/标记/剩余雷数），**真实雷位只存在于 `Board._mines`**，
  并提供 `audit_no_leak()` 审计。
- **`sim/engine_adapter.py`**：把项目求解器封装成只接受 `BoardView` 的决策器。
- **`sim/diagnose_engine.py`**：区分「确定性推理错误」与「概率猜测失败」。
- **`sim/play_matches.py`**、**`sim/first_click_rules.py`**：批量对局与首点规则对比。

**已知局限**：仓库内**没有任何真实棋盘截图+标注**，所有识别准确率结论仅在合成棋盘上成立。

### 逆向工程结论（Arbiter 0.52.3）

**结论：雷生成 = 纯拒绝采样，循环体内无任何排除规则。「首点必非雷」来自延迟布雷。**

- 原程序为 **UPX 加壳**（壳内仅 18 个函数）；脱壳后 1945 KB、**5305 个函数**。
  SHA256(脱壳) = `07c1907b13a2d55aaa2a11860bae8adb86a2531c563ea411a246b5bd93a25ad8`
- 编译器：**Borland Delphi**（`x86:LE:32:default:borlanddelphi`）。
- 随机数：**Delphi System.Random**（LCG），REA 反编译逐字确认：
  ```
  RandSeed = RandSeed * 0x08088405 + 1
  Random(n) = (uint64)n * RandSeed >> 32
  ```
  LCG 常数 `0x08088405` 全代码段**仅 1 处**（VA 0x4033d1）。
- 种子：**CryptGenRandom** 播种（每局新种子，序列不可预测）。
- 布雷函数 `0x4d9228`（雷数由 EAX 入参）：
  ```
  count=1; while count<=mineCount:
      repeat: x=Random(W+1); y=Random(H+1)
      until GetCell(x,y)==UNOPENED          ← 唯一过滤条件
      SetCell(x,y,MINE); count++
  ```
- **决定性证据**：布雷函数体内引用 宽(2处)/高(2处)/棋盘(1处)，
  而**雷数引用 0 处**（雷数由 EAX 传入）→ 证明它就是"按给定雷数拒绝采样布雷"。
- 6 处调用点：`0x531990`, `0x531b40`, `0x531d02`, `0x531f4d`, `0x532170`, `0x53232e`
  （全在同一函数 `0x5313b8` 的不同分支）。
- **首点安全机制**：先点开（含 flood-fill）→ 再布雷 → 拒绝采样跳过已开格。
  ⇒ **只保证首点本格安全**，不保证 3×3。（早期版本曾误以为等价于"3×3 排除"，
  2026-11-03 已修正模拟器，详见下文「模拟器首点规则错误」。）

### 实测基线（`sim/baseline_winrate.py`，2000 局，seed=20261102）

| 策略 | 胜率 | 95% CI | 平均踩雷步数 |
|---|---:|---|---:|
| B1 纯瞎猜（从不推理） | **0.000%** (0/2000) | [0.00%, 0.19%] | 4.3 |
| B2 纯推理+瞎猜（无解即随机） | **0.750%** (15/2000) | [0.46%, 1.23%] | 64.1 |

口径：真实扫雷规则（30×16、99 雷、首点安全、flood-fill），
**不含视觉、不含点击、不含概率枚举**。这两个数是"无智能下限"。

> ⚠️ **2026-11-03 注**：B1/B2 是在**旧模拟器（首点 3×3 排除）**下跑的，
> 该口径使开局已知区域虚高约 5.3 倍（见下文「模拟器首点规则错误」）。
> 因 B1 恒为 0、B2 的绝对值极小，这两个数作为"无智能下限"的定性结论仍然成立，
> 但**精确数值应在修正后的模拟器上重跑**（尚未重跑）。

对照：项目真实胜率 **33.86%**（107/316，`state/game_stats.json`）。
45 倍差距全部来自"猜得更准"（概率枚举 + 胜率搜索）——这是算法改进唯一有价值的方向。

### 测试与验证

- `bench/verify_fixes_negative.py`：负向自测 **3/3 PASS**（R1 截窗、R2 崩溃兜底、R3 滑窗）。
- `bench/verify_vision_adversarial.py`：随机棋盘等价性 **0 分歧**。
- `bench/verify_adversarial_mutation.py`：变异体检出，证明测试非假绿。
- `bench/verify_pbs_fix_e2e.py` / `dp_generating_function.py`：DP vs 暴力枚举 **5/5 一致**。
- `bench/compare_native.py`：C++ 与 Python **ALL CONSISTENT**。
- `unittest`：**97 通过 / 1 既有失败**（`test_ui_heatmap.test_stats_line_content`，
  断言旧文案「当前点击方格不是雷的概率」，实际已改为「当前格非雷的概率」。
  **未修改该测试**——不为通过测试而改测试，待确认文案为最终版后再更新断言）。

### 已知未修复项

| 项 | 状态 | 原因 |
|---|---|---|
| 14/25 个模板尺寸与补丁不匹配 | 未修 | 需确认真实截图集后决定；当前识别仍可用 |
| 识别无置信度阈值（模板损坏会静默出错） | 未修 | 阈值需真机重标 |
| 点击后固定 `sleep(0.1)`（100 ms/步） | 未改 | 需真实游戏窗口验证「棋盘稳定检测」 |
| `_ps_record_ms` 全量重写（可改 JSONL 再快 26x） | 未改 | 收益小于前述项，留待后续 |
| README / requirements 与实际环境不符（写 Python 3.7，实为 3.10） | 未改 | 需你确认支持基线 |

---

## [Unreleased] - 2026-11-03 · win_rate 节点数硬上限（防内存爆炸）

用户诉求：位集重构降 win_rate 每节点成本、防止内存爆炸。**结论是先做了更根本的一步**——
位集对总内存几乎无用，真正的防爆手段是节点数封顶。生产算法逻辑未变（逐位对拍为证）。

### 修复（内存安全）

#### 1. `win_rate` 节点数硬上限 `node_cap`（默认启用 2e6）

- **文件**：`cpp/mscore.cpp`（`WinRateCtx.node_cap` + `NodeCapExceeded`）、
  `utils/probability.py`（`_wr_node_cap()` / `_WrNodeCapExceeded` / 降级分支）
- **问题**：`wr_f` 的 `memory` **只写不删**（`cpp/mscore.cpp` 末尾只赋值），
  因此 `live_nodes == 累计访问节点数`，随 (候选数 × 世界分叉 × 深度) 组合增长。
  一旦局面异常变大（或阈值被放宽），节点数可到 1e7 量级 → 直接 OOM。
- **修复**：C++ 侧在 `wr_f` 入口计数，超 `node_cap` 抛
  `runtime_error("win_rate node cap exceeded ...")`；Python 侧**按消息子串**
  识别后转 `_WrNodeCapExceeded`，由 `number5_1` 捕获并**降级走
  `process_bigger_situation`（贪心）**。
- **关键设计**：超限**不回退纯 Python**（那里同样无上限，退回等于没拦）；
  也**不**让异常落到外层 `except`（会被当作"求解失败"→ 触发完整重扫，更糟）。
  只有含 `node cap exceeded` 的异常才降级，其他 `runtime_error` 保持原有
  "回退纯 Python"语义，不会被静默吞成贪心结果。
- **取值标定**（`bench/calibrate_node_cap.py`，真实 RSS 峰值采样）：

  | cap | ΔRSS |
  |---|---:|
  | 2e3 | 0.00 MB |
  | 1e4 | 0.10 MB |
  | 5e4 | 0.73 MB |
  | 2e5 | 4.04 MB |
  | 不限制 | 8.25 MB |

  实测每节点 **中位 15.2 B / 最大 21.2 B**（按 25 B 留余量）。
  `2e6 × 25B ≈ 48 MB`，对照 1.5GB 预算留约 30x 余量 ⇒ 取 **2e6**。
  ⚠️ **不用"本机用例是否触发"反推 cap**：防护器的预期行为就是正常路径不触发。
  实测本机最大节点需求仅 424,114，故 2e6 下零触发是**正确**的。

### 否证（位集重构方向）

- `bench/mem_model.py`：位集把每世界内存 2304B → 64B（36x），但**总内存几乎不变**
  （19.2 MB → 19.1 MB），因为 memo 节点开销完全主导。
  实测每节点 15–21 B，世界列表在生产规模下不足 0.15 MB。
  ⇒ 位集只在"世界数极大、节点数相对小"的窄场景有意义，**不作为防爆手段**。
  该方向暂不实现；若后续要做，须走"独立函数 + 逐位对拍 + 峰值实测"完整验收。

### 验证（三道闸全绿，30 项断言）

| 闸 | 脚本 | 结果 |
|---|---|---|
| 超限必抛 + 默认不变 | `bench/verify_node_cap.py` | PASS 7 / FAIL 0 |
| 端到端降级 + 负向 | `bench/verify_node_cap_e2e.py` | PASS 20 / FAIL 0 |
| 生产默认逐位一致 | `bench/verify_node_cap_default.py` | PASS 3 / FAIL 0 |

要点：
- 默认 cap=2e6 与不限制**逐位一致**（33 个用例，|Δ| < 1e-12）
- 整局 10 局 `won/died/opened` 逐局一致，且默认 cap 下**零误降级**
- 注入 cap=1：异常穿越调用链进入贪心降级、正常返回、`_wr_nodecap_hits` 递增
- 普通 `RuntimeError` 不进入贪心、不增加计数（负向测试）
- 一键复跑：`verify_node_cap.cmd`

### 新增

- `bench/calibrate_node_cap.py`：真实 RSS 峰值 + 节点需求分布标定
- `bench/mem_model.py`：内存容量模型（位集收益上限分析）
- `bench/verify_node_cap{,_e2e,_default}.py`：三道验收闸
- `bench/compare_all_arms.py` + `run_baselines.cmd`：多臂整局配对统计与测量入口
  （**未跑**：2000 局 × 6 臂的全量基线仍待执行）

### 未完成

- `process_bigger_situation` wrapper 未做逐决策对拍（`bench/ab_policy.py`）
- 整局配对 A/B 全量未跑 → **不给出任何整局胜率变化数字**
- 各臂峰值内存未在整局规模实测（仅单次调用口径）

---

## [Unreleased] - 2026-11-03 · 选格策略对比与枚举器修复

本轮**生产算法零改动**，只修评估工具的正确性缺陷、把「safety=1.0 是否必为最优点击」
这个命题机检落实，并把各选格策略放到同一个精确 oracle 下对比。
完整报告见 `review/算法选格对比与枚举器修复.md`。

### 修复（正确性，影响此前全部 bench 读数）

#### 1. 世界枚举器把「组内列号」当成「全局前沿下标」

- **文件**：`bench/compare_2ply_vs_winrate.py`（仅 bench，不涉生产）
- **问题**：`groups[gi]` 的第 `ci` 列只对应**该组自己的第 ci 个前沿格**，
  原实现 `fm.append(clicks[ci])` 直接用作全局下标，导致不同组的雷
  被写到重叠坐标上并互相覆盖。
- **影响**：每个世界的雷数只有 95/96/97，而真实 `a=99`。所有下游量
  （逐格雷概率、safety、V*、各臂排名）**系统性偏移**。修正后用例 3 的 V*
  从 0.8462/1.0000（两实现互相矛盾）变为 **0.2692**（三实现一致）。
- **修复**：按组累积偏移 `group_offset`，用 `clicks[base + ci]`。
- **新增守卫**：`emit()` 内置守恒断言（世界雷数严格 == a、基底雷全含、
  世界内无已开格、雷数分项守恒、组列总数 == len(clicks)）。
  回归脚本 `bench/verify_world_enum.py` → 11/11 用例雷数分布恰好 {99}。

#### 2. 「两个独立实现读数一致」不等于正确

- 原 `ExactEngine`（位运算）与 `Indep`（frozenset）**共用同一个**
  `enumerate_worlds`，共享错误输入源 ⇒ 一起错且错得一致。
- 新增 `bench/oracle_bruteforce.py`：**只读 cell_value 重枚举世界**，
  完全不碰 clicks/clicks9/groups/ck，才是真独立真值源。
- `bench/verify_all_oracles.py`：33 个用例，三实现不一致 **0**。

### 新增（命题机检）

#### safety=1.0 的格点击后最优胜率不降低 —— 已验证

- 命题：无点击次数限制、允许连续行动时，必然安全格 c 满足
  `after(c) = V*(S)`（构造性证明：先点 c 拿到信息不改变任何后续可选策略，
  且信息单调不减）。
- **实测**：33 个用例中 6 个含安全格候选，共 **23 个安全格样本**；
  弱占优（≥）与强等式（==）**均 0 违反**；不存在风险格严格优于所有安全格。
- 脚本：`bench/verify_safe_dominance.py`、`bench/verify_all_oracles.py`。

### 新增（算法臂对比，仅选格质量，不代表整局胜率）

- `bench/arms_lookahead.py`：纯 safety / 期望展开 / 2-ply / 3-ply /
  生产 win_rate / 精确 oracle，在同一决策状态上比较。
- **结果（33 局面）**：平均 regret 生产 win_rate **0.0015**（最低之一）、
  3-ply 0.0017、纯 safety 0.0044、2-ply 0.0075、期望展开 0.0090。
  配对符号检验：所有臂之间 p ≥ 0.25，**未检出显著差异**。
  ⇒ 在 33 个样本中未检测到前瞻臂相对生产 win_rate 的显著优势，
  因此暂无充分依据替换生产算法。**统计不显著只表示当前样本未检出差异，
  不能证明各臂等效或前瞻无价值。**
- `bench/cost_arms.py`：单决策耗时。精确 oracle 中位 0.14ms、
  3-ply 0.15ms（纯 Python 原型），生产 win_rate 中位 0.29ms / 最大 2017ms（C++）。
- `bench/summarize_arms.py`：配对统计汇总。

### 新增（整局配对 A/B 台架，未跑完）

- `bench/ab_policy.py`：六臂整局配对 A/B（baseline/safety/expand/ply2/ply3/exact）。
- `bench/verify_ab_baseline.py`：**台架等价性闸门**。首轮抓出我复刻的
  `play()` 循环漏了 3 处（`in_decision` 复位、10/11 标记回写、
  stall 需连续 3 次），12 局有 5 局步数不同；修正后 **12/12 逐局一致**。
- **全量 A/B 未跑**，故本 CHANGELOG 与报告均不给出任何整局胜率变化数字。
  已知未验证：`process_bigger_situation` wrapper 未做逐决策对拍、峰值内存未测、
  world/node cap 回退次数未批量汇总。

### 结论

- **暂无充分依据替换生产算法**：33 个局部局面的差异不显著（p ≥ 0.25），
  样本量不足以下"换/不换"的结论；统计不显著只表示未检出差异，不等于各臂等效。
- 生产运行路径（`utils/probability.py` → `cpp/mscore.cpp`）**不经过**被修的
  bench 函数，生产行为未受影响。
- 本轮修正的两条通用教训已写入脚本注释：
  ① 免费推理饱和必须「逐格开、每格重新分叉」，且必须「对所有分叉递归饱和」
     （否则结果依赖先开哪个安全格，实测同局面 1.0000 vs 0.8462）；
  ② 验证实现独立性时必须确认**输入构造**也独立。

---

## [Unreleased] - 2026-11-03 · 算法更新评估

本轮**不改决策算法本身**，只做三件事：修正测量偏差、加内存保护、把"算法该不该
更新"论证清楚。完整报告见 `review/算法更新评估.md`。

### 修复（测量偏差，影响所有胜率数字）

#### 1. 模拟器首点规则错误 —— 极限胜率被系统性高估 11.5pp

- **文件**：`sim/minesweeper_sim.py`
- **问题**：旧 `generate()` 用「首点 3×3 排除」，但 Arbiter 的真实机制是
  **延迟布雷**：先点开首格 → 再布雷 → 拒绝采样只跳过**已开格**。
  因此 Arbiter **只保证首点本格安全**，不保证 3×3。
- **实测差异**（`bench/first_click_fidelity.py`，400 局）：

  | | 首点展开均值 | 开局仅 1 格的比例 |
  |---|---:|---:|
  | 真实（只跳过首点 1 格） | **7.7 格** | **83.2%** |
  | 旧模拟器（3×3 排除） | **41.2 格** | **0%** |

  旧模拟器每局**白送 5.3 倍已知区域**。
- **修复**：`generate()` 改为只把首点本格排除在布雷池外（延迟布雷语义）。
- **影响**：算法极限胜率 **52.0% → 40.5%**（81/200，CI [33.9%, 47.4%]）。
  对照项目真实对局 33.86%，差距从不可解释的 18pp 收敛到合理的 **6.6pp**。
- **交叉验证**：`bench/check_opening_shape.py` 显示修正后 **83.8% 的对局开局只有
  1 个已知格**，首格数字分布 0:16.2% / 1:32.5% / 2:33.0% / 3:12.8% — 与用户
  实际观察「真实开局很可能只有一个已知格子」完全吻合。

### 新增（内存安全防护）

#### 2. `win_rate` 内存预算闸门

- **文件**：`utils/probability.py`（`_wr_memory_ok`，调用点 `_number5_1_core`）
- **背景**：`win_rate` 成本是 `nworlds × 2304B × n_candidates × 递归层数`，
  是**乘积**结构。本轮曾**两次把本机内存顶爆**，根因是探针在生产配置之外
  强制 `LIMIT_BONUS_WIN_RATE=999`（让每个局面都走完整递归）。
- **防护**：调用前按 `total × (1+F+U) × 2304B` 估算，超 **1.5 GB** 直接降级到
  `process_bigger_situation`（贪心最大安全率），不进入 `win_rate`；
  同一 `(total, n_cand)` 只警告一次，避免刷屏。
- **为什么必须在调用前拦**（而不是靠 C++ 抛异常）：
  ① C++ 抛异常后会回退**纯 Python** 实现，那里没有上限、同样吃内存；
  ② 异常抛在计算中途时已分配过一部分内存，峰值仍可能顶爆；
  ③ 频繁抛异常 + warning 会拖慢决策。
- **验证**：`bench/verify_wr_guard_gate.py` **5/5 PASS**（负向用例覆盖：
  `total=40320, n_cand=20` 预计 1858MB 被拒；`n_cand=480` 预计 44591MB 被拒）。
  闸门若被改成恒 True，这两条会变红。
- **生产影响**：**零**。生产实测（12 局）win_rate 仅触发 8 次、n_cand 中位数 13、
  世界数中位数 4、单次内存约 0.15MB — 正常对局**从不触发**该闸门。

### 实测结论（算法该不该更新）

核心数学关系（用户指出并已实测验证）：

```
win_rate(c) = safety(c) × E[点击 c 后继续获胜的概率 | c 不是雷]  ≤  safety(c)
```

| 结论 | 证据 |
|---|---|
| `win_rate ≤ safety` 恒成立 | `bench/verify_winrate_le_safety.py`：11 用例 **0 例违反** |
| 原剪枝 `cp[i] < running_max` 是**最紧上界的有效分支定界** | 由上式直接推出；无可改进空间 |
| `win_rate` 与纯贪心（max-safety）在 **10/11** 局面选同一格 | `bench/_summary_gap.py`：不一致 1/11 = 9.1% |
| 前瞻确有信息（1/11 局面选不同格；存在 safety=1.0 但 win_rate=0.25 的格） | 同上，用例 [3]/[5]/[9]/[10] |
| 一步前瞻（2-ply）**无增益** | `bench/compare_2ply_vs_winrate.py`：11/11 与纯 safety 同格 |
| 覆盖缺口：`limitation` 中位数 **61**，阈值仅 8 ⇒ 只有 **33.1%** 决策能进 win_rate | `bench/probe_limitation.py`：60 局 511 次决策 |
| win_rate 单次最坏 **502ms** | `bench/bench_winrate_cases.py`：11 用例合计 708ms，单个占 70% |

**判断**：骨架正确，不该换算法（与 `review/算法域.md` 的前沿调研结论一致：
MDD 标"不确定/需原型基准"、SAT"求一解不等于概率分布"，均不推荐）。
该更新的是**扩大 win_rate 的可用范围**（降每节点成本 → 再提阈值），
但收益上限有限（仅 ~9% 决策会被改变），应作为渐进改进而非大幅跃升。

### 试过但无效的优化（记录以防重踩）

| 尝试 | 结果 | 证据 |
|---|---|---|
| 补全记忆化键（旧键只哈希 `boards[0]`，补 clicks+depth） | 输出逐位相同但**耗时翻倍** | 中位 0.69→1.32ms，最大 502→1060ms |
| 删除剪枝 `cp[i] < running_max` | **错误**（剪枝是有效分支定界） | 节点数 99970→111143，结果不变 |
| 一步前瞻 2-ply 替代 win_rate | **无增益** | 11/11 选同一格 |

### 本轮自纠的三条错误结论（如实记录）

1. **误判"顺序敏感"为 P0 缺陷** —— 我做"只反转 clicks 不反转 groups"的实验，
   但 `clicks[i]` 与 `groups` 的列是**一一绑定**的（`r[i]==1 → clicks[i]` 是雷），
   所以那等于构造了三个**不同棋盘**。据此后删除剪枝、改排序 tie-break、
   改记忆化键，**全部基于错误测试**，已全部回退，`cpp/mscore.cpp` 与原作者版本一致。
2. **误判"剪枝是伪优化"** —— 用户指出 `win_rate = safety × 后续胜率`，
   故 `win_rate ≤ safety`，用 safety 作上界剪枝是标准且最紧的分支定界。
   实测 0 例违反后确认。剪枝已原样保留。
3. **内存事故归因错误** —— 不是生产缺陷，是我方探针强制 `wr_threshold=999`
   造成的测量污染。生产配置下 win_rate 触发极少、内存 0.15MB。

**教训**：验证"同一局面的不同排列"必须同时打乱所有一一绑定的输入；
禁止在生产配置之外强制触发热路径；指数递归探针必须先估内存并设硬上限。

### 新增脚本（可复现）

| 脚本 | 作用 |
|---|---|
| `bench/first_click_fidelity.py` | 首点规则保真度（7.7 格 vs 41.2 格） |
| `bench/check_opening_shape.py` | 开局形态（83.8% 开局仅 1 格） |
| `bench/verify_winrate_le_safety.py` | 验证 win_rate ≤ safety |
| `bench/_summary_gap.py` | safety 最高格 vs win_rate 最高格 |
| `bench/compare_2ply_vs_winrate.py` | 2-ply vs 完整 win_rate |
| `bench/verify_wr_guard_gate.py` | 内存闸门负向自测 |
| `bench/collect_wr_cases.py` | 采集 win_rate 固定用例（生产配置，安全） |
| `bench/bench_winrate_cases.py` | win_rate 基准与逐位对拍 |
| `bench/probe_limitation.py` | limitation 分布与阈值覆盖率 |
| `sim/eval_harness.py` | 算法极限胜率（复刻生产 `play()` 全流水线，**0.5s/局**，比旧脚本快 12x） |
| `bench/measure_noise.py` | 评估台噪声地板（跨批 σ=3.66pp）与复现性（同 seed 必一致） |
| `bench/paired_eval.py` · `compare_arms.py` | 配对 A/B（同 seed 同棋盘，消棋盘难度方差） |

### 测试

- `unittest`：**96 通过 / 1 既有失败**（`test_ui_heatmap.test_stats_line_content`
  断言旧文案，代码文案是最终版；按纪律**不为过测试而改测试**）。
- 内存闸门负向自测 **5/5 PASS**。
- win_rate 逐位对拍：改动实验期间 11/11 用例 Δ=0（确认无残留改动）。
- `cpp/mscore.cpp` 已 `git checkout` 还原至原作者版本（无未提交改动）。

### 待决策（不在本轮范围）

| 项 | 状态 |
|---|---|
| 路线 A：位集重构降 win_rate 每节点成本（2304B→60B/世界），再提阈值 | 设计完成，未实施 |
| 路线 B：中盘引入新选格信源（信息增益/预期展开面积） | 未试验，需配对实验证明 |
| 路线 C：MDD/BDD 原型基准（scientist 列为"不确定"） | 未试验 |
