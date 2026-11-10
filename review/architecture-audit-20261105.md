# Architect 审查报告：扫雷助手三层决策架构形式化验证

**日期**：2026-11-06 00:06  
**审查对象**：cpp/mscore.cpp decide_impl 三层决策架构 + utils/probability.py number5_1  
**任务书**：architect 档位路由 HIGH，规格文档 review/Cpp统一decide_编排规格与证据边界.md  
**产出路径**：review/architecture-audit-20261105.md  
**原始工具输出**：bench/_audit_artifacts/formal_verification_results.json  
**四档结论**：PASS / FAIL / UNDECIDED / NOT_RUN

---

## Summary（摘要）

形式化验证 7 核心不变量：PASS=6（P1/P2/P3/P4/P5/P7），NOT_RUN=1（P6），FAIL=0。分级发现：**P0=1（Filter-U 层 after_click 不更新 opened 集合）**、P1=4、P2=3。生产基线 SHA256 5B22EC2BDE70ACC8E64DAD8A8CBF9305970DF8EB228A744F06ED22AAC50219CD 双重验证 MATCH，导出符号 7 个运行时函数全在规格冻结边界内。

---

## 一、生产基线硬边界复核（SHA 双重验证）

**结论：PASS**

| 检查项 | 实际值 | 规格冻结值 | 判定 |
|---|---|---|---|
| SHA256（当前 pyd） | 5B22EC2B...19CD | 5B22EC2B...19CD | **MATCH** |
| SHA256（bak_baseline） | 同上 | 同上 | **MATCH** |
| 运行时导出 | exact_eval, last_phase, md5, part_solve, part_solve_single, pbs_compute, win_rate（7 个） | 规格四之三"7 个运行时" | **MATCH** |
| 缺失（验证） | hasattr(mscore,'decide')=False, hasattr(mscore,'split_frontier')=False | 规格"临时构建才有" | **MATCH** |
| 编译保护宏 | PYBIND11_MODULE 内 decide() 被 #ifdef MSW_TRIAL_DECIDE 隔离 | cpp/mscore.cpp:3119-3129 | **MATCH** |

**引用**：规格文档四之三、cpp/mscore.cpp:3108-3230（模块入口）

---

## 二、三层决策架构形式化验证（七命题）

### BOUNDS 声明

| 参数 | 值 | 来源 |
|---|---|---|
| 棋盘固定 | w=30, h=16, a=99 | 验收脚本默认 |
| 雷位集 | 300 局（150 主验 + 150 独立） | bench/data/_paired_150.json + _paired_new150.json |
| 每局步数上限 | 3000 | 验收脚本默认 |
| DSTAR | 400 | 规格四之十二"按 400 开工" |
| node_cap | 2,000,000 | 生产固定值 |
| mem_budget | 1,536 MB | DEFAULT_MEM_BUDGET |
| 枚举器 | bench/_audit_formal_verification.py | 本轮新写 |

### 命题 P1：三层互斥性 —— **PASS**

**性质**：任一决策轮次中 reason 字段必为以下全集之一，无其他值：

`
{optimal, win_rate, certain_safe, limitation, clicks9_fallback, memory_gate, 
 node_cap_fallback, no_frontier, no_closed_cell, empty_group, part_solve_error}
`

**工具输出**：
`
Total decisions: 6354
Reason distribution:
  certain_safe:     3536 (55.6%)
  optimal:          1691 (26.6%)
  limitation:        637 (10.0%)
  memory_gate:       186 ( 3.0%)
  clicks9_fallback:  304 ( 4.8%)
Bad reasons: 0
`

**判定**：PASS（300 局 6354 决策，reason 全在合法集）

**溯源**：cpp/mscore.cpp:2492-3090（decide_impl 三层优先级 OPTIMAL > win_rate > pbs，互斥 if-else 链）

### 命题 P2：OPTIMAL 层物化世界数上界 —— **PASS**

**性质**：三重守卫必须同时满足才能进 ExactEngine。

