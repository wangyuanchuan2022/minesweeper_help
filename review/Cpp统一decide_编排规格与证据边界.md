# C++ 统一 decide() 编排规格与证据边界

日期：2026-11-05　状态：**规格冻结待对齐，decide() 未导出**

> ## ⚠️ 头号口径（advisor 强制，所有汇报必须复述）
>
> **当前对拍为 Python 生产编排内部观测，无 C++ 侧参与，0 不一致 ≠ C++↔Python 语义对齐。**
>
> `bench/verify_decision_orchestration.py` 只 hook Python `number5_1` 做三条弱断言。
> 该文件 `mismatches=0` 不是 C++ 对齐证据。
>
> **2026-11-05 更新**：已另建 `bench/compare_trial_decide.py`，唯一 reference =
> **Python 生产 `number5_1` 的实际输出**（选格/confidence/分支路径），被测 =
> 隔离 trial 构建 `utils/trial/mscore...pyd` 里的 C++ `decide()`。
> 在**方案 A 策略变更**（确定性破平局替代 np.random.choice）下，
> 6 个真实决策点 **6/6 一致**，且**并列集成员按 (x,y) 排序后逐坐标比对**、并含
> 强制并列构造 case 验证（见 `bench/data/cpp_python_output_parity.json`）。
> 但这是 `CPP_PYTHON_OUTPUT_PARITY_PARTIAL`：样本仅 6、且是策略变更后语义，
> **不是原生产随机语义对齐**，整局 A/B 前必须重新预注册。
>
> ### 已钉死的生产 win_rate 返回契约（实测，非推断）
>
> `number5_1` 调 `win_rate(clicks=8, clicks9=7)`，返回
> `sorted_clicks = 15 = clicks + clicks9` 按 cp 稳定降序重排；
> `np.random.choice(np.arange(15), p=where_max)` 的索引池就是这 15 个。
> 即 **clicks9 确实进入候选返回**。对拍器 Python reference 必须消费这个真实
> `sorted_clicks`，不得用采集状态的 `case['clicks']` 原序重建（曾因此产生
> 假差异 `[(1,14),(2,14)]` vs `[(1,15),(1,16)]`）。
>
> ### 确定性破平局规则（方案 A，用户批准）
>
> 并列候选中取 **`(x,y)` 字典序最小的坐标值**，与列表顺序无关。
> C++ `deterministic_tie_break` 与 Python reference `det_choice` 均已显式注释该规则。
> 两边并列集先按 (x,y) 排序再逐成员比对，排除顺序假差异。
> 强制并列 case（5 个等概率候选）验证两边都取 `(1,9)`。

本文件只记录「把生产 `number5_1` 的选格推理下沉到 C++」时必须原样保留的
语义与当前证据边界。**不是实现，也不是已通过的对拍结论。**

---

## 零、用户确立的边界（不得擅自扩大或缩小）

- **Python 保留**：跨局状态统计（`till_now_winrate`、`checked` 跨局缓存、
  `_wr_update` 阈值反馈/持久化、各类累计计数）、截图/图片识别、实际点击、UI 壳。
- **下沉 C++**：一切影响「输出最佳点击位置」的推理——切分、分组、枚举、
  内存/节点闸门、win_rate 前瞻、pbs 降级、破平局、open_num5x5 加权、选格；
  以及推理过程中的 progress bar 信号（回调形式下沉）。
- 目的：决策成为可脱离 UI 统一测试的纯 C++ 入口。

---

## 一、⚠️ 两套约束数字规则必须原样保留（advisor 指定，禁止顺手统一）

生产代码里存在**两条不同的取值范围**，这不是 bug，是既有语义：

| 用途 | 位置 | 规则 | 含义 |
|---|---|---|---|
| **分组**（click_list 连通分量） | `utils/deduction.py:258-265` `get_set_1` | `0 < v < 8` | 取 **1..7**，排除 0 与 8 |
| **枚举**（part_solve 的 `_cs`） | `utils/probability.py:1690`（C++ `py_part_solve:610-612`） | `1 <= v <= 8` | 取 **1..8**，包含 8 |

`number5_1` 先用 `get_set_1`(1..7) 把 frontier 合并成 `click_list`，再对每组
调 `part_solve`，后者内部用另一套 `_cs`(1..8) 构建约束。C++ `decide()` 必须
**分别复现这两套范围**，不得改成同一套、不得"修齐"。同一格在两处可能归属
不同，这正是生产行为。

---

## 二、`number5_1` 选格编排逐项规格（来自逐行读码）

| # | 环节 | 生产语义 | 文件行号 |
|---|---|---|---|
| 1 | 盘面切分 | `cv2.distanceTransform` on 内部 bg，9/10 为障碍；`res<=1.5`→frontier，否则 interior；**外框哨兵不参与 bg** | `probability.py:503-530` |
| 2 | 分组 | 每个 frontier 取 `get_set_1`(1..7)，与已有组有交集则传递合并；`click_list` 展平后**重写 clicks** | `:569-612` |
| 3 | limit 预算 | 组 > `limit+3` 或超预算 → 该组格从 clicks **迁移到 clicks9**（近似，丢失局部约束） | `:614-691` |
| 4 | 缓存 | `self.checked[tuple(group)]` 命中则跳过 part_solve（**跨局状态，留 Python**） | `:667-673` |
| 5 | 枚举 | 每组调 `part_solve(group, ..., _cs=1..8)`，得 `res_list[i]`、`ck[i]`、`total=∏ck` | `:701-737` |
| 6 | 难度 | `limitation = 0.8*len(clicks9) + log2(total)` | `:793` |
| 7 | 阈值 | `_wr_current_threshold(_tb)` 动态反馈；默认 `6+2=8`（**跨局，留 Python，作为入参传入**） | `:796-798` |
| 8 | 闸门 | `limitation <= wr_threshold and _wr_memory_ok(clicks, clicks9, total)` | `:803-804` |
| 9 | 内存公式 | `total * (1+len(clicks)+len(clicks9)) * 2304B <= 1.5GB` | `:1345-1349` |
| 10 | win_rate | 调 C++ `win_rate(clicks, clicks9, ...)`；**返回 `sorted_clicks = clicks + clicks9`（按 cp 稳定降序重排，共 F+U 格），clicks9 确实进入候选返回**（实测钉死，见头部「已钉死的返回契约」）；node cap 超限抛 `_WrNodeCapExceeded` → 捕获 → 降级 pbs（绝不回退纯 Python） | `:810-823`, `:1401-1423` |
| 11 | 选格 | `win_rate=np.around(...,5)`；`np.where(==max)`；`np.random.choice` **随机破平局**（下沉，需可复现 RNG） | `:833-837` |
| 12 | pbs 分支 | C++ `pbs_compute` 得逐格 safety；`res>=0.99` 全追加；否则 `np.random.shuffle` + `open_num5x5` 加权 + clicks9 置信度比较 | `:1153-1268` |
| 13 | open_num5x5 | 5×5 窗口内 `0<=v<=8` 或越界各计 1（下沉） | `:1555-1573` |
| 14 | 过程信号 | `_pv_mapped_emit` / `Visible_signal` 在枚举与决策段发射（下沉为回调） | `:657,666,673,839` |
| 15 | 失败回退 | part_solve/win_rate/pbs 的 C++ 调用异常时回退纯 Python（**需明确是否下沉**） | `:1424,1683,1163` |

