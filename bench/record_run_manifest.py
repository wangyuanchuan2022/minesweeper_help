# -*- coding: utf-8 -*-
"""为一次套件运行记录「代码版本 + 参数 + 环境」清单（可复现性凭证）。

为什么需要：套件会把结果写进带时间戳的目录，但如果不知道当时是哪个
commit、哪些参数，结果就无法回溯。本脚本把这些落成 manifest.json。

用法: python bench/record_run_manifest.py --out state/ab/runs/<stamp>
"""
import argparse
import json
import os
import platform
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def git(args):
    try:
        p = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:                                   # noqa: BLE001
        return None


def sha256_file(path):
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # ---- 关键二进制/数据文件指纹：确认测量对象本身没变 ----
    artifacts = {}
    for rel in ("utils/mscore.cp310-win_amd64.pyd", "cpp/mscore.cpp",
                "utils/probability.py", "sim/minesweeper_sim.py",
                "sim/eval_harness.py", "bench/ab_policy.py"):
        if os.path.exists(rel):
            artifacts[rel] = {
                "sha256": sha256_file(rel),
                "size": os.path.getsize(rel),
            }

    manifest = {
        "created_utc": __import__("datetime").datetime.utcnow().isoformat()
                       + "Z",
        "cwd": os.getcwd(),
        "git": {
            "head": git(["rev-parse", "HEAD"]),
            "branch": git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "describe": git(["describe", "--always", "--dirty"]),
            "status_porcelain": git(["status", "--porcelain"]),
        },
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "artifacts": artifacts,
        "env": {k: os.environ.get(k) for k in
                ("MSW_WR_NODE_CAP", "QT_QPA_PLATFORM",
                 "PYTHONIOENCODING")},
    }

    # 依赖版本（缺就跳过，不报错）
    try:
        import numpy
        manifest["python"]["numpy"] = numpy.__version__
    except Exception:                                   # noqa: BLE001
        pass

    out = os.path.join(args.out, "manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # 控制台摘要（给启动器的 manifest.log 用）
    print(f"manifest written: {out}")
    print(f"  git HEAD   : {manifest['git']['head']}")
    print(f"  git branch : {manifest['git']['branch']}")
    print(f"  git dirty  : {manifest['git']['describe']}")
    print(f"  python     : {manifest['python']['version'].splitlines()[0]}")
    print(f"  cpu        : {manifest['platform']['cpu_count']}")
    print("  artifacts  :")
    for rel, info in artifacts.items():
        print(f"    {rel}  {info['sha256'][:16]}  {info['size']} B")
    dirty = manifest["git"]["status_porcelain"]
    if dirty:
        print("  WARNING: working tree is dirty -- results are tied to the "
              "commit above PLUS the uncommitted changes listed in "
              "manifest.json.")


if __name__ == "__main__":
    main()
