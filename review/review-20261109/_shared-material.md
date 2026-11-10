# 评审材料（两席盲评共用，除角色外逐字一致）

## 评审对象（钉死）
先读 `review/review-20261109/review-spec.json`（覆盖分母：21 个文件 / 15,002 行），
逐个审阅其中 `reviewable_files` 列出的**全部**文件。不要审 `excluded_with_reason`
里的（`ui/bg_rc.py` 是自动生成资源、`bench/` 是实验过程物、`tests/`、`sim/`）。

## 代码事实（客观描述，非结论）
- `cpp/mscore.cpp` 是 C++ 决策内核（4,719 行），含三层决策结构：
  OPTIMAL（`world_upper <= DSTAR`）、win_rate（前瞻）、pbs（贪心）。
  文件内包含 `COST_CAP_LOG2`、`DEPTH_CAP`、`BUDGET_COST_UNITS` 三处标识符（可搜索定位）。
- `cpp/ab_native.cpp`（860 行）是 AB 实验桥接层。
- `utils/probability.py`（2,268 行）是 Python 侧决策编排，含 `_wr_update`、`_decide_unavailable`。
- `utils/solver.py`（662 行）是自动/帮助主循环。
- `utils/vision.py`（544 行）是视觉识别。
- `main.py`（2,617 行）是 UI 主窗口。
- `ui/preview_popup.py`（907 行）是 7×7 状态转移预览浮层。
- `utils/decide_trace.py`（147 行）与 `utils/analyze_decide_trace.py`（177 行）是两个新增的观测/分析模块。

## 必须核查的跨语言契约
`decide()` 返回的 dict 字段集合在 C++ 生产端与 Python 消费端是否一致：
`reason` / `x` / `y` / `confidence` / `world_upper` / `prob` / `tied` /
`limitation` / `cost_log2` / `transitions` / `transition_meta`。
关注：字段名拼写不一致、缺失字段的兜底、类型不匹配。

## 项目约束（外部给定，非评审对象）
- 内存硬上限 **1.5GB 不可调**（用户设定）。任何"调高内存上限"的建议无效。
- 帮助模式 `is_play=False` 与自动模式 `is_play=True` 的行为必须区别对待。
- 中局（世界数超枚举上限）**不得**计算状态转移，只能返回明确原因。
- **不得**用采样/蒙特卡洛伪造状态转移概率，只允许精确世界枚举。
- `ui/window.py` 与 `ui/window.ui` 是手工同步的，**禁止**建议用 `pyuic5` 全量重生成
  （会破坏手工修复）。

## 风险声明
行为兼容（决策算法与门槛逻辑存在计算口径变化）、输入处理（盘面数组与视觉识别输入）。
无密钥凭据面。

## 产出格式（强制）
写入 `review/review-20261109/<角色名>.md`：
1. P0/P1/P2 分级；每条发现必须带 `文件:行号` 证据（行号须真实存在于该文件）
2. 末尾必须有 `## 【改进点】` 小节
3. **覆盖账**：spec 中每个文件落四态之一
   `reviewed` / `skipped(理由+复跑方式)` / `blocked(原因)` / `waived(引用用户指示原文)`
4. **评审身份块**：模型名、实际达成模式（`实机` / `截图` / `仅代码`，
   纯静态审阅写"仅代码"并说明降级理由）、断言鉴别力自查

## 纪律
- **只读**：除上述报告文件外，不得修改任何文件。
- 发现问题必须有代码证据；不要凑数报无效问题，也不要因"看起来能跑"放过真实缺陷。
- 规模提示：15,002 行核心代码 + 4,719 行 C++，请完整覆盖，不要只看前几个文件。
- 若某文件确实无法评审，用 `blocked` 如实记录，不要静默跳过。