> ⚠️ **已作废的错误前提（留痕，禁止据此修 C++ 候选集）**：
> 曾有 advisor blocker 称「生产 win_rate 候选池仅前沿 clicks、clicks9 不进候选返回」。
> 实测 `number5_1(clicks=8, clicks9=7)` 的 `win_rate` 返回 `sorted_clicks=15`，
> `np.random.choice(np.arange(15))` 的并列索引 `[0,1]` 映射到 `[(1,15),(1,16)]`
> （均来自 clicks9）。**该 blocker 前提错误**。C++ `decide()` 把 clicks9 列入
> 候选是正确的；当时 3 处 `TIE_SET` 假差异源于对拍器 Python reference 用
> `case['clicks']` 原序重建坐标池，而非 C++ 候选集漂移。后续维护者不得据此
> 「修正」C++ 候选集，否则会把对的改成错的。

---

## 三、当前对拍证据与边界（诚实口径，2026-11-05 更新）

工具：`bench/verify_decision_orchestration.py`
证据：`bench/data/decision_orchestration_parity.json`

### 真实样本来源（不是合成、不是改参数造的）

| 数据集 | 采集方式 | 覆盖分支 |
|---|---|---|
| `bench/data/midgame_cases.json` | `collect_midgame_cases.py`，真实对局、只采 limitation>8 的中局点 | pbs / open_num5x5 |
| `bench/data/wr_cases_big.json` | `collect_wr_cases.py`，**生产真实对局中 hook `ProbabilityMixin.win_rate`**、生产阈值 8、计算前捕获 | _wr_memory_ok / win_rate |

`wr_cases_big.json` 33 点全部 limitation 1.0~7.6（<=8）、w=30 h=16 a=99、
is_play=True、不含雷位字段。它正是生产会走 win_rate 的真实决策点，
**不是**抬高阈值或压 node_cap 造的。

### 已真实观测（生产固定值：th=8、node_cap=2e6、mem=1.5GB，16 个决策点）

| 路径 | 覆盖局数 | 状态 |
|---|---:|---|
| `get_set_1` + `part_solve` | 有 | 前半段编排已观测 |
| `_wr_memory_ok` 闸门（allowed=True） | 6 | **真实覆盖** |
| `win_rate` 成功路径 | 6 | **真实覆盖** |
| `process_bigger_situation`（pbs） | 6 | **真实覆盖** |
| `open_num5x5` 加权 | 6 | **真实覆盖** |
| `mismatches` | 0 | **仅三条 Python 内部弱断言全过，非 C++ 对齐** |
| 注入必红负向自测 | PASS | 仪器有区分力 |

### 弱断言清单（advisor nit：必须显式标注，避免误用）

`cross_check()` 实际只做以下三件事，**全部在 Python 侧**：

1. 用真实 `cv2.distanceTransform` 算出 frontier 数，与 `get_set_1` 调用次数比；
2. 有 frontier 时确认 `part_solve` 被调用了一次；
3. 从 `_wr_memory_ok` 自己记录的入参重算 `total*n_cand*2304B <= 1.5GB`，
   与它自己返回的 allow 比对。

**明确未比对**（`not_compared`，对拍器 summary 里也列了）：

- `part_solve` 的解数量 / `res` 解矩阵；
- `win_rate` 的 `res` 数组 / 返回的 `sorted_clicks`；
- `pbs_compute` 的加权 `res` / `open_num5x5` 数值；
- 最终选格坐标 `best`；
- **C++ `decide()` 的任何输出**（未导出、`#if 0`）。

因此 `verdict` 已改为 `PYTHON_PATHS_OBSERVED_WEAK_ASSERTIONS_PASS`，
`comparison_scope=PYTHON_INTERNAL_ONLY_NO_CPP_PARITY`。**该读数不得用于
指导 `decide()` 编排实现。**

### RNG 口径（advisor：比对选格前必须固定）

生产用 `np.random.choice`（win_rate 破平局）与 `np.random.shuffle`（pbs 加权），
**未固定种子时同一盘面 6 次实测跑出 `(1,15)` 与 `(1,16)` 两种选格**。

当前对拍器 `run_case(..., rng_seed=...)` 在**每个 case 开头独立重设**
`np.random.seed`，不是全局只固定一次。实测：

- 3 个不同 case，各用独立 seed（1000+ci），同 case 连跑 **12/12 选格一致**；
- 顺序独立性：跑完其它 case 后回头跑同一 case（seed=777），选格仍一致；
- 比对任何坐标前必须传 `--rng-seed`；`rng_seed=None` 时 `best` 只是观察值，
  **不是可复现断言**（summary 里 `rng_pinned=false` 会显式标出）。

### 仍未覆盖（诚实缺口）

| 路径 | 覆盖 | 说明 |
|---|---:|---|
| `win_rate` node-cap → pbs 降级 | **0** | 生产 2e6 节点上限下，这批真实 win_rate 样本都没触发；**不得**为了覆盖而人为压 cap |

### 已作废的错误做法（留痕，禁止重犯）

中间版本对拍器曾：
1. 把 `wr_threshold` 抬到 99；
2. 把 `node_cap` 从生产 2e6 缩到 20000；
3. 据此造出「gate→win_rate→立即 node cap→pbs」的读数并宣称终端路径覆盖。

这是**非生产路径**，advisor 指出后已全部回退。现版本全程生产固定值，
win_rate 覆盖来自 `collect_wr_cases.py` 的真实 hook 样本。

### 结论

> **当前对拍为 Python 生产编排内部观测，无 C++ 侧参与，0 不一致 ≠ C++↔Python 语义对齐。**
> gate / win_rate 成功 / pbs / open_num5x5 加权已在生产真实样本上被「观测到」，
> 但三条弱断言只验证了 Python 内部自洽，未比任何返回值或选格，更没有 C++ 参与。
> 15 条编排规格（两套约束范围、随机破平局、pbs 加权、node-cap 降级）**全部仍未对齐**。
> 在建立「C++ decide() 输出 vs Python number5_1 输出」的逐项对拍之前，
> **不得导出或实现 `decide()`**。

---

## 四、split_frontier 假绿教训（已修正，勿重蹈）

早期 `bench/verify_decision_primitives.py` 用「自写 reference 对拍自写 C++」，
两边都把哨兵外框 0 当已开数字 → 读数一致但**都错**。实测用真实
`cv2.distanceTransform` 对 200 合成盘面发现 13~45 处不一致。已改为：
- C++ `py_split_frontier`：内部限定 8 邻域、9/10 为障碍、排除哨兵外框；
- 参考实现：直接调 cv2，不再自写规则；
- 独立交叉对拍 300 盘面 0 不一致，含阳性对照。

---

## 一之二、⚠️ pbs 决策语义的不可修改边界（2026-11-05 单步对拍钉死）

以下隐式契约经 25 个真实决策点逐位对拍确认（`compare_trial_decide.py`，
25/25 一致），是 C++ `decide_impl` 的**不可修改边界**。任何改动必须先跑
同一对拍验证。