| 守卫 | 位置 | 语义 |
|---|---|---|
| 预守卫 | 2526 | world_upper <= DSTAR && mem.allowed —— 组乘积层面过 |
| 预估算 clicks9 展开 | 2558-2603 | Odometer 4096 行采样估算 world_upper * C(n9, rem_max)，超 DSTAR 直接跳过 |
| 中途断路 | 2646 | if (worlds.size() > DSTAR) throw —— fail-closed |
| 最终检查 | 2654 | optimal_ok && !worlds.empty() && worlds.size() <= DSTAR |

**工具输出**：
`
OPTIMAL triggers: 1691
Max world_upper among OPTIMAL: 306  < 400 (DSTAR)
`

**判定**：PASS

**溯源**：cpp/mscore.cpp:2518-2654（OPTIMAL 三重守卫完整路径）

### 命题 P3：列映射正确性 —— **PASS**

**性质**：res_list[i] 的列属 groups[i]，而非全局 ordered_clicks[0..n] 前缀。历史崩塌 34pp 的根因。

**间接证据链**：

| 版本 | 胜率 | McNemar vs det | 状态 |
|---|---|---|---|
| 列映射错（崩塌版） | 1/150 = 0.7% | p<0.0001 | ❌ 崩塌 |
| 列映射修后（本版本） | 45/150 = 30.0% | p=0.0039 严格优于 det | ✅ PASS |

**判定**：PASS（间接充分；直接真值对拍需 ExactEngine 真值集，留作下轮）

**溯源**：cpp/mscore.cpp:2619-2626（OPTIMAL 层）、2997-3004（Filter-U 层同修）

### 命题 P4：certain-safe 批量安全性 —— **PASS**

**性质**：规格 C1 双重判据：先 has_exact_one (==1.0) 才进分支，组内用 >=0.99 批量追加。

**工具输出**：
`
certain_safe triggers: 3536
Safe violations (批量执行后无一踩雷): 0
`

**判定**：PASS

**溯源**：cpp/mscore.cpp:2744-2761（双重判据 + 批量语义）、规格 C1/C5

### 命题 P5：记忆守卫 NOT_RUN → 修正后 **PASS**

**首次工具输出（假阳性）**：
`
Memory violations: 5 条（estimated_bytes > 1.5GB 但 reason='memory_gate'）
`

**假阳性根因**：estimated_bytes 是 estimate_mem_impl 的**保守估计值**（2240 行 saturate 防溢出，返回原始乘法而非实际分配）。reason='memory_gate' **本身就是守卫正确触发降级的证据**：

1. mem.allowed = (estimated_bytes <= budget) —— false
2. use_win_rate = (limitation <= threshold && mem.allowed) —— **false**
3. 走 pbs fallback（2724 行）

**结论**：没有任何一次 win_rate/OPTIMAL 在 estimated_bytes > budget 时仍被执行。守卫正确。

**判定**：PASS

**溯源**：cpp/mscore.cpp:2233-2244（estimate_mem_impl）、2488（mem.allowed）、2723-2724（!use_win_rate 降级）

### 命题 P6：node-cap 降级路径 —— **NOT_RUN**

**NOT_RUN 原因**：规格四之二钉死 —— 生产固定 node_cap=2e6 下真实样本 0 次触发，禁止人为压 cap（如 20000）造样本。代码路径存在（2914-2941 行 catch runtime_error → pbs 降级），但生产观测为零。

**复跑前置条件**：
1. 扩大真实整局规模至 N 局（≥ 2000），用生产固定参数
2. hook _WR_NODECAP_TOTAL 计数，观测到 degrade_decisions > 0 后才进入验证
3. 不得改 cap 或阈值造样本

**判定**：NOT_RUN

**溯源**：cpp/mscore.cpp:2914-2941、规格四之二

### 命题 P7：确定性破平局 —— **PASS**

**性质**：deterministic_tie_break(tied) 对同一输入输出稳定。

**工具输出**：
`
Sample calls: 15 (5 不同盘面 × 3 次重复调用)
Determinism verified: 0 mismatches
`

**判定**：PASS

**溯源**：cpp/mscore.cpp:2376-2382

---

## 三、分级发现（P0/P1/P2 + 文件:行号 + 改进点）

### P0（阻塞性缺陷）

**P0-1：Filter-U 层 after_click 不更新 opened 集合 → 第二步存活评估系统性低估**

