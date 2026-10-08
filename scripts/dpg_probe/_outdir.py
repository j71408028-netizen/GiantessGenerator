"""探针统一产物目录：dpg_probe 下所有探针的落盘产物都写这里。

产物目录 = ``scripts/_out/dpg_probe/``，整目录被根 ``.gitignore`` 一行覆盖；
探针目录里因此只剩脚本本体，``git status`` 与 ``ls`` 都干净。目录在导入时自动创建。

导入方式（探针与脚本同目录，脚本目录本就在 ``sys.path[0]``）::

    import _outdir
    ... = _outdir.OUT_DIR            # 产物目录路径
    ... = _outdir.out("crash.dmp")   # 产物目录下某个文件的路径
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(_HERE), "_out", "dpg_probe")
os.makedirs(OUT_DIR, exist_ok=True)


def out(name):
    """返回 ``_out/dpg_probe/`` 下某个产物文件的绝对路径。"""
    return os.path.join(OUT_DIR, name)
