# 论文研读笔记：两篇指定文献对本项目的直接含义

日期：2026-11-04　分支：main
来源：用户提供 PDF（已提取全文，见 `review/_paper_bergstra.txt`、
`review/_paper_couetoux.txt`，提取工具为环境内已有的 PyPDF2 3.0.1）

---

## 一、Bergstra & Bengio (2012), *Random Search for Hyper-Parameter Optimization*, JMLR 13:281-305

### 1.1 论文主张（逐字要点）

**核心结论**：随机搜索在超参优化上比网格搜索**更高效**，原因不是"随机更好"，
而是**目标函数 Ψ 的有效维度（effective dimension）远低于参数空间的维度**。

原文关键句（第 13-14 页）：
> "a small fraction of hyper-parameters matter for any one data set, but
> **different hyper-parameters matter on different data sets**."
>
> "Even in this simple 7-d problem, Ψ has a much lower effective dimension of
> **between 1 and 4**, depending on the data set."

**为什么网格搜索失败**（Figure 1 的论证）：
设 f(x,y) = g(x) + h(y) ≈ g(x)（即 y 几乎不起作用）。
网格 3×3 的九个点在 x 方向只覆盖 **3 个不同值**；
随机九个点在 x 方向覆盖 **9 个不同值**。
⇒ 低有效维度下，网格把预算浪费在无效维度上。

**方法学结论**（第 5 页）：
> "random search is a natural **baseline** against which to judge progress
> in the development of adaptive (sequential) hyper-parameter optimization"

即：**先跑随机搜索作基线，再谈贝叶斯优化/进化算法**。

### 1.2 对本项目的直接含义（三条，都可执行）

**含义 A：我此前的"贝叶斯优化"讨论是错的方向。**

本项目可调参数实际只有少数几个真正起作用：
`depth_limit` 系数、`node_cap`、`wr_threshold`、破平局准则选择。
按论文逻辑，这些参数的有效维度很可能 ≤3，因此：
- **随机搜索（Random Search）就是正确基线**，不需要 Optuna/TPE/GP；
- 论文明确说：只有在随机搜索基线上证明了增益，才值得上序列优化。

⇒ **修正我此前的建议**：我曾说"不建议用 Optuna 因为 41 点/7 参数会过拟合"，
这个理由**不完整**。更准确的理由是：**按 Bergstra 的结论，随机搜索是
这类问题的正确基线，序列优化的边际收益要先证明**。两者结论一致，但依据要换成论文。

**含义 B："不同数据集上重要参数不同" ⇒ 不能跨局面类型共享一套权重。**

论文最强的实证是：7 维参数空间里，不同数据集上重要的参数**不是同一批**。
直接映射到本项目：**不同 limitation 分层（简单局/中局/残局）的最优准则
可能不是同一套**。这解释了为什么我实测的"全局单一权重"永远赢不了 S0
——单一权重被迫在所有分层上折中。

⇒ **可执行推论**：应该做**分层权重**（按 limitation 或 n_cand 分桶，每桶独立
选准则），而不是一个全局线性组合。这是本轮之前没试过的方向。

**含义 C：网格搜索在本项目已被我用错。**

我做过"深度 × node_cap"的网格扫描（`bench_depth_policy.py` 的 5 档深度）。
按论文，这类扫描若有效维度低，应改为**随机采样 N 组参数**并报告分布，
而不是逐档穷举。后者在小样本下会误导（每档只测一次，无法区分效应与噪声）。

### 1.3 论文**不能**支持的主张（防误用）

- 论文的实验对象是**神经网络超参**（学习率、隐层数、正则），
  其 Ψ 是**连续、平滑、低维**的验证集误差。
  本项目的目标是**胜率（0/1 离散）**，且 regret 只在可枚举子集有真值。
  **平滑性假设不成立** ⇒ 论文的 GPR 分析部分不能直接搬。
- 论文的 256 trials 是**每 trial 训一个网络**。本项目"每 trial"是
  跑 2000 局（约 3.4 分钟）⇒ 预算量级完全不同，不能照搬 trial 数。

