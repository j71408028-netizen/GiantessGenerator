"""宿主自持 GL 纹理：``glGenTextures`` / ``glTexImage2D`` / ``glTexSubImage2D`` 的最小绑定。

**为什么需要它**：``hello_imgui.TextureGpu`` 只有 ``texture_id`` / ``width`` /
``height``，没有任何**原地更新像素**的 API（官方口径是"每帧新建 + 丢旧句柄"）。
小游戏的离屏画布需要"内容变化后只重传变化的那几块"，所以必须自己建纹理并调
``glTexSubImage2D``；将来的原生（编译型）小游戏后端也要一块由宿主持有、可绑
FBO 的纹理来画（见 ``.workbuddy/imgui-migration/52-analysis-gpu-texture-native.md``）。

口径（P6 分析 §6 定死，别在这里漂）：

- 纹理由**宿主创建并持有**，显示侧只用 ``imgui.ImTextureRef(int(gl_name))``
  ——OpenGL3 后端的 ``ImTextureID`` 就是 GLuint，裸名字直接可用（实测）；
- GL 1.1 入口（纹理）从 ``opengl32.dll`` 导出表取；GL 3.0 入口（FBO）不在这张
  导出表里，必须经 ``wglGetProcAddress`` 取——**实测不需要额外加载器**
  （``missing: []``，见 ``.workbuddy/p6_probe/probe_gltex.py``）；
- 只能在 **GL 上下文当前** 的帧回调 / 帧内绘制里调用；没有上下文时
  :func:`available` 返回假，调用方应静默跳过（离线守卫里就是这条路）。

与 ``gl_util`` 的分工：那个模块只做"整帧 ``glReadPixels`` 抓帧"，本模块只做
"纹理上传"。两边各绑各用得到的入口，互不依赖。
"""

import ctypes
import sys

# ---------------- GL 枚举 ----------------
GL_TEXTURE_2D = 0x0DE1
GL_RGBA = 0x1908
GL_RGBA8 = 0x8058
GL_UNSIGNED_BYTE = 0x1401
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_CLAMP_TO_EDGE = 0x812F
GL_LINEAR = 0x2601
GL_UNPACK_ALIGNMENT = 0x0CF5
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_FRAMEBUFFER_COMPLETE = 0x8CD5

#: 纹理路径必需的 GL 1.1 入口（缺任何一个 :func:`available` 即为假）
_REQUIRED = ("glGenTextures", "glBindTexture", "glTexImage2D", "glTexSubImage2D",
             "glTexParameteri", "glPixelStorei", "glDeleteTextures")
#: FBO（GL 3.0）：只服务将来的原生后端，缺了不影响 CPU 画布
_FBO = ("glGenFramebuffers", "glBindFramebuffer", "glFramebufferTexture2D",
        "glDeleteFramebuffers", "glCheckFramebufferStatus")

_lib = None
_proto = None
_get_proc = None
_f = {}            # 名字 -> 已绑定的函数
_missing = []      # 解析失败的入口
_state = "unloaded"   # unloaded | loaded | failed


def _open_lib():
    """按平台打开 GL 库并给出函数原型 / 扩展入口取指器。"""
    global _lib, _proto, _get_proc
    if sys.platform.startswith("win"):
        _lib = ctypes.WinDLL("opengl32.dll")
        _proto = ctypes.WINFUNCTYPE
        _get_proc = _proto(ctypes.c_void_p, ctypes.c_char_p)(
            ("wglGetProcAddress", _lib))
    else:
        # 注意：不能写 ``import ctypes.util``——那会把 ``ctypes`` 变成局部名字，
        # 后面每处 ``ctypes.c_*`` 都会 UnboundLocalError（gl_util 真踩过）。
        import ctypes.util as _ctypes_util
        name = ("libGL.so.1" if sys.platform.startswith("linux")
                else (_ctypes_util.find_library("OpenGL") or "libGL.dylib"))
        _lib = ctypes.CDLL(name)
        _proto = ctypes.CFUNCTYPE
        try:
            _get_proc = _proto(ctypes.c_void_p, ctypes.c_char_p)(
                ("glXGetProcAddressARB", _lib))
        except Exception:
            _get_proc = None