| # | 契约 | 生产锚点（函数名:行号） | C++ 侧硬条件 | 误改的漂移风险 |
|---|---|---|---|---|
| C1 | **certain-safe 双重判据**：先 `1 in res`（float32 中**精确 1.0**）才进分支；分支内追加所有 `res>=0.99` | `ProbabilityMixin.process_bigger_situation:1171`（`if 1 in res:`）+ `:1173`（`res[index] >= 0.99`） | `has_exact_one` 循环用 `== 1.0`，组内用 `>= 0.99` | 只用 `>=0.99` → 0.991 被误判 certain-safe（case 7 实测），批量分支提前触发，选格漂移 |
| C2 | **res 无舍入**：判据全程作用于 `py_pbs_compute` 返回的原始 float32 | `pbs_compute` 返回 `py::array_t<float>`（mscore.cpp `py_pbs_compute`）；`:1171` 直接作用 | `rr` 即 float32 unchecked 视图，不引入 `np.around` | 加 `around(res,5)` 会改 tied 集合成员（0/1 归一化后尤其敏感） |
| C3 | **并列=容差并列**：`0.0005 >= max_val - res[p] >= -0.0005`，减法在 **float32** | `:1206`（`0.0005 >= max_val - res[p] >= -0.0005`） | `(float)bestv - rr(k) <= 0.0005f` | double 减法纳入假并列成员（case 9 实测 cpp 6 vs py 1） |
| C4 | **clicks9 兜底比较在 round5 后**：`confidence=round(max_val,5)`，`_confidence=round(1-mine9/n,5)`，严格 `>` 才切换 | `:1222`（`confidence = round(max_val, 5)`）+ `:1224`（`_confidence = round(...)`）+ `:1225`（`>`） | `bestv_r5`、`interior_safety_r5` 均 `std::round(x*1e5)/1e5`，比较 `>` | 不舍入会在"看似相等"的值上错误切换分支（残局选格漂移） |
| C5 | **批量返回**：certain-safe 分支把**所有** `res>=0.99` 格加入 `pos` 一次返回 | `:1172-1174`（`for index in range(len(res)): ... pos.append(...)`） | 返回 `coords_to_pylist(certain)` 整批 | 逐格返回 → 决策轮次 12→159、慢 48 倍、胜率 -10pp（实测） |
| C6 | **坐标契约**：`(x,y)` 1-based，x=列 y=行 | `clicks.append(tuple((i + 1, j + 1)))`（`:528`） | `out_xy[2k]=x, out_xy[2k+1]=y`；`g.idx_of(bx,by)` | x/y 交换 → 全部选格错位、踩雷 |
| C7 | **open_num5x5 口径**：5×5 窗口（±2），越界 +1，`0<=v<=8` +1（候选自身=9 不计） | `ProbabilityMixin.open_num5x5:1555-1573` | 内联 opens 循环（weighted_tied / interior_tied 两处） | 计数差 1 → op_num max 组反转 → 选格完全不同 |

### 对拍器的字段语义（避免再混淆）

- C++ `tied` = stage 1（res 容差并列集，可含 6 成员）；
- C++ `weighted` = stage 2（open_num5x5 max 组，通常 1 成员）；
- Python det_choice 记录的是 **stage 2**（`p>0` 即 `op_num==max` 组）；
- ⇒ 对拍必须 **stage2 对 stage2**（`cpp["weighted"]` vs `py["tied"]`），
  case 9/11 的假差异皆因 stage1 对 stage2。

---

## 四之二、⚠️ node-cap 降级路径是未验证关键假设（advisor 指定，禁止提前实现）

对拍器当前读数：

```
win_rate_node_cap_fallback = 0
```

即 **生产固定值（node_cap=2e6）下的真实样本里，没有任何一次 win_rate
触发节点超限并降级到 pbs**。这使 `number5_1` 的
`except _WrNodeCapExceeded → process_bigger_situation` 分支（`probability.py:812-823`）
成为**未验证的关键假设**。

明确规定：

- ❌ **禁止**在 C++ `decide()` 里提前实现该分支的语义对齐（不能凭代码阅读
  推断"捕获异常→pbs 就够了"）。
- ❌ **禁止**用缩小 node_cap（如 20000）或抬高阈值造的假样本来实现/验证它——
  上两版已被证伪并作废，那是非生产路径。
- ✅ 必须取得**生产 2e6 上限下真实触发**的 hook 样本，或经用户明确批准
  的等价采集口径，才能把该分支纳入 `decide()` 规格。
- 在补齐前，`decide()` 的 win_rate 失败处理只能保持「未定义/不导出」，
  不得以「已对齐」口吻描述。

### node-cap 真实样本获取方案（只写方案，本轮不执行）

现有证据：`state/ab/prod_params/prod_params_summary.json` 显示生产参数
（th=8/12/16、node_cap=2e6、12 局）下 `degrade_decisions=0`、
`n_degraded_games=0`；即**小样本里该分支本就不触发**，不是对拍器漏了。
`bench/sweep_prod_params.py` / `bench/sweep_cap.py` 已有
`prod_nodecap_snapshot()` 差值口径（读 `_WR_NODECAP_TOTAL` 前后差），
`bench/verify_node_cap_e2e.py`（已随 win_rate API 删除） 有完整的「触发→降级→计数」端到端验证，
但它们用的是 `cap=1` 这种**非生产值**（该脚本自述用于负向测试，
不能当生产样本）。

合规方案（需用户批准后才执行，禁止自行启动长跑）：

1. **不改任何生产参数**：th=8、node_cap=2e6、mem=1.5GB、w=30 h=16 a=99。
2. 扩大真实整局规模：用 `bench/ab_policy.py::play_one` 的 baseline 臂，
   在**更多 seed 区间**（如 20261102 起再扩 N 局）跑生产真实对局；
   hook `probability._WR_NODECAP_TOTAL` 的每局前后差值，
   一旦某局 `prod_nodecap > 0`，用 `collect_wr_cases.py` 同款
   「计算前捕获」把该决策点的 `clicks/clicks9/ck/groups/cell_value`
   落盘为 `wr_nodecap_cases.json`。
3. 判据：样本必须来自 `degrade_decisions>0` 的真实局，且记录
   `visited/cap` 原值；**不得**为了凑样本改 cap 或阈值。
4. 成本：这是消耗 CPU 的长跑，按用户纪律需先出成本核算书并获批准。
5. 若扩大 N 后仍为 0：如实标注「生产 2e6 下该分支在观测窗口内未触发」，
   把它列为**未验证关键假设**继续挂着，不得用任何近似样本替代。

---

## 四之三、⚠️ 临时验证导出 vs 生产基线 mscore.pyd 的硬边界（advisor 指定）

后续所有汇报、规格文档、脚本注释都必须显式区分以下三者，禁止混用：

精确口径（advisor nit，逐字固定）：

> **临时构建**（含额外导出的 `split_frontier / estimate_mem / decision_preflight /
> packed_board_storage`）**仅用于对拍，不得用于生产**；
> **生产基线** SHA=`5B22EC2BDE70ACC8E64DAD8A8CBF9305970DF8EB228A744F06ED22AAC50219CD`
> 且**仅导出 7 个运行时函数**：
> `exact_eval / last_phase / md5 / part_solve / part_solve_single / pbs_compute / win_rate`。

| 对象 | 内容 | 导出/可用 API | 用途 |
|---|---|---|---|
| **生产基线 pyd** | `utils/mscore.cp310-win_amd64.pyd`，sha256 `5B22EC2B...19CD`（HANDOFF 第 0 步冻结） | 运行时可见**仅 7 个**：`exact_eval / last_phase / md5 / part_solve / part_solve_single / pbs_compute / win_rate`（导出符号检查器计 8 个，含 `PyInit_mscore` 模块入口）。**无** `decide / split_frontier / estimate_mem / decision_preflight / packed_board_storage` | 生产运行时；不得覆盖、不得假定含新 API |
| **临时验证构建** | `cpp/build.bat` 用当前 `cpp/mscore.cpp` 源码重新编译出的 pyd（验证后即恢复基线；备份在 `.bak_decision_*` / `.bak_packed_diag` 等） | 额外含 `estimate_mem / split_frontier / decision_preflight / packed_board_storage`；`decide()` 仍 `#if 0` 未导出 | **仅对拍/契约验证，不得用于生产，不是当前生产运行时** |
| **源码状态** | `cpp/mscore.cpp` 工作区改动（未提交） | 源码里已写上述原语与 `#if 0` 的 `decide()` 草稿 | 待语义对齐后才允许构建为生产 pyd |