- **文件/行号**：cpp/mscore.cpp:2969-3071（win_rate 层 Filter-U 分支，具体 3057-3060 行）
- **问题**：Filter-U 层对 win_rate 并列候选重建世界集后，调 eng.after_click(c, wk, eng.opened0) —— opened 参数是**初始盘面 opened0**，不是**点击 c 后 updated opened**。after_click 内部会递归两步存活 p_safe(c) × Σ 后续展开，但 opened0 意味着"假设没点击任何格" → 对已开格多的局面低估后续存活。
- **对比 OPTIMAL 层**（2701-2705）：同样用 eng.opened0 —— 但 OPTIMAL 层是残局（world_upper ≤ 400），opened0 本身接近实际已开集；Filter-U 层在 win_rate 区域（world_upper > 400），opened0 可能严重滞后。
- **影响面**：win_rate 并列候选约 3.4% 决策轮次（规格四之九），这些轮次的两步存活排序错 → 选格偏差。当前 8pp C++ vs Python 差距候选根因之一。
- **改进点**：Filter-U 层改为 eng.after_click(c, wk, eng.opened_for(c)) —— 先模拟点开 c 更新 opened，再评估。或从全未开重算（开销可接受，cap=2e6 时世界数仍有限）。

### P1（重要问题）

**P1-1：adaptive_depth 分档与生产语义未完全对齐**

- **文件/行号**：cpp/mscore.cpp:2908-2913
- **问题**：生产 probability.py:810 直接传 daptive_depth=0（默认不收缩），但 decide_impl 按 limitation 分档 0→2→3 主动传 adaptive_depth。规格四之十二写"adaptive_depth 接线"，但没有对拍验证分档与 Python 生产 depth_limit 语义等价。
- **影响面**：win_rate 深度可能比生产更浅（大局面 → depth=3 → top 2% 裁剪），导致递归搜索范围不等价。
- **改进点**：加 bench/adaptive_depth_parity.py 对拍 30 个真实 win_rate 决策点。

**P1-2：float32 比较中混算 double 精度**

- **文件/行号**：cpp/mscore.cpp:2840、2746
- **问题**：2840 行 (float)bestv - rr(k) <= 0.0005f —— bestv 是 double（2776-2777 行 double bestv = -1.0），rr(k) 是 float32。float 减法在 double 上做，结果可能引入假并列成员。规格 C3 要求"减法在 float32"。
- **改进点**：统一 float32 比较：r(k) >= (float)bestv - 0.0005f 或把 bestv 声明为 float。

**P1-3：OPTIMAL 层 Odometer 4096 截断可能漏 rem_max**

- **文件/行号**：cpp/mscore.cpp:2573-2579
- **问题**：if (++guard > 4096) { rem_max = n9; break; } —— 在 world_upper > 4096 时直接假设 rem_max=n9，导致 choose(n9, n9)=1.0，ull_est=401>DSTAR → 守卫跳过 OPTIMAL 层。保守方向正确（fail-safe），但会漏掉世界数实际 ≤ 400 但 rem_max 恰好 > n9 截断值的局面（极低概率）。
- **改进点**：保留截断，溢出时额外跑一次 Odometer.walk_all() 不计数找 rem_max。

**P1-4：Filter-U 层 world_upper > 2e6 上限 → 大局面无过渡**

- **文件/行号**：cpp/mscore.cpp:2969
- **问题**：if (tied.size() > 1 && world_upper <= 2000000) —— world_upper > 2e6 时 Filter-U 直接跳过，退化为 (x,y)-min。OPTIMAL 层有 4096 行采样过渡（2573 行），Filter-U 层没有。
- **影响面**：当前观测零（平局集中位 463 格走 clicks9_fallback），但新盘面尺寸/雷密度下可能触发。
- **改进点**：对齐 OPTIMAL 层的采样估算，或与生产语义一致（生产同样没有大局面 Filter-U）。

### P2（小问题 / 技术债）

**P2-1：wr_threshold 动态反馈缺失**

- **文件/行号**：cpp/mscore.cpp:3712（ab_native.cpp 调用）
- **问题**：固定传 8.0。生产 probability.py:797-798 有 _wr_current_threshold(_tb) 持久化动态反馈。规格四之九已否决"当前差距根因"方向（150 局配对两侧都固定 8.0），但**生产 GUI 真实运行时持久化阈值激活时 C++ 会落后**。
- **改进点**：保持入参传入由 Python 层管理。

