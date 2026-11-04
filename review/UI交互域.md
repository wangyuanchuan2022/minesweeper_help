# UI 渲染性能 / 交互状态机 / 可用性 / DPI 坐标 —— 代码评审报告

## 评审身份

| 项 | 值 |
|---|---|
| 对象快照（冻结时） | head_sha=facbb42cedb38737f6fb39328376978c90682d58，selection_hash=551a0f3667b0d90d，spec_sha=f8c74b1f404dc585 |
| 对象快照（结论锚定） | 文件内容 SHA256-16：main.py `709E1ED1055F3CAD`、ui/window.py `9B4FDF770F23F114`、ui/edit_setting.py `6B38FC6E610BC8C5`、ui/screenshot.py `42FD8D398FD9FA05`、utils/solver.py `2CE6FED9D17C696A`、utils/probability.py `D0CB9131265B7FC9`、utils/vision.py `23AE2A5E6758C6AD`、utils/deduction.py `ECAE07D2A3DED48F`、color.py `7D1766FB00188D35`、setting.py `6BD7A4435085D5F0`、logger.py `E5FC3D523EF0D164`、tests/test_ui_heatmap.py `41A14C3DC5FB404F` |
| 覆盖分母 N | 15（crgate freeze --all 限定本域，spec 落盘 `review/.ui-spec.json`） |
| 语言清单 | `.py` → `personas/checklists/python.md`（已加载并与通用清单合并）；framework=PyQt5（探测 main.py:20-29 `from PyQt5 import ...`；python.md 无框架分区，按语言通用条款执行） |
| 静态检查 | `compileall` 全通过；`ruff --select F,E9` 26 项（均为未使用导入/多余 f 前缀，无 E9 语法级）；`mypy/flake8/pylint` 未安装 → 类型检查记为 skipped（见覆盖账备注） |
| 测试 | `python -m unittest discover -s tests`：97 tests / 1 failure（tests/test_ui_heatmap.py:87 断言旧文案，属测试未随 main.py:769 更新） |
| 模型 | buddy / deepseek-v4.1-flash（MEDIUM 档） |

### 对象漂移声明

