# C++ 「前瞻 / 状态转移」算法说明（2026-11-08 快照）

> 用户要求先展示 C++ 当前算法，再决定怎么改。本文描述**本文写作时**的磁盘代码，
> 不改任何实现。所有引用均为 `cpp/mscore.cpp` 行号。
>
> ⚠️ **后续变更留痕**：本文写完后，为修复「浮层显示的是最优格而非悬停格的转移」，
> `cpp/mscore.cpp` 又新增了 `TransitionEngineCache`（thread_local 世界集缓存）、
> `compute_transitions_for_cell(eng,x,y)` 与导出 `transitions_for(cv,a,w,h,x,y)`。
> 因此第七节「当前没有的能力」中「没有按任意指定格算转移的导出接口」**已不再成立**；
> 但「中局不计算状态转移（世界数超 20 万上限即返回明确原因）」的裁决保持不变。
> 本文保留作为算法结构与决策链的留痕快照。

## 一、两套不同用途的引擎

| 用途 | 入口 | 产出 | 是否被 UI 用作「状态转移」 |
| --- | --- | --- | --- |
| 决策打分（选最优格） | `decide_impl` 三层：OPTIMAL / win_rate / pbs | `x,y`（决策格）+ `prob`（逐格安全概率） | 否 |
| 精确前瞻 `ExactEngine` | `V_exact` / `after_click` / `click_outcomes` | 给**一个格子**算「点击后的可观测分支分布」 | 是 |

关键点：**决策层本身会遍历所有候选格打分**（`for (int c : cands)`），
但「状态转移」只在决策结果确定后，对**最终选中的那一个 best_cell** 算一次。
这就是你说的「现在做的前瞻肯定不能只前瞻一个格」的代码层面来源。

## 二、决策层：确实遍历了所有候选格

`OPTIMAL` 层（`cpp/mscore.cpp:2778-2785`）：

```cpp
for (int c : cands) {
    double v = eng.after_click(c, wk, eng.opened0);
    if (first || v > v_opt) { v_opt = v; best_cell = c; first = false; }
}
```

`cands` = 所有未开格（`cpp/mscore.cpp:2765-2770`），`after_click` 内部
（`:2048-2058`）会调 `click_outcomes` 递归到 `V_exact`。所以**决策打分是
全候选、多步递归**的，不是单格。

`win_rate` 层（`:3165`）与 pbs 层也各自对所有候选格算胜率/安全概率，
输出 `prob`（逐格安全概率，`:2794-2799` 是 OPTIMAL 层补的）。

## 三、状态转移：只对 best_cell 算一次

### 3.1 OPTIMAL 分支（`cpp/mscore.cpp:2800-2854`）

```cpp
const double p_clicksafe =
    eng.click_outcomes(best_cell, wk, eng.opened0, outs);   // :2811
for (auto& o : outs) {
    branch["probability"] = py::float_(o.weight);
    branch["board"] = transition_branch_board(
        eng, o.wk, o.opened,
        eng.coord_x(best_cell), eng.coord_y(best_cell));    // :2835-2837
    transitions.append(branch);
}
```

### 3.2 帮助模式补算（`cpp/mscore.cpp:3510-3762`）

`py_decide` 包装层（`:3799`）对 `is_play=False` 且决策层没自带转移时，
调 `help_exact_transitions(cell_value, a, w, h, best_x, best_y)`：

```cpp
const int best_cell = best_y * W2 + best_x;                 // :3715
eng.click_outcomes(best_cell, wk, eng.opened0, outs);       // :3723
branch["board"] = transition_branch_board(
    eng, o.wk, o.opened, best_x, best_y);                   // :3746-3747
```

**两处都写死了 `best_cell`**，也就是 C++ 决策选出的那个最优格。

## 四、`click_outcomes` 到底算了什么（`:1969-2000`）

输入：一个格子 `cell`、当前世界集 `wk`、当前已开位集 `opened`。

1. 从所有一致世界里筛出「该格不是雷」的世界 `safe_idx`（`:1971-1974`）
2. 对每个安全世界调 `flood(wb, opened, cell)` 做扫雷式 0 泛洪，得到新已开位集 `op`
3. 用 `Obs = (完整已开位集, 新开格及其数字)` 作签名分组（`:1976-1988`）
4. 每个分组产出一个 `Outcome{该分支的世界子集 wk, 新已开位集, weight}`，
   其中 `weight = 该分支世界数 / 安全世界总数`（`:1990-1998`）
5. 返回 `p_safe = 安全世界数 / 全部一致世界数`

即：**给定点击该格且没踩雷，出现各个可观测局面的条件概率**，概率之和 = 1。

## 五、`transition_branch_board`（`:3473-3508`）

把某个 `Outcome` 渲染成以 **best_cell 为中心**的 7×7 局部局面：

- 已开格 → `eng.number(idx, mine)` 数字
- 该分支所有一致世界共有的雷位（`union_inter` 的 `inter`） → 编码 10（确定雷）
- 其余未开 → 编码 9
- 棋盘外 → -1

中心坐标参数 `cx, cy` 在两处调用点都传的是 `best_x, best_y`。

## 六、Python / UI 侧如何消费（问题所在）

`utils/probability.py` 的 `_number5_1_core` 把 C++ 返回的
`transitions` / `transition_meta` 存进 `self._last_heatmap`（`:832-841`），
经 `solver._emit_heatmap`（`utils/solver.py:496-497`）进 GUI payload。

`main.py` 三个 hover provider **共用同一份**：

| provider | 行号 | 取数 |
| --- | --- | --- |
| `_preview_payload_for_candidate` | `:857-879` | `payload.get("transitions")` |
| `_preview_payload_for_heatmap_cell` | `:881-897` | `payload.get("transitions")` |
| `_preview_payload_for_board_cell` | `:1429-1447` | 转调上面两个 |

`payload` 全是 `self._last_heatmap_payload`，即**最近一次决策的那一份**，
它是针对 `best_cell` 算的。

**结论（可由上述代码直接验证）**：无论鼠标悬停在哪个格子上，浮层显示的
都是「点击最优格之后」的分支分布与 7×7 局面；`transition_branch_board`
的 7×7 窗口也始终以最优格为中心，不是悬停格。

## 七、当前没有的能力

- 没有对「任意指定格 (x,y)」单独算转移的导出接口：
  `help_exact_transitions` 是 `static`，未 `m.def` 导出（`m.def` 列表见
  `cpp/mscore.cpp:3471+`），只在 `py_decide` 内部被调一次。
- 没有「一次算出多个格子的转移」的结构：返回 dict 里只有一份
  `transitions` / `transition_meta`。
- `ExactEngine::world_bits`（本轮世界集）在 `help_exact_transitions`
  内是**局部构造、用完即毁**，没有跨调用缓存；若要对多个格子分别算，
  每次都要重新枚举一遍完整世界集（`Odometer` + `ComboIter`，
  `:3536-3713`）。

## 八、相关约束（已确认）

- 世界枚举硬上限 `HELP_TRANSITION_WORLD_CAP = 200000`（`cpp/mscore.cpp:3458`），
  超限返回 `world_count_exceeded`，UI 显示准确原因，不合成概率。
- 内存闸门 1.5GB / `node_cap` 200 万属于不可调整项（用户裁决）。
- 帮助模式预算 90s（30s × 3）；自动模式 30s，且自动模式不算转移。
