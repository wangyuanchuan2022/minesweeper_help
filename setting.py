# -*- coding: utf-8 -*-
"""全局配置与资源路径解析。

资源路径一律**不依赖进程当前工作目录（cwd）**：项目根按本文件位置
（``__file__``）定位，或由环境变量 ``MINESWEEPER_HELP_ROOT`` 显式指定
（打包/测试/多副本部署时用）。所有模块统一通过 ``project_path()`` 取资源
绝对路径，避免从非项目根启动时读不到 cfg.json / image/ / data.json / state/。

对外接口（既有调用点保持不变）：
- ``cfg``：项目配置字典（模块导入时读取一次）
- ``win_name``、``sleep``、``num_workers``：既有全局参数
新增接口：
- ``PROJECT_ROOT`` / ``CFG_PATH`` / ``IMAGE_DIR`` / ``STATE_DIR`` / ``DATA_PATH``
- ``project_path(rel)``：相对项目根的路径 -> 绝对路径（绝对路径原样返回）
- ``load_cfg(path=None)``：读取配置 JSON（缺省 ``CFG_PATH``）
"""
import json
import os

# 项目根：显式资源根（环境变量）优先，否则取本文件所在目录。
_PROJECT_ROOT_ENV = "MINESWEEPER_HELP_ROOT"
PROJECT_ROOT = os.path.abspath(
    os.environ.get(_PROJECT_ROOT_ENV) or os.path.dirname(os.path.abspath(__file__))
)

CFG_PATH = os.path.join(PROJECT_ROOT, "cfg.json")
IMAGE_DIR = os.path.join(PROJECT_ROOT, "image")
STATE_DIR = os.path.join(PROJECT_ROOT, "state")
DATA_PATH = os.path.join(PROJECT_ROOT, "data.json")

# 关闭 cwd 归一的开关：置 "1" 时本模块只提供绝对路径、不改进程 cwd。
# 存在的意义：让「路径解析不依赖 cwd」这件事可以独立验证（测试用），
# 也便于宿主/测试框架自行控制工作目录。
_NO_CHDIR_ENV = "MINESWEEPER_HELP_NO_CHDIR"


def normalize_cwd(root=None):
    """把进程 cwd 归一到项目根（显式资源根）。

    历史代码在两个本任务文件范围之外的模块里仍用 cwd 相对路径：
    ``utils/vision.py``（image/ 模板）与 ``logger.py``（logs/）。它们
    与本文件的 ``PROJECT_ROOT`` 同源，因此在导入期把 cwd 归一到项目根，
    即可让这些历史路径同样收敛到显式资源根——从任意目录启动都不会再
    读错/写错位置。它们本身应在后续改动中改为 ``project_path()``。

    归一失败（目录不存在/权限）只静默跳过：调用方都还有绝对路径兜底。
    """
    if os.environ.get(_NO_CHDIR_ENV) == "1":
        return
    root = root or PROJECT_ROOT
    try:
        os.chdir(root)
    except OSError:
        pass


normalize_cwd()


def project_path(rel):
    """把相对项目根的路径解析成绝对路径；已绝对路径原样返回。"""
    rel = os.fspath(rel)
    if os.path.isabs(rel):
        return rel
    return os.path.join(PROJECT_ROOT, rel)


