# 智能扫雷助手

基于 Python 3.10 + PyQt5 + C++17 (pybind11) 的 Windows 扫雷助手，对接
Minesweeper Arbiter 0.52.3，提供「自动扫雷」与「帮助人类」两种模式。

> **现行架构与测量口径请看 [docs/现行架构与测量.md](docs/现行架构与测量.md)**。
> 本文件只讲「怎么跑起来」和项目结构；算法细节、E2E 探针、环境变量、热键、
> 事件驱动局末流程都在那份文档里。
>
> 历史实验的编年全景、可信度分级与【有效】/【有条件】/【作废】标记见
> [docs/实验总报告.md](docs/实验总报告.md)。那份报告是**历史快照**：其中标注
> 【作废】的读数禁止引用，涉及 `win_rate` / `part_solve` / `exact_eval` /
> `wr_threshold` 的章节描述的是**已删除的旧实现**，仅作史料保留。

## 项目特点

- **唯一决策路径**：所有选格都来自 C++ `decide`（唯一入口），按论文
  AAAI-17 WS-17-15 Algorithm 3：`‖S‖ ≤ criterion` 走 OPTIMAL 精确穷举，
  否则走 pbs + PSEQ 启发式。Python 侧**没有第二套决策实现**。
- **算法上限**：39.85% ± 0.23pp（95% CI [39.62%, 40.08%]，178,690 局纯 C++
  `decide` 循环口径，Arbiter 延迟布雷、30×16 / 99 雷、角落首点、视觉 100%
  正确的离线模拟）——40% 目标未严格达成。不同口径的胜率不可直接相减，
  详见 [docs/实验总报告.md](docs/实验总报告.md)。
- **自动模式**：扫描棋盘 → `decide` → 点击/批量安全格 → 重扫 → 循环到胜负；
  热力图按逐格安全概率上色。
- **帮助模式**：给出全部确定安全格与确定雷格，支持「点击所有确定方格」批量执行；
  不自动点击。
- **悬浮预览**：悬停任意未开格，展示点击该格之后的多个 7×7 可达局面及真实
  条件概率（纵向列表，一行一个）。Auto 页面**不接**状态转移预览，只显示热力图。
- **全局热键终止**：自动跑起来后鼠标被占用也能停（默认 ESC）。
- **E2E 遥测**：`MSW_E2E_PROBE=1` 打开，JSONL 记录 span/decision/click/mem/mark/error，
  默认关闭、默认上限 128MB。

## 环境要求

- **Python 3.10**（`<PY310_PYTHON>` = 你的 py310 环境解释器路径；3.12 下
  `win32gui` DLL 加载失败。仓库内命令统一用该占位符，避免写入本机绝对路径）
- Windows + MSVC 2022（仅重新构建 C++ 模块时需要）
- 依赖见 [requirements.txt](requirements.txt)：PyQt5、OpenCV、numpy、pyautogui、pywin32、Pillow
- 扫雷程序：Minesweeper Arbiter 0.52.3（路径与窗口标题在 [cfg.json](cfg.json) 配置）

## 快速开始

1. 用 py310 环境安装依赖：`pip install -r requirements.txt`
2. 确认 [cfg.json](cfg.json) 里的 `w/h/a`、`bx/by`、`cell_width`、`path`、`win_name`
   与本机窗口一致
3. 运行 `python main.py`
4. 若 C++ 模块需更新：**完全关闭程序** → 运行 [cpp/build.bat](cpp/build.bat)
   生成 `cpp/mscore_new.pyd` → 双击 [apply_native_fix.cmd](apply_native_fix.cmd)
   安装到 `utils/mscore.cp310-win_amd64.pyd` → 重启

> ⚠️ `utils/mscore*.pyd` 在程序运行时被锁定；不要直接覆盖，也不要杀用户进程。

## 核心算法（现行）

```
盘面 cell_value (h+2, w+2 int32；9=未开 / 10=标记雷 / 0-8=已开数字)
  │
  ├─ validate_board_consistency()   真实盘面逻辑校验（在 C++ decide 之前）
  │
  └─ native.decide(cell_value, a, w, h, node_cap, is_play)
        │
        ├─ count_situations_exact()  精确 ‖S‖ = 完整一致世界总数
        │
        ├─ ‖S‖ ≤ DSTAR  →  OPTIMAL(...)            论文 Algorithm 3 line 2
        │                  DSTAR_AUTO=256（MSW_DSTAR 可覆盖）
        │                  帮助模式 DSTAR×3 = 768
        │
        └─ 否则          →  pbs + PSEQ（启发式）   论文 Algorithm 3 line 4
                           内存 1.5GB 硬闸门不可调
                           MID-LOOP GUARD 在物化世界数超 DSTAR 时 fail-closed
```

