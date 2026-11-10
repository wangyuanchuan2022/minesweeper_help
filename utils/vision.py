# -*- coding: utf-8 -*-
"""视觉识别：模板加载、屏幕/棋盘截图、格子比对与整盘扫描。

自 utils/util.py 拆分而来。``BoardVisionMixin`` 不单独使用，
由 ``utils.solver.Solver`` 混入，依赖实例属性：
``w``/``h``/``cell_width``/``screenshot_w``/``screenshot_h``/``img``/
``images``/``_images_cell_width``/``_images_mtime``/``_locate_templates``/
``_bx``/``_by``。

识别契约（见 review/fix-vision-contract.md）：

- ``load_img`` 加载后立即校验模板集：文件缺失、通道数 != 3、dtype != uint8、
  尺寸既不属于当前标定又不构成一致模板族 ⇒ 抛 ``TemplateValidationError``
  （带文件名与实测形状），不再把坏模板带进识别路径。
- ``compare_img`` / ``compare_cells_batch`` 在「没有可用模板」或「最优匹配
  分数超过 ``LOW_CONFIDENCE_THRESHOLD``」时返回 ``UNKNOWN``，而不是硬归到
  0~10 中的某个类别；阈值置 ``None`` 可恢复旧的「总是给一个类别」行为。
- ``complete_scan`` / ``rescan_after_click`` 不把 ``UNKNOWN`` 写进 ``cell_value``
  （棋盘编码 0..11 有意义）：少量不可识别格保持「未开(9)」并告警，不可识别
  比例超过 ``UNKNOWN_ABORT_RATIO`` 时抛 ``LowConfidenceScanError`` 显式中止。
"""
import os
import time

import numpy as np
import pyautogui
import win32gui
import win32ui
import win32con
from PIL import ImageGrab
import cv2 as cv

import setting
from logger import get_logger
from .mm0 import ClientToScreen
from . import e2e_probe as probe

logger = get_logger(__name__)

# 识别值编码（与 cell_value 的既有编码并存）：
#   0..8 = 已开数字；9 = 未开；10 = 推理标记雷；11 = 推理标记安全；
#   UNKNOWN = 本次无法给出可信类别（模板不可用/匹配分数超阈值）。
# UNKNOWN 刻意取负数，避免与任何既有编码（0..11）碰撞；调用方必须显式判断，
# 不得把它当数字写入棋盘。
UNKNOWN = -1

# TM_SQDIFF_NORMED 的“可信”阈值：分数 <= 该值才认为匹配成功。
# 依据 review/视觉域.md 的实测分离度：真实模板/真实数字 patch 最优分
# <= 0.0026，纯噪声 patch 最小分 >= 0.2519（中位 0.2965），白/灰纯色底
# 分别为 0.0848 / 0.0596。0.05 在两个分布之间留 ~20 倍余量。
# ⚠️ 该阈值为合成数据标定值，真机抗锯齿/主题差异下需重标（产品裁决点，
# 见 review/fix-vision-contract.md）；可通过改本常量整体调整，置 None 关闭护栏。
LOW_CONFIDENCE_THRESHOLD = 0.05

# 一轮扫描中无法识别的格子占比达到该比例 ⇒ 判定为模板集/标定整体失效。
# 单个/少量低置信度格子只告警并跳过（保持真机正常路径不受影响）。
#
# 2026-11-08 由 0.5 下调到 0.25（真实故障实测依据）：
#   抓图退化时（窗口未置顶/被遮挡/DPI 变化）识别器会把大片格子误认成
#   「数字 0」。黑屏/噪声实测：142/480 = **29.6%** 无法识别，而旧阈值 50%
#   使这类盘面**顺利通过**——错误识别结果被写入棋盘，num9 在相邻两次扫描
#   间剧烈跳变（真机日志实测 479→99→469→479），最终 decide 连续 6 次
#   返回无效坐标并报「决策不可用」。
#   取 0.25 的依据：退化盘实测 29.6% > 25%（能拦住）；正常对局局部失败
#   （模板轻微不匹配/局末弹窗边缘）通常远低于此，留 25% 余量。
UNKNOWN_ABORT_RATIO = 0.25

# 不可识别比例超阈值时是否抛 LowConfidenceScanError。
# 默认 False：调用链（complete_scan ← solver.play/help ← QThread.run）没有
# 通用异常捕获，未处理异常会让整个进程 abort（实测 exit -1073740791），
# 比"继续跑"更糟。默认行为 = logger.error + UI warning_signal 显式告警 +
# 不可信格保持未开(9)。置 True 可在自动化/测试环境把它升级为硬失败。
STRICT_LOW_CONFIDENCE = False


