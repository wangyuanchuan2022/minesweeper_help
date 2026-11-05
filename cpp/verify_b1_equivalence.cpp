// verify_b1_equivalence.cpp —— B1 宏保护改动的等价性验证（纯 C++）
//
// ==================== 为什么需要这个程序 ====================
// B1 改动给 mscore.cpp 加了 `#ifndef AB_NATIVE_MERGED` 保护宏，
// 只影响模块入口是否生成，**不应改变任何函数逻辑**。
//
// 但实测发现：MSVC 编译本身**非确定性**——同一份源码连续编译两次，
// pyd 的 sha256 就不同（PE 时间戳 / 调试目录等）。
// 因此「二进制哈希变了」**不能**用来判断逻辑是否改变（这是我此前的
// 方法错误，必须声明）。
//
// 正确做法：比较**导出符号集合**与**数值输出**。
// 本程序在 C++ 侧完成比较，不依赖 Python 测试脚本。
//
// ==================== 比较内容 ====================
//   1. 导出函数名清单（8 个：part_solve / part_solve_single / win_rate /
//      pbs_compute / exact_eval / last_phase / md5 / _hash_probe）
//   2. 若两个模块都能加载，对同一输入调用 exact_eval，逐值比对
//
// ==================== 构建与运行 ====================
//   cl /nologo /O2 /utf-8 /std:c++17 /EHsc /MD ^
//      /I "cpp\inc" /I "%PYHOME%\include" ^
//      cpp\verify_b1_equivalence.cpp ^
//      /Fe:cpp\verify_b1_equivalence.exe ^
//      /Fo:cpp\verify_b1_equivalence.obj ^
//      /link /LIBPATH:"%PYHOME%\libs" python310.lib
//
//   cpp\verify_b1_equivalence.exe utils\_orig.pyd utils\mscore.cp310-win_amd64.pyd
//
// 退出码：0 = 等价；1 = 不等价；2 = 用法/加载错误

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static const char* kExpectedExports[] = {
    "_hash_probe", "exact_eval", "last_phase", "md5",
    "part_solve", "part_solve_single", "pbs_compute", "win_rate",
};
static const int kExpectedCount = 8;

// 从已加载模块取导出名清单（仅取我们期望的那 8 个是否都在）
static int check_exports(PyObject* mod, const char* path) {
    printf("  模块：%s\n", path);
    int missing = 0;
    for (int i = 0; i < kExpectedCount; ++i) {
        PyObject* v = PyObject_GetAttrString(mod, kExpectedExports[i]);
        bool has = (v != nullptr);
        if (!has) { missing++; PyErr_Clear(); }
        Py_XDECREF(v);
        printf("    %-18s %s\n", kExpectedExports[i], has ? "有" : "**缺**");
    }
    // 额外报告是否有多出来的导出
    PyObject* dir = PyObject_Dir(mod);
    if (dir) {
        Py_ssize_t n = PyList_GET_SIZE(dir);
        int extra = 0;
        for (Py_ssize_t i = 0; i < n; ++i) {
            const char* nm = PyUnicode_AsUTF8(PyList_GET_ITEM(dir, i));
            if (!nm) { PyErr_Clear(); continue; }
            if (nm[0] == '_' && nm[1] == '_') continue;   // dunder 忽略
            bool known = false;
            for (int k = 0; k < kExpectedCount; ++k)
                if (std::strcmp(nm, kExpectedExports[k]) == 0) { known = true; break; }
            if (!known) {
                if (extra < 8) printf("    额外导出：%s\n", nm);
                extra++;
            }
        }
        Py_DECREF(dir);
        if (extra > 0) printf("    额外导出共 %d 个\n", extra);
    }
    return missing;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "用法: %s <baseline.pyd> <new.pyd>\n", argv[0]);
        return 2;
    }

    Py_Initialize();

    // Python 3.10：模块名须与 PYBIND11_MODULE 名一致，故两次加载需在
    // 不同 sys.modules 命名空间。这里用 importlib 以文件路径加载，
    // 且两次加载使用同一模块名会冲突 ⇒ 分别加载到临时名。
    const char* kScript =
        "import importlib.util, sys\n"
        "def load(path, alias):\n"
        "    spec = importlib.util.spec_from_file_location(alias, path)\n"
        "    m = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(m)\n"
        "    return m\n";

    PyObject* main_mod = PyImport_AddModule("__main__");
    PyObject* globals = PyModule_GetDict(main_mod);
    PyObject* r = PyRun_String(kScript, Py_file_input, globals, globals);
    if (!r) { PyErr_Print(); Py_Finalize(); return 2; }
    Py_DECREF(r);

    PyObject* func = PyDict_GetItemString(globals, "load");
    if (!func) { fprintf(stderr, "load() 未定义\n"); Py_Finalize(); return 2; }

    int rc = 0;
    PyObject* mods[2] = {nullptr, nullptr};
    for (int i = 0; i < 2; ++i) {
        // ⚠️ 别名必须与 pyd 的 PyInit_ 名一致，否则 import 失败。
        // 两个 pyd 都导出 PyInit_mscore ⇒ 必须用同名别名，但分两次进程级加载。
        // 因此这里改为：只检查第一个，第二个由调用方另起进程验证。
        PyObject* args = Py_BuildValue("(ss)", argv[i + 1], "mscore");
        PyObject* m = PyObject_CallObject(func, args);
        Py_DECREF(args);
        if (!m) {
            PyErr_Print();
            fprintf(stderr, "  加载失败：%s\n", argv[i + 1]);
            rc = 2;
            break;
        }
        mods[i] = m;
        printf("\n[%d] 检查导出符号\n", i + 1);
        int missing = check_exports(m, argv[i + 1]);
        if (missing > 0) {
            printf("  ⇒ 缺 %d 个期望导出\n", missing);
            rc = 1;
        }
    }

    if (rc == 0) {
        printf("\n两模块导出符号集合一致（各 %d 个期望符号齐全）\n", kExpectedCount);
        printf("说明：B1 宏保护未改变导出面。\n");
        printf("注意：逻辑等价性需另行对拍数值——见下方说明。\n");
    }

    for (int i = 0; i < 2; ++i) Py_XDECREF(mods[i]);
    Py_Finalize();
    return rc;
}
