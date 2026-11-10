⚠️ 隔离污染：`本轮改动已完成 C++ 编译与 318 项单测通过`（该作者基线说明未采信；本报告仅依据源码、只读扫描与本席命令输出）

# 安全评审报告

**范围：** `review-spec.json` 的 21 个生产文件（15,002 行）及任务点名的 `cfg.json` / `requirements.txt`；本地 Windows 桌面扫雷助手，无网络服务面  
**整体风险等级：** **HIGH**（原生栈越界可在正常决策路径破坏内存；多道资源闸不能落实 1.5GB 硬上限）  
**评审身份：** kimi-k3.1-pro；实际达成模式：**仅代码**。降级理由：角色只读，未编译、未启动 GUI、未运行 ASan/UBSan，也未产生测试或构建产物；不引用作者测试结论。  
**交付状态：** DELIVERED_WITH_EVIDENCE（以报告落盘与点对点完成广播为准；不自称 DONE）

## 摘要

- **Critical / P0：1**
- **High / P1：4**
- **Medium / P2：1**
- **Low / P2：1**
- 密钥：未发现真实硬编码密钥；Git 历史扫描同样未发现密钥形态命中。
- 依赖：`npm audit --package-lock-only --omit=dev` 返回 0，但 `package.json` 无依赖，不能据此证明 Python 依赖安全；`pip-audit` 未安装且只读纪律禁止安装，故 **skipped**。
- 最高优先级不是 Web 注入，而是 **C++ 栈内存破坏、原生边界校验、完整世界枚举的内存闸门失效**。

## P0 / Critical 问题（立即修复）

### 1. PSEQ 并列候选超过 64 时发生栈越界写

**分级：** P0 / CRITICAL  
**类别：** CWE-121 Stack-based Buffer Overflow；OWASP A04/A05（内存不安全设计与原生边界失效）  
**位置：** `cpp/mscore.cpp:3111-3114`、`cpp/mscore.cpp:3164`；同类第二处 `cpp/mscore.cpp:3454-3455`、`cpp/mscore.cpp:3499`  
**可利用性：** 本地盘面输入即可触发，无需认证、无远程入口；要求同一决策中 `tied.size() > 64`，在 30×16 盘面最多可有 480 个候选。  
**影响范围：** 破坏当前原生栈帧，可能崩溃、错误决策/错误点击，理论上存在进程内内存破坏后果；Python 异常处理无法可靠拦截原生栈破坏。

`nTied` / `nT` 直接来自未封顶的 `tied.size()`，但计数器固定为 `[64][10]`，循环上界却是 `ti < nTied` / `ti < nT`。第一处 `counts[ti][mn]++` 与第二处 `cnts[ti][mn2]++` 均在 `ti >= 64` 时越界。第二处进入 Filter-U 的条件仅是 `tied.size() > 1 && world_upper <= 2000000`，没有 64 上限。

**【改进点】修复：** 两处都改为按实际并列数分配的 `std::vector<std::array<int, 10>>`，不要仅把 64 改成 480 后继续保留固定栈数组；同时加 65 个并列候选的 ASan 负向回归。

```cpp
// BAD（两处同构）
const size_t nTied = tied.size();
int counts[64][10];
memset(counts, 0, sizeof(counts));
// ...
if (mn <= 8) { counts[ti][mn]++; }  // ti 可 >= 64

// GOOD
#include <array>
#include <vector>

const size_t nTied = tied.size();
std::vector<std::array<int, 10>> counts(nTied);  // 值初始化为 0
// ti 与 counts.size() 始终同长；下标仍检查数字范围
if (mn >= 0 && mn <= 8) {
    ++counts.at(ti).at(static_cast<size_t>(mn));
}
// 第二处同样：std::vector<std::array<int, 10>> cnts(nT);
```

**验证建议（未执行）：** 构造至少 65 个同分候选，分别强制走 pbs+PSEQ 与 win_rate Filter-U；ASan 下必须无越界，且普通构建输出坐标仍在盘面内。

## P1 / High 问题

### 2. 分组解在世界数与内存预检之前已无限物化，1.5GB 硬上限可被提前突破

