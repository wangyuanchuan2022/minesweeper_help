# -*- coding: utf-8 -*-
"""C++ 覆盖率一键链路（MinGW g++ --coverage + gcov）。

用法（在用户自己的终端跑，需 PY310_ROOT / MINGW_BIN 环境变量）：
  set PY310_ROOT=<你的 py310 环境根>
  set MINGW_BIN=<含 g++.exe 的 MinGW bin 目录>
  python tools/cpp_cov_build.py

步骤：
  1) tools/extract_cpp_pure.py 从 cpp/mscore.cpp 抽纯算法段（生产源码零改动）
  2) g++ --coverage -static 编译 tools/cpp_cov_driver.cpp（include 该段）
  3) 把 python310.dll 复制到 exe 同目录（Windows DLL 搜索必命中）
  4) 运行 driver（真实盘面形状数据），产出 .gcda
  5) gcov 解析，tools/parse_cpp_coverage.py 产出 state/audit/cpp_coverage.json

口径：仅 cpp/mscore.cpp 纯算法段语句行覆盖；不含 pybind 绑定层、不含
分支覆盖；与 Python 侧 tools/coverage_probe.py 分列，不相加。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_cov"
OUT.mkdir(parents=True, exist_ok=True)

PY310 = os.environ.get("PY310_ROOT", "").strip()
MINGW = os.environ.get("MINGW_BIN", "").strip()
if not PY310 or not MINGW:
    print("FATAL: 必须设置 PY310_ROOT 与 MINGW_BIN 环境变量")
    print("  PY310_ROOT = 你的 Python 3.10 环境根目录（含 Include/ 与 libs/）")
    print("  MINGW_BIN  = 含 g++.exe / gcov.exe 的 MinGW bin 目录")
    print("  本脚本不硬编码任何本机绝对路径，避免写入仓库。")
    sys.exit(1)
GPP = str(Path(MINGW) / "g++.exe")
GCOV = str(Path(MINGW) / "gcov.exe")
PY_INC = str(Path(PY310) / "Include")
PY_LIBS = str(Path(PY310) / "libs")
PY_DLL = str(Path(PY310) / "python310.dll")
PB_INC = str(ROOT / "cpp" / "inc")

for p in (GPP, GCOV, PY_DLL):
    if not Path(p).exists():
        print("FATAL: 缺少", p, " 请设置 PY310_ROOT / MINGW_BIN")
        sys.exit(1)

# 干净起点：清掉旧 gcda/gcov，保证单 run
for f in list(OUT.glob("*.gcda")) + list(OUT.glob("*.gcov")):
    f.unlink()

# 1) 抽取纯算法段
ex = subprocess.run([sys.executable, str(ROOT / "tools" / "extract_cpp_pure.py")],
                    capture_output=True, text=True, timeout=60)
print("EXTRACT:", ex.stdout.strip(), ex.stderr.strip())
if ex.returncode != 0:
    sys.exit(ex.returncode)

# 2) 编译（静态链接去掉 MinGW 运行时 DLL 依赖）
exe = OUT / "cpp_cov_driver.exe"
cmd = [
    GPP, "-std=c++17", "-O0", "--coverage", "-static",
    "-static-libgcc", "-static-libstdc++",
    "-I", PB_INC, "-I", PY_INC, "-I", str(ROOT / "output"),
    str(ROOT / "tools" / "cpp_cov_driver.cpp"),
    "-o", str(exe),
    "-L", PY_LIBS, "-lpython310",
]
print("BUILD:", " ".join(cmd[:5]), "...")
r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
print("BUILD_RETURN:", r.returncode)
if r.stdout.strip():
    print("BUILD_STDOUT:", r.stdout[-1000:])
if r.stderr.strip():
    print("BUILD_STDERR:", r.stderr[-2000:])
if r.returncode != 0:
    sys.exit(r.returncode)

# 3) DLL 与 exe 同目录（subprocess 的 env PATH 对 Windows DLL 搜索不生效）
shutil.copy2(PY_DLL, str(OUT / "python310.dll"))

# 4) 运行
run = subprocess.run([str(exe)], capture_output=True, text=True,
                     timeout=120, cwd=str(OUT))
print("RUN_RETURN:", run.returncode)
print("RUN_STDOUT:", (run.stdout or "")[-2000:])
if run.returncode != 0:
    print("RUN_STDERR:", (run.stderr or "")[-1000:])
    sys.exit(run.returncode)

# 5) gcov（只解析 mscore_pure.inc 对应的 gcno）
gcno = OUT / "cpp_cov_driver.gcno"
gc = subprocess.run([GCOV, "-b", "-c", gcno.name],
                    capture_output=True, text=True, timeout=180, cwd=str(OUT))
print("GCOV_RETURN:", gc.returncode)
for ln in (gc.stdout or "").splitlines():
    if "mscore_pure" in ln or "Lines executed" in ln and "%" in ln:
        print("  ", ln.strip())

# 6) 汇总报告
rep = subprocess.run([sys.executable, str(ROOT / "tools" / "parse_cpp_coverage.py")],
                     capture_output=True, text=True, timeout=60)
print(rep.stdout.strip())
if rep.returncode != 0:
    print(rep.stderr.strip())
    sys.exit(rep.returncode)