def validate_cfg(c):
    """配置 schema 与范围校验（唯一集中校验点，UI 与手工编辑都必须经过）。

    ⚠️ 2026-11-09 安全评审 P1 #5 引入：原 ``load_cfg`` 只做 ``json.load``，
    允许 ``w/h/a`` 取 0..10000 等极端值。实测风险：
      · ``main.py`` 会按配置创建 ``w*h`` 个 Qt 按钮 ⇒ 10000×10000 声明一亿个，
        远超 1.5GB 硬上限（用户铁律，不可调高）
      · 0 尺寸 / ``a >= w*h`` 会让原生内核拒绝或不可预测降级
      · 非原子写盘中途退出会留下截断 JSON，导致应用下次无法启动

    约束来源（按当前原生内核真实支持能力，不抬高上限）：
      · 棋盘尺寸：原生 ``decide_impl`` 支持一般尺寸，但 UI 按格创建控件；
        当前生产标定 30×16（``cfg.json``），上限取 30×16 的保守包络。
      · 总雷数：``0 <= a < w*h``（雷数必须严格小于格数，否则首点安全无解）
      · ``cell_width`` 必须为正（模板补丁尺寸 = cell_width 派生）
      · 缺字段直接抛 ValueError（fail-closed，不静默用默认值填坑）

    失败形态：抛 ``ValueError``（含具体字段与实测值），由调用方决定是
    提示用户修配置还是拒绝启动。绝不静默降级——静默降级会让用户以为
    配置生效了而实际跑的是别的东西。
    """
    if not isinstance(c, dict):
        raise ValueError(f"配置必须是 JSON 对象，实测类型 {type(c).__name__}")
    required = ("w", "h", "a", "cell_width", "win_name")
    missing = [k for k in required if k not in c]
    if missing:
        raise ValueError(f"配置缺少必填字段: {missing}")

    def _as_int(key, lo, hi, hi_note=""):
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"配置字段 {key} 必须是整数，实测 {v!r}")
        iv = int(v)
        if iv < lo or iv > hi:
            raise ValueError(
                f"配置字段 {key}={iv} 超出允许范围 [{lo}, {hi}]"
                + (f"（{hi_note}）" if hi_note else ""))
        return iv

    # 棋盘尺寸：1..30 × 1..16（当前生产标定 30×16；不抬高上限）
    c["w"] = _as_int("w", 1, 30, "当前原生/UI 支持的最大宽度")
    c["h"] = _as_int("h", 1, 16, "当前原生/UI 支持的最大高度")
    # 总雷数：必须严格小于格数
    ncell = c["w"] * c["h"]
    c["a"] = _as_int("a", 0, ncell - 1, f"雷数必须 < w*h={ncell}")
    # 格宽：必须为正（模板补丁尺寸由它派生）
    c["cell_width"] = _as_int("cell_width", 1, 200, "格宽像素")

    # 其余可选数值字段（存在则校验类型/范围，缺省不强制）
    for key, lo, hi in (("bx", -10000, 10000), ("by", -10000, 10000),
                        ("limit", 1, 100000)):
        if key in c:
            c[key] = _as_int(key, lo, hi)

    if not isinstance(c["win_name"], str) or not c["win_name"]:
        raise ValueError(f"配置字段 win_name 必须是非空字符串，实测 {c['win_name']!r}")
    if "path" in c and not isinstance(c["path"], str):
        raise ValueError(f"配置字段 path 必须是字符串，实测 {type(c['path']).__name__}")
    return c


def load_cfg(path=None):
    """读取配置 JSON 并做 schema 校验；缺省读项目根 cfg.json（与 cwd 无关）。

    校验失败抛 ``ValueError``（含具体字段），**不静默填默认值**——
    见 ``validate_cfg`` 的失败形态说明。
    """
    with open(path or CFG_PATH, encoding="utf-8") as f:
        return validate_cfg(json.load(f))


def save_cfg(cfg_dict, path=None, encoder=None):
    """原子保存配置：先写临时文件再 ``os.replace``，替代直接覆盖写。

    ⚠️ 2026-11-09 安全评审 P1 #5 引入：原 ``main.py:2458/2471`` 用
    ``open(CFG_PATH, "w")`` 直接覆盖。中途退出/写入异常会留下**截断 JSON**，
    下次启动 ``load_cfg`` 直接失败 ⇒ 应用无法启动（配置丢失且无备份）。

    原子性保证：
      ① 先校验再落盘（非法配置绝不写入，避免把坏配置固化）
      ② 写 ``<path>.tmp`` 成功后 ``os.replace``（Windows/POSIX 均为原子替换）
      ③ 异常时清理 tmp 并把原异常抛出（不静默吞掉，避免"看起来保存成功"）
    """
    target = path or CFG_PATH
    validated = validate_cfg(dict(cfg_dict))   # 深拷贝语义：不改调用方对象
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(validated, f, cls=encoder)
        os.replace(tmp, target)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return validated


cfg = load_cfg()

sleep = 0.01
win_name = cfg["win_name"]  # Minesweeper Arbiter
