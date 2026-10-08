"""导出 _dearpygui 里每个绑定函数的 C 函数指针（转成 RVA），用于给崩溃地址命名。

pybind11 绑定对象就是 builtin_function_or_method（PyCFunction），
PyMethodDef.ml_meth 即原生函数入口。RVA = meth - 模块基址（跨进程可比）。

产物：_out/dpg_probe/_dpg_bindings.json（产物目录见 _outdir.py）。
"""
import ctypes
import json
import sys
from ctypes import wintypes

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class PyMethodDef(ctypes.Structure):
    _fields_ = [("ml_name", ctypes.c_char_p),
                ("ml_meth", ctypes.c_void_p),
                ("ml_flags", ctypes.c_int),
                ("ml_doc", ctypes.c_char_p)]


class PyCFunctionObject(ctypes.Structure):
    _fields_ = [("ob_refcnt", ctypes.c_ssize_t),
                ("ob_type", ctypes.c_void_p),
                ("m_ml", ctypes.POINTER(PyMethodDef)),
                ("m_self", ctypes.c_void_p),
                ("m_module", ctypes.c_void_p),
                ("m_weakreflist", ctypes.c_void_p),
                ("vectorcall", ctypes.c_void_p)]


def func_ptr(obj):
    if not isinstance(obj, type(len)) or str(type(obj)) != "<class 'builtin_function_or_method'>":
        return None
    try:
        cfo = ctypes.cast(id(obj), ctypes.POINTER(PyCFunctionObject))
        return ctypes.cast(cfo.contents.m_ml, ctypes.c_void_p).value, \
            cfo.contents.m_ml.contents.ml_meth
    except Exception:
        return None


def harvest(module):
    table = {}
    for name in dir(module):
        try:
            obj = getattr(module, name)
        except Exception:
            continue
        got = func_ptr(obj)
        if not got:
            continue
        mdef, meth = got
        if meth:
            table[name] = meth
    return table


import dearpygui._dearpygui as idepg  # noqa: E402

base = ctypes.windll.kernel32.GetModuleHandleW("_dearpygui.pyd")
print(f"module base = 0x{base:x}")

rows = {}
for name, addr in harvest(idepg).items():
    rows[name] = addr - base
for name, addr in harvest(sys.modules["dearpygui"]).items():
    rows.setdefault(name, addr - base)

with open(_outdir.out("_dpg_bindings.json"), "w", encoding="utf-8") as fh:
    json.dump(rows, fh, indent=1, sort_keys=True)
print(f"wrote {len(rows)} entries -> {_outdir.out('_dpg_bindings.json')}")