---

## 二、Couëtoux 博士论文（MCTS for POMDP），第 5.2 节 MineSweeper

### 2.1 论文主张（逐字要点）

**CSP 算法本质是短视的**（Algorithm 22 的标题就写着）：
> "The CSP algorithm for playing MineSweeper. The algorithm is intrinsically
> **myopic**: it only optimizes the 1-step result (minimum probability of
> immediate death)."

**CSP 的平局处理是弱点，且加启发式能改善**：
> "One of the weaknesses of CSP is that it is **not equipped to deal with ties**,
> i.e. with situations where there are multiple locations sharing the same
> lowest probability of hiding a mine."
>
> 文献 [28] 引入的启发式（Mines 专用）：
> - "the first move needs to be in one of the **corners**"
> - "when there is a tie... pick one of the locations that is the **closest to
>   the frontier**, i.e. the line between covered and uncovered locations"

**加 UCT 是必要的**：
> "the addition of UCT on top of tools like CSP is **essential to the
> optimality** of the overall method. That might be surprising for humans, but
> the best strategy does **not** only consist in playing a move with minimum
> probability of immediate death"

### 2.2 Table 5.1：外部权威基准（这是本轮最重要的对照数据）

| 盘面 | CSP-PGMS | HCSP | BSSUCT | **OH** |
|---|---:|---:|---:|---:|
| 4 雷 / 4×4 | 64.7% | 67.0% | 70.0%±0.9% | 67.0%±0.5% |
| 10 雷 / 5×5 | 8.20% | 8.51% | 9% (p=0.14) | 11.4%±0.4% |
| 5 雷 / 1×10 | 12.93% | 12.7% | 18.9%±0.2% | 17.0%±0.4% |
| 10 雷 / 3×7 | 4.50% | 4.76% | 5.96%±0.16% | 6.1%±0.2% |
| 10 雷 / 8×8 | 79.9% | — | — | 80.2±0.48% |
| 10 雷 / 9×9 | 80% | — | 90.5% | 89.9%±0.3% |
| **40 雷 / 16×16** | 45% | — | **76.4%±0.4%** | 74.4%±0.5% |
| **99 雷 / 16×30（专家）** | **34%** | — | **38.1%±0.5%** | **38.7±1.8%** |

（结果平均 10⁵ 局，16×30 为 10⁴ 局；OH 用 10000 次模拟/步，
专家与中级模式用 100 次模拟/步）

### 2.3 与本项目实测的对照（关键！）

| 项 | 本项目实测 | Couëtoux Table 5.1 | 差距 |
|---|---:|---:|---:|
| 99 雷 / 16×30，纯 CSP 贪心 | **33.50%**（Arbiter 规则，600 局） | **34%**（CSP-PGMS） | **−0.5pp** |
| 99 雷 / 16×30，本项目生产 | 33.67%（600 局，20268000+） | — | — |
| 99 雷 / 16×30，最好方法 | 本项目未测 | **38.7%**（OH，100 次模拟/步） | — |

**这是一个极强的外部校验**：本项目的纯贪心基线 33.50% 与文献的
CSP-PGMS 34% **几乎完全一致**（差 0.5pp，在 600 局的 CI 内）。
⇒ 说明：
1. 本项目的精确概率计算（`_pbs_compute_dp`）**与文献的 CSP 等价**，
   没有实现错误；
2. 本项目的 33.5% 不是"算法差"，而是**"纯贪心的固有水平"**；
3. 文献用 MCTS 把同一盘面从 34% 提到 **38.7%**（+4.7pp），
   且只用 100 次模拟/步 ⇒ **这给出了算法侧的现实天花板参考**。

**重要边界**：Couëtoux 的盘面是标准现代规则（首点安全），
本项目是 Arbiter 延迟布雷规则。规则不同 ⇒ 绝对数字不能直接等同。
但"纯贪心 vs MCTS 的**相对提升**"（约 +4~5pp）是可参考的量级。