硬性纪律：

1. 任何写 `from utils import mscore; mscore.split_frontier(...)` 的验证脚本，
   必须先确认当前 pyd 是「临时验证构建」；若当前是基线，会直接 `AttributeError`。
   验证脚本不得假定新 API 在生产基线里存在。
2. 编译临时验证产物前先备份基线，验证结束**立即**从
   `utils/mscore.cp310-win_amd64.pyd.bak_baseline` 恢复，并复核 sha256。
   （此前 `build_ab_native.bat` 输出误改为 `mscore.pyd` + 后台任务取消导致
   恢复步骤没跑，基线曾被覆盖为 `B5015173...`，已修复。）
3. 汇报措辞固定：
   - ✅「源码已实现、临时构建已对拍通过、当前生产运行时未启用」
   - ❌「已导出 / 生产已有该 API」（指新原语时）
4. 不得用二进制 hash 论证「逻辑未变」（MSVC 非确定性）；重新构建生产 pyd
   后须用导出符号对比 + 分进程数值对拍，且基线 hash 变化是预期事件。

---

## 四之四、方案 A 整局小样本策略 pilot（2026-11-05，advisor 要求）

工具：`bench/ab_optionA_10games.py`
证据：`bench/data/optionA_10games.json`

### 设计（严格遵守 advisor 约束）

- 两臂**同 seed 配对**，seed=`20274000+`（HANDOFF 第九节预注册策略臂区间，
  **不是**历史 seed 20261102）。
- 生产参数：w=30 h=16 a=99、limit=80、world_cap=4096、node_cap=200000。
- `baseline` 臂：**完全不 patch**，原生产 `np.random.choice/shuffle` 随机破平局。
- `optionA` 臂：**范围变更：仅 win_rate 破平局**采用确定性规则（并列取
  `(x,y)` 字典序最小），patch 仅在 `number5_1` 执行期活跃（`ACTIVE` 标志），
  作用域外用原函数；restore 用 id 硬断言。
- **pbs 保持原生产随机语义，明确不纳入 optionA 确定性化范围**：pbs 的
  `poses` 是 `res` 阈值筛出的动态子集，choice_p 长度与 `process_bigger_situation`
  的 args[4] `clicks` 系统性不等，捕获/重写会越界到 pbs 选格逻辑；用户已批准
  收窄范围，禁止后续维护者把 pbs 纳入 patch。
- 除 win_rate 破平局外，不改分组、`np.around(...,5)`、win_rate/pbs 计算。
- 逐局记录：seed、won、steps、n_decisions、n_ties、branches。

### ⛔ 全部 10 局读数已作废（INVALID DATA，禁止引用为上表结论）

本小节此前记录的 `baseline 3/10 (30%) vs optionA 2/10 (20%)、Δ−10pp`
**全部作废**，不得用于任何策略决策（否决 / 采纳 / 扩大样本 / 预注册）。
作废原因（逐条实测坐实，非推测）：

1. **hook 漏记**：一局 `number5_1` 被调用 8~11 次、
   `np.random.choice` 9~13 次，但诊断只记到 `n_dec=1`。
   根因是 `number5_1` wrapper 每次调用都清空 `scope.decisions`，
   只剩最后一次。已修（清空移至 `run_arm` 每局一次）。
2. **det_choice 误改非破平局逻辑**：`_choice` 曾仅凭 `p` 的数学形态
   （均匀/0-1）拦截，把**首点 471 格均匀随机**（`probability.py:553-555`）
   和 **open_num5x5 加权选择**也改成确定性规则。
   实测这两类 p 与 win_rate 破平局 p 的 `np.where(==max)/sum` 形态
   **完全相同**，纯形态判据必然误拦。已改为**显式 branch 标记**
   （win_rate / pbs hook 设置）作为唯一 gate。
3. **Python 绑定 bug**（更早一版）：`_core() missing 1 required positional
   argument: 'cell_value'` 导致 optionA 10 局全崩、0 胜。
4. **seed 曾用历史区间** 20261102（非 HANDOFF 预注册 20274000+）。

中间还出现过 `0/10`、`−30pp`、`−10pp` 等多个无效读数，
**一律作废**。唯一保留的是脚本与白盒测试代码，不是数字。

### 修复后已通过的前置验证（本轮，未跑胜率）

- `bench/verify_optionA_scope.py`：**白盒测试全过**
  - win_rate 破平局 → 拦截；
  - **pbs 分支 → 明确不拦截、原样转发**（用户批准的范围边界，poses 捕获不做）；
  - 首点均匀随机 → **原样转发**；
  - **同一个 p、只差 branch 标记** → win_rate 标记拦截 / branch=none 原样转发（决定性对照）；
  - `number5_1` 作用域外 → 不拦截；
  - `restore` 用 `sys.modules['numpy.random'].choice` 的 **id 比较**验证完全还原。
- 单局端到端：`n51_calls=11`、`choice_calls=13`、`n_decisions=13` 三者一致
  （修复前只记 1），作用域自测通过。

### 结论（更正）

> **当前没有任何可信的方案 A 整局胜率读数。**
> 上一版小样本结论「方向未定、未发现显著危害」同样作废。
> 必须先用修复后的脚本重跑小样本（3~5 局，验证每局
> `n_decisions` 覆盖 win_rate 与 pbs 两分支）、再谈扩大样本。
> 任何关于 40% 或方案 A 优劣的判断都缺乏证据。

---

## 四之五、纯 C++ 整局驱动首次跑通（2026-11-05）

### 架构（用户目标已实现）

```
Python: 只做 I/O（构造 cell_value / 点击）
C++   : ab_native.cpp 整局循环 + abnative_decide() 胶水
        → decision_layer::decide_impl()（与 number5_1 做过 6/6 双实现比对的同一份逻辑）
```

构建：`cpp/build_wholegame.bat`（`AB_NATIVE_MERGED` + `MSW_TRIAL_DECIDE`），
输出隔离到 `utils/trial_wholegame/`，**生产基线 SHA 双重校验不被触碰**。

### 修掉的三个真实缺陷（逐个实测定位，非猜测）

1. **盘面外框编码错误**：驱动构造 `cell_value` 时外框填 `9`，
   而生产实测外框**全为 0**（`unique(border) == [0]`）。外框 9 会污染
   `split_frontier` 的 8 邻域判定 → 前沿集合错 → 选格偏移。
   已改为外框 0、内部拷 `g.cell`。
2. **构建产物路径冲突**：`build_trial.bat` 与 `build_wholegame.bat` 曾同时
   输出 `utils/trial/mscore.pyd` 互相覆盖。已分离为
   `utils/trial_decide/` 与 `utils/trial_wholegame/`，并清理遗留 `utils/trial/`。
3. **pbs 分支缺 certain-safe**：生产 `probability.py:1171-1189` 在
   `res>=0.99` 时先把所有确定安全格点掉；`decide_impl` 原来只取 argmax。
   已补齐，驱动器实测 `certain_safe_hits` 显著非零。

### 读数（seed=20274000+，w=30 h=16 a=99，world_cap=200000，wr_threshold=8）