**分级：** P1 / HIGH  
**类别：** CWE-400 Uncontrolled Resource Consumption；OWASP A04/A05  
**位置：** `cpp/mscore.cpp:2513-2536`（先 `py_part_solve`、后 `estimate_mem_impl`）；无上限增长点 `cpp/mscore.cpp:466-469`、`cpp/mscore.cpp:513`、`cpp/mscore.cpp:531`  
**可利用性：** 本地大前沿盘面/错误配置；无需特殊权限。  
**影响范围：** 在内存闸门执行前即可长时间枚举并积累指数级解矩阵，导致 UI 冻结、进程或系统内存压力，直接违反不可调的 1.5GB 约束。

`PartSolveCtx` 的 `node_cap` 与 `solution_cap` 默认均为 0（不限制），`py_part_solve` 又没有给生产 `decide_impl` 传入上限。`decide_impl` 只有所有组求解完成后才计算 `world_upper` 与 `mem`，因此后续闸门再严格也无法追回已发生的分配。

**【改进点】修复：** 把节点/解数上限贯通到 `py_part_solve` 的生产调用；在每次 `emplace_back` **之前**检查；逐组累计保守内存并在处理下一组前 fail-closed。不得靠提高预算解决。

```cpp
// BAD：生产调用使用默认 node_cap=0 / solution_cap=0
py::tuple r = py_part_solve(coords_to_pylist(g), cell_value, a, w, h, no_progress);
// 内存预检却在全部分组之后

// GOOD：为 py_part_solve 增加显式 cap 参数并贯通到 PartSolveCtx
py::tuple r = py_part_solve(coords_to_pylist(g), cell_value, a, w, h,
                            no_progress, /*node_cap=*/2'000'000,
                            /*solution_cap=*/per_group_cap);

// PartSolveCtx::recurse：在任何新分配之前失败关闭
if (solution_cap > 0 &&
    solutions.size() >= static_cast<size_t>(solution_cap)) {
    aborted = true;
    return;
}
solutions.emplace_back(state.begin(), state.end());
```

**验证建议（未执行）：** 注入必须超过解数上限的真实多元素盘面（至少 3 个分组），断言在 cap 处停止、无超预算峰值，并返回明确 `part_solve_cap`，而不是等到全量枚举后才降级。

### 3. 内存估算漏乘 `C(n9, rem)`，与前瞻成本门使用不同世界数

**分级：** P1 / HIGH  
**类别：** CWE-400；OWASP A04（安全闸门设计口径错误）  
**位置：** `cpp/mscore.cpp:2535-2536`、`cpp/mscore.cpp:2570-2584`、`cpp/mscore.cpp:2954-2956`；实际扩展分配 `cpp/mscore.cpp:1130-1170`  
**可利用性：** 本地含内部无约束格的盘面；满足前瞻成本门即可进入。  
**影响范围：** `mem.allowed=true` 不能代表完整世界枚举在预算内；完整世界列表及递归副本可突破 1.5GB，造成进程/系统拒绝服务。

`mem` 在 `log2_total_est` 之前仅用 `world_upper` 计算；但 `world_upper` 只是各前沿组解数乘积，完整世界还要乘内部格组合数。随后 `use_win_rate` 同时检查 `log2_total_est <= COST_CAP_LOG2` 和旧 `mem.allowed`，二者衡量的不是同一个集合。

**静态反例（仅推导，未实机）：** `world_upper=401, n9=16, rem=4` 时，`C(16,4)=1820`，完整世界约 `401×1820=730,420`，`log2_total_est≈19.48<20`，可过成本门；旧估算若候选数为 100 仅约 `401×100×2304≈92MB`，而同一公式的完整规模约 `730420×100×2304≈168GB`。该例用于证明口径不一致，不声称实测 RSS。

**【改进点】修复：** 先算完整世界上界，再以饱和转换传给 `estimate_mem_impl`；`py_win_rate` 物化循环还需按同一预算设置中途硬停止，不能只靠 `node_cap` 限制递归 memo。

```cpp
// BAD：mem 只看组解乘积，use_win_rate 却看完整规模 log2_total_est
const auto mem = estimate_mem_impl(world_upper, candidates, mem_budget);

// GOOD：log2_total_est 计算完成后统一口径；指数转整数必须饱和
const int64_t full_world_upper =
    log2_total_est >= 63.0 ? INT64_MAX
                           : static_cast<int64_t>(std::ceil(std::exp2(log2_total_est)));
const auto mem = estimate_mem_impl(full_world_upper, candidates, mem_budget);
const bool use_win_rate = log2_total_est <= COST_CAP_LOG2 &&
                          mem.allowed && world_upper > DSTAR;
// py_win_rate 的 cell_value_list 增长处也按 full_world_upper/mem_budget fail-closed。
```

