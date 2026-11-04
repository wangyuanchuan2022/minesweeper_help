# 会话交接文档（Session Handoff）

日期：2026-11-04
上一会话：`session-d88a2d3b-bc8b-4512-b930-6cba9a920020`（上下文 99%，按用户生命周期规则交接）
git HEAD：`87976d3`（分支 main）

---

## 一、What We Are Building

**目标（goal 仍 active，未达成）**：
> 通过优化算法，使胜率达到 **40% 以上**

- Goal id：`goal-93401fa9-4dc0-42b2-af17-a0650f5bfd05`，revision 4，round 7/256
- 项目：`D:\python\projects\minesweeper_help`（扫雷助手，PyQt5 + C++ pybind11 加速）
- 环境：Python `D:\anaconda3\envs\py310\python.exe`（3.12.7 不行，DLL 报错）

### 关键背景：规则决定了难度
- **Arbiter = 延迟布雷**：首点只保证**本格**安全，不保证 8 邻域（已逆向 `ms_arbiter.exe @0x4d9228`）
- 现代规则（首点+8 邻域安全）下同算法可达 **49.50%**（600 局实测，CI [45.52, 53.49]）
- **规则效应 = +16.00pp**（McNemar 161 vs 65，p=1.4e-10）
- 所以 40% 目标必须在 **Arbiter 规则下**靠算法拿到 **+6.5pp**（33.5% → 40%）

---

## 二、What WORKED（带证据）

### 2.1 P2 角落开局：正式实验**统计显著**
- 文件：`state/ab/firstclick_p2_formal.json`，日志 `bench/data/p2_v2.log`
- 配置：N=2000/臂，seed **20272000+**，两臂失败 0，完整性门禁通过

| 臂 | 胜率 | 95%CI | 首格为0率 |
|---|---:|---|---:|
| center（生产现状） | 727/2000 = **36.35%** | [34.27, 38.48] | — |
| **corner** | 792/2000 = **39.60%** | [37.48, 41.76] | **49.20%** |

- **Δ = +3.25pp**，配对 corner 独赢 382 / center 独赢 317，**McNemar p = 0.0154**
- 中间指标：corner 首格为 0 率 **49.20%** vs 理论 **49.85%**（组合学吻合）
- **⚠️ 状态：未满足原预注册采纳条件**（要求效应量 ≥ MDE=4.26pp）
  - 准确表述：「统计显著但未满足原采纳条件」
  - **不能**事后改口径宣称采纳；也**不能**说结果无效（MDE 是设计灵敏度，不证明效应为假）

### 2.2 C++ 精确 V* 引擎：逐位对拍通过
- `cpp/mscore.cpp` 新增 `ExactEngine` + `exact_eval` 绑定
- `bench/verify_exact_cpp.py`：**140/140 候选格 maxΔ = 0.000e+00**，加速 9.2~15.0x
- 阴性对照：丢 1 个世界后 18/18 格变化 ⇒ 对拍器有区分力

### 2.3 P1-b 精度验证（**仅 25 点，不可外推**）
- `bench/verify_vstar_precision.py`：25 点，cap×4 + time_cap 30→120s，**V* 逐点不变 25/25**
- **不能**说"全部 300 点精度已验证"（只验 25 点 = 8.3%，且是便利样本）
- **不能**把"提高 cap 不变"等同于"已知答案正确性验证"

### 2.4 仪器缺陷：同进程耗时漂移（已量化并加协议）
- `bench/report_repeat_slowdown.py`：同进程连续调 win_rate，节点数恒定但耗时漂移
  - G6 4,626 节点稳定；G8 78,282 稳定；G9 313,940 稳定
  - **G10 1,241,184 → 5.44x**；**G11 4,081,650 → 2.36x**
  - 新进程测量稳定：4 进程 876ms，极差 **2.1%**
- **根因未明**（已排除 GC/算法路径/无差别堆碎片/后台负载/CPU 频率；C++ 分配机制是**未验证推测**）
- ⇒ **任何同进程内耗时对比不得用于策略排序**
- 强制协议：`bench/timing_protocol.py`（新进程 / 随机交替，极差>20% 自动标 unreliable）

### 2.5 P1 样本扩样：41 → 300 点
- `state/ab/merged_regret.json`：**300 个可评估决策点**
- 生产 regret：中位 **0.006118**，均值 0.018199，最大 0.175926，**完全选对 98/222 = 44.1%**
- limitation 范围 8.01~26.33（中位 10.79），世界数中位 183

### 2.6 P2 接线阳性对照通过
- `bench/verify_p2_wiring.py`：首点 center `[15,8]` vs corner `[0,0]`；
  两臂 `first_num` 各 **8/8** 与首点一致；同 seed 下两臂棋盘 **8/8 不同**（证明首点真影响布雷池）

---

## 三、What Did NOT Work（+ 确切原因）