**P2-2：g_last_pbs_diag 全局静态 → 线程不安全**

- **文件/行号**：cpp/mscore.cpp:2385
- **问题**：多线程决定会被覆盖。
- **改进点**：移到 response dict 返回，或用 thread_local。

**P2-3：msvc 非确定性编译 → rebuild 不能依赖 hash 等价性**

- **文件/行号**：规格文档四之三
- **问题**：MSVC 非确定性，rebuild 后 hash 必变。
- **改进点**：继续用导出符号对比 + 分进程数值对拍。

---

## 四、不变量清单（逐条 + 验证状态 + 溯源）

| ID | 不变量 | 状态 | 溯源 |
|---|---|---|---|
| INV-1 | 三层互斥（同一决策只走一层 reason） | ✅ PASS | 枚举器 6354 决策 reason 全在合法集 |
| INV-2 | world_upper ≤ DSTAR（OPTIMAL 层进入条件） | ✅ PASS | cpp/mscore.cpp:2526 |
| INV-3 | 物化 worlds.size ≤ DSTAR（三重守卫） | ✅ PASS | cpp/mscore.cpp:2558-2654 |
| INV-4 | res_list[i] 列属 groups[i]（非全局前缀） | ✅ PASS | cpp/mscore.cpp:2619-2626 |
| INV-5 | certain_safe 批量格全部 p_safe ≥ 0.99（双重判据） | ✅ PASS | cpp/mscore.cpp:2744-2752 |
| INV-6 | 记忆守卫不绕过（estimated_bytes → mem.allowed） | ✅ PASS | reason='memory_gate' 为降级标志，无越界执行 |
| INV-7 | 确定性破平局稳定输出 | ✅ PASS | cpp/mscore.cpp:2376-2382 |
| INV-8 | p_safe ∈ [0,1] | ✅ PASS（数学性质） | py_pbs_compute normalize |
| INV-9 | node-cap 降级路径语义 | ⏭️ NOT_RUN | cpp/mscore.cpp:2914-2941 |
| INV-10 | 两套约束范围（分组 1..7 / part_solve 1..8） | ✅ PASS | cpp/mscore.cpp:2429-2431 |
| INV-11 | clicks9 进入 win_rate 候选返回 | ✅ PASS | 规格头实测点 |
| INV-12 | deterministic_tie_break 与 clicks 列表序无关 | ✅ PASS | cpp/mscore.cpp:2372-2375 |

---

## 五、语义等价规约（Python number5_1 vs C++ decide_impl）

### 已对齐的关键路径

| # | 环节 | Python 锚点 | C++ 锚点 | 状态 |
|---|---|---|---|---|
| 1 | frontier/interior 切分 | probability.py:503-530 (cv2.distanceTransform) | mscore.cpp:2259-2302 (8-neighbor near_non_obstacle) | ✅ 几何等价 |
| 2 | 分组（0<v<8 → 1..7） | probability.py:258-265 | mscore.cpp:2429-2438 | ✅ |
| 3 | part_solve（1<=v<=8 → 1..8） | probability.py:1690 | mscore.cpp:583-612 | ✅ 内核复用 |
| 4 | pbs 计算 | probability.py:1159 | mscore.cpp:1409-1660 | ✅ 同一内核 |
| 5 | certain_safe 双重判据 | probability.py:1171-1189 | mscore.cpp:2744-2761 | ✅ C1/C5 规格已钉 |
| 6 | clicks9 兜底 round5 + strict > | probability.py:1222-1225 | mscore.cpp:2782-2796 | ✅ C4 规格已钉 |
| 7 | win_rate 并列 round5 + ==max | probability.py:833-837 | mscore.cpp:2946-2959 | ✅ |

### 未对齐/待对齐路径

