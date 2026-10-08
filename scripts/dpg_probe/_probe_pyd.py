"""解析 _dearpygui.pyd 的 PE 导出表 / 调试目录，看能否给地址命名。"""
import ctypes
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import dearpygui  # noqa: E402

pyd = os.path.join(os.path.dirname(dearpygui.__file__), "_dearpygui.pyd")
data = open(pyd, "rb").read()
print(f"file: {pyd} size={len(data)}")

pe = data.index(b"PE\0\0")
opt = pe + 24
magic = struct.unpack_from("<H", data, opt)[0]
n_sections = struct.unpack_from("<H", data, pe + 6)[0]
size_opt = struct.unpack_from("<H", data, pe + 20)[0]
print(f"magic=0x{magic:x} sections={n_sections} size_opt={size_opt}")
# 数据目录（64 位 PE 从 opt+112 开始）
dd_off = opt + (112 if magic == 0x20B else 96)
names = ("export", "import", "resource", "exception", "cert", "reloc", "debug",
         "arch", "globalptr", "tls", "loadcfg", "boundimp", "iat", "delayimp",
         "clr", "reserved")
dirs = {}
for i in range(16):
    rva, size = struct.unpack_from("<II", data, dd_off + i * 8)
    dirs[names[i]] = (rva, size)
print("data dirs:", {k: (hex(v[0]), v[1]) for k, v in dirs.items() if v[1]})

sections = []
for i in range(n_sections):
    off = pe + 24 + size_opt + i * 40
    name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
    vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
    sections.append((name, vaddr, vsize, rawptr, rawsize))
    print(f"  {name:8s} rva=0x{vaddr:x} vsize=0x{vsize:x} raw=0x{rawptr:x}")


def rva_to_off(rva):
    for name, vaddr, vsize, rawptr, rawsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return rawptr + (rva - vaddr)
    return None


exp_rva, exp_size = dirs["export"]
if exp_size:
    eo = rva_to_off(exp_rva)
    n_funcs, n_names = struct.unpack_from("<II", data, eo + 20)
    addr_rva, name_rva = struct.unpack_from("<II", data, eo + 28)
    print(f"exports: {n_funcs} funcs, {n_names} named")
    ao, no = rva_to_off(addr_rva), rva_to_off(name_rva)
    got = []
    for i in range(min(n_names, 40)):
        nrva = struct.unpack_from("<I", data, no + i * 4)[0]
        noff = rva_to_off(nrva)
        end = data.index(b"\0", noff)
        got.append(data[noff:end].decode("ascii", "replace"))
    print("first names:", got[:20])
else:
    print("no export table")

dbg_rva, dbg_size = dirs["debug"]
if dbg_size:
    do = rva_to_off(dbg_rva)
    n = dbg_size // 28
    for i in range(n):
        typ, sz, rva, ptr = struct.unpack_from("<IIII", data, do + i * 28 + 12)
        print(f"debug entry type={typ} size={sz} rva=0x{rva:x}")
        if typ == 2:      # CODEVIEW
            cv = rva_to_off(rva)
            if cv:
                path = data[cv + 24:cv + 24 + sz - 24]
                print("  pdb:", path.split(b"\0")[0].decode("ascii", "replace"))
else:
    print("no debug directory")