| 臂 | 局数 | 胜 | 胜率 | 踩雷局 | 备注 |
|---|---:|---:|---:|---:|---|
| **Oracle**（看真雷位） | 5 | 5 | **100%** | 0 | 模拟层正确性对照 |
| **production**（C++ decide） | 40 | 12 | **30.0%** | 28 | certain_safe 命中数千次 |

证据：`bench/data/pure_cpp_wholegame_baseline.json`
耗时：40 局约 58 秒（≈1.45 s/局，含 C++ 决策）。

### 证据边界（必须随读数一起报）

- n=40，p≈0.30 时 Wilson 95%CI 约 **[18.1%, 45.4%]**，跨度 27pp。
- 与历史基线 36.35%（P2 配置 2000 局）**方向接近但本样本 CI 覆盖它**，
  因此**不能**说「C++ 实现比生产差」，也**不能**说「等效」。
- 此前所有「0 胜 / 100 步硬停」读数来自 **3 局小样本 + 外框缺陷未修**，
  已作废。
- 本读数**不是** 40% 目标证据：40 局 MDE 约 14pp，无法判定 40%。
- 早期 1~2 步即死的局（`steps_per_game` 含 1,2,2,1,2）反映**必然盲猜局面**：
  `limitation > wr_threshold=8` 时直接走 pbs，没有 win_rate 前瞻。

### 与 40% 目标的差距分析（下一步方向）

生产在同等局面会用 `win_rate` 前瞻降低盲猜损失。当前 C++ 路径
`wr_threshold=8` 使绝大多数中局 `limitation`（真实中位约 61）**全部超出阈值**，
直接 pbs 盲猜。这与生产 Python 行为一致（生产同样如此），
所以 30% vs 36.35% 的差距更可能来自：
1. `decide_impl` 的 pbs 分支仍缺生产的部分细节（`np.random.shuffle` 加权破平局、
   `open_num5x5` 加权、clicks9 置信度比较 → 可能改选内部格）；
2. 破平局改为确定性（方案 A）本身的策略影响（尚未单独量化）。

### 结论

> **C++ 全推理整局驱动已跑通并产出可复现胜率读数 30.0%（12/40）。**
> 但这既不是 40% 证据，也不能据此判定与生产等效。
> 下一步必须先补齐 pbs 分支剩余语义（第 12 条规格），再做同 seed 配对 A/B。

---

## 四之六、同棋盘配对对照（2026-11-05，RNG/首点/布雷顺序三重对齐后）

### 本轮修掉的三个对拍失效根因（逐个实测坐实）

1. **RNG 不同**：C++ `std::mt19937_64` vs Python `random.Random`，
   同 seed 生成**不同棋盘**（实测 3 seed 首点数字 2/4/4 vs 2/1/1）。
   ⇒ 修法：新增 `run_ab_mines(mines)` / `debug_first_board_mines(mines)`，
   由 Python 生成雷位直接喂 C++，绕过 RNG。
2. **首点坐标基准不同**：Python `Board` 是 **0-based**（`open(15,8)` = 
   1-based (16,9)）；C++ `idx_of` 是 1-based。C++ 首点已改为
   `idx_of(w/2+1, h/2+1)` 对齐 Python 0-based 中心。
3. **布雷时序不同**：Python `open()` 里 `generate()`（撒雷，池=全盘-首格）
   **先于** `_flood()`；C++ 曾先 flood 后撒雷 → 首格恒 0、flood 大片。
   已恢复「撒雷在 flood 前」。

### 同棋盘逐格对拍（advisor blocker 的验收）

Python 生成 3 副雷位 → C++ `debug_first_board_mines` 重建首帧：

```
seed 20274000 inner_identical True diff 0
seed 20274003 inner_identical True diff 0
seed 20274006 inner_identical True diff 0
```

### 同棋盘 50 局配对对照（真对照，非跨 seed 比较）

| 臂 | 胜率 | 配对独赢 |
|---|---:|---|
| Python 生产 | 14/50 = 28.0% | only_PY = 8 |
| C++ decide | 8/50 = 16.0% | only_CPP = 2 |

**McNemar 精确 p = 0.0879**：方向上 Python 优 12pp，但未达显著；
需 ~150 局同棋盘配对才能以 80% 功效确认 12pp 差距。
证据：`bench/data/paired_sameboard_50.json`。

### 结论（更正此前两条过强结论）

1. ❌ 「C++ 26% 显著低于基线 36.35%（CI 外）」**作废**——那是跨 seed 区间
   的无效比较（PY 历史数据用 seed 20261102+，C++ 用 20274000+）。
2. ❌ 「同 seed 胜率一致 30% vs 30%」**作废**——RNG 不同导致棋盘不同，
   该"一致"是巧合。
3. ✅ 成立的是：**同棋盘配对**下 C++ 落后 12pp（p=0.088，未显著）。
   差距来源指向 pbs 分支剩余缺失（`np.random.shuffle` 加权破平局、
   `open_num5x5` 加权在**前沿并列**时的应用——certain-safe 与
   clicks9 兜底已实现），及 win_rate 分支细节。

### 性能（advisor 关心的"为什么更慢"）

- 根因已修：`decide()` 曾逐格返回，决策轮次 159/局 vs 生产 12/局
  （生产一次决策批量点掉所有 `res>=0.99` 安全格）。
- 修后 C++ **0.28 s/局** vs Python 同进程 6.4 s/局（漂移污染，历史口径
  0.102 s/局）⇒ C++ 约快 2~4 倍，不再更慢。

---

## 四之七、open_num5x5 加权破平局补齐后的同棋盘复测（2026-11-05）

### 本轮改动（单变量）

规格第 12 条剩余部分补齐：pbs 前沿**并列**时按生产
`probability.py:1209-1217` 的 `open_num5x5` 加权选择（确定性等价：
并列中取 5×5 已开格数最大者，等计数回退 `(x,y)` 最小）。
certain-safe、clicks9 兜底此前已实现，本轮未动。

### 同棋盘 50 局复测（同一批 Python 生成的雷位）

| 版本 | C++ 胜率 | only_PY / only_CPP | McNemar p |
|---|---:|---:|---:|
| 加权前（坐标最小破平局） | 8/50 = 16.0% | 8 / 2 | 0.0879 |
| **加权后（本轮）** | **10/50 = 20.0%** | **6 / 2** | **0.2188** |

Python 参照恒 14/50 = 28.0%。

### 解读（证据边界）

- 加权让 C++ **多赢 2 局、与 Python 差距从 12pp 缩到 8pp**，方向为正；
- 但 n=50 的 McNemar 两种版本都**不显著**（0.088 / 0.219），
  差距是否真实需 ~150 局配对确认；
- C++ Wilson 95%CI [11.2, 33.0]，与 PY 28% 重叠——同样不能下"更差"结论；
- per_game_s 0.147（批量决策后稳定快于 Python 同进程读数）。

### 结论

> pbs 前沿并列的加权破平局已补齐并产生正向读数（+2 局）。
> 规格第 12 条（certain-safe 全追加、clicks9 兜底、open_num5x5 加权）
> 在 C++ `decide_impl` 中已全部实现。
> 40% 目标未达成；下一步是扩大同棋盘配对样本（~150 局）判定剩余 8pp
> 差距的真实性，再决定是否需要继续找语义差异或进入方案 A 胜率评估。

---

## 四之八、150 局同棋盘配对判定（2026-11-05，MDE 核算后）

### 前置（全部通过）