class TemplateValidationError(RuntimeError):
    """模板集不可用（文件缺失/损坏、通道数/dtype/尺寸异常）。"""


class LowConfidenceScanError(RuntimeError):
    """整盘识别失败比例过高，棋盘不可信，必须中止本轮识别。"""


def _is_low_confidence(score):
    """匹配分数是否不达置信阈值；阈值置 None 表示关闭护栏（旧行为）。"""
    return LOW_CONFIDENCE_THRESHOLD is not None and score > LOW_CONFIDENCE_THRESHOLD


def _normalized_score(res):
    """把 TM_SQDIFF_NORMED 响应图折算成「最优点分数」（= ``np.min(res)``）。

    ⚠️ 置信判据必须用**最优点分数**而不是 ``np.sum(res)``：
    (1) 域报告 review/视觉域.md:151 的阈值分离度就是按「min over 模板 的最优分」
        标定的；用总分会让阈值失去原有标定依据。
    (2) 模板比补丁小时响应图有多个偏移点，总分随偏移数线性放大，会让小模板
        （或标称尺寸与实际补丁差 1px 的路径）被系统性误判为低置信度。
    排序语义仍保持 ``np.sum(res)`` 不变（与生产既有行为逐位一致）。
    """
    res = np.asarray(res, dtype=np.float64)
    return float(np.min(res))