| 尝试 | 结果 | 原因 |
|---|---|---|
| **adaptive_depth 避免降级** | 规则**实际触发 0 次**（400 局，248 次 win_rate 调用，`shrunk=0`） | 预算 1e4 对真实 `n_cand×total` 分布（中位 4032）太高；**且高复杂度局面根本不进 win_rate**（被 `limitation<=wr_threshold` 与 `_wr_memory_ok` 拦）。⚠️ 不可推出"应降阈值" |
| **连续权重优化（77 点）** | LOO 全部劣于 S0 | 随机搜索 in-sample 0.0140→**LOO 0.0271**；坐标下降 0.0113→**LOO 0.0228**；S0=0.0161。in-sample 变好而 LOO 变差 = 过拟合直接证据 |
| **128 子集穷举（41 点）** | 7/127 等权优于 S0、5/127 LOO 优于 S0，但**最佳 LOO 与 S0 配对 6好/6差 p=1.000** | Δ−0.0025 仅为 MDE(0.0090) 的 28% |
| **破平局准则整局 A/B（640 局）** | `open_plus_frontier` +0.16pp，CI [−3.53,+3.84]，p=1.000 | 两轮换 seed 后**符号翻转** |
| **位置先验 L 单独使用** | regret 0.0968，显著**更差**（p=0.001） | 但**消融显示它是最大边际贡献项**（+0.0224，第二名 4.6 倍）⇒ 只在破平局时有用 |
| **G（全局雷数一致性）** | **机检恒等于 S0**（330/330 逐点相等，阴性对照 19 格） | 结构性冗余，设计矩阵秩亏 1 |

---

## 四、What Has NOT Been Tried Yet

1. **分层权重**（Bergstra 推论）：按 limitation 分桶、每桶独立选准则。
   论文依据：Ψ 有效维度 7 维实测只有 1~4，且"不同数据集上重要参数不同"。
   **这是解释"全局单一权重永远赢不了 S0"的关键假设，未验证。**
2. **GA / 更复杂搜索**：连续权重不可穷举 ⇒ 需启发式搜索。
   但按 Bergstra，随机搜索是基线；**GA 不是唯一路径**（坐标下降/局部搜索都是候选）。
3. **3-ply 整局 A/B**：regret 最优（0.0048，回收 87.6%）但覆盖率仅 **53.1%**，整局未测。
4. **降级的真实质量损失**：只有间接证据（22 点 3 次 cap、p90 18.2s），**未量化**。
5. **完整 V* 的已知答案对照**：5×5 探针在 4 个 cap 配置下**都未求出值**，仍是缺口。

---

## 五、Current State of Files（关键）

### 数据
| 文件 | 内容 |
|---|---|
| `bench/data/midgame_cases.json` | 400 点（发现集一部分） |
| `bench/data/midgame_cases_ext.json` | 1430 点（发现集另一部分） |
| `bench/data/midgame_cases_ext2.json` | 456 点 = **验证集，未评估，禁止提前查看** |
| `state/ab/merged_regret.json` | 300 点 q（无 F） |
| `state/ab/mm_discovery.json` | **77 点**（有 F） |
| `state/ab/firstclick_p2_formal.json` | P2 正式结果 |

### 脚本
| 脚本 | 用途 |
|---|---|
| `bench/ab_first_click_corner.py` | P2 首点 A/B（含完整性硬门禁） |
| `bench/dump_metric_matrix_ext.py` | F 矩阵导出（多输入 + C++ 真值 + journal） |
| `bench/optimize_weights.py` | 随机搜索 + 坐标下降（固定预算对比） |
| `bench/layered_weights.py` | 分层权重（支持 discovery/validation 模式） |
| `bench/classify_p1_coverage.py` | 全样本失败分类 + 分层成功率 |
| `bench/verify_*` | 各类仪器验证（exact_cpp / vstar_precision / p2_wiring / journal_resume / after_click_semantics） |

### 报告（review/）
- `P2预注册_角落开局.md`、`P2复现预注册.md`
- `P1预注册_发现验证集冻结.md`
- `F矩阵说明与口径边界.md`、`regret评估原理与口径边界.md`
- `B线_自适应深度未生效.md`、`论文研读_随机搜索与MCTS扫雷.md`

---

## 六、三个后台任务（**新会话必须先核验再续跑**）

| 任务 | 进程 PID | 日志 | journal | 进度（交接时） |
|---|---:|---|---|---|
| **P1-c 全样本覆盖分类** | 35916 | `bench/data/p1_coverage.log` | `state/ab/p1_coverage.json.journal.jsonl` | **250/462（ok=181）** |
| **P2 独立复现** | 43744 | `bench/data/p2_repl.log` | 无（结果在 `state/ab/firstclick_p2_replication.json`） | **center 600/2000** |
| **F 矩阵扩样** | 47664 | `bench/data/mm_full.log` | `state/ab/mm_full.jsonl` | **130/462** |