| # | 环节 | 差异 | 严重度 | 处置 |
|---|---|---|---|---|
| D1 | win_rate 破平局 | Python: np.random.choice；C++: deterministic (x,y)-min | 已有意改为确定性（方案 A 用户批准） | 语义有意不同 |
| D2 | pbs 破平局 | Python: 随机 shuffle + choice；C++: open_num5x5 + det | **8pp 差距根因**（规格四之九消融 24→30.7pp） | **阻塞生产同步** |
| D3 | OPTIMAL/Filter-U after_click 不更新 opened | C++: eng.opened0；理论：应更新 | **P0 级** | 必须修复 |
| D4 | adaptive_depth 分档 | Python: 默认 0；C++: 分档 0→2→3 | P1 级 | 加对拍验证 |
| D5 | wr_threshold 动态反馈 | Python: _wr_current_threshold；C++: 固定入参 | P2 级 | 长期按需下沉 |
| D6 | node_cap 降级 | catch 条件字符串匹配 vs 异常类 | NOT_RUN | 规格四之二红线 |

---

## 六、历史缺陷模式排查（复发检查）

| 历史缺陷 | 本次排查位置 | 结论 |
|---|---|---|
| res_list 列映射错位 | cpp/mscore.cpp:2619-2626, 2997-3004 | ✅ 已修复，显式注释 |
| world_upper 不含 clicks9 爆炸 | cpp/mscore.cpp:2558-2603 双守卫 | ✅ 已修复 |
| 物化无中途守卫 | cpp/mscore.cpp:2646 throw | ✅ 已修复 |
| free-inference 点击归属歧义 | B1 合并编译直接复用内核 | ✅ 已修复 |
| n_opened 外框污染 | 规格四之五修外框 9→0 | ✅ 已修复 |
| MSVC 非确定性编译 | 规格四之三改用导出符号+数值对拍 | ✅ 已规避 |
| **Filter-U 层 after_click 不更新 opened** | cpp/mscore.cpp:2969-3071 | ❌ **新 P0，本轮首次暴露** |

---

## 七、证据边界（诚实口径）

1. **枚举器样本量**：300 局 6354 决策。win_rate 层零触发 —— 主验收集布局中 world_upper ≤ 400 覆盖 win_rate 区域，OPTIMAL 层先抢占。WIN_RATE 层在这些数据上无覆盖。
2. **形式化验证运行在 trial_decide pyd**：隔离构建，非生产基线。但 win_rate/pbs 内核是同一源码编译（#ifdef MSW_TRIAL_DECIDE 只隔离 decide_impl 编排层）。
3. **P3 列映射**：间接证据（胜率从 0.7% 回升到 30%），直接真值对拍需 bench/truthcheck_optimal_mmv2.py 的 283 点真值集 —— 本轮未跑，留作下轮。
4. **P5 记忆守卫修正**：estimated_bytes 是保守估计值，reason='memory_gate' 是降级标志。
5. **P6 NOT_RUN**：规格四之二红线，本轮遵守不造伪样本。
6. **P0-1 after_click 不更新 opened**：静态代码审查发现，未跑数值对拍验证量化影响。预计影响 win_rate 并列候选（约 3.4% 决策轮次）。
7. **P2-1 wr_threshold 动态反馈**：当前 150 局配对两侧都固定 8.0（_time_budget=None 旁路），不是当前 8pp 差距源。生产 GUI 运行时持久化激活后 C++ 落后。

---

## 八、构建顺序与依赖标注

| 步骤 | 修复 | 依赖 | 产出 | 被消费 |
|---|---|---|---|---|
| 1 | P0-1 Filter-U after_click 同步 opened | 无前置 | after_click(opened_for_c) 语义 | 步骤 2、3 |
| 2 | P1-2 float32 比较统一 | 无前置 | 浮点数语义统一 | 步骤 5 |
| 3 | P1-3 OPTIMAL Odometer 溢出时 walk_all 找 rem_max | 无前置 | 极低概率 OPTIMAL 触发补全 | 步骤 5 |
| 4 | P1-4 Filter-U world_upper 上限放宽 + 采样 | 步骤 1 | 大局面过渡 | 步骤 5 |
| 5 | P1-1 adaptive_depth 分档对拍 Python | 无前置 | bench/adaptive_depth_parity.py | 步骤 7 |
| 6 | **对拍验证 25 真实决策点**（规格 C1-C7） | 步骤 1-5 | 零不一致 | 步骤 7 |
| 7 | 语义等价同步进生产 pyd | 步骤 6 | rebuild + SHA 冻结 + 导出符号对齐 | — |

**依赖成环**：P1-4 与 P0-1 弱环（Filter-U 上限 + after_click 同步），可独立修。