def _bind(name, restype, *argtypes, via_ext=False):
    """解析一个入口（先按需走扩展取指，再退回库导出表）；成功返回 True。"""
    addr = 0
    if via_ext and _get_proc is not None:
        try:
            raw = _get_proc(name.encode("ascii"))
            addr = int(ctypes.cast(raw, ctypes.c_void_p).value or 0)
        except Exception:
            addr = 0
    if not addr:
        try:
            addr = int(ctypes.cast(getattr(_lib, name), ctypes.c_void_p).value)
        except Exception:
            addr = 0
    if not addr:
        _missing.append(name)
        return False
    _f[name] = _proto(restype, *argtypes)(addr)
    return True


def _load():
    """惰性加载（只做一次；失败后不再重试）。返回纹理路径是否可用。"""
    global _state
    if _state != "unloaded":
        return _state == "loaded"
    try:
        _open_lib()
    except Exception:
        _state = "failed"
        return False
    ok = True
    ok &= _bind("glGenTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
    ok &= _bind("glBindTexture", None, ctypes.c_uint, ctypes.c_uint)
    ok &= _bind("glTexImage2D", None, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                ctypes.c_uint, ctypes.c_void_p)
    ok &= _bind("glTexSubImage2D", None, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                ctypes.c_uint, ctypes.c_void_p)
    ok &= _bind("glTexParameteri", None, ctypes.c_uint, ctypes.c_uint, ctypes.c_int)
    ok &= _bind("glPixelStorei", None, ctypes.c_uint, ctypes.c_int)
    ok &= _bind("glDeleteTextures", None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))
    # FBO 入口（GL 3.0）只在扩展表里；取不到就记进 _missing，不影响上船
    _bind("glGenFramebuffers", None, ctypes.c_int,
          ctypes.POINTER(ctypes.c_uint), via_ext=True)
    _bind("glBindFramebuffer", None, ctypes.c_uint, ctypes.c_uint, via_ext=True)
    _bind("glFramebufferTexture2D", None, ctypes.c_uint, ctypes.c_uint,
          ctypes.c_uint, ctypes.c_uint, ctypes.c_int, via_ext=True)
    _bind("glDeleteFramebuffers", None, ctypes.c_int,
          ctypes.POINTER(ctypes.c_uint), via_ext=True)
    _bind("glCheckFramebufferStatus", ctypes.c_uint, ctypes.c_uint, via_ext=True)
    _state = "loaded" if ok else "failed"
    return ok


def available() -> bool:
    """纹理路径是否可用（离线/无 GL 环境下为假，调用方应静默跳过）。"""
    return _load() and all(name in _f for name in _REQUIRED)


def framebuffer_available() -> bool:
    """FBO 入口是否可用（原生后端预留；CPU 画布不需要）。"""
    return _load() and all(name in _f for name in _FBO)


def missing():
    """解析失败的 GL 入口名（诊断用）。"""
    _load()
    return list(_missing)


def _data_ptr(data):
    """把 numpy 数组 / bytes 变成 ``void*``；返回 ``(指针, 保活对象)``。

    调用方必须在 GL 调用期间持有保活对象：``bytes`` 走 ``ctypes`` 时不带
    自有缓冲，指针会悬空。
    """
    if data is None:
        return None, None
    if isinstance(data, (bytes, bytearray, memoryview)):
        buf = bytes(data)
        return ctypes.cast(ctypes.c_char_p(buf), ctypes.c_void_p), buf
    return data.ctypes.data_as(ctypes.c_void_p), data