**验证建议（未执行）：** 用上述三元素以上参数组做注入必红测试：修复前旧门放行、修复后在任何大分配前拒绝；同时验证小规模合法世界仍精确枚举，不采用采样。

### 4. `transitions_for` 原生入口未验证形状与尺寸，负尺寸可形成巨大越界读

**分级：** P1 / HIGH  
**类别：** CWE-125 Out-of-bounds Read / CWE-20 Improper Input Validation；OWASP A03（原生输入边界）  
**位置：** `cpp/mscore.cpp:3971-3976`；下游同样缺独立校验 `cpp/mscore.cpp:3745-3764`  
**可利用性：** 本地 Python 调用者向 pyd 直接传畸形数组/尺寸，或配置被篡改；不是远程攻击面。正常 GUI 当前传入合法数组，利用条件因此评为 HIGH 而非 CRITICAL。  
**影响范围：** 原生越界读、访问冲突、潜在进程内存安全问题；`utils/native.py:155-157` 的 Python `except` 无法拦截硬访问冲突。

`py_transitions_for` 直接按 `(h+2)*(w+2)` 计算 `ncell` 并拷贝 `raw`，没有 `decide_impl` 那样的正数及 shape 检查。比如负 `h` 可使乘积为负，再转成巨大 `size_t`；正但失配的 shape 也会越界读。

**【改进点】修复：** 所有公开 pybind 入口共享一个溢出安全的校验器，在任何 `request().ptr`、坐标索引、位集初始化之前调用；`parse_coords` 也应接收 w/h 并检查 `1<=x<=w && 1<=y<=h`。

```cpp
// BAD
const int32_t* raw = static_cast<const int32_t*>(cell_value.request().ptr);
const size_t ncell = (size_t)(h + 2) * (w + 2);
std::vector<int32_t> cv(raw, raw + ncell);

// GOOD：decide/transitions_for/part_solve 等入口统一调用
static void validate_board_shape(
    const py::array_t<int32_t, py::array::c_style | py::array::forcecast>& b,
    int w, int h) {
    if (w <= 0 || h <= 0)
        throw std::invalid_argument("w/h must be positive");
    const int64_t rows = static_cast<int64_t>(h) + 2;
    const int64_t cols = static_cast<int64_t>(w) + 2;
    const int64_t cells = rows * cols;  // 先做受控 int64 运算，再转 size_t
    if (cells <= 0 || cells > PACK_MAX_CELLS)
        throw std::invalid_argument("board exceeds native capacity");
    if (b.ndim() != 2 || b.shape(0) != rows || b.shape(1) != cols)
        throw std::invalid_argument("cell_value shape mismatch");
}
// transitions_for：validate_board_shape(cell_value, w, h); 然后才取 data()/request().ptr。
```

**验证建议（未执行）：** 直接 pyd 输入至少三组：负 h、shape 失配、超容量；三组都必须在读取指针前抛 `ValueError`/原生转换异常，进程不崩溃。合法 30×16 用例作阳性对照。

### 5. 配置允许 0/10000 及非原子落盘，可在 GUI 阶段制造超预算分配或损坏配置

**分级：** P1 / HIGH  
**类别：** CWE-20 / CWE-400 / CWE-662（非原子配置更新）；OWASP A05/A08  
**位置：** `main.py:2344-2360`（w/h/a 均允许 0..10000）、`main.py:1169-1182` 与 `main.py:1216-1234`（按配置创建按钮）、`setting.py:68-74`（加载无 schema 校验）、`main.py:2458-2472`（直接覆盖写）  
**可利用性：** 普通设置界面即可输入，或手工改 `cfg.json`；无需恶意远程输入。  
**影响范围：** 例如 10000×10000 会在进入 C++ 前尝试创建一亿个 Qt 按钮，远超 1.5GB；0 尺寸/非法雷数导致原生拒绝或不可预测降级；写入中途退出可留下截断 JSON，使应用下次无法启动。

