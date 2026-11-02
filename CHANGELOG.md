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
  与本项目模拟器"先排除 3×3 再布雷"行为等价，但 Arbiter 的安全区可能更大。

### 实测基线（`sim/baseline_winrate.py`，2000 局，seed=20261102）

| 策略 | 胜率 | 95% CI | 平均踩雷步数 |
|---|---:|---|---:|
| B1 纯瞎猜（从不推理） | **0.000%** (0/2000) | [0.00%, 0.19%] | 4.3 |
| B2 纯推理+瞎猜（无解即随机） | **0.750%** (15/2000) | [0.46%, 1.23%] | 64.1 |

口径：真实扫雷规则（30×16、99 雷、首点 3×3 安全、flood-fill），
**不含视觉、不含点击、不含概率枚举**。这两个数是"无智能下限"。

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