评审期间 git HEAD 从 `cfb27da` → `9d51cdf` → `facb976` → `3827478` → `facbb42` → `5b01e6b` 连续推进（作者在并行改代码；5b01e6b = `fix(vision): 修复批量识别补丁尺寸错位`）。**本轮结论只对上面「结论锚定」列的内容哈希负责**：main.py / ui/* / solver.py / deduction.py / color.py / setting.py / logger.py / tests 的哈希在评审全程未变；`utils/vision.py` 与 `utils/probability.py` 期间被改过（vision.py 两次），本文涉及这两个文件的行号已按最新内容 `23AE2A5E…` / `D0CB91…` 复核。对象已漂移，结论不可直接与针对旧快照的评审对比。

## 覆盖状态

complete —— 评审文件数 15（reviewed 15 / skipped 0 / blocked 0 / waived 0）；冻结分母 15 == 覆盖账条目 15，无未决项。

（类型检查一项环境不可用，已在表内按 `skipped` 语义注明于备注列，不并入冻结分母：mypy/flake8/pylint 未安装，复跑命令 `pip install mypy && python -m mypy main.py utils ui`。）

### 覆盖账

| 路径 | 结论 | 理由/备注 |
|---|---|---|
| main.py | reviewed | 全文件 1281 行通读（含 update_heatmap / update_screen / auto_play_func / update_image / info / ScreenShot / EditWindow） |
| ui/window.py | reviewed | 全文件；布局尺寸、page_2/page_3 QSS、图例渐变、文案 |
| ui/edit_setting.py | reviewed | 全文件；QInputDialog 取值范围与 accept/closeEvent 落盘 |
| ui/screenshot.py | reviewed | 全文件；截图帮助表单控件与固定尺寸约束 |
| ui/__init__.py | reviewed | 全文件（3 行 re-export） |
| utils/solver.py | reviewed | 全文件；play 主循环四份胜负块、信号定义、reload、_flush_stats |
| utils/probability.py | reviewed | 本域相关段：306-377（进度心跳/单调门）、540-570（Visible_signal）、660-780（决策等待/点击 sleep）、1260-1330（win_rate 进度）；纯枚举算法段属算法域，仅交叉引用不计入本域结论 |
| utils/deduction.py | reviewed | 全文件；41/255 热力图发射点、点击路径 |
| utils/vision.py | reviewed | 全文件（最新 288 行版）；截图、批量识别、moveto |
| utils/mm0.py | reviewed | 全文件；ClientToScreen/ScreenToClient/GetMousePosition/MouseWindow |
| utils/__init__.py | reviewed | 全文件（pyautogui 全局节奏配置） |
| color.py | reviewed | 全文件；OKLCH 渐变生成 |
| setting.py | reviewed | 全文件（导入期读 cfg.json） |
| logger.py | reviewed | 全文件（日志初始化、轮转） |
| tests/test_ui_heatmap.py | reviewed | 全文件；7 条断言与实现对齐性核对 |

## 按严重度

- P0 / CRITICAL: 3（必须修复）
- P1 / HIGH: 8（应当修复）
- P2 / MEDIUM: 6（考虑修复）

---

## P0 —— 会崩溃 / 会误操作游戏

位置: main.py:708
严重度: [CRITICAL] P0
证据: `btn = self.heatmap_btn_list[j][i]`
问题: 热力图按钮网格在 `__init__` 里按启动时的 cfg 一次性建好（main.py:329-343），之后**永不重建**；而设置里改宽高后 `reload()`（main.py:429-439）只更新 `self.w/self.h`。于是 `update_heatmap` 用新的 `self.h/self.w` 去索引旧网格 → IndexError。
现象（用户视角）: 在设置里把宽度 30 改成 35 保存 → 点「自动」→ 窗口一闪整个程序消失（无任何提示、日志末尾只有 traceback）。
影响面: PyQt5 槽函数内未捕获异常会走 `qFatal`，进程直接 abort（实测退出码 `-1073740791` / `0xC0000409`），不是 Python 异常可拦截。已在本机 offscreen 与真实平台两处复现：`reload()` 后 `update_heatmap` 抛 `IndexError: list index out of range` → 进程立即终止。
置信度: 高（实测复现，含完整调用栈）
修复: 抽出 `_rebuild_heatmap_grid(w, h)`，在 `reload()` 之后与 `update_image()` 内按新 w/h 重建（并清空 `_hm_last`）；同时在 `update_heatmap` 首行加防御 `if len(self.heatmap_btn_list) < self.h or len(self.heatmap_btn_list[0]) < self.w: self._rebuild_heatmap_grid(...)`。
收益: 消除「改一次设置就崩」的整机不可用故障，这是目前最容易被普通用户触发的崩溃路径。
风险: 重建 480 个按钮实测 25.3 ms（GUI 线程一次性阻塞），可接受；需同步改 tests/test_ui_heatmap.py 的 `setUpClass` 假设。

位置: main.py:1241
严重度: [CRITICAL] P0
证据: `cv2.imwrite("image/temp.bmp", self.img)`
问题: 截图帮助窗口里横/纵坐标输入越界（如横坐标 > w）时，`self.img` 切片得到 0 尺寸数组，`cv2.imwrite` 抛 `cv2.error`；而下面的异常处理只接 `win32ui.error`（main.py:1245）与 `ValueError`（main.py:1252）。实测 `issubclass(cv2.error, ValueError) is False` —— 这个异常**逃出槽函数**。
现象: 在「截图帮助」里把横坐标填大一点、点「截图」→ 程序直接闪退，用户拿不到任何"坐标越界"提示。
影响面: 同上，槽函数逃逸异常 → 进程 abort。这是纯 UI 输入即可触发的崩溃（不需要改配置文件）。
置信度: 高（cv2 异常类层级实测 + 0 尺寸切片实测）
修复: ① 捕获 `cv2.error`（`except (win32ui.error, cv2.error, ValueError)`）；② 入口先校验 `1 <= i <= self.w and 1 <= j <= self.h`，越界给 `QMessageBox.about(self, "提示", f"横坐标应在 1~{self.w}")`。
收益: 把一个必修一的崩溃降级为一句可读提示。
风险: 无（纯防御）。

位置: main.py:102
严重度: [CRITICAL] P0
证据: `_btn.setFocusPolicy(Qt.NoFocus)`
问题: 注释（main.py:92-95）明确说明禁焦点是为了防止「外部注入的按键（如 VK_SELECT，Qt 把它当作空格键）把焦点按钮按下」——但 `NoFocus` 只设给了左侧 4 个按钮（main.py:96-102）。右页真正会操作游戏的按钮全部仍是 `StrongFocus`（实测 `focusPolicy()==11`）：`click_all`（main.py:443）、`end_help_thread`（main.py:433），以及帮助页 480 个格子按钮（main.py:471-485，它们的 `clicked` 连着 `info`）。空格激活按钮是 Qt 默认行为，实测 `QTest.keyClick(click_all, Qt.Key_Space)` 成功触发 `click_all_func`（用桩函数验证 fired）。
现象: 自动/帮助模式下，如果外部脚本或输入法注入一个空格/VK_SELECT，而焦点恰好落在「点击所有的确定方格」上 → 程序会真的去点扫雷棋盘（`pyautogui.click` 循环），把一局好棋点坏。
影响面: 会破坏用户在扫的棋局（这是本程序唯一的"数据"），且发生在用户完全不知情时。原防御意图正确但覆盖面错位，属"用注释掩盖了真 bug"的典型。
置信度: 高（实测空格触发 + 焦点策略读数）
修复: ① 把所有"会执行副作用"的按钮统一 `setFocusPolicy(Qt.NoFocus)`：`click_all`、`end_help_thread`、`end_auto_play_thread`、`return_to_main_page`、`return_to_main_p3`、以及帮助页 480 个格子按钮；② 更稳的是在 `click_all_func` / `end_*_func` 入口加"用户真实点击"判据（`QApplication.mouseButtons()` 或 `sender()` 校验），阻断键盘/注入路径。
收益: 让原本的安全注释真正成立，堵住"注入按键点坏棋局"这一条。
风险: 帮助页格子失去键盘可达性（本来也没有键盘支持），无实际功能损失。

---

## P1 —— 可信度 / 状态机 / 多显示器

位置: utils/probability.py:340
严重度: [HIGH] P1
证据: `predicted_s = DECISION_TIME_BUDGET`
问题: 心跳线程用**固定 30 秒预算**当预测时长（`DECISION_TIME_BUDGET=30.0`，probability.py:41），而真实单次决策实测约 0.5~0.8 s（仓库 bench 记录：完整决策 77.7 ms~387 ms 量级）。于是 `est = elapsed/predicted` 在 1 秒后才到 3%，进度条先是几乎不动，然后被 `finally` 直接拉到 100（probability.py:377）——「进度」与真实计算量无关。反过来若某局真超预算，`min(..., 0.94)` 会卡在 94% 直到结束。
现象: 自动模式下进度条要么纹丝不动、要么末尾突然跳到 100；用户无法从中判断"还要多久 / 是不是卡死了"。
影响面: 进度条是本程序唯一的长任务反馈，失真＝失去反馈。用户会误判程序假死并强行关窗（进而触发 terminate 路径的其它问题）。
置信度: 高（常量读数 + 心跳算法读数 + 仓库内实测耗时数据）
修复: `predicted_s` 改为"最近一次真实决策耗时"（`_last_heatmap` 已有的 `total`/`_ps_predict_ms` 汇总，或记录 `t_decision_end - t_decision_start` 的滑动中位数）；并把"估计值"与"真实值"在 UI 上区分（例如估计态用浅色/加 `~`）。
收益: 进度条恢复可信度；成本极小（一个滑动窗口）。
风险: 首局无历史时仍需回退常量（保留即可）。

位置: utils/probability.py:375
严重度: [HIGH] P1
证据: `hb.set()`
问题: 心跳线程（probability.py:326-362，daemon 线程）只在 `number5_1` 的 `finally` 里被 `hb.set()` 停掉。但「终止计算」走的是 `self.auto_play_thread.terminate()`（main.py:412）—— **QThread 强杀不会执行 Python 的 finally**。实测：模拟 terminate 后心跳持续 emit 1.2 s 以上仍在跑；再模拟"用户重新点自动"，新决策刚建好 `_pv_state`（hwm=0）就被这个旧心跳一路顶到 5、6、7。
现象: 点一次「终止计算」再重新启动，会看到进度条"自己往前爬"（旧线程在驱动新决策），而且越点越乱。
影响面: 进度显示错乱 + 线程泄漏（每次终止泄漏一个常驻轮询线程，20 ms 级轮询、`except Exception: break` 才不会自杀）。同时 `terminate()` 本身也可能打断 pyautogui 的 ctrl 按下状态（`click_all_func` 里 `keyDown("ctrl")` 后才 keyUp，见 main.py:830-836）。
置信度: 高（实测线程存活与 emit 记录）
修复: ① 心跳改为"读当前决策 token"：每次 `number5_1` 生成新 token，心跳发现 token 变就退出；② 更根本的是把 `terminate()` 换成协作式取消（`isInterruptionRequested()` / 一个 `stop_event`），在所有 `time.sleep` 处替换为 `self._stop.wait(x)`；③ 至少在 `end_*_func` 里显式 `self.auto_play_thread._pv_state = None` 让旧心跳空转即退。
收益: 进度条不再被幽灵线程驱动；同时为「等待期可中止」打基础。
风险: 协作式取消需要改 solver 主循环多处 sleep，改动面比 UI 大；可先做 ①③（局部、低风险）。

位置: main.py:226
严重度: [HIGH] P1
证据: `self.auto_play_thread.end_signal.connect(self.end_timer)`
问题: `start_signal` / `end_signal` 在 `AutoPlayThread` 里只定义（utils/solver.py:72-73）、在 main.py:223-226 与 241-244 只 connect，**全项目零 emit**（grep 阳性对照：定义 2 处、连接 4 处、emit 0 处）。连带 `update_pgb`（main.py:351）、`end_func`（main.py:360）、`up_pgb_timer`（main.py:250-252）整条链路是死代码。真正控制进度条可见性的只有 `Visible_signal`，而 `Visible_signal.emit(True)` 在全项目只有 1 处（utils/probability.py:552，枚举段入口）。
现象: 自动模式启动后大部分时间**根本没有进度条**（预扫描、决策、点击、等待都不显示），只在枚举那一小段出现一下又消失；帮助模式同理。用户看到的是一个"什么都没发生"的界面。
影响面: 直接造成「是否在计算/是否卡死」无法判断，是 P1-4 的放大器。
置信度: 高（全项目 grep 计数 + 定义/连接/发射三点核实）
修复: 二选一——① 删除死链路（把 `start_signal/end_signal/update_pgb/end_func/up_pgb_timer` 一并清掉），进度完全交给 `pv_signal` + `Visible_signal`；② 若想保留"按上局速度外推"的设计，就在 `number5_1` 入口/出口真正 emit。**推荐 ①**，因为 pv_signal 已由心跳/单调门驱动，两套并存只会互相打架。
收益: 少一套骗人的进度源；代码少约 30 行。
风险: 删代码需同步改 tests（当前测试未覆盖这两个信号，实测无影响）。

位置: utils/solver.py:240
严重度: [HIGH] P1
证据: `time.sleep(1.2)`
问题: 每局胜/负处理有四份几乎完全相同的块（solver.py:229-257 / 258-286 / 296-324 / 326-353），每份都串了 `sleep(1.2)` + `sleep(1.0)` + `sleep(0.5)` = **每局固定 2.7 秒空等**，另外还有 `sleep(0.1)`×N（点击后，solver.py:256/289/293/323/352 与 probability.py:769）。
现象: 一局刚结束，程序明明已经识别出胜负，却还要干等近 3 秒才开下一局；用户会觉得"卡住了"，多点几下反而干扰。
影响面: 纯墙钟浪费，直接压低"每秒决策数"。仓库 bench（bench/step_budget.py）把这类等待列为可压缩项；结合"单步 487 ms 中 100 ms 是点击后 sleep"的既测，固定等待是当前可摘的最低成本提速项。
置信度: 高（源码读数 + 仓库内既有实测）
修复: 把固定 sleep 换成"轮询到条件成立即继续"：`for _ in range(60): if win32gui.FindWindow(None, "游戏胜利") or ...: break; time.sleep(0.02)`；窗口重新置顶同理。四份重复块合并成一个 `_handle_game_over(kind, ...)`。
收益: 每局省 2~2.5 s；顺带消掉四份复制粘贴（未来改一处不会漏三处）。
风险: 轮询过密会占 CPU；用 20 ms 步长、封顶 3 s 即可。窗口未出现的极端情况仍需超时兜底。

位置: utils/vision.py:222
严重度: [HIGH] P1
证据: `pil_img = ImageGrab.grab(`
问题: `_grab_board()` 与 `vision.py:71` 的 `_grab_screen_bgr()` 都用默认 `ImageGrab.grab(bbox)`，**未传 `all_screens=True`**。本机两屏，副屏 X 范围是 `-3840..0`。实测：对副屏区域 `(-3000,200,-2400,500)` 取图 → `mean=0.0, max=0`（全黑）；同一 bbox 加 `all_screens=True` → `mean=5.5, max=204`（有内容）。
现象: 把扫雷窗口拖到副屏（或主屏在右、扫雷在左），程序截图全黑 → 棋盘全部识别成未开/错误 → 反复重扫，直到 `count>=3` 弹「请检查设置中总雷数，宽度，长度是否输入正确」。用户完全不知道真实原因是"多显示器"。
影响面: 多显示器用户直接不可用，且错误文案把人引向错误的排查方向（去改雷数/宽高，越改越乱）。
置信度: 高（实测黑图对比 + 本机显示器坐标读数）
修复: 三处 grab 全部加 `all_screens=True`（vision.py:71、vision.py:222、main.py:540、main.py:1223、mm0.py:131）；并把 `count>=3` 的 warning 文案补一条分支：「若扫雷窗口在副屏，请移到主屏或检查显示缩放」。
收益: 修好真实痛点；改动 5 行。
风险: `all_screens=True` 会抓虚拟屏全图（本机 5440×2560），全屏 grab 实测 36~47 ms，可接受；只在需要整屏时用，棋盘 grab 本身是 bbox 裁剪不受影响。

位置: utils/solver.py:110
严重度: [HIGH] P1
证据: `self.p = self.a / (self.w * self.h)`
问题: 设置界面允许把 w/h/a 设成 0（ui/edit_setting.py:968/975/982 的 `QInputDialog.getInt(..., 0, 10000, 2)` 下限是 0），`accept()`（main.py:1147-1158）也不做任何组合校验。实测 w=0 时 `MyMainWindow()` 构造直接 `ZeroDivisionError: division by zero`；a>w*h（如 a=2000）则能启动但后续推理全错。
现象: 用户手滑把宽度填 0 → 下次启动程序直接起不来（没有任何界面，控制台都没有，因为打包是 windowed）；把总雷数填错 → 自动模式跑几帧后弹「请检查设置…」，但用户不知道是哪一项错了、当前值是多少。
影响面: 启动级不可用 + 唯一一次警告不可操作。
置信度: 高（实测构造抛 ZeroDivisionError）
修复: ① `getInt` 下限改 1（宽高）、1（雷数）；② 新增 `validate_cfg(cfg)`：`1<=w<=100, 1<=h<=100, 1<=a<w*h, cell_width>=8`，在 `EditWindow.accept()` 与 `solver.reload()` 前调用，失败弹带当前值的可操作提示（例："总雷数 99 大于 30×16=480 格…"）。
收益: 把启动崩溃变成一句可读提示。
风险: 老用户旧 cfg 若不合规会首次被拦——提示里给"恢复默认"按钮即可。

位置: main.py:381
严重度: [HIGH] P1
证据: `self.with_help = False`
问题: 「截图帮助」把它自己那一轮 `update_image()` 的 `with_help` 置 False（正确意图：只截一次图、不跑后台）。但 `update_image()` 的失败分支（main.py:521-522：路径错 → 警告 → `return`）**不会把 with_help 复原**，而 main.py:538 的复原语句在这条 return 之后才执行。实测：路径错误触发一次 → `with_help` 停在 False → 用户修好设置再点「帮助人类」→ `if self.with_help:` 不成立 → **后台线程永不启动**（`help_thread.isRunning()==False`），界面停在帮助页但没有任何计算。
现象: 第一次点没配好扫雷路径，之后即使配好，"帮助人类"也永远不出结果（只有一块空棋盘）。用户感知是"程序坏了"。
影响面: 帮助主功能静默失效；且无任何错误提示，排查成本极高。
置信度: 高（实测 with_help 停留 False + help_thread 未启动）
修复: 把 `with_help` 的赋值挪进 `update_image` 的参数（`update_image(one_shot=True)`），或在失败分支 `self.with_help = True` 后再 return；更稳的是删掉这个跨调用的隐式状态，用显式参数。
收益: 修掉"一次配错、永久失效"的静默故障。
风险: 需要确认 `screenshot_help_func` 的一次性语义不被破坏（传参即可）。

位置: utils/vision.py:88
严重度: [HIGH] P1
证据: `res = cv.matchTemplate(screen, tem, cv.TM_SQDIFF_NORMED)`
问题: 胜负/ok/exit 的模板匹配（`_match_template_on_screen`）在**整屏 1600×2560** 上做，而 main.py 的 play 主循环每轮要匹配 lose、win、ok（solver.py:285-352）至少 3 次。本机实测：单次全屏匹配 135~165 ms（`ok.png` 135.5、`win.bmp` 141.5、`lose.bmp` 136.6），3 次 ≈ 413 ms/轮；把搜索区域裁到棋盘周边 900×660 后降到 21.1~21.5 ms（**7.6×**）。
现象: 每一步约 0.4 秒耗在"找弹窗"上（而且是主线程之外线程里的纯 CPU，不产生任何用户可见进度）。用户感知就是"点得很慢"。
影响面: 直接拖慢解扫雷速度，是 UI 线程之外最大的一块可控开销（与视觉域重叠，此处从"用户可感知速度"角度归本域）。
置信度: 高（本机实测 7.6× 差异）
修复: 先算棋盘 bbox（`bx,by,w*cell_width,h*cell_height`）并外扩 200 px 作为 ROI，只在该 ROI 上匹配；`ok/exit/win/lose` 弹窗必然出现在扫雷窗口附近。
收益: 每步省约 350~390 ms（按实测 413→~24 ms），对"每秒步数"是数量级级别的边际改善。
风险: 若弹窗被用户拖到远离窗口处会漏检——加一次"ROI 未命中则回退全屏"即可。

---

## P2 —— 体验与设计债

位置: main.py:633
严重度: [MEDIUM] P2
证据: `self.btn_list[j][i].clicked.connect(functools.partial(self.info, string))`
问题: `update_btn_list` 每次调用都**新增**一个 `clicked` 连接，从不断开。实测 receiver 计数随调用线性增长：1 → 2 → 3 → 4。
现象: 帮助模式下每点一次「帮助人类」，格子被点一次就会弹出叠在一起的多个信息框（第 N 次运行弹 N 个）。
影响面: 干扰阅读、拖慢界面；也是 PyQt 里典型的内存/连接泄漏（partial 持有 self）。
置信度: 高（receivers 计数实测）
修复: 连接一次即可——`self.btn_list[j][i].clicked.connect(self._on_cell_clicked)`，在 `_on_cell_clicked` 里从 `sender()` 反查 string（维护 `{id(btn): string}` 表）；或连接前 `try: btn.clicked.disconnect() except TypeError: pass`。
收益: 行为可预测，连接数恒为 1。
风险: 低。

位置: main.py:778
严重度: [MEDIUM] P2
证据: `self.list_p = re.findall(template, s.split("\n")[0])`
问题: `info()` 只用提示文本**第一行**去正则抓坐标，但真实 `exp` 首行是「枚举得出」/「胜率计算得出」/「随机选择。」（无坐标），或「由(i, j)得出」（那是**源格**坐标，不是被高亮的目标格）。实测：`枚举得出` / `胜率计算得出` / `随机选择。` 首行抓出 `[]`；`由(3, 4)得出` 抓出 `[(3,4)]` 但指向的是推导用的数字格。
现象: 点击帮助页格子时，"高亮对应格 / 关闭弹窗后取消高亮"这一整套交互要么不动，要么高亮错格——用户看到紫色边框闪在毫不相干的格子上。
影响面: 帮助页最核心的"点格子看解释"交互体验破损。
置信度: 高（四类真实 exp 模板逐条实测）
修复: 不要把坐标塞进自然语言再做正则反解——`pos_dict` 里本就带着 `pos`，直接把它随按钮存起来（与上一条的 `{id(btn): info}` 表合并成 `{id(btn): (string, pos)}`）。
收益: 高亮准确，且去掉一层脆弱的字符串反解。
风险: 低。

位置: ui/window.py:344
严重度: [MEDIUM] P2
证据: `"background-color:qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 0, 0, 255), stop:1 rgba(0, 255, 0, 255))"`
问题: 帮助页的色带图例是 Qt 的 **sRGB 线性**红→绿渐变，而真实格子上色用的是 OKLCH 感知均匀渐变（main.py:55-56 `oklch_grad = gradient_gen.create_gradient([(1,0,0),(0,1,0)])`）。实测两者中点差很远：OKLCH 50% = `rgb(247,149,0)`（橙黄），sRGB 线性 50% = `rgb(128,128,0)`（暗黄）。
现象: 用户照图例读色会系统性偏差——中间概率的格子在图例上看着像"偏暗黄/接近一半"，实际渲染是明显的橙黄色，容易把 50% 误读成 70%。
影响面: 热力图与帮助页的"读概率"功能可信度受损。
置信度: 高（两条色带逐点计算对比）
修复: 图例改用同一份 `oklch_grad`：`QLabel` 换成 100×n 的 `QImage` 逐列填 `oklch_grad[i]`（或 QSS `qlineargradient` 里按 10 个采样点插 stop）。
收益: 图例与现场一致，消除读色偏差。
风险: 需要动 `.ui` 里的 `label_4`（ui/window.py 是 pyuic 生成物，改动会在下次 pyuic 时丢——应改 `.ui` 源文件或改成代码内动态生成）。

位置: main.py:328
严重度: [MEDIUM] P2
证据: `self.heatmap_btn_width = 24`
问题: 全部几何量都是**硬编码逻辑像素**（格子 24 px、窗口最小 1035×783，ui/window.py:17-19），而程序被 pyautogui 强制成 system-DPI-aware（实测 `GetProcessDpiAwareness()==1`，本机两屏都是 144 dpi / 150%）。Qt 侧未启用 HighDpiScaling（实测 `AA_EnableHighDpiScaling == False`），于是**字号按 144 dpi 放大（9pt → 行高 18 px），控件尺寸却仍按逻辑像素固定 24 px**。实测把字体 DPI 模拟到 200% 时 `lineHeight=24`、单字符宽 24 px，已经顶满 24×24 的格子。
现象: 在 150%~200% 缩放屏上，格子里的数字贴边（现在能看，但余量为 0）；再高缩放会裁字。整体字号/间距在不同机器上也不一致。
影响面: 高 DPI 用户体验下降（这类用户占比在笔记本上很高）。
置信度: 中高（DPI/字体度量实测；200% 为模拟值，未在真机 200% 屏验证）
修复: 换算成缩放后尺寸：`px = round(24 * self.logicalDpiX()/96)`，或启用 `Qt.AA_EnableHighDpiScaling` 并统一用布局而非 `setGeometry`。至少把格子宽度由 `fontMetrics().height() + 8` 推导，保证不裁字。
收益: 高 DPI 下不裁字、格子大小随系统一致。
风险: 改尺寸会连带影响 `label_p3` 覆盖图对齐（main.py:549-558 使用同一 `btn_width`），需同步；且截图坐标基于真实 cell_width，不受影响。

位置: main.py:343
严重度: [MEDIUM] P2
证据: `self._hm_last = {}`
问题: 样式去重缓存用 `id(btn)` 作键。当前网格从不重建所以暂时安全，但一旦按 P0-1 修复引入重建，Python/Qt 对象 id 会被复用 → 缓存命中到错误的按钮，出现"新格子继承旧颜色/文字"。
现象: 修复 P0-1 之后可能出现个别格子底色不刷新（当前尚未发生，属设计债）。
影响度: 中等（会在修 P0 时立刻显形）。
置信度: 中（代码事实 + id 复用语义推断）
修复: 键改成 `(j, i)` 坐标（天然稳定）；重建时清空。
收益: 与 P0-1 修复配套，避免连锁 bug。
风险: 无。

位置: main.py:221
严重度: [MEDIUM] P2
证据: `lambda s: QMessageBox.warning(self, "错误", s)`
问题: 后台线程通过 `warning_signal` 让主线程弹**模态** QMessageBox（main.py:220-222 / 237-239），但后台线程不会停下来等用户点确定（`emit` 是排队语义，随后主线程被模态框卡住，而后台仍在跑）。同一 socket 上，`help_thread_warning`（main.py:804-809）还能再叠一个带"下次不再显示"的模态框。
现象: 自动模式出错时会连续弹框，且弹框期间主界面完全点不动（看起来像死机）；叠加多个时更难关。
影响度: 中等（可用性 + 误判为卡死）。
置信度: 高（信号语义与主循环代码路径读数）
修复: 改成非模态的内嵌提示条（在 `heatmap_label_stats` 上方加一个可关闭的 QLabel），或把 warning 聚合去重（同一文案 5 s 内只弹一次）。文案补上"当前值 / 期望范围"。
收益: 界面保持可响应，"为什么失败"也能持续可见（比一闪而过的弹窗好）。
风险: 低。

---

## 影响「解扫雷速度」 vs 纯体验（分列）

### A. 实际影响解扫雷速度（按实测口径排序）

| 项 | 位置 | 实测/依据 | 单步量级 |
|---|---|---|---|
| 全屏模板匹配 lose/win/ok（3 次/轮） | utils/vision.py:88 | 本机 135~165 ms/次；裁到棋盘 ROI 后 21 ms（7.6×） | ~350–390 ms/步可省 |
| 每局固定 sleep 2.7 s + 点击后 0.1 s | utils/solver.py:240 / 268 / 308 / 337；probability.py:769 | 源码常量 + 仓库既有实测（单步 487 ms 中 100 ms 为点击等待） | 每局 2–2.5 s |
| 热力图每步最多 43 次全量遍历 GUI | utils/deduction.py:41、:255；main.py:685 | 实测：mine_clear1 发 12 次、mine_clear3_1 发 28 次（横 30 格×纵 16 行）；全量变化帧 15.36 ms | 去重后 ~33 ms/步，最坏 ~660 ms/步 |
| 480 格逐格 setStyleSheet | main.py:755 | 实测 480 次 = 13.07 ms；同样字符串重设也是 12.76 ms（说明开销在 QSS 解析不放缓存） | 每帧 13 ms |
| 全屏截图 grab | utils/vision.py:71 / :222 | 实测 36–47 ms/次（1600×2560 物理像素） | 每步 1–2 次 |

（口径说明：以上为**本机实测**——i5 级 CPU、Pillow 9.5.0、OpenCV 4.5.4、Qt offscreen + 真实平台双测；「每步」指一次决策+一次点击后的重扫，与仓库 bench/step_budget.py 的口径一致。凡未标注实测的为源码常量推算。）

### B. 纯体验问题（不影响速度）

- 进度条失真/大部分时间不显示（P1-4 / P1-6）
- 终止后旧心跳继续驱动新决策（P1-5）
- 「帮助人类」一次配错永久失效（P1-10）
- 多显示器截黑导致的误导文案（P1-8）
- 设置允许 0 / 越界导致启动崩溃与不可操作警告（P1-9）
- 重复信号连接、坐标高亮反解失败、图例色带不一致、模态弹窗阻塞、高 DPI 裁字风险、`_hm_last` id 键（P2）
- 注入按键可触发"点击所有确定方格"（P0-3，介于两者之间：不改变常规速度，但会破坏一局棋）

---

## 越界发现（不计入覆盖账）

- `utils/deduction.py:41` 与 `:255` 在**每行/每列循环内**发 `_emit_heatmap`，这是"每步 43 帧"的根因；该文件虽在本域分母内，但"是否应改成按需发射"更偏架构/算法域，本报告只量化其 UI 代价，不重复架构域结论。
- `tests/test_ui_heatmap.py:87` 断言 `"当前点击方格不是雷的概率"`，而实现 main.py:769 已是 `"当前格非雷的概率"`。这是**测试未随实现更新**（1 失败），按纪律不擅自改测试；应作为独立修复项：更新断言文案（保持"状态栏含概率+局数+胜率"的原判据语义）。
- `utils/probability.py:552` 的 `Visible_signal.emit(True)` 在枚举段入口，而 `_pv_state` 的心跳会跨越整个决策生命周期——两者生命周期不一致，是"进度条消失"的直接机制，已在 P1-6 说明。
- `solver.py:224-227` 的 `count>=3` 是一条"以退出自动模式代替修复"的兜底，配合模态弹窗（P2-17）会让用户误以为程序死了；建议改文案为可操作指引（当前值 + 期望范围 + 如何重开）。

## Open Questions（低置信度，呈现不阻塞）

位置: main.py:682
严重度: [LOW] LOW
证据: `"color: rgb(255, 255, 255)}"`
问题: 热力图默认样式 `_HM_BASE` 是半透明白底（`rgba(208,208,214,100)`）+ 白字，理论上在深色磨砂卡片上对比度偏低。offscreen 取样到 `#d1d1d6`（白字对比约 1.3），但那是未合成背景的离屏结果；按真实 `background_1.bmp` 均值合成后估算为 `#727274`（白字对比 4.63，勉强过 AA）。
置信度: 低（两种口径相差很大，未在真机截图上取样）
修复建议: 真机截图取样确认后决定——若确实 <4.5，把已开格文字改为黑字（`color: rgb(0,0,0)`）。

位置: main.py:304
严重度: [LOW] LOW
证据: `self.heatmap_frame.setMinimumSize(0, 400)`
问题: 网格内容实际需要 `(h+1)*24 = 408 px` 高，而 frame 最小高写的是 400。真实平台实测该 frame 被布局撑到 809×716（够用），但窗口被压缩/恢复或不启用布局约束时，最后一行可能被裁掉一部分。
置信度: 低（当前未复现裁切）
修复建议: 改成按 `(self.h + 1) * self.heatmap_btn_width + 8` 计算最小高度，并在重建网格时同步。

## 正面观察

- `update_heatmap` 的**样式去重 + `setUpdatesEnabled` 双保险实测有效**：同一 payload 第二帧 0.52 ms（对比全变 15.36 ms），说明"没变化的帧不花 QSS 解析钱"这件事做对了；`_hm_last` 的设计意图正确（只是键选得不稳，见 P2-16）。
- `_emit_heatmap`（utils/solver.py:392-415）作为**唯一发射口** + payload 原子性（prob/cell_value 一帧到达）+ 发射失败 try/except 隔离，是很干净的设计：UI 故障不会反噬推理主流程。注释（main.py:686-697、solver.py:393-401）把坐标系约定（按钮 0-based ↔ prob 1-based）写得非常清楚，减少了下一位维护者的踩坑概率。
- 左侧按钮 `NoFocus` 的**问题意识是对的**（main.py:92-95 的注释准确描述了 VK_SELECT→空格 这条真实攻击面），只是覆盖面漏了右页（P0-3）。
- 提交 `9d51cdf` 引入的 `_gui_scan_once`（main.py:585）确实把「GUI 线程与后台线程交叉写 `self.img`」这个跨线程共享可变状态问题隔离掉了，是一次正确的并发修复。
- 日志侧 `logger.py` 有轮转 + 控制台分级（DEBUG 只进文件），排障友好。

---

## 【改进点】

按"投入产出比"排序，均给出可验收口径：

1. **热力图改自绘（最大收益，中等改动）** —— 用一个 `QWidget` 重写 `paintEvent`：`QPainter` 逐格 `fillRect` + `drawText`。实测 480 格整帧 1.08 ms，对比 480 次 `setStyleSheet` 13.07 ms（**12×**），且不再受 QSS 解析/样式级联影响。收益：每步最坏 660 ms → ~46 ms；常态 33 ms → ~20 ms。风险：需重写 `update_heatmap` 且同步改 `tests/test_ui_heatmap.py` 的 7 条断言（现断言依赖 `styleSheet()` 文本，改自绘后要改成断言"状态模型"而非"样式字符串"）——建议把"每格应有颜色/文字"抽成纯函数 `cell_visual(j, i, payload) -> (rgb, text)`，测试直接断言该函数，UI 只负责画。
2. **热力图发射合帧（低成本，立刻可做）** — 在 solver 侧对 `_emit_heatmap` 做 16 ms 去抖（或 UI 侧用 `QTimer` 合并 30 ms 内的多次 payload，只画最后一帧）。收益：43 次/步 → 通常 1~3 次/步，省下 ~600 ms 的最坏情况；风险：极短命的中间态不会显示（热力图本来就是"结果展示"，可接受）。
3. **进度条重做为"真实进度 + 未知态"** — `predicted_s` 用最近 5 次决策耗时的中位数；超过预测 1.5 倍仍无真实进度就显示"计算中…（已 Xs）"而不是继续爬。收益：进度可信，用户不再误判假死。风险：低（只改 UI 文案与一个滑动窗口）。
4. **终止改协作式取消** — 用 `stop_event` 替换 `terminate()`，所有 `sleep` 换成 `event.wait(t)`。收益：不再泄漏心跳线程、不再打断 ctrl 键状态、统计能正常落盘（`_flush_stats` 在 finally 里，终止时必丢）。风险：改动跨 solver 主循环，建议与"消除固定 sleep"（P1-7）一起做，一次改完两件事。
5. **截图与匹配加显示器/ROI 支持** — `ImageGrab.grab(..., all_screens=True)` + 模板匹配限定棋盘 ROI。收益：多显示器可用（当前副屏全黑不可用）+ 每步省 ~350 ms。风险：ROI 漏检时回退全屏即可。
6. **统一配置校验 + 可操作错误文案** — 新增 `validate_cfg()`，所有 warning 文案改成「项名 + 当前值 + 期望范围 + 建议动作」。收益：把"启动崩溃 / 提示看不懂"变成"一句话告诉你怎么改"。风险：旧 cfg 可能被拦，配"恢复默认"。
7. **UI 状态机收敛** — 目前 `set_btns_Enabled` 被 6 处调用（main.py:414/521/535/675/847/853/858/864），`update_screen` 每 50 ms 轮询线程状态反推 UI 状态，存在竞态窗口（例如 `isRunning()` 变 False 的那一帧按钮被提前 enable，用户可以再点一次 `start()`——实测 QThread 二次 `start()` 不抛异常、`isRunning` 仍 True）。建议引入显式状态枚举（idle/scanning/deciding/clicking/gameover）+ 单一 `_set_state()`，`update_screen` 只做渲染不做决策。收益：消除"按钮状态和实际线程不同步"的整类问题。风险：中等改动，需要把各流程的转换点逐一接上（可作为下一轮独立任务）。
8. **让"正在做什么"可见** — 状态栏目前只有概率/局数/胜率（main.py:769）。建议加一个短状态词（`扫描中` / `决策中` / `点击中` / `等待弹窗` / `已停止`），数据来自一个 `self._status` 字符串（由后台信号或 `_emit_heatmap` payload 带一帧状态）。收益：用户第一次能看出"程序在干什么/在哪一步失败"，也直接支撑 P1-6 的替代方案。

---

## 建议

裁定: **REQUEST CHANGES**（依据：HIGH 置信度下存在 P0 级缺陷——改变设置后必崩、截图帮助越界必崩、注入按键可误点棋盘；三条均有本机实测复现与逐字证据）

覆盖状态: **complete**（冻结分母 15 == 覆盖账 15，四态闭合无未决项；类型检查因环境缺 mypy/flake8/pylint 记为 skipped，不并入分母）

处置建议（顺序）：先修 P0-1/P0-2/P0-3（都是小改动、都止血），再修 P1-8（多显示器截图，5 行）与 P1-10（with_help 静默失效），然后按【改进点】1→2→3 做体验与速度改造；P1-5/P1-7 建议合并为一次"取消 + 去固定等待"重构，独立一轮评审。