**【改进点】修复：** 在 `setting.load_cfg()` 做唯一、集中的 schema 与范围校验，UI 使用同一约束；按当前实际支持的棋盘/native 容量拒绝超限，不调高内存。配置写临时文件并 `os.replace`。

```python
# BAD：getInt(..., 0, 10000, ...)；load_cfg 只 json.load；open(CFG_PATH, "w") 直接覆盖

# GOOD（setting.py：UI 与手工编辑都必须经过这里）
def validate_cfg(c):
    w, h, a = c["w"], c["h"], c["a"]
    if not (1 <= w <= 30 and 1 <= h <= 16):
        raise ValueError("棋盘尺寸超出当前原生内核支持范围")
    if not (0 <= a < w * h):
        raise ValueError("总雷数必须在 [0, w*h) 内")
    if c["cell_width"] <= 0:
        raise ValueError("cell_width 必须为正")
    return c

def load_cfg(path=None):
    with open(path or CFG_PATH, encoding="utf-8") as f:
        return validate_cfg(json.load(f))

# GOOD（保存侧；getInt 同步使用相同上下限）
tmp = setting.CFG_PATH + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(validate_cfg(self.cfg), f, cls=MyEncoder)
os.replace(tmp, setting.CFG_PATH)
```

**验证建议（未执行）：** 10000×10000、0 宽、a>=w*h 三个以上非法输入必须 fail-closed；合法 30×16 作阳性对照；模拟写临时文件后异常，旧配置必须仍可解析。

## P2 / Medium 问题

### 6. 非 MSVC 分支的内建函数封装自我递归，替代编译链会栈耗尽

**分级：** P2 / MEDIUM  
**类别：** CWE-674 Uncontrolled Recursion；OWASP A05  
**位置：** `cpp/mscore.cpp:59-62`  
**可利用性：** 仅在 GCC/Clang 构建；当前文档指定 MSVC，故不是当前 MSVC 生产二进制漏洞。  
**影响范围：** 替代构建一旦调用位扫描/计数函数即无限递归直至栈耗尽，原生进程拒绝服务。

**【改进点】修复：** 非 MSVC 分支调用真正的编译器内建，而非同名自身。

```cpp
// BAD
static inline int msw_ctzll(unsigned long long x) { return msw_ctzll(x); }
static inline int msw_popcountll(unsigned long long x) { return msw_popcountll(x); }

// GOOD
static inline int msw_ctzll(unsigned long long x) {
    return x ? __builtin_ctzll(x) : 64;  // 调用方正常只在 x!=0 时使用
}
static inline int msw_popcountll(unsigned long long x) {
    return __builtin_popcountll(x);
}
```

## P2 / Low 问题

### 7. 默认配置含本机绝对安装路径，分享仓库/诊断包时泄漏本机目录信息

**分级：** P2 / LOW  
**类别：** CWE-200 Information Exposure（轻微）；OWASP A05  
**位置：** `cfg.json:1`  
**可利用性：** 仅在配置、仓库或诊断包被分享时；本地运行无直接攻击收益。  
**影响范围：** 暴露扫雷程序安装目录 `D:/Arbiter_0.52.3/ms_arbiter.exe`；**不是密码、token 或密钥**。

**【改进点】修复：** 仓库只保留脱敏 `cfg.example.json`，真实 `cfg.json` 加 `.gitignore`；路径由用户在本机设置界面选择，错误日志不要额外回显完整目录。

```python
# GOOD：首次启动没有本机配置时，不提交真实绝对路径
DEFAULT_CFG = {"path": "", "win_name": "Minesweeper Arbiter "}
# get_ms_path() 仍由本机用户显式选择 exe；subprocess.Popen 不启用 shell=True。
```

## 已核查但未列为漏洞的重点