### ⚠️ 新会话第一步必须做的核验
```powershell
# 1. 进程是否还活着
Get-Process -Id 35916,43744,47664 -ErrorAction SilentlyContinue | Select Id,CPU

# 2. 日志是否还在推进（间隔 60s 对比 mtime 与尾行）
Get-Item bench\data\p1_coverage.log,bench\data\p2_repl.log,bench\data\mm_full.log | Select Name,LastWriteTime,Length
Get-Content bench\data\p1_coverage.log -Tail 3
Get-Content bench\data\p2_repl.log -Tail 3
Get-Content bench\data\mm_full.log -Tail 3
```
- **进程已死但 journal 存在** ⇒ 用 `--no-resume` 之外的默认参数**续跑**（journal 会自动跳过已完成点）
- **不得重跑已完成样本**
- **P1-c 进度不能当最终结果**：样本按 worlds 升序排列，先跑简单的 ⇒ 当前 72.4% 不代表全样本

---

## 七、Decisions Made（不要推翻）

1. **B 线例外授权**：整局引擎仍用 Python（用户明确批准），**耗时读数一律不可用于排序**
2. **不缩 world cap**：保持 20000 冻结口径，缺口用追加独立采集补（已补 ext2 456 点）
3. **发现/验证集按数据来源划分**（非随机切分）：发现 = cases+ext，验证 = ext2
4. **验证集只评估一次**，看到结果后不得改配置重试
5. **每个配置独立新进程**测耗时，或随机交替取中位/p99 并报极差
6. **预注册判据不得事后修改**

---

## 八、Blockers & Open Questions

1. **40% 目标未达成**：corner 39.60% < 40%，CI 跨 40% ⇒ 需独立复现确认
2. **P2 复现未完成**（600/2000），方向是否一致未知
3. **全样本未覆盖率未知**（P1-c 250/462）——此前"47% 点被丢弃"是**错误外推**，已删
4. **F 矩阵只有 77 点**（扩样中 130/462），不足以支撑 7 维权重优化
5. **完整 V* 的已知答案对照缺失**（5×5 探针跑不完）
6. **regret → 整局胜率的转换关系未量化**

---

## 九、Exact Next Step（固定顺序，不要跳步）

### 第 0 步：核验（见第六节命令）
确认三个任务状态，决定是等还是续跑。

### 第 1 步：等 P1-c 完成 → 得到真实全样本未覆盖率
- 输出：`state/ab/p1_coverage.json`
- 必须报告：ok/node_cap/time_cap/err 各多少，按 limitation/worlds 分层成功率
- **用这个替换掉所有"47%"表述**

### 第 2 步：等 P2 复现完成 → 判定角落开局
- 读 `bench/data/p2_repl.log` 与 `state/ab/firstclick_p2_replication.json`
- 判据（已冻结在 `review/P2复现预注册.md`）：
  - **主判据：Δ 方向与正式实验一致（corner > center）**
  - 不因结果修改判据
  - 不把两轮合并成"官方"数字宣称达标
- 若方向相反 ⇒ 判定**未复现**，不得采纳

### 第 3 步：等 F 矩阵扩样完成（目标 300+ 点）
- 输出 `state/ab/mm_full.json`
- 检查 `skipped` 分布，报告真实成功率

### 第 4 步：随机搜索基线（固定预算）
```bash
python bench/optimize_weights.py --matrix state/ab/mm_full.json \
       --random-trials 800 --cd-rounds 20
```
- 必须报 LOO，不报 in-sample 作结论

### 第 5 步：坐标下降精修
- 同一脚本内已实现，记录**实际评估次数**保证公平比较

### 第 6 步：冻结权重 → 验证集**只评估一次**
```bash
python bench/dump_metric_matrix_ext.py \
       --inputs bench/data/midgame_cases_ext2.json \
       --max-worlds 20000 --out state/ab/mm_validation.json
```
- ⚠️ 这一步**必须等权重完全冻结后**才做

### 第 7 步：整局胜率 A/B 验证 40% 目标
- 把优化出的权重接入生产选格逻辑，跑 2000 局/臂配对 A/B
- 预注册判据后再跑，不得事后改

---

## 十、复现全部关键读数

```bash
# 二进制版本（必须一致）
sha256sum utils/mscore.cp310-win_amd64.pyd
# 5B22EC2BDE70ACC8E64DAD8A8CBF9305970DF8EB228A744F06ED22AAC50219CD

# P2 正式（已完成，勿重跑）
python bench/ab_first_click_corner.py --compare center corner --games 2000 --seed 20272000

# P2 复现（进行中）
python bench/ab_first_click_corner.py --compare center corner --games 2000 --seed 20273000

# 仪器验证全套
python bench/verify_exact_cpp.py
python bench/verify_after_click_semantics.py
python bench/verify_vstar_precision.py --limit 25
python bench/verify_p2_wiring.py
python bench/verify_journal_resume.py
```