class BoardVisionMixin:
    # 每个格子数字对应的模板文件（顺序与 self.images 一致）
    _TEMPLATE_LAYOUT = [
        ["0.bmp", "0_1.bmp", "0_2.bmp"],
        ["1.bmp", "1_1.bmp", "1_2.bmp"],
        ["2.bmp", "2_1.bmp"],
        ["3.bmp", "3_1.bmp"],
        ["4.bmp", "4_1.bmp"],
        ["5.bmp", "5_1.bmp"],
        ["6.bmp", "6_1.bmp"],
        ["7.bmp", "7_1.bmp"],
        ["8.bmp", "8_1.bmp"],
        ["9.bmp", "9_1.bmp", "9_2.bmp"],
        ["10.bmp", "10_1.bmp"],
    ]

    def _template_mtime(self):
        """返回所有格子模板文件的最大修改时间，作为缓存指纹。

        缺少任何一个模板文件都返回 -1.0（让缓存失效），同时记录 **哪个** 文件
        缺失——具体失败由 ``load_img`` 的校验抛出，这里只负责缓存指纹。
        """
        try:
            return max(
                os.path.getmtime(os.path.join("image", fname))
                for row in self._TEMPLATE_LAYOUT
                for fname in row
            )
        except OSError as e:
            logger.debug("模板 mtime 读取失败（缓存失效）: %s", e)
            return -1.0

    def _validate_templates(self, images):
        """校验模板集可用性；不可用时抛 ``TemplateValidationError``（含文件名）。

        校验分两级：

        1. **致命**（抛 ``TemplateValidationError``）：读取失败/为 None、通道数
           != 3、dtype != uint8，或**没有任何**模板能用于当前补丁尺寸。
        2. **自动忽略**（不记日志）：模板比当前补丁大，属于另一套标定（本仓
           实际存在：``image/*_1.bmp`` 是 28×20 的 cw=36 标定集，在 cw=24 的
           生产补丁 18×12 下不可用）。这类模板由 ``_active_templates`` 按尺寸
           过滤，**不删文件、不致命、不输出日志**——用户明确要求「模板不能用
           的时候自动忽略」，此前每次 ``load_img`` 都灌一条 INFO 是日志垃圾的
           主要来源。直接 raise 会破坏既有生产初始化（``Solver.__init__`` →
           ``load_img``）。

        ``cell_width`` 从 24 切到 36 时补丁变为 (28,20)，大模板族自动转为可用。
        """
        patch_h = 2 * (self.screenshot_h // 2)
        patch_w = 2 * (self.screenshot_w // 2)
        problems = []
        unusable = []
        valid = 0
        for row_i, row in enumerate(self._TEMPLATE_LAYOUT):
            for col_i, fname in enumerate(row):
                img = images[row_i][col_i]
                path = os.path.join("image", fname)
                if img is None:
                    problems.append(f"{path}: 读取失败（文件缺失或不是有效的 BMP）")
                    continue
                if img.ndim != 3 or img.shape[2] != 3:
                    problems.append(f"{path}: 期望 3 通道 BGR，实测 shape={img.shape}")
                    continue
                if img.dtype != np.uint8:
                    problems.append(f"{path}: 期望 dtype=uint8，实测 {img.dtype}")
                    continue
                th, tw = img.shape[:2]
                if th > patch_h or tw > patch_w:
                    unusable.append(f"{path}({th}x{tw})")
                    continue
                valid += 1
        if problems:
            raise TemplateValidationError(
                "格子模板集不可用，拒绝启动识别：\n  " + "\n  ".join(problems)
                + "\n恢复指引：从版本库恢复 image/ 下的模板文件，或检查文件是否被"
                  "截图功能覆盖损坏（image/temp.bmp 写入路径）。")
        if valid == 0:
            raise TemplateValidationError(
                f"格子模板集对当前标定不可用：没有模板能匹配补丁 {patch_h}x{patch_w}"
                f"（cell_width={self.cell_width}）；不可用模板 "
                + "、".join(unusable)
                + "。恢复指引：核对 cfg.json 的 cell_width 与 image/ 下模板的标定"
                  "尺寸是否一致。")
        # 尺寸不匹配的模板静默忽略：unusable 已在 valid==0 的致命分支里作为
        # 错误详情使用，其余情况无需任何日志输出（用户裁决 2026-11-08）。

    def load_img(self):
        """加载格子模板；仅在 cell_width 变化或模板文件更新时重新读取。

        加载后立即校验（缺失/通道/dtype/尺寸），失败抛 ``TemplateValidationError``。
        校验通过才更新缓存字段，避免坏模板被缓存复用。
        """
        mtime = self._template_mtime()
        if (
            self.images is not None
            and self._images_cell_width == self.cell_width
            and self._images_mtime == mtime
        ):
            return
        images = [
            [cv.imread(os.path.join("image", fname)) for fname in row]
            for row in self._TEMPLATE_LAYOUT
        ]
        self._validate_templates(images)
        self.images = images
        self._images_cell_width = self.cell_width
        self._images_mtime = mtime

    @staticmethod
    def _window_rect():
        """返回 Arbiter 主窗口的**屏幕坐标**矩形 (l, t, r, b)，找不到返回 None。

        ⚠️ 必须与生产同款 DPI 口径：``utils/__init__.py`` 在导入 pyautogui 时
        触发 SetProcessDPIAware，之后 win32gui.GetWindowRect 拿到的是**物理像素**
        （本项目 1600x2560 屏上实测 765x600）。若在未 DPI-aware 的进程里调用，
        会拿到逻辑坐标 510x400 ⇒ 裁剪错位、模板命中失败（实测踩过）。
        """
        hwnd = win32gui.FindWindow(None, setting.win_name)
        if not hwnd:
            return None
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001 —— 窗口已销毁
            return None
        if r <= l or b <= t:
            return None
        return (int(l), int(t), int(r), int(b))

    @staticmethod
    def _grab_region_bgr(rect):
        """抓屏幕上一块矩形区域并转 BGR（屏幕 DC BitBlt，含独立弹窗）。

        用 **屏幕 DC** 而非窗口 DC：BitBlt 从桌面上取像素，所以覆盖在 Arbiter
        之上的「游戏胜利/失败」弹窗也会被抓到（这正是弹窗模板匹配需要的）。
        若用窗口 DC，弹窗是独立顶层窗口，抓不到 ⇒ 胜负判定会失效。

        实测（本项目本机）：屏幕 DC BitBlt 抓 765x600 窗口区域中位 6.3ms，
        PIL ImageGrab 整屏 44.6ms / 区域 38.7ms；两法逐像素一致
        （mean 差 0.002、max 1，仅 BGRA→BGR 的舍入）。
        """
        l, t, r, b = rect
        w, h = int(r - l), int(b - t)
        if w <= 0 or h <= 0:
            raise ValueError(f"invalid region: {rect}")
        hdc = win32gui.GetDC(0)
        srcdc = win32ui.CreateDCFromHandle(hdc)
        memdc = srcdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        try:
            bmp.CreateCompatibleBitmap(srcdc, w, h)
            memdc.SelectObject(bmp)
            memdc.BitBlt((0, 0), (w, h), srcdc, (int(l), int(t)),
                         win32con.SRCCOPY)
            arr = np.frombuffer(bmp.GetBitmapBits(True),
                                dtype=np.uint8).reshape(h, w, 4)
            return cv.cvtColor(arr, cv.COLOR_BGRA2BGR)
        finally:
            try:
                win32gui.DeleteObject(bmp.GetHandle())
            except Exception:  # noqa: BLE001
                pass
            try:
                memdc.DeleteDC()
                srcdc.DeleteDC()
            except Exception:  # noqa: BLE001
                pass
            win32gui.ReleaseDC(0, hdc)

    def _grab_dialog_screen(self, pad=0):
        """只抓 Arbiter 窗口矩形（含其上弹窗），返回 (img, origin_x, origin_y)。

        用于胜负/退出弹窗的模板匹配：把 2560x1600 整屏缩到 765x600 窗口矩形，
        ``cv.matchTemplate`` 的面积从 4.1M 降到 0.46M（本机实测整屏 135–616ms
        → 窗口 15–87ms，6.7–8.8×）。调用方必须把匹配到的局部坐标加上
        ``origin`` 才是可点击的屏幕坐标。

        ``pad``：向四周额外扩展的像素数。某些模板（如 158px 宽的 ok.png）
        比窗口左边界余量还宽，紧贴窗口边缘时会被裁掉一部分 ⇒ 命中点偏移
        （实测差 5px）。调用方可按需给少量 padding，仍远小于整屏。

        窗口找不到 / BitBlt 失败时**回退整屏**（origin=(0,0)），绝不因优化
        让胜负判定失效。
        """
        rect = self._window_rect()
        if rect is None:
            return self._grab_screen_bgr(), 0, 0
        try:
            if pad:
                rect = (rect[0] - pad, rect[1] - pad,
                        rect[2] + pad, rect[3] + pad)
            with probe.span("vision.grab_dialog_screen"):
                img = self._grab_region_bgr(rect)
            return img, rect[0], rect[1]
        except Exception as e:  # noqa: BLE001 —— 优化失败必须透明回退
            logger.debug("窗口区域抓屏失败，回退整屏：%s", e)
            probe.mark("vision.dialog_region_fallback", error=str(e)[:80])
            return self._grab_screen_bgr(), 0, 0

    @staticmethod
    def _grab_screen_bgr():
        """抓取整屏并转为 BGR。

        ⚠️ 2026-11-09 健壮性修复：`ImageGrab.grab()` 在 Windows 上会**间歇性**
        抛 `OSError: screen grab failed`（GDI 抓屏在锁屏切换、显卡驱动重置、
        远程会话重连、以及其它进程短暂占用桌面时都会失败）。原实现让它直接
        上抛 ⇒ `Solver.play()` 的 except 链只捕 FailSafe/win32ui.error ⇒
        **整条自动线程被 OSError 打死**，用户看到的就是「自动模式突然停了」。
        实测（本轮真实端到端测量）：连续两次运行分别在第一次抓屏就抛该错误。

        现在改成短重试（3 次、20ms 间隔）。抓屏是幂等只读操作，重试无语义风险；
        连续 3 次仍失败才上抛，让上层按既有契约处理（不静默吞掉）。
        """
        with probe.span("vision.grab_screen") as _on:
            last = None
            for attempt in range(3):
                try:
                    bg = ImageGrab.grab()
                    bg = np.array(bg)
                    return cv.cvtColor(bg, cv.COLOR_RGB2BGR)
                except OSError as e:
                    last = e
                    if _on and attempt:
                        probe.mark("vision.grab_screen_retry", attempt=attempt + 1,
                                   error=str(e)[:80])
                    time.sleep(0.02)
            raise last

    def _load_locate_template(self, f):
        """加载并缓存对话框模板（ok/exit/win/lose）。"""
        if f not in self._locate_templates:
            tem = cv.imread(f)
            if tem is None:
                raise FileNotFoundError(f"无法读取模板文件: {f}")
            self._locate_templates[f] = tem
        return self._locate_templates[f]

    def _match_template_on_screen(self, screen, f, origin=(0, 0)):
        """在已抓取的图上做模板匹配，返回 (最小 SQDIFF, 屏幕 x, 屏幕 y)。

        ``origin``：该图左上角对应的**屏幕坐标**。整屏图传 (0,0)；
        窗口区域图传窗口矩形左上角（``_grab_dialog_screen`` 的返回值），
        这样返回的 x/y 始终是 `pyautogui.click` 可直接使用的屏幕坐标。
        """
        tem = self._load_locate_template(f)
        h, w = tem.shape[:2]
        with probe.span("vision.match_template", template=os.path.basename(f),
                        screen_h=int(screen.shape[0]), screen_w=int(screen.shape[1])):
            res = cv.matchTemplate(screen, tem, cv.TM_SQDIFF_NORMED)
            _min = np.min(res)
            x, y = cv.minMaxLoc(res)[2]
        x, y = x + w / 2 + origin[0], y + h / 2 + origin[1]
        return _min, x, y

    def locate_exit(self, screen=None, origin=(0, 0)):
        """定位退出按钮中心（屏幕坐标）。

        ⚠️ 2026-11-09 性能优化（用户指令「只在 Arbiter 窗口矩形内匹配」）：
        ``screen`` 未给时先抓 Arbiter 窗口矩形（765x600，BitBlt 6.3ms）做匹配，
        而不是整屏（2560x1600，44.6ms + 慢 6.7-8.8x 的 matchTemplate）。

        **安全网**：标题窗口（"游戏胜利/失败"）是独立顶层窗口，其 X 按钮
        理论上可能落在主窗口矩形之外（实测无弹窗时 exit.bmp 的整屏伪匹配
        点 x=431 < 窗口左边界 464）。因此当窗口内匹配分数不够好
        （min > 0.05）时，自动回退整屏重新定位，绝不因加速让关闭动作失效。
        这条回退是窄豁免，只在「窗口内没找到可信 X」时触发。
        """
        if screen is None:
            screen, origin = self._grab_dialog_screen(pad=24)   # locate_exit
        _min, x, y = self._match_template_on_screen(screen, "image/exit.bmp", origin)
        if origin != (0, 0) and _min > 0.05:
            logger.debug("窗口内 exit.bmp 匹配不佳(%.4f)，回退整屏定位", _min)
            probe.mark("vision.locate_exit_fullscreen_fallback",
                       window_min=round(float(_min), 4))
            full = self._grab_screen_bgr()
            if full is not None and hasattr(full, "shape"):
                _min2, x2, y2 = self._match_template_on_screen(
                    full, "image/exit.bmp", (0, 0))
                if _min2 < _min:
                    return x2, y2
        return x, y

    def _locate(self, f, screen=None, origin=(0, 0)):
        """判断模板是否出现，返回 (是否命中, 屏幕 x, 屏幕 y)。

        与 :meth:`locate_exit` 同款：``screen`` 未给时自动用窗口区域图，
        传入 ``origin`` 时把局部命中点换算回屏幕坐标。
        """
        if screen is None:
            screen, origin = self._grab_dialog_screen(pad=24)   # _locate
        _min, x, y = self._match_template_on_screen(screen, f, origin)
        return _min < 0.015, x, y

    def _dialog_templates(self, names):
        """在一次窗口区域抓屏上批量判定多个弹窗模板，返回 {name: (ok, x, y)}。

        局末主循环原本每轮要 ``_grab_screen_bgr()`` 一次（44.6ms）+
        4 次整屏 ``matchTemplate``（各 135–616ms）。改为：
        一次 BitBlt 窗口区域（6.3ms）+ N 次窗口内匹配（各 15–87ms），
        且**同一张图**复用，避免每判定一次就重抓一次屏。

        ⚠️ 防御：若窗口区域抓屏不可用（返回 None，或测试替身把抓屏 stub 成
        None），必须回退到 ``_grab_screen_bgr()`` 而不是把 None 喂给
        ``cv.matchTemplate``。原实现只捕 Exception，None 会漏过去 ⇒
        AttributeError 打死自动线程（本轮全量单测实测捕获）。

        ⚠️ padding：默认向四周扩 24px。最宽的弹窗模板 ok.png 是 158px，
        紧贴窗口边缘时若无 padding 会被裁掉 ⇒ 命中点偏移（实测差 5px）。
        扩展后区域 813x648（0.53M 像素），仍远小于整屏 4.1M。
        """
        screen, ox, oy = self._grab_dialog_screen(pad=24)
        if screen is None or not hasattr(screen, "shape"):
            screen = self._grab_screen_bgr()
            ox, oy = 0, 0
        # 整屏路径也可能被 stub 成 None（轻量实例/测试替身）：此时不判定
        # 任何弹窗，返回「全部未命中」，语义安全（不会误判胜负）。
        if screen is None or not hasattr(screen, "shape"):
            return {f: (False, 0.0, 0.0) for f in names}
        out = {}
        for f in names:
            try:
                _min, x, y = self._match_template_on_screen(screen, f, (ox, oy))
                out[f] = (_min < 0.015, x, y)
            except Exception as e:  # noqa: BLE001 —— 单个模板失败不影响其余判定
                logger.debug("弹窗模板 %s 匹配失败（忽略）: %s", f, e)
                out[f] = (False, 0.0, 0.0)
        return out

    def _slice_cell(self, board_img, i, j):
        x = i * self.cell_width
        y = j * self.cell_width
        w = self.screenshot_w
        h = self.screenshot_h
        return board_img[
               y - self.cell_width // 2 - h // 2: y - self.cell_width // 2 + h // 2,
               x - self.cell_width // 2 - w // 2: x - self.cell_width // 2 + w // 2,
               :,
               ]

    def cell_screenshot(self, i, j):
        return self._slice_cell(self.img, i, j)

    def compare_img(self, img, no_10):
        """单格识别（参考实现，保留兼容）。

        ⚠️ 性能：这是 480 格 × 25 模板 = 12000 次 cv.matchTemplate 的慢路径。
        批量扫描请用 `compare_cells_batch`（实测快 13～18 倍且逐格等价）。

        返回：0..10 的识别值；模板集不可用或最优匹配分数超过
        ``LOW_CONFIDENCE_THRESHOLD`` 时返回 ``UNKNOWN``（不再强行归类）。

        排序语义与改动前逐位一致（跨模板取 ``np.sum(响应图)`` 最小者）；
        置信判据单独用最优点分数 ``np.min(响应图)``，见 ``_normalized_score``。

        模板过滤判据与 ``_active_templates`` 完全一致（按实际补丁尺寸），
        因此本方法与 ``compare_cells_batch`` 的识别结果逐格可比。
        """
        sh = 2 * (self.screenshot_h // 2)
        sw = 2 * (self.screenshot_w // 2)
        best_score = np.inf
        best_min = np.inf
        best_row = None
        for i, row in enumerate(self.images or []):
            for t in row:
                if t is None:
                    continue
                if t.shape[0] > sh or t.shape[1] > sw:
                    continue
                try:
                    res = cv.matchTemplate(img, t, cv.TM_SQDIFF_NORMED)
                except cv.error:
                    continue
                score = float(np.sum(res))
                if score < best_score:
                    best_score = score
                    best_min = _normalized_score(res)
                    best_row = i

        if best_row is None:
            logger.warning("compare_img: 没有可用模板，返回 UNKNOWN")
            return UNKNOWN
        if _is_low_confidence(best_min):
            logger.debug("compare_img: 最优点分数 %.4f 超过阈值 %.4f，返回 UNKNOWN",
                         best_min, LOW_CONFIDENCE_THRESHOLD)
            return UNKNOWN

        res = best_row
        if no_10:
            if res == 10:
                res = 9
        return res

    def _active_templates(self):
        """返回可参与打分的模板 [(数字行, float32 模板)]。

        尺寸判据必须与 ``_slice_cell`` 的**实际**补丁尺寸一致：
        补丁 = 2*(screenshot_h//2) × 2*(screenshot_w//2)（screenshot_w 为奇数时
        实得尺寸比标称值小 1）。若误用标称尺寸，比补丁宽 1px 的模板会让
        ``pw = sw - tw + 1`` 变成 0，批量路径的 total 恒为 0（假完美分）。
        """
        sh = 2 * (self.screenshot_h // 2)
        sw = 2 * (self.screenshot_w // 2)
        out = []
        for row_i, row in enumerate(self.images or []):
            for t in row:
                if t is None:
                    continue
                if t.shape[0] <= sh and t.shape[1] <= sw:
                    out.append((row_i, t.astype(np.float32)))
        return out

    def compare_cells_batch(self, cells, no_10=True):
        """批量识别多格（矢量化，与 compare_img 逐格等价，实测快 13～18 倍）。

        cells: [(x, y)] 1-based 坐标列表
        返回: [(x, y, 识别值)]，识别值与 compare_img 完全一致；最优点分数超过
        ``LOW_CONFIDENCE_THRESHOLD`` 的格子返回 ``UNKNOWN``（与参考实现同判据）。

        等价性说明：对每格补丁，用 numpy 按 TM_SQDIFF_NORMED 同一定义
        R = Σ(I-T)²/sqrt(ΣI²·ΣT²) 在模板覆盖的所有偏移上求和，再取跨模板最小者，
        与 cv.matchTemplate + np.sum + argmin 的语义逐位一致
        （bench/bench_vision_vector.py 验证：480 格 0 格不一致）。
        """
        if not cells:
            return []
        # ⚠️ 必须与 _slice_cell 的实际取窗一致，而不是直接用 screenshot_w/h。
        # _slice_cell 用的是 y±(cw//2±h//2)、x±(cw//2±w//2)，每侧各 h//2、w//2，
        # 所以实际补丁尺寸 = 2*(screenshot_h//2) × 2*(screenshot_w//2)；
        # screenshot_w=13 时实得 12（奇数被整数除法丢掉），若按 13 取窗会整体错位 1px。
        sh = 2 * (self.screenshot_h // 2)
        sw = 2 * (self.screenshot_w // 2)
        act = self._active_templates()
        if not act:
            # 没有任何可用于当前补丁的模板：配置级失败，显式抛出（调用方会回退
            # 逐格参考实现；参考实现同样给不出可信结果时由 _apply_recognition 汇总
            # 成 LowConfidenceScanError）。绝不静默返回一批假类别。
            raise TemplateValidationError(
                f"compare_cells_batch: 没有任何可用模板（当前补丁 {sh}x{sw}，"
                f"cell_width={self.cell_width}；模板集未加载或尺寸全部不匹配）")

        # 取出所有补丁：(M, sh, sw, 3) float32
        xs = np.array([c[0] for c in cells], np.int64)
        ys = np.array([c[1] for c in cells], np.int64)
        lx = xs * self.cell_width - self.cell_width // 2 - sw // 2
        ly = ys * self.cell_width - self.cell_width // 2 - sh // 2
        yy = ly[:, None, None] + np.arange(sh)[None, :, None]
        xx = lx[:, None, None] + np.arange(sw)[None, None, :]
        patches = self.img[yy, xx, :].astype(np.float32)

        best = np.full(len(cells), np.inf)
        best_min = np.full(len(cells), np.inf)
        best_row = np.zeros(len(cells), np.int32)
        for row_i, tem in act:
            th, tw = tem.shape[:2]
            ph, pw = sh - th + 1, sw - tw + 1
            total = np.zeros(len(cells), np.float64)
            cur_min = np.full(len(cells), np.inf)  # 该模板在偏移上的最优点分数
            t2 = float(np.sum(tem * tem))
            # 逐偏移累加响应（偏移数通常很小：模板已接近补丁尺寸）
            for dy in range(ph):
                for dx in range(pw):
                    win = patches[:, dy:dy + th, dx:dx + tw, :]
                    d = win - tem[None]
                    num = np.einsum("mhwc,mhwc->m", d, d)
                    den = np.sqrt(np.maximum(
                        np.einsum("mhwc,mhwc->m", win, win) * t2, 1e-12))
                    r = num / den
                    total += r
                    np.minimum(cur_min, r, out=cur_min)
            better = total < best
            best[better] = total[better]
            best_min[better] = cur_min[better]
            best_row[better] = row_i
        out = []
        for k, (x, y) in enumerate(cells):
            # 置信判据用最优点分数（与 compare_img 的 _normalized_score 同一语义）
            if _is_low_confidence(float(best_min[k])):
                out.append((x, y, UNKNOWN))
                continue
            v = int(best_row[k])
            if no_10 and v == 10:
                v = 9
            out.append((x, y, v))
        return out

    def _grab_board(self):
        """抓取棋盘区域并转为 BGR，同时更新 self.img。"""
        with probe.span("vision.grab_board"):
            hwnd = win32gui.FindWindow(None, setting.win_name)
            bx, by = ClientToScreen(hwnd, self._bx, self._by)
            pil_img = ImageGrab.grab(
                (
                    bx + 0.5 * self.cell_width,
                    by + 0.5 * self.cell_width,
                    self.w * self.cell_width + bx + 0.5 * self.cell_width,
                    self.h * self.cell_width + by + 0.5 * self.cell_width,
                )
            )
            pil_img = np.array(pil_img)
            self.img = cv.cvtColor(pil_img, cv.COLOR_RGB2BGR)
            return self.img

    @staticmethod
    def _cell_changed(old_cell, new_cell, threshold=3.0):
        """比较新旧格子图像，判断是否发生变化（用于点击后只重扫变化区域）。"""
        if old_cell is None or old_cell.shape != new_cell.shape:
            return True
        diff = np.abs(old_cell.astype(np.int16) - new_cell.astype(np.int16))
        return float(diff.mean()) > threshold

    def _apply_recognition(self, cell_value, recognized, total, context,
                           emit_warning=True):
        """把一批识别结果写回棋盘，并按比例判定整盘是否可信。

        ``UNKNOWN`` 不写入 ``cell_value``（棋盘编码 0..11 都有语义，写入 -1 会
        污染下游推理）：该格保持调用方初始化的「未开(9)」，下一轮重新识别。

        不可识别比例 >= ``UNKNOWN_ABORT_RATIO`` 时判定棋盘不可信：
        - 先 ``logger.error`` + ``warning_signal`` 显式告警（用户可见，不静默）；
        - ``STRICT_LOW_CONFIDENCE=True`` 时抛 ``LowConfidenceScanError`` 中止，
          否则**不再写入任何识别结果**并原样返回棋盘（本轮识别整体作废，
          比"写一半真一半假"更安全）。
        """
        unknown = 0
        for x, y, v in recognized:
            if v == UNKNOWN:
                unknown += 1
        ratio = unknown / float(total) if total else 0.0
        if unknown and ratio >= UNKNOWN_ABORT_RATIO:
            msg = (f"{context}：{unknown}/{total} 格无法可信识别"
                   f"（比例 {ratio:.0%} ≥ 阈值 {UNKNOWN_ABORT_RATIO:.0%}），"
                   "棋盘不可信，请重新截图/校准模板与窗口位置")
            logger.error(msg)
            probe.mark("vision.low_confidence_abort", context=str(context),
                       unknown=int(unknown), total=int(total), ratio=round(ratio, 4))
            # 轻量实例（测试用 Solver.__new__）没有走 Qt 基类初始化，访问
            # pyqtSignal 描述符会抛 RuntimeError；告警链路失败绝不影响识别路径。
            if emit_warning:
                try:
                    warn = getattr(self, "warning_signal", None)
                    if warn is not None:
                        warn.emit(msg)
                except Exception as e:  # noqa: BLE001
                    logger.debug("低置信度告警发射失败（忽略）: %s", e)
            if STRICT_LOW_CONFIDENCE:
                raise LowConfidenceScanError(msg)
            return cell_value  # 整轮作废，不写入半真半假的结果

        if unknown:
            logger.warning("%s：%d/%d 格无法可信识别（比例 %.2f，保持未开）",
                           context, unknown, total, ratio)
            # 只在「确有不可识别格」时打点：全识别成功的扫描是常态（每局上百次），
            # 每次都写会把 probe.jsonl 撑爆；分母用 decision/scan 事件计数即可。
            probe.mark("vision.unknown", context=str(context), unknown=int(unknown),
                       total=int(total), ratio=round(ratio, 4))
        for x, y, v in recognized:
            if v == UNKNOWN:
                continue
            cell_value[y, x] = v
        return cell_value

    def complete_scan(self, cell_value, no_10=True):
        """全盘扫描：识别所有仍为未开(9)的格子。

        走批量矢量化路径（compare_cells_batch），与逐格 compare_img 逐位等价，
        实测 480 格从 ~254ms 降到 ~20ms（13～18x）。批量失败时自动回退逐格。

        不可识别格保持「未开(9)」（不写入 ``UNKNOWN``），比例过高抛
        ``LowConfidenceScanError``（见 ``_apply_recognition``）。
        """
        pyautogui.moveTo(10, 640, _pause=False)
        self._grab_board()

        cells = [(x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)
                 if cell_value[y, x] == 9]
        if not cells:
            return cell_value
        with probe.span("vision.complete_scan", cells=len(cells)) as _on:
            try:
                recognized = self.compare_cells_batch(cells, no_10=no_10)
            except LowConfidenceScanError:
                raise
            except Exception as e:  # noqa: BLE001 —— 批量失败退回逐格，保证功能不变
                logger.warning("批量识别失败，回退逐格 compare_img：%s", e)
                if _on:
                    probe.mark("vision.batch_fallback", where="complete_scan",
                               n_cells=len(cells))
                recognized = [(x, y, self.compare_img(self.cell_screenshot(x, y), no_10))
                              for x, y in cells]
        return self._apply_recognition(cell_value, recognized, len(cells), "全盘扫描")

    def rescan_after_click(self, cell_value, emit_warning=None):
        """点击后重扫：抓取新棋盘，只重新识别发生变化的未开格子。

        ``emit_warning=False`` 仅供自动模式的点击后重扫使用：局末失败弹窗会
        暂时改变棋盘像素，整盘低置信度是正常节奏，不应弹出模板校准告警。
        异常仍按既有 ``LowConfidenceScanError`` 契约抛出，由自动循环作废本轮并
        交回主循环处理胜负；帮助模式和默认调用仍显示诊断告警。

        扫雷点击空白格会触发 flood-fill，实际打开的区域可能远大于 5×5，
        因此不能固定扫描 5×5 窗口，而是对「所有仍标记为未开(9)」的格子做变更检测，
        只对真正变化的格子重新做模板匹配（变化的格子再批量识别）。

        不可识别格保持「未开(9)」；变化格里不可识别比例过高抛
        ``LowConfidenceScanError``（见 ``_apply_recognition``）。
        """
        if emit_warning is None:
            emit_warning = not bool(self.__dict__.get(
                "_suppress_rescan_warning", False
            ))
        old_img = self.img
        self._grab_board()
        changed = []
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                if cell_value[y, x] == 9:
                    new_cell = self._slice_cell(self.img, x, y)
                    old_cell = (
                        self._slice_cell(old_img, x, y) if old_img is not None else None
                    )
                    if self._cell_changed(old_cell, new_cell):
                        changed.append((x, y))
        if changed:
            with probe.span("vision.rescan_after_click", changed=len(changed)) as _on:
                try:
                    recognized = self.compare_cells_batch(changed, no_10=True)
                except LowConfidenceScanError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.warning("批量重扫失败，回退逐格：%s", e)
                    if _on:
                        probe.mark("vision.batch_fallback",
                                   where="rescan_after_click", n_cells=len(changed))
                    recognized = [
                        (x, y, self.compare_img(self._slice_cell(self.img, x, y), no_10=True))
                        for x, y in changed
                    ]
                self._apply_recognition(
                    cell_value, recognized, len(changed), "点击后重扫",
                    emit_warning=emit_warning,
                )
        return cell_value