- 返回值含 `x, y, confidence, path, reason, prob, tied, weighted` 及诊断
  `situations`（精确 ‖S‖；`-1` 未计数、`DSTAR+1` 为超限哨兵）、`dstar`、`limitation`
  （仅诊断，不参与决策）。
- `wr_threshold` 形参在 [utils/native.py](utils/native.py) 里仅为兼容旧调用方保留，
  **不再转发给 C++、不起任何作用**。
- 决策不可用时（pyd 缺失 / `has_decide()` 为 False / 抛异常 / 返回无效坐标）：
  统一走「ERROR 日志 + 用户可见警告 + 全盘安全重扫 + 不点击」，不回退到 Python。

细节见 [docs/现行架构与测量.md](docs/现行架构与测量.md) 与
[docs/变量名与算法细节.md](docs/变量名与算法细节.md)（后者含历史命名陷阱，
读时注意其时间标注）。

## 状态转移预览（帮助页）

数据来自 C++ `transitions_for(cv, a, w, h, x, y)`：复用本次 `decide` 缓存的世界集，
按 `click_outcomes` 分组。转移概率条件于「被点击格是安全的」，UI 显示
`p_click_safe` 与 `world_count`；`HELP_TRANSITION_WORLD_CAP=200000` 以上时不计算
转移、不合成概率，只显示准确原因。7×7 窗口以**鼠标所在格**为中心。

## 使用模式

### 自动模式

扫描 → `decide` → 点击/批量安全格 → 重扫 → 循环，直到胜负。
终止方式：GUI 停止按钮 / **全局热键 ESC**（`MSW_STOP_HOTKEY` 可改虚拟键码，
`=0` 关闭）。局末重开走**事件驱动**（检测弹窗消失、棋盘变化、首点完成），
`MSW_LEGACY_SLEEP=1` 可回退到旧固定 sleep 链做对照。

### 帮助模式

扫描 → `decide` 循环（只收集候选，不点击）→ 标出确定安全格与确定雷格 →
由人类点「点击所有确定方格」批量执行。悬停未开格看 7×7 状态转移预览。

## 测量与验证

- **E2E 实测跑**：[run_e2e_900.cmd](run_e2e_900.cmd)（真实 GUI + Arbiter，
  `MSW_E2E_PROBE=1`；须在用户自己的终端运行，沙箱是低完整性级别，驱动不了
  Arbiter 输入）。分析器 [bench/analyze_e2e.py](bench/analyze_e2e.py) 按
  `game_begin→game_end` 墙钟、分层 span、`DSTAR+1` 哨兵切分出真实口径。
- **死代码审计**：[bench/audit_dead_code.py](bench/audit_dead_code.py)（AST 静态）。
- **正确性验证**：[bench/verify_getlist_fix.py](bench/verify_getlist_fix.py)、
  [bench/verify_prob_truth.py](bench/verify_prob_truth.py)、
  [bench/verify_paper_layers.py](bench/verify_paper_layers.py)、
  [bench/repro_world_count_split.py](bench/repro_world_count_split.py)。
- **单元回归**：`python -m unittest discover -s tests -q`（当前 307 项，
  skipped=1）。
- 两轮真机 E2E 结论见
  [state/e2e_900/分析结论-253局.md](state/e2e_900/分析结论-253局.md) 与
  [state/e2e_900/分析结论-第二轮201局.md](state/e2e_900/分析结论-第二轮201局.md)。

> 测量纪律：先验证仪器再信读数；「总数相同」不等于 A/B 等效（要同 seed
> 配对 McNemar）；单点内存峰值不是泄漏证据；判阈值/触发门类的优化必须配对比较。

## 项目结构