- `py_split_frontier` 的 `at()` lambda：在 `rows/cols>=3` 与内层循环约束下，当前索引不越界；其问题不是 lambda 本身，而是其它公开入口缺共享校验。
- `world_upper` 的 `INT64_MAX` 饱和乘法与 `estimate_mem_impl` 的 `long double` 饱和计算本身能防整数回绕成“放行”；缺陷是送入估算的世界数口径不完整。
- `total` / `total_w` / `denom` 主要除零点已有显式检查；`wr_f` 的空世界检查位置虽晚于 `hashes[0]`，但当前生产分组只产生非空组，未把不可达形态上报为漏洞。
- `rs3`…`rs8` 是 `thread_local` xorshift 决策随机源，不用于认证、令牌或安全目的；不报告“弱随机”。固定种子也只影响决策可复现性。
- `catch (const std::exception&)` 的 OPTIMAL/transition 路径均转为降级或失败结果，未发现捕获后继续使用已失效中间状态的可达证据。
- `subprocess.Popen(mine_dir)` 位于 `utils/mm0.py:31`，未启用 `shell=True`，路径来自本机用户选择 exe，属于桌面工具既定功能；未报命令注入。
- 未发现 `pickle` / `eval` / `exec` / `os.system` 的生产使用；`json` 不执行反序列化代码。截图文件名来自固定下拉列表，未报路径穿越。
- `.pyd` 使用包内相对导入；若攻击者已能写项目目录，则已具同用户代码执行能力，未把该同权限情形夸大为新路径劫持漏洞。发布包仍应校验哈希。
- MD5 在 `cpp/mscore.cpp` 用于 memo 兼容/摘要，不是口令哈希，按误报规则排除。

## OWASP Top 10 适用性清单

| 类别 | 结论 |
|---|---|
| A01 访问控制 | 不适用：无 API 路由、账号、CORS、上传或网络授权面；本地 OS 用户即操作者。 |
| A02 加密失败 | 未发现密钥/口令处理；MD5 仅 memo，不作安全用途。 |
| A03 注入 | 无 SQL/NoSQL/ shell 拼接 / `eval` / `pickle`；发现原生输入校验缺陷，见 P1-4。 |
| A04 不安全设计 | P0-1、P1-2、P1-3：固定栈数组与资源闸设计未覆盖真实枚举规模。 |
| A05 安全配置错误 | P1-4、P1-5、P2-6、P2-7；无 Web debug/CSP 适用面。 |
| A06 易受攻击组件 | **未完整审计**：npm 空项目 0；pip-audit skipped，不能宣称无 HIGH/CRITICAL CVE。 |
| A07 认证失败 | 不适用：无登录、会话、JWT、密码。 |
| A08 完整性失败 | 本地配置非原子写入见 P1-5；发布 pyd 建议哈希校验，但同用户可写目录不构成新增权限边界。 |
| A09 日志失败 | 有滚动日志与决策 trace；未发现记录密码/密钥。无集中安全监控需求。 |
| A10 SSRF | 不适用：无出站 URL 请求或网络 fetch。 |

## 密钥扫描、误报与依赖审计留痕

- 跨源码/配置扫描 `api[_-]?key`、`password`、`secret`、`token` 及常见 provider token 形态：未发现真实凭据。
- `git log -p --all -- '*.py' '*.cpp' '*.json' '*.txt'`：只出现普通英文单词/变量别名类文本，没有密钥形态证据。
- 已核实误报：文档中的工具描述、`sim/minesweeper_sim.py` 的 `secret` 字段别名（且 sim 不在覆盖分母）、Git 历史普通英文单词；均不按泄漏上报。
- `npm audit --package-lock-only --omit=dev`：输出 `found 0 vulnerabilities`；但 `package.json` 为 `{}`，覆盖价值有限。
- `python -m pip_audit --version`：失败，`No module named pip_audit`。**item 级 skipped**：Python 依赖 CVE 未审计；原因=工具未安装且角色禁止安装。复跑命令：`python -m pip_audit -r requirements.txt`。未把此项计为通过。
- ASan/UBSan、编译与 GUI 动态验证：**item 级 skipped**，原因=只读评审且不得生成构建/运行产物。复跑方式：对每条发现使用报告中的注入用例，在独立构建目录启用 ASan/UBSan；不得用本报告静态审阅替代动态内存验证。

## 安全清单

- [x] 无硬编码密钥（工作树与 Git 历史只读扫描；命中误报已说明）
- [ ] 输入已验证（P1-4 原生 shape、P1-5 配置范围未满足）
- [ ] 注入/内存危险模式已验证（无命令/SQL 注入；发现 P0 栈越界）
- [x] 认证/授权适用性已评估（桌面无认证面）
- [ ] 依赖已审计（npm 空依赖完成；pip-audit skipped，见复跑命令）
- [x] 全部适用 OWASP 类别已按本地威胁模型评估
- [x] `decide` 契约已核对：C++ `make_response` 产出 `reason/x/y/confidence/world_upper/prob/tied/transitions/transition_meta`，`py_decide` 补 `limitation/cost_log2`；Python 按这些字段消费，未发现拼写型字段错配

