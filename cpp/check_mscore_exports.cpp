// check_mscore_exports.cpp —— 检查一个 mscore pyd 的导出符号集合（纯 C++）
//
// ==================== 用途 ====================
// B1 改动给 mscore.cpp 加了 `#ifndef AB_NATIVE_MERGED` 保护宏。
// 该宏只控制「是否生成 PYBIND11_MODULE(mscore, m)」入口，
// **不应改变任何导出函数与逻辑**。
//
// ⚠️ 已验证的事实：MSVC 编译**非确定性**——同一份源码连续编译两次，
//    pyd 的 sha256 就不同（PE 时间戳/调试路径等）。
//    ⇒ **不能**用"二进制哈希变了"判断逻辑是否改变（此前我用它当证据是错的）。
//
// 本程序提供**可比对的确定性证据**：导出符号集合。
// 分别对「B1 改后的 pyd」与「改动前的备份 pyd」运行，
// 两者导出符号集合必须完全一致。
//
// ==================== 构建 ====================
//   cpp\build_check_exports.bat
//
// ==================== 运行 ====================
//   cpp\check_mscore_exports.exe utils\mscore.cp310-win_amd64.pyd
//   cpp\check_mscore_exports.exe utils\mscore.cp310-win_amd64.pyd.bak_baseline
//
// 退出码：0 = 期望符号齐全；1 = 缺符号；2 = 用法/加载错误

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static const char* kExpected[] = {
    "_hash_probe", "exact_eval", "last_phase", "md5",
    "part_solve", "part_solve_single", "pbs_compute", "win_rate",
};
static const int kExpectedCount = 8;

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "用法: %s <path_to_mscore.pyd>\n", argv[0]);
        return 2;
    }
    Py_Initialize();

    // 用 importlib 以文件路径加载（模块名须为 mscore，与 PyInit_mscore 一致）
    const char* kScript =
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('mscore', PATH)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n";

    PyObject* main_mod = PyImport_AddModule("__main__");
    PyObject* globals = PyModule_GetDict(main_mod);
    PyObject* path_obj = PyUnicode_FromString(argv[1]);
    PyDict_SetItemString(globals, "PATH", path_obj);
    Py_DECREF(path_obj);

    PyObject* r = PyRun_String(kScript, Py_file_input, globals, globals);
    if (!r) {
        PyErr_Print();
        fprintf(stderr, "加载失败：%s\n", argv[1]);
        Py_Finalize();
        return 2;
    }
    Py_DECREF(r);

    PyObject* mod = PyDict_GetItemString(globals, "m");
    if (!mod) {
        fprintf(stderr, "模块对象缺失\n");
        Py_Finalize();
        return 2;
    }

    printf("模块：%s\n", argv[1]);
    int missing = 0;
    for (int i = 0; i < kExpectedCount; ++i) {
        PyObject* v = PyObject_GetAttrString(mod, kExpected[i]);
        bool has = (v != nullptr);
        if (!has) { missing++; PyErr_Clear(); }
        Py_XDECREF(v);
        printf("  %-18s %s\n", kExpected[i], has ? "有" : "**缺**");
    }

    // 列出全部非 dunder 导出（确定性排序，便于跨版本比对）
    PyObject* dir = PyObject_Dir(mod);
    printf("全部导出（排序）：\n");
    if (dir) {
        Py_ssize_t n = PyList_GET_SIZE(dir);
        // 收集名字后排序
        std::vector<std::string> names;
        for (Py_ssize_t i = 0; i < n; ++i) {
            const char* nm = PyUnicode_AsUTF8(PyList_GET_ITEM(dir, i));
            if (!nm) { PyErr_Clear(); continue; }
            if (nm[0] == '_' && nm[1] == '_') continue;
            names.push_back(nm);
        }
        // 插入排序（数量少）
        for (size_t i = 1; i < names.size(); ++i) {
            std::string key = names[i];
            size_t j = i;
            while (j > 0 && names[j - 1] > key) {
                names[j] = names[j - 1];
                --j;
            }
            names[j] = key;
        }
        for (auto& s : names) printf("  %s\n", s.c_str());
        printf("导出总数（不含 dunder）：%zu\n", names.size());
        Py_DECREF(dir);
    }

    Py_Finalize();
    if (missing > 0) {
        printf("⇒ 缺 %d 个期望符号\n", missing);
        return 1;
    }
    printf("⇒ 期望符号齐全（%d 个）\n", kExpectedCount);
    return 0;
}