```
.
├── main.py                  # GUI 主窗口（自动页 / 帮助页 / 悬浮预览）
├── ui/
│   ├── window.ui/.py        # Qt 界面定义（手工同步，勿整体 pyuic5 重生成）
│   └── preview_popup.py     # 7×7 状态转移浮层
├── utils/
│   ├── solver.py            # Solver(QThread)：视觉扫描 + 自动/帮助主循环 + 热键
│   ├── probability.py       # number5_1 主入口 / 盘面校验 / 决策不可用处理
│   ├── vision.py            # 棋盘 OCR / 窗口内模板匹配 / 低置信度处理
│   ├── native.py            # C++ 模块加载与 decide / transitions_for 包装
│   ├── e2e_probe.py         # E2E JSONL 遥测（默认关闭）
│   ├── decide_trace.py      # 每次 C++ decide 的独立决策链追踪
│   ├── deduction.py         # 兼容层推理（生产已移除）
│   └── mscore.cp310-win_amd64.pyd   # 编译产物（运行期加载）
├── cpp/
│   ├── mscore.cpp           # C++ 内核：decide / OPTIMAL / pbs+PSEQ / 世界枚举
│   ├── build.bat            # 构建脚本 → cpp/mscore_new.pyd
│   └── inc/                 # vendored pybind11 头文件
├── bench/                   # 现行验证脚本 + 历史实验脚本（见下）
├── state/                   # 运行期数据（gitignore；不作废的有效语料见下）
├── docs/
│   ├── 现行架构与测量.md    # ★ 现行唯一权威说明
│   ├── 实验总报告.md        # 历史编年全景 + 可信度分级 + 作废清单
│   ├── 变量名与算法细节.md  # 命名陷阱（历史快照，含已删 API 描述）
│   ├── 胜率测试套件说明.md  # 历史 S0–S7 实验台架（史料）
│   ├── e2e测量与死代码审计-20261109.md
│   └── 局末事件驱动与热键-20261109.md
├── tests/                   # unittest 回归套件（307 项）
├── CHANGELOG.md / HANDOFF*.md   # 历史快照
└── README.md
```

`bench/` 里仍保留大量历史实验脚本：只有名字出现在
[docs/现行架构与测量.md](docs/现行架构与测量.md)「现行验证入口」一节的脚本
保证与当前代码一致；其余为史料，可能引用已删除 API，**不要当作可运行工具**。
清理记录见 [state/audit/cleanup_plan.json](state/audit/cleanup_plan.json) 与
[state/audit/cleanup_exec.json](state/audit/cleanup_exec.json)。

`state/` 保留的有效数据：`decide_trace.jsonl`（逐次 decide 追踪）、
`game_stats.json`（累计局数/胜局）、`stress20k/`（17.9 万局逐局语料）、
`audit/`（验证与清理产物）、`dstar_10k/arm_400.json` 与 `arm_800.json`、
`e2e_900/分析结论-*.md`。被 20MB 上限截断的探针、跨轮混算的 summary 与
旧实验臂数据已删除，见 [state/audit/cleanup_exec.json](state/audit/cleanup_exec.json)。

## 已知未闭合缺口

详见 [docs/实验总报告.md](docs/实验总报告.md) 第 8.4 节与
[docs/现行架构与测量.md](docs/现行架构与测量.md)「未闭合项」，摘要：

1. 生产 GUI 自动模式 vs 纯 `decide` 循环的端到端胜率对拍（同 seed 约 20% vs 40%，【未验证】）
2. 深局面（worlds>2000）上的 LP 可分离性：零证据
3. 残局候选概率覆盖率 90.91%，未达 95% 门线
4. 真实截图 + 标注黄金集（DPI / 主题 / 缩放 / 遮挡）完全缺失
5. 50% 目标按哪个口径验收：需用户裁定
6. 论文对齐后 C++ 性能退化（2000 局 724s vs 旧 129s）未收敛；
   `coarse_cap = DSTAR*4` 收紧为 `DSTAR` 的提案未验证
7. P0 遗留：C++ `counts[64][10]` 固定数组与无界 `nTied`（最大观测 35/300 局，
   非安全上界证明）

## 技术栈

- Python 3.10 + PyQt5 (GUI)
- OpenCV (图像识别) + Numpy (矩阵运算)
- pyautogui / pywin32 (窗口与点击) + Pillow
- C++17 + pybind11 (MSVC 2022，决策内核)

## 参考资料

- [docs/现行架构与测量.md](docs/现行架构与测量.md) — 现行架构与测量口径
- [docs/实验总报告.md](docs/实验总报告.md) — 历史实验权威汇总与作废清单
- [docs/变量名与算法细节.md](docs/变量名与算法细节.md) — 反直觉命名与算法陷阱
- [Windows API 文档](https://learn.microsoft.com/zh-cn/windows/win32/api)
- [扫雷算法策略](https://minesweeper.online/help/patterns)

## License

MIT License