## 【改进点】

1. **立即（P0）**：`cpp/mscore.cpp` 两处 `[64][10]` 改动态二维数组；补 65 并列候选双路径 ASan 负向测试。
2. **24 小时至 1 周（P1）**：把 `node_cap/solution_cap` 贯通到生产 `py_part_solve`，解物化前检查；以 `log2_total_est` 对应的完整世界数重做内存门，并在 `py_win_rate` 物化中途执行同一预算。
3. **1 周（P1）**：为全部 pyd 公开入口加统一正数/shape/容量/坐标校验；为 `transitions_for` 加负尺寸、失配、超容量注入必红用例。
4. **1 周（P1）**：`setting.load_cfg()` 集中校验，UI 范围复用；限制实际支持盘面，配置原子替换，杜绝在 Qt 阶段创建超预算控件。
5. **1 月（P2）**：修正非 MSVC 内建封装；脱敏默认绝对路径。
6. **审计补完**：在允许安装审计工具的环境运行 `python -m pip_audit -r requirements.txt`，将结果按 CVE 与可达性补入本报告；不得将本次 skipped 视为无漏洞。

## 覆盖账（21/21 reviewed）

| 文件 | 状态 | 安全面结果 |
|---|---|---|
| `cpp/mscore.cpp` | reviewed | P0 栈越界；P1 预检顺序/内存口径/原生 shape；P2 编译器递归 |
| `cpp/ab_native.cpp` | reviewed | 位集容量与雷坐标有检查；主生产风险来自所调 `decide_impl` |
| `utils/probability.py` | reviewed | decide 契约、异常降级、组合/除零、随机用途已核；内存问题在 C++ 侧 |
| `utils/solver.py` | reviewed | 配置消费、线程与统计文件；配置范围问题联合 main/setting 上报 |
| `utils/vision.py` | reviewed | UNKNOWN、模板/截图输入、批量索引；无危险反序列化 |
| `utils/deduction.py` | reviewed | 邻域边界与点击路径；无新增安全发现 |
| `utils/native.py` | reviewed | pyd 相对导入与异常回退；Python except 不能替代原生校验 |
| `utils/decide_trace.py` | reviewed | 白名单摘要、20MB 停止追加；无密钥记录 |
| `utils/analyze_decide_trace.py` | reviewed | JSONL 本地解析；无代码执行 |
| `utils/combinatorics.py` | reviewed | 组合比值/生成器数值边界；未发现独立新漏洞 |
| `utils/mm0.py` | reviewed | `Popen` 无 shell；用户 exe 启动属既定桌面功能 |
| `utils/clicking.py` | reviewed | 坐标图遍历；无外部输入执行面 |
| `utils/misc.py` | reviewed | JSON 编码与日志输出；无密钥 |
| `utils/util.py` | reviewed | 仅 re-export，无独立危险逻辑 |
| `main.py` | reviewed | 设置输入、Qt 控件分配、配置写入、点击/预览数据流 |
| `setting.py` | reviewed | 路径解析与 JSON 加载；缺集中 schema 校验 |
| `logger.py` | reviewed | 滚动日志；无敏感凭据记录证据 |
| `ui/window.py` | reviewed | 手写布局/QSS；无输入执行面；未建议 pyuic 重生成 |
| `ui/preview_popup.py` | reviewed | 只展示真实 payload；无 HTML/脚本注入面 |
| `ui/edit_setting.py` | reviewed | Qt 控件定义；实际输入范围在 main.py 评价 |
| `ui/screenshot.py` | reviewed | 固定控件定义；文件写入逻辑在 main.py，固定名称无穿越 |

**文件级 skipped / blocked / waived：无。** 依赖审计与动态内存验证的 skipped 已单列，未混入文件通过项。  
**断言鉴别力自查：** 未改解析器、分类器、闸门或测试，R5 代码改动判据不适用；本报告每条内存/闸门发现均给出来源行与至少一个可区分修复前后的负向输入设计，P1-3 另给三参数静态反例。所有动态验证均明确标为未执行，不把静态推断写成 PASS。