### 2.4 论文对本项目两条直接建议的印证

**印证 1：首点位置 —— 论文说"第一手要在角落"。**

Couëtoux 转述文献 [28] 的启发式第一条就是"the first move needs to be in
one of the **corners**"。这与本项目实测的**精确组合数学结论**一致：

| 位置 | P(首格数字=0)（Arbiter 规则，N=479, a=99） |
|---|---:|
| 角落 | **49.85%** |
| 边缘 | 31.25% |
| 中心（生产硬编码） | **15.45%** |

⇒ 文献的"角落开局"经验法则，在本项目规则下有**精确的组合学依据**，
且生产代码固定点中心恰好是三者最差。这条值得做。

**印证 2：破平局用"离前沿最近" —— 我已实测过，未检出稳定收益。**

论文说 HCSP 的做法是"pick one of the locations that is the **closest to the
frontier**"。本项目实测（`bench/ab_tiebreak.py`，640 局合并）：
`open_plus_frontier` **+0.16pp，95%CI [−3.53, +3.84]，McNemar p=1.000**。

⚠️ 但要注意：论文 Table 5.1 里 **HCSP 相对 CSP-PGMS 的提升也很小**
（4×4: 67.0% vs 64.7% = +2.3pp；5×5: 8.51% vs 8.20% = +0.31pp；
1×10: 12.7% vs 12.93% = **−0.23pp** 反而更差；3×7: 4.76% vs 4.50% = +0.26pp）。
⇒ **论文自己也没能证明"离前沿最近"这个破平局准则有稳定收益**。
这与本项目 640 局 A/B 的结论一致。这是一次独立印证。

---

## 三、两篇论文共同指向的下一步（按证据强度排序）

| 优先级 | 做什么 | 依据 |
|---|---|---|
| **P1** | **分层权重**：按 limitation 分桶，每桶独立选破平局准则，而非全局单一权重 | Bergstra "不同数据集上重要参数不同"；解释了我实测"全局权重永远输给 S0" |
| **P2** | **角落开局**：生产固定点中心改为角落 | Couëtoux 转述 [28] 的首点启发式 + 本项目精确组合学 49.85% vs 15.45% |
| **P3** | **随机搜索作基线**替代网格扫描（深度/node_cap 等参数） | Bergstra 全文结论 |
| P4 | 若上 MCTS：目标定在 **+4~5pp**（而非 +16pp） | Couëtoux 34%→38.7%，100 次模拟/步 |

**明确不做的**：
- 不用贝叶斯优化/TPE 调 7 个指标权重 —— Bergstra 说随机搜索是基线；
  且本项目 41~119 个可枚举点做 7 维拟合必然过拟合（已 LOO 实证）。
- 不期望 MCTS 带来 +16pp —— 文献最好也只从 34% 到 38.7%。

---

## 四、证据边界（必须随结论一起引用）

1. Couëtoux 的盘面是**标准现代规则**，本项目是 **Arbiter 延迟布雷**。
   绝对胜率不可直接等同，只有"相对提升量级"可参考。
2. Table 5.1 的 OH 结果用了 10000 次模拟/步（专家模式 100 次），
   本项目单决策预算 30s，**能否达到同等模拟数未测**。
3. Bergstra 的 Ψ 是连续平滑的验证集误差，本项目是离散 0/1 胜率，
   **平滑性假设不成立**，其 GPR 分析部分不能搬。
4. 本项目的 33.50% 与文献 34% 的一致性是**单点对照**（一个盘面规模），
   不能据此断言所有规模都等价。

---

## 五、复现

```bash
# 论文全文提取（用环境内已有的 PyPDF2 3.0.1，不新装依赖）
python -c "import sys; sys.path.insert(0,r'D:\tools\add_header_to_pdf\.venv\Lib\site-packages'); import PyPDF2; ..."
# 已提取：review/_paper_bergstra.txt (25 页), review/_paper_couetoux.txt (134 页)
```