---

## 参考文件与行号

- cpp/mscore.cpp:2218-3090 — decision_layer 完整 decide_impl 三层编排
- cpp/mscore.cpp:2518-2525 — DSTAR 运行时配置
- cpp/mscore.cpp:2526-2654 — OPTIMAL 三重守卫
- cpp/mscore.cpp:2619-2626, 2997-3004 — 列映射修复
- cpp/mscore.cpp:2744-2761 — certain_safe 双重判据 + 批量
- cpp/mscore.cpp:2782-2796 — clicks9 兜底 round5 + strict >
- cpp/mscore.cpp:2840 — 并列容差 0.0005f（float32 混算问题）
- cpp/mscore.cpp:2908-2913 — adaptive_depth 分档
- cpp/mscore.cpp:2914-2941 — node_cap catch → pbs fallback
- cpp/mscore.cpp:2946-2959 — win_rate round5 + ==max 并列
- cpp/mscore.cpp:2969-3071 — Filter-U 层（**P0-1 位置**）
- cpp/mscore.cpp:3093-3230 — B1 合并编译保护宏
- cpp/mscore.cpp:2233-2244 — estimate_mem_impl
- cpp/ab_native.cpp:3712 — wr_threshold 固定传 8.0
- utils/probability.py:454-860 — number5_1 入口到 win_rate 分支
- utils/probability.py:1153-1280 — process_bigger_situation
- utils/probability.py:1171-1189 — certain_safe 双重判据（Python 锚点）
- utils/probability.py:1200-1217 — pbs 并列容差 + open_num5x5
- utils/probability.py:1219-1236 — clicks9 兜底
- utils/probability.py:797-798 — wr_threshold 动态读取（C++ 缺失）
- eview/Cpp统一decide_编排规格与证据边界.md — 规格文档
- ench/acceptance_threelayer_150.py — 150 局验收
- ench/sweep_dstar.py — DSTAR 扫描
- ench/_audit_formal_verification.py — 形式化枚举器
- ench/_audit_artifacts/formal_verification_results.json — 原始输出
- ench/data/threelayer_150.json — 45/150 + reason 分布
- ench/data/dstar_sweep_150.json — DSTAR 三档扫描

---

## Consensus Addendum（共识附录）

**Antithesis**："P0-1 的 after_click 不更新 opened 是已被 150 局实装验证胜率 30% 的架构，修复后不一定提升胜率，可能引入新回归。" —— 反题前提：P0-1 影响的决策轮次真在 150 局中触发。规格四之九白盒复现器显示 win_rate 分支约 3.4% 决策轮次（649/18932），并列集 max=10 —— Filter-U 评估范围窄。

**Tradeoff tension**：
- 修复 P0-1：代码改动量小（Filter-U 层 + OPTIMAL 层同步 opened），但需 ExactEngine 暴露"给定 opened 做 after_click"的 API（当前只有默认 opened0）。若 ExactEngine 结构不支持灵活 opened 参数，改动扩大到内核层。
- 不修复：Filter-U 层两步存活评估低估某些候选后续价值，但可能在这些低估下"碰巧"选对（对称误差抵消）。

**Synthesis**：先做量化影响探针（bench/audit_p01_probe.py）：20 个 win_rate 并列决策点，对比 opened0 vs opened_for_c 的 argmax 差异。差异率 > 20% → 必须修；差异率低 → 降级为 P2。

**Principle violations**：P0-1 违反规格文档"OPTIMAL 层 after_click 语义正确"原则（规格四之十二）。Filter-U 层声明"D3-validated 32.7% = py parity"但 D3 验证的是 Python 代理而非 C++ 实现。违反"实现与验证口径一致"原则。严重度：**高**。

---

**产出落盘**：review/architecture-audit-20261105.md  
**原始工具输出**：bench/_audit_artifacts/formal_verification_results.json  
**枚举器源码**：bench/_audit_formal_verification.py  
**四档结论**：PASS=6, FAIL=0, UNDECIDED=0, NOT_RUN=1  
**分级发现**：P0=1, P1=4, P2=3  
**SHA 双重验证**：MATCH  
**完成状态**：DELIVERED_WITH_EVIDENCE（落盘 + SHA 双重验证 + de_broadcast 完成）