1. 盘面一致性：10 个 seed 逐格 `inner_identical=True, diff=0`。
2. 单步对拍：25 个真实决策点 **25/25 一致**（含首/中/残局），
   期间修掉 4 处语义漂移（见「一之二」节 C1-C7 硬约束清单）。
3. 构建隔离：`trial_decide` / `trial_wholegame` 哈希不同、mtime 独立、
   生产基线 SHA 未动。
4. MDE 核算：保守（p_d=0.25）下 150 局可检出 ~6.5pp，覆盖观察差距。

### 读数（seed 20274000+，同 150 副雷位）

| 臂 | 胜率 | Wilson 95% CI |
|---|---:|---|
| Python 生产 | 49/150 = **32.7%** | [25.7, 40.5] |
| C++ decide | 36/150 = **24.0%** | [17.9, 31.4] |

**配对 McNemar 精确 p = 0.0243**（only_PY=24，only_CPP=11）
⇒ **Python 生产显著优于当前 C++ decide（8.7pp）**。

证据：`bench/data/_paired_150.json`、`bench/data/paired_150_stats.json`。
C++ per_game_s 1.07（批量决策后仍快于 Python 同进程读数）。

### 解读（诚实口径）

- 25 点单步对拍全绿**不能**保证整局长尾分布无差异——150 局里出现了
  单步样本未覆盖的决策形态。这是"单步抽样 vs 整局分布"的覆盖缺口，
  与记忆条目「区分未观察到与不可能」同源。
- 差异方向与 50 局试点（8pp，p=0.22）一致，扩大样本后显著化。
- 差距来源候选（2026-11-05 白盒复现器全量回放后**已更新**，
  见「四之九」节）：
  1. ~~`wr_threshold` 动态反馈（生产 `_wr_update` 每局 ±0.5 调整阈值，
     C++ 固定 8.0）~~ **已否决**：本轮实测确认本次 150 局配对的
     Python 臂同样未激活动态阈值（`ab_policy.play_one` 里
     `_time_budget=None`，`probability.py:796` 的 `if _tb is not None`
     门被旁路），两侧都在固定 8.0 下比赛。此候选不构成差距根因。
     （注：生产 GUI 真实使用中动态阈值是激活的，
     `state/time_budget.json` 持久化 wr.t=8.0；差异只在台架。）
  2. pbs 前沿并列的 `np.random.shuffle` + 加权**随机抽样**（方案 A 只
     确定性化 win_rate 分支，pbs 保持随机，但 C++ 全确定性）
     —— **升为头号候选**；
  3. win_rate 分支的深度/节点细节。

### 结论

> **C++ decide 与生产在整局上存在 8.7pp 的显著差距（p=0.024）。
> 在差距根因定位并修复前，C++ 路径不得用于 40% 目标的胜率主张，
> 更不得同步进生产。** 下一步优先核查 `wr_threshold` 动态反馈缺失
> （C++ 固定 8.0 vs 生产持久化动态阈值）对整局的影响。

---

## 四之九、白盒复现器全量回放：分支触发率与根因更新（2026-11-05）

### 方法（仪器先验证，再测量）

用 Python 独立复现 C++ `play_one_mines` 的完整决策循环
（首点 1-based 中心 → `abnative_decide_batch` certain-safe cap-64 批量
→ 单次 `abnative_decide` → 首个未开格 fallback），对每局每决策记录
reason 字符串与落点。**仪器验证三层全过才采信读数**：

1. 盘面构造契约：`debug_first_board_mines` 对照自制 build_frame，
   10/10 逐格一致（含首点坐标、cv 边界 0、撒雷先于 flood）；
2. reason 字符串格式：独立进程探针实测（`clicks9_fallback` /
   `limitation` / `certain_safe` / `win_rate`），不靠猜；
3. 整局逐位对齐：**150/150 局胜负 + 步数与 C++ 臂完全一致**
   （复现器成为合法取证仪器）。

### 读数（150 局全量回放，18932 个决策轮）

| 分支 | 触发次数 | 占比 |
|---|---:|---:|
| certain_safe | 17708 | 93.5% |
| win_rate | 649 | 3.4% |
| limitation (pbs) | 320 | 1.7% |
| clicks9_fallback | 164 | 0.9% |
| memory_gate | 91 | 0.5% |

踩雷分支分布：win_rate 39 / limitation 42 / clicks9_fallback 28 /
memory_gate 5（无 certain_safe 死亡，符合构造）。

Python 臂对照（20 局样本，`ab_policy.play_one` 计数包装）：
win_rate 约 1 次/局、pbs 约 5 次/局——两侧 win_rate 触发频率同量级。

### 差距的分布位置

- 开局（前 2 次点击/轮内死亡）：py 33 局 vs cpp 32 局——**两侧相等**
  （固定选格踩雷期望本就相同），开局不是差距来源。
- **中盘才是**：双方都活过开局阶段的 72 局中，py 赢 38 vs cpp 赢 29
  （only_PY 13 vs only_CPP 4）。

### 根因清单更新

| 候选 | 状态 | 证据 |
|---|---|---|
| ~~wr_threshold 动态反馈缺失~~ | **否决** | py 臂 `_time_budget=None`（ab_policy.py:295），动态链路被 `probability.py:796` 旁路，两侧均固定 8.0 |
| pbs 破平局随机性（py np.random vs cpp 确定性 (x,y)-min） | **头号候选（未验证）** | 差距聚集在中盘（活过开局的 72 局 38:29）；(60,inf] 局面平局集中位 463 格（中局评估报告 §二），破平局空间大 |
| win_rate 分支深度/节点细节 | 次要候选 | 两侧触发频率同量级（~1 次/局） |

### 下一步（单变量消融，待用户批准）

用白盒复现器做单变量实验：只把 cpp 臂 pbs 破平局改为
np.random.shuffle 等价随机（或反向把 py 臂确定性化），150 局配对，
隔离剩余差距的归属。复现器单局约 0.9s，150 局约 2.5 分钟，
成本可忽略。

### 消融结果（2026-11-05，用户批准选项 A 并指定用启发式参数破平局）

**预注册**：固定输入 = `_paired_150.json` 150 局同雷位；单变量 =
仅 pbs 破平局方式（limitation / clicks9_fallback / memory_gate
三分支的 tied 集内选择）；统计 = 各臂胜率 + Wilson CI +
配对 McNemar 精确 p。

| 臂 | 破平局方式 | 胜 | 胜率 | Wilson 95% CI |
|---|---|---:|---:|---|
| det | C++ 确定性 (x,y)-min | 36 | 24.0% | [17.9, 31.4] |
| **prodR** | 生产等价随机（shuffle→open_num5x5 加权→choice，probability.py:1200-1217 逐语义） | **46** | **30.7%** | [23.8, 38.5] |
| **pureR** | 纯均匀随机（对照） | **48** | **32.0%** | [25.1, 39.8] |
| py | Python 生产臂 | 49 | 32.7% | [25.7, 40.5] |

配对 McNemar：

| 比较 | only_A | only_B | p | 判定 |
|---|---:|---:|---:|---|
| prodR vs det | 16 | 6 | **0.0356** | **随机显著优于确定性（+6.7pp）** |
| pureR vs det | 20 | 8 | **0.0232** | 同上（+8pp） |
| prodR vs py | 17 | 20 | 0.2315 | **差距不再显著** |
| det vs py（原始差距） | 11 | 24 | 0.0243 | 显著（原读数复现） |

### 消融结论