def create_texture(width, height, data=None):
    """建一张 RGBA8 纹理并（可选）上传整张像素；返回 GL 名，失败返回 None。

    ``data`` 为 ``(height, width, 4)`` 的 uint8（numpy 数组或 bytes），
    行 0 = 纹理 v=0 = 绘制矩形上边（imgui 的 uv (0,0) 落在 p_min）。
    """
    if not available():
        return None
    width, height = max(1, int(width)), max(1, int(height))
    names = (ctypes.c_uint * 1)()
    _f["glGenTextures"](1, names)
    tex = int(names[0])
    if tex == 0:
        return None
    _f["glBindTexture"](GL_TEXTURE_2D, tex)
    for pname, value in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                         (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                         (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
        _f["glTexParameteri"](GL_TEXTURE_2D, pname, value)
    _f["glPixelStorei"](GL_UNPACK_ALIGNMENT, 1)
    ptr, keep = _data_ptr(data)
    try:
        _f["glTexImage2D"](GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0,
                           GL_RGBA, GL_UNSIGNED_BYTE, ptr)
    finally:
        del keep
    return tex


def upload(texture, x, y, width, height, data) -> bool:
    """只更新纹理的一个子矩形（``glTexSubImage2D``）；失败返回 False。

    ``texture`` 为 :func:`create_texture` 返回的 GL 名；``data`` 必须是**连续**
    的 ``(height, width, 4)`` uint8（行 0 = 该子矩形上边）。
    """
    if texture is None or not available():
        return False
    width, height = int(width), int(height)
    if width <= 0 or height <= 0 or data is None:
        return False
    _f["glBindTexture"](GL_TEXTURE_2D, int(texture))
    _f["glPixelStorei"](GL_UNPACK_ALIGNMENT, 1)
    ptr, keep = _data_ptr(data)
    try:
        _f["glTexSubImage2D"](GL_TEXTURE_2D, 0, int(x), int(y), width, height,
                              GL_RGBA, GL_UNSIGNED_BYTE, ptr)
    finally:
        del keep
    return True


def delete_texture(texture):
    """删除一张纹理（无上下文时应由调用方跳过，见 stage 的 ``_gl_ops_ok``）。"""
    if texture is None or not available():
        return
    try:
        _f["glBindTexture"](GL_TEXTURE_2D, 0)
        names = (ctypes.c_uint * 1)(int(texture))
        _f["glDeleteTextures"](1, names)
    except Exception:
        pass


# ---------------- FBO（原生后端预留，P6 不使用） ----------------
def create_framebuffer(texture):
    """把 ``texture`` 挂成一张 FBO 的颜色附件；返回 ``(fbo_id, complete)``。

    供未来的编译型小游戏后端使用：宿主交出 ``fbo_id``，游戏自己绑它、往纹理上
    画。CPU 画布路径（PIL + ``glTexSubImage2D``）不需要 FBO。取不到 GL 3.0 入口
    时返回 ``None``。
    """
    if texture is None or not framebuffer_available():
        return None
    names = (ctypes.c_uint * 1)()
    _f["glGenFramebuffers"](1, names)
    fbo = int(names[0])
    if fbo == 0:
        return None
    _f["glBindFramebuffer"](GL_FRAMEBUFFER, fbo)
    _f["glFramebufferTexture2D"](GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                 GL_TEXTURE_2D, int(texture), 0)
    complete = int(_f["glCheckFramebufferStatus"](GL_FRAMEBUFFER)) == GL_FRAMEBUFFER_COMPLETE
    _f["glBindFramebuffer"](GL_FRAMEBUFFER, 0)
    return fbo, complete


def delete_framebuffer(fbo):
    if fbo is None or not framebuffer_available():
        return
    try:
        _f["glBindFramebuffer"](GL_FRAMEBUFFER, 0)
        names = (ctypes.c_uint * 1)(int(fbo))
        _f["glDeleteFramebuffers"](1, names)
    except Exception:
        pass


__all__ = ["available", "framebuffer_available", "missing", "create_texture",
           "upload", "delete_texture", "create_framebuffer", "delete_framebuffer"]