> **8.7pp 差距的根因就是 C++ 的确定性 (x,y)-min 破平局。**
> 换成生产等价随机破平局后 C++ 胜率 24.0%→30.7%（p=0.036），
> 与 Python 生产臂的差距从 8.7pp（p=0.024）缩到 2pp（p=0.23，不显著）。
> 随机性本身（而非 open_num5x5 加权）贡献主要收益：
> 纯均匀随机 32.0% 反而略高于加权随机 30.7%（2 胜，未达显著）。
>
> **机制解读**：确定性 (x,y)-min 在中盘关键决策上系统性偏向坐标
> 最小格（左上角区域），深局面平局集中位 463 格时该偏差被放大；
> 随机破平局没有这个系统性偏差。

### 证据边界

1. 单次运行、单一随机种子（prodR=20276000 / pureR=20277000）——
   效应量含种子幸运成分，**需独立 seeds 复现后才能定案**；
2. prodR vs pureR 的 2 胜差提示 open_num5x5 加权可能略有负贡献
   （边角副作用，中局评估报告 节 2.1 已知），但 n=150 分不出来；
3. py 臂本身也是随机破平局——py 比 pureR 高 1 胜在噪声范围内；
4. 此消融证明的是「确定性→随机」的方向性收益，**不是** 40% 目标证据；
5. 破平局方式只影响 limitation/clicks9/memory_gate 三分支
   （约 575 个决策轮/18932），certain_safe（93.5%）不受影响——
   改这一个变量的影响面天然受限，+6~8pp 的效应量相当可观。

---

## 四之十一、Filter-U（D3）多种子复现、平局集分布与仪器校准（2026-11-05）

### 仪器校准（重要纠正，防后续实验误判）

**Filter-U 是完全确定性策略**：argmax u 值、无随机分支。验证方式：
两个独立随机种子（20280100 / 20280200）跑同一 150 局雷位集，
结果**逐局完全相同**（150/150 identical，均 49/150 = 32.7%）。

⇒ **教训**：对确定性策略，"多种子复现"必须换**雷位集**
（重新生成 layouts，如新 seed 段 20275000+），而不是换策略侧
随机种子——后者是无效操作（结果必然恒同）。任何"换种子后结果
完全一致"的读数，先区分两种可能：策略确定性（有效重复性证据）
vs 种子未接线（仪器故障）。本次用代码审查排除后者（rng 参数
确实未进入决策路径）。

### 平局集大小分布（cap=16 的依据，n=1200 破平局决策）

| 平局集大小 | 占比 |
|---|---:|
| [0, 8) 格 | 89.7% |
| [8, 16) 格 | 10.3% |
| >16 格 | **0%**（max=10） |

⇒ cap=16 实测零超限。深局面内部格大平局集（中位 463）走
clicks9_fallback 的 open_num5x5 分支，不进 tied 评估范围。

### C++ 化耗时推算（PoC 前置，量级估算口径）

- Python 代理实测：filterU 破平局 51.7ms/决策（均 3.8 候选，
  每候选 13.5ms = Python decide 一次）；
- C++ 同内核锚点：主 decide Python 2.7ms/call vs C++ 整局
  1.07s/150 局（约 126 主决策）⇒ C++ 单 decide ≈ 0.05ms；
- 外推：C++ filterU ≈ 3.8 候选 × 0.05ms + 克隆/flood 微秒级
  ≈ **0.2~0.5ms/决策**，对 80ms 硬约束余量 160 倍+。
- ⚠️ 此为量级估算（内外核比值外推），E1 实装后必须逐次原始
  数据复测（非均值，覆盖 cap≤10 全部场景）。

### E1 验收 checklist（实装前三项，不得跳过）

1. **独立雷位集配对验证**：重新生成 150 局雷位（非 20274000+ 段），
   三臂（det / filterU / pureR）配对 + McNemar——确定性策略的
   "换种子复现"必须落在雷位集上（见仪器校准）；
   **prodR 须同时覆盖种子方差**：每雷位集跑 2 个 prodR 随机种子，
   确认随机策略胜率的种子间稳定性（filterU/pureR 各 1 个种子即可，
   pureR 虽随机但同批对拍内自洽）；
2. **耗时逐次复测**：C++ 实装后采集单决策耗时**逐次原始数据**
   （非均值），覆盖全部破平局场景（cap≤10），对照 ≤80ms 硬门；
3. **论文 U 定义对拍**：148 点已知真值集上，D3 的 Filter-U 实现
   与论文 U 策略（u(b)=Σp(b,n)·p'(b,n)，两步存活最大化）的
   **选格重合率**——重合率不足 100% 时，论文 39.14% 的外推不适用，
   须以本仓实装定义的实测为准。

### E1 C++ 改动点预标注（供 code review）

内核层**零新增**——两步存活内核已存在：`ExactEngine::after_click`
（cpp/mscore.cpp:2024，`p_safe × Σ weight × V_exact`，即论文 u(b)
定义）；世界集构建范式已有（py_exact_eval，cpp/mscore.cpp:2109-2207：
hex 世界 → world_bits → all_wkey）。实装改动集中在
`decision_layer::decide_impl` 编排层的 **pbs 三分支**：

| 分支 | 现状位置（mscore.cpp） | 改动 |
|---|---|---|
| certain_safe 批量 | ~2521-2530 | **不动**（批量语义，非破平局） |
| clicks9_fallback | ~2558-2604 | interior_tied 选格 → filter-U argmax（候选 ≤ cap） |
| limitation/memory_gate 前沿 tied | ~2606-2650 | weighted_tied 选格 → filter-U argmax（候选 ≤ cap） |
| node_cap_fallback | ~2667-2690 | 同前沿 tied 处理 |

实现要点：① 在 decide_impl 内将 py_part_solve 的 res_list/ck_list
转为 ExactEngine 世界集（一次构建，逐候选复用，不逐候选全量
decide）；② tied 候选 > cap=16 时退回现状确定性 (x,y)-min（实测
n=1200 中 max=10，预期零触发，退路保底）；③ 不调用 ply_score
（after_click 已覆盖两步存活语义，避免额外内核）。

### cap=16 参数选择依据（实测留痕）

- 平局集大小分布（n=1200 破平局决策，det 臂 150 局回放采集）：
  [0,8) 89.7% / [8,16) 10.3% / **>16 占 0%（max=10）**；
- 深局面内部格大平局集（中位 463）走 clicks9_fallback 的
  open_num5x5 分支，不进前沿 tied 评估范围；
- 结论：cap=16 在当前分布下零触发退路；若未来盘面配置变化
  （尺寸/雷密度），须重测分布后再定 cap。

### E4 前置约束（防 D4 场景错配重演）

PSEQ 离线对拍**限定在约束前沿格破平局场景**（pbs 三分支 tied 集
内），不得在论文全流程决策场景（含首点、全盘乱猜区）对拍。
D4 教训：全盘乱猜区的角落偏好用于约束前沿平局集时与 det 完全
同分布（p=0.75）——外部策略必须先核对适用场景。

---

## 四之十二、三层架构实装与验收（2026-11-05，用户指令驱动）

### 架构（用户指令映射）

用户指令：「实现论文里的最优策略，当 win rate 穷举时间小于一秒时，
进行残局的穷举，精确找到最优解」+「主参数是前瞻若干步，步数取决于
复杂度」。映射为 decide_impl 三层（优先级 OPTIMAL > win_rate > pbs）：

1. **OPTIMAL 层**（论文 §6 popt 精确穷举）：`world_upper ≤ DSTAR=400`
   （用户拍板「按 400 开工、验收时看触发率再调」）时物化完整世界集，
   ExactEngine::after_click 逐候选精确评估取 argmax，reason=`optimal`。
2. **前瞻层**：win_rate 照常（limitation ≤ 8），**深度自适应接线**——
   adaptive_depth 按 limitation 分档（≤10 → 0 不收缩；10-14 → 2；
   >14 → 3，复用既有成本分位表）。
3. **复杂层**：pbs + 破平局（前沿 tied 用 open_num5x5 代理；
   win_rate 层并列时用 ExactEngine 两步存活 u(b) argmax，cap 2e6）。

### 两次内存爆炸事故与双守卫（重要教训）

**根因**：`world_upper` 只计前沿组解数乘积，**不含 clicks9 的
C(n,rem) 组合展开**——`world_upper≤400` 的门放过了内部格爆炸
（clicks9=40、rem=20 时单层枚举 C(40,20)≈1.4e11）。用户两次手动
叫停（python 冲到 2.8GB）。**双守卫修复**：
- 预守卫：进层前估算完整世界数 `world_upper × C(n9, rem_max)`
  （Odometer 采样限 4096 行），超 400 跳过该层；
- 中途守卫：物化循环逐 world 检查 `size > 400` 立即断路（fail-closed）。
- 守卫后 OPTIMAL 层内存上界 ≈ 400 × 72B ≈ 29KB，结构性安全。

### 列映射 bug（崩塌 34pp 的根因，二分定位）

首版 150 局验收 1/150 = 0.7%（McNemar p<0.0001 vs det）——崩塌。
二分（20 局/轮 × 3 轮）：禁 win_rate Filter-U 无效 → 禁 OPTIMAL
恢复 3/20 → **定位 OPTIMAL 层**。真值对拍实锤：**res_list[i] 的列
属于 groups[i] 的格子，不是全局 ordered_clicks 的前缀**——原代码把
每个组的行都映射到 ordered_clicks[0..n]，产生垃圾世界 → after_click
值全错 → 灾难性选格。两处（OPTIMAL 层 + win_rate 层 Filter-U）同修。
修复后同 150 局：45/150 = **30.0%**（p=0.0039 严格优于 det 36/150，
only_new=9/only_det=0）。

### 真值核对（checklist 第 3 项，含伪差异甄别）

- mm_full_v2 已知真值集（283 点，worlds 2~6480，q 为精确 V*）：
  40 个 worlds≤400 点中对拍，34 个 DIFF 中 4 个 OPTIMAL DIFF 全部
  为**伪差异**——mm 真值只覆盖前沿格（candidates() 排除确定安全格），
  而 OPTIMAL 层正确地选中了 **P(mine)=0 的确定安全内部格**
  （6/6 验证 PROVEN-SAFE）——OPTIMAL 层比 mm 真值集更优；
- 手工小盘验证：3×3 单雷中心'1'盘，8 邻格各 1/8 雷率，u=0.875
  全并列——引擎语义正确（我最初"应返回 1.0"的预期是错的：
  无推理可区分时 1/8 是真实概率）。

### 150 局验收（主雷位集，seed 20274000+）

| 臂 | 胜率 | vs det |
|---|---:|---|
| det（旧构建） | 36/150 = 24.0% | — |
| **三层架构** | **45/150 = 30.0%** | **only_new=9/only_det=0，p=0.0039** |

分支：certain_safe 55.7% / optimal 25.7% / limitation 10.3% /
clicks9_fallback 5.4% / memory_gate 3.0%。OPTIMAL 触发率 25.7%
（≥10% 硬门 ✅）。总耗时 38s/150 局（快于 det 臂同批 140s）。
慢调用 6/18932=0.03%（最坏 13.3s）——**存量行为**（pbs 大世界 DP，
det 臂同在且从未计时），非回归；预守卫已加 4096 行采样上限。

### 独立雷位集复现（checklist 第 1 项，seed 20285000+ 段）

| 臂 | 胜率 | vs det p | vs py p |
|---|---:|---:|---:|
| py 生产 | 54/150 = 36.0% | — | — |
| det（旧构建） | 55/150 = 36.7% | — | — |
| **三层架构** | **60/150 = 40.0%** | 0.161 | 0.162 |
| pureR | 61/150 = 40.7% | 0.156 | 0.135 |
| prodR seed1 | 59/150 = 39.3% | 0.237 | 0.189 |
| prodR seed2 | 59/150 = 39.3% | 0.234 | 0.191 |

**方向确认**：三层/随机臂一致高于 det 与 py 约 3-4pp，prodR 双种子
仅差 1 胜（随机臂种子方差稳定，checklist prodR 方差项 ✅）。
**但本样本全部不显著**（p=0.14~0.24）。

### 40% 目标判定（严格口径）

- 主雷位集：30.0%（该批雷位偏难）；
- **独立雷位集：40.0% 点估计恰好触线**（CI [32.5, 48.0] 含 40%），
  pureR 40.7%；
- **不能宣称「达到 40% 以上」**：单侧 150 局、点估计恰在线上、
  CI 覆盖 40%、且未对 det/py 显著化。按预注册纪律需：独立第三段
  雷位集复现 + 预注册判据（如「新雷位集上 3 臂均值 ≥40% 且 vs py
  McNemar p<0.05」）后才可主张。
- 诚实的当前表述：**三层架构 + 破平局修复把 C++ 臂从 24% 拉到
  与 py 同档（36-40%），方向与幅度一致，显著化需扩样**。

---

## 五、下一步前置条件（按顺序，不得跳）

1. ~~取得生产真实 win_rate 决策点~~ ✅ 已完成（`wr_cases_big.json`，hook 采集）。
2. 用生产固定值跑对拍覆盖 gate + win_rate 成功 ✅ 已完成；
   **node-cap 降级仍缺真实样本**（不得人为压 cap 制造）。
3. 逐项比对第一节 15 条规格，尤其「两套约束范围」「随机破平局」「pbs 加权」——
   当前只观测到路径，尚未逐位比对返回值/选格/降级原因。
4. 全绿后再导出 C++ `decide()`，并做同 seed 逐步对齐（HANDOFF 第 1d 步）。
5. 之后才进入整局配对 A/B 验证 40% 胜率。

---

## 六、环境（口径更正）

- 当前生产 `utils/mscore.cp310-win_amd64.pyd` = **基线** sha256 `5B22EC2B...19CD`
  （已核对）。基线二进制是上一会话冻结的原始产物，运行时可见仅 7 个函数
  （`exact_eval / last_phase / md5 / part_solve / part_solve_single /
  pbs_compute / win_rate`；导出符号检查器计 8 个，含 `PyInit_mscore` 模块入口）。
  实测 `hasattr(mscore,'decide')=False`、`hasattr(mscore,'split_frontier')=False`。
  详见「四之三」节的临时构建/生产基线硬边界。
- `cpp/mscore.cpp` **源码**里含新增原语 `estimate_mem / split_frontier /
  decision_preflight / packed_board_storage`，但那是**临时编译时**才进入产物；
  每次编译验证后都把 pyd 恢复基线。此前汇报把它们说成「已导出」是口径错误：
  应表述为「源码已实现、临时构建已对拍通过、当前生产运行时未启用」。
- `decide()` 在 `cpp/mscore.cpp` 用 `#if 0` 隔离、**源码层也未导出**。
- 要真正让 Python 薄调用层用上这些能力，需在语义对齐完成后重新构建生产 pyd
  并做导出符号/数值对拍，届时基线 hash 会变（MSVC 非确定性，不能用 hash 论证逻辑）。
