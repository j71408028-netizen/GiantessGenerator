import os
import queue
import threading

import numpy as np
from PIL import Image
from PIL import ImageFilter

from dungeon import process_log
from dungeon.actions import VISUAL_FILTER_KEYS

#: 背景重采样的防抖任务 key（同 key 的任务互相顶掉，天然去重）
BG_REFRESH_TASK = "background:refresh"
#: 背景淡入淡出的帧任务 key
_FADE_TASK_KEY = "background:fade"

#: 背景淡入淡出：帧数、每帧间隔（30 帧 × 0.03s ≈ 0.9s，与原实现一致）
_FADE_STEPS = 30
_FADE_STEP_SECONDS = 0.03


class PixelWorker:
    """单个后台工作线程 + FIFO 队列：所有重像素活都排在这儿。

    L3 之前"每次换背景新起一条淡入淡出线程、每次 resize 新起一个 Timer"，
    线程数随浏览/缩放次数线性增长，还得各自轮询 ``_closing``。收成一个可复用的
    工作者后：

    - window 层的后台线程只剩 **AI 类（流式/细节提问/选项/结局）+ 本工作者**，
      每会话的线程开销恒定；
    - 串行执行天然互斥：重采样与混合不会互相抢 PIL 的内部缓冲；
    - ``shutdown()`` 排个毒丸即可收工，不再依赖 ``join(timeout=0.5)`` 碰运气。
    """

    def __init__(self, name="dungeon-pixel-worker"):
        self._jobs = queue.Queue()
        self._thread = None
        self._sentinel = object()
        self._name = name

    def submit(self, fn) -> bool:
        """排一个像素任务；工作线程按需创建。"""
        thread = self._ensure_thread()
        if thread is None:
            return False
        self._jobs.put(fn)
        return True

    def shutdown(self, timeout=0.5):
        """收工：排毒丸并等线程退出（有任务在跑时最多等 ``timeout`` 秒）。"""
        thread = self._thread
        if thread is None:
            return
        self._jobs.put(self._sentinel)
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
        self._thread = None

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _ensure_thread(self):
        thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def _loop(self):
        while True:
            job = self._jobs.get()
            if job is self._sentinel:
                self._jobs.task_done()
                return
            try:
                job()
            except Exception as exc:
                process_log.log(f"[PixelWorker] 像素任务异常: {exc}")
            finally:
                self._jobs.task_done()


# 滤镜键单一出处：清单（键+展示名）定义在 dungeon/actions.py::VISUAL_FILTERS，
# 这里只提供键 → PIL 滤镜的映射；键不一致在导入时直接报错，不再靠注释手工同步
_PIL_FILTERS = {
    "blur": ImageFilter.BLUR, "contour": ImageFilter.CONTOUR,
    "detail": ImageFilter.DETAIL, "edge_enhance": ImageFilter.EDGE_ENHANCE,
    "edge_enhance_more": ImageFilter.EDGE_ENHANCE_MORE, "emboss": ImageFilter.EMBOSS,
    "find_edges": ImageFilter.FIND_EDGES, "sharpen": ImageFilter.SHARPEN,
    "smooth": ImageFilter.SMOOTH, "smooth_more": ImageFilter.SMOOTH_MORE,
}
if set(_PIL_FILTERS) != set(VISUAL_FILTER_KEYS):
    raise RuntimeError(
        "滤镜键不一致：dungeon/actions.py 的 VISUAL_FILTERS 与 "
        "dungeon/background.py 的 _PIL_FILTERS 需要同步")


def _rotate_safe(image, angle):
    """按对角线放大后旋转、再居中裁回原尺寸；透明边角用边缘色填充。

    旋转会引入透明边角（内容转出画布）。为让背景铺满时不留黑缝，先在
    对角线上扩充透明画布旋转（旋转不会丢失内容），裁回原尺寸后对透明
    像素用就近的非透明边缘色填充。
    """
    w, h = image.size
    diag = int((w * w + h * h) ** 0.5) + 1
    phase = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    phase.paste(image, ((diag - w) // 2, (diag - h) // 2))
    rotated = phase.rotate(angle, resample=Image.BICUBIC)
    out = rotated.crop(((diag - w) // 2, (diag - h) // 2,
                        (diag - w) // 2 + w, (diag - h) // 2 + h))
    fill = _edge_tint(image)
    if out.getchannel("A").getextrema() != (255, 255) and fill is not None:
        alpha = out.getchannel("A")
        solid = Image.new("RGBA", out.size, fill)
        out = Image.composite(out, solid, alpha)
    return out


def _edge_tint(image):
    """取图像四边中间点的非透明平均色，用作旋转后透明边角的填充。"""
    w, h = image.size
    if w < 4 or h < 4:
        return None
    pts = [
        (w // 2, 1), (w // 2, h - 2), (1, h // 2), (w - 2, h // 2),
        (w // 2, h // 2),
    ]
    rs = gs = bs = 0
    cnt = 0
    for x, y in pts:
        r, g, b, a = image.getpixel((x, y))
        if a > 0:
            rs += r
            gs += g
            bs += b
            cnt += 1
    if not cnt:
        return None
    return (rs // cnt, gs // cnt, bs // cnt, 255)


class DungeonBackground:
    """副本背景图的加载、裁剪、滤镜和淡入淡出。

    时间线交给帧时钟（``owner._frame``），重活留给 :class:`PixelWorker`：

    - **何时**走一步淡入淡出、何时触发一次 resize 后的重采样——都是帧任务，跟着
      会话生死，不必再轮询 ``_closing`` 也不必 join；
    - 真吃 CPU 的重采样与像素混合则丢给 :class:`PixelWorker`，不占帧循环的时间。

    纹理口径（迁移后简化）：imgui 的 ``TextureGpu`` **不能原地改像素**（P0-S3），
    所以每次上屏都是"新建一个精确尺寸的句柄 + 丢掉旧的"。因此旧实现里那套
    「纹理只增不减 + ``uv`` 子区域映射 + 组合缓冲」全部删除——那是为绕开
    DPG 对动态纹理改尺寸的存储复用怪癖才存在的。上传的数据统一是
    ``H×W×4`` 的 uint8 连续数组（``hello_imgui.create_texture_gpu_from_rgba_data``
    的入参形状）。
    """

    def __init__(self, owner):
        self.owner = owner
        self._worker = PixelWorker()
        #: 重采样来源代数：change() 换源图（含滤镜/旋转）时 +1，作缓存键一部分
        self._src_gen = 0
        #: 重采样结果缓存：(来源代, 宽, 高) → HxWx4 uint8。最多 2 份——
        #: 窗口/全屏来回切换（客户区比例略有不同，必须各自重采样）时
        #: 第二个方向直接命中，免掉一次重采样
        self._resize_cache = {}

    def cancel_pending_refresh(self):
        """取消尚未执行的 resize 重采样任务（退出/冻结背景时用）。"""
        owner = self.owner
        frame = getattr(owner, "_frame", None)
        if frame is not None:
            frame.cancel(BG_REFRESH_TASK)

    def target_size(self):
        """当前背景纹理的目标像素尺寸。

        立即模式下布局单位就是像素（``io.display_size``），所以直接用布局尺寸，
        不需要再单独维护一套纹理尺寸与 ``uv`` 映射。
        """
        owner = self.owner
        return (max(1, int(owner._layout_w)), max(1, int(owner._layout_h)))

    @property
    def worker_alive(self) -> bool:
        """像素工作线程是否还活着（自检用：会话收尾后应为 False）。"""
        return self._worker.alive

    def shutdown(self):
        """会话收尾：背景像素工作者一并收工。"""
        self.cancel_pending_refresh()
        self._worker.shutdown()

    def change(self, image_path, smooth_transition=False, filter_effect=None,
               rotate_angle=None, blur_radius=None):
        owner = self.owner
        if not image_path:
            return
        full_path = self.resolve_path(image_path)
        if not full_path or not os.path.exists(full_path):
            process_log.log(f"背景加载失败: 图片文件不存在 -> {image_path}")
            return
        try:
            new_pil = Image.open(full_path).convert("RGBA")
            # 入口动态背景：先轻微旋转（按对角线放大后旋转再居中裁回原尺寸，
            # 避免旋转后四角露出透明），再模糊或滤镜
            if rotate_angle:
                new_pil = _rotate_safe(new_pil, rotate_angle)
            if blur_radius:
                new_pil = new_pil.filter(ImageFilter.GaussianBlur(blur_radius))
            elif filter_effect:
                new_pil = self.apply_filter(new_pil, filter_effect)
        except Exception as exc:
            process_log.log(f"图片加载错误: {exc}")
            return

        owner._bg_pil_full = new_pil
        self._src_gen += 1
        width, height = self.target_size()
        if owner._bg_pil_original is None or not smooth_transition:
            # 无过渡（首图/强制刷新）：在当前线程同步应用。改走"延迟一帧 + 队列"时
            # 首图常因视口尚未稳定、revision 被后续刷新顶掉而延迟数秒才显示。
            resized = self.crop_and_resize(new_pil, width, height)
            self.apply_data(resized, self.pil_to_rgba(resized), width, height)
            return

        owner._bg_revision += 1
        revision = owner._bg_revision
        self._start_fade(new_pil, width, height, revision)

    def _start_fade(self, new_pil, width, height, revision):
        """交叉淡入淡出：**节拍**由帧时钟走，**混合**由像素工作者做。

        旧实现是一条专门的线程 ``sleep(0.03)`` 30 次；现在每帧只决定是否该走下
        一步，实际混合提交给 :class:`PixelWorker`，结果经 ``_frame.call`` 回到
        主线程上纹理。``busy`` 是简单的背压：上一帧的混合还没做完就跳过这一帧，
        避免工作者队列堆积。
        """
        owner = self.owner
        frame = owner._frame
        state = {"pair": None, "step": 0, "busy": False, "preparing": False}

        def prepare_pair():
            """准备工作帧（旧图去缩放 + 新图重采样）：重活，走工作者线程。"""
            state["preparing"] = True
            def job():
                old_resized = owner._bg_pil_original
                new_resized = self.crop_and_resize(new_pil, width, height)
                if old_resized.size != (width, height):
                    old_resized = old_resized.resize(
                        (width, height), Image.Resampling.LANCZOS)
                old_data = self.pil_to_rgba(old_resized)
                frame.call(owner._apply_prepared_bg, old_resized, old_data,
                           width, height, revision)
                state["pair"] = (old_resized, new_resized)
            self._worker.submit(job)

        def blend_step():
            """混合出第 step 张中间图并提交给主线程上纹理（重活，走工作者线程）。"""
            pair = state["pair"]
            step = state["step"]

            def job():
                old_resized, new_resized = pair
                alpha = step / max(1, _FADE_STEPS - 1)
                blended = Image.blend(old_resized, new_resized, alpha)
                frame.call(owner._set_bg_texture, self.pil_to_rgba(blended),
                           width, height, revision)
                state["busy"] = False
            state["busy"] = True
            self._worker.submit(job)

        def tick():
            if owner._closing or revision != owner._bg_revision:
                frame.cancel(_FADE_TASK_KEY)
                return
            if state["pair"] is None:
                if not state["preparing"]:
                    prepare_pair()
                return          # 工作帧还在准备，下一帧再走
            if state["busy"]:
                return          # 上一帧的混合还没落地，这一帧让位
            if state["step"] >= _FADE_STEPS:
                frame.call(owner._finish_bg_fade, state["pair"][1], width, height,
                           revision)
                frame.cancel(_FADE_TASK_KEY)
                return
            blend_step()
            state["step"] += 1

        # 同 key 互斥：新的淡入淡出顶掉上一条还在跑的（revision 检查也会挡一道）
        frame.every(_FADE_STEP_SECONDS, tick, key=_FADE_TASK_KEY)

    def refresh(self, delay=0.08):
        """resize 后按当前尺寸重算背景（带防抖）。

        防抖由帧时钟承担（同 key 的任务互相顶掉，替代 ``threading.Timer`` +
        ``cancel()``）；真正的重采样仍然丢给像素工作者，不阻塞帧循环。
        """
        owner = self.owner
        full = owner._bg_pil_full
        if full is None:
            return
        if owner._layout_w <= 1 or owner._layout_h <= 1:
            return
        width, height = self.target_size()

        owner._bg_revision += 1
        revision = owner._bg_revision
        self.cancel_pending_refresh()

        def fire():
            if owner._closing or revision != owner._bg_revision:
                return
            self._worker.submit(
                lambda: self._prepare_refresh(full, width, height, revision))

        owner._frame.after(delay, fire, key=BG_REFRESH_TASK)

    def _prepare_refresh(self, full, width, height, revision):
        """（工作者线程）重采样到当前尺寸并交回主线程上纹理。

        结果按 (来源代, 尺寸) 缓存两份：窗口/全屏来回切换时第二个方向
        直接命中，不再重采样。
        """
        owner = self.owner
        key = (self._src_gen, width, height)
        data = self._resize_cache.get(key)
        pil_img = None
        if data is None:
            resized = self.crop_and_resize(full, width, height)
            data = self.pil_to_rgba(resized)
            self._resize_cache[key] = data
            while len(self._resize_cache) > 2:
                self._resize_cache.pop(next(iter(self._resize_cache)))
            pil_img = resized
        owner._frame.call(owner._apply_prepared_bg, pil_img,
                          data, width, height, revision)

    def apply_data(self, pil_img, rgba, width, height):
        """把 ``width × height`` 的 RGBA 数据上到背景纹理（仅帧线程）。

        迁移后**每次都是新纹理**（``TextureGpu`` 不能原地改像素，P0-S3 实测）：
        由 ``ui._apply_bg_data`` 调 ``create_texture_gpu_from_rgba_data`` 建新句柄、
        再丢掉旧句柄。旧实现那套「纹理只增不减 / uv 子区域 / 组合缓冲」是新架构下
        完全不需要的复杂度，已随本次迁移删除。

        ``pil_img`` 为 None 表示淡入淡出中间帧等不更新 ``_bg_pil_original`` 的写入。
        """
        self.owner._apply_bg_data(pil_img, rgba, width, height)

    def resolve_path(self, image_path):
        owner = self.owner
        processed = str(image_path).lstrip("\\/")
        candidates = []
        if os.path.isabs(image_path):
            candidates.append(os.path.normpath(image_path))
        if owner.scenario_id and owner.scenario_repo:
            candidates.append(os.path.normpath(os.path.join(owner.scenario_repo.root, owner.scenario_id, processed)))
        candidates.extend([
            os.path.normpath(processed),
            os.path.normpath(os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", processed)),
        ])
        for path in dict.fromkeys(candidates):
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def apply_filter(image, filter_effect):
        return image.filter(_PIL_FILTERS[filter_effect.lower()]) \
            if filter_effect.lower() in _PIL_FILTERS else image

    @staticmethod
    def crop_and_resize(image, width, height):
        width, height = int(width), int(height)
        if width <= 1 or height <= 1:
            return image
        target_ratio = width / height
        image_width, image_height = image.size
        if image_width / image_height > target_ratio:
            new_width = int(image_height * target_ratio)
            left = (image_width - new_width) // 2
            cropped = image.crop((left, 0, left + new_width, image_height))
        else:
            new_height = int(image_width / target_ratio)
            top = (image_height - new_height) // 2
            cropped = image.crop((0, top, image_width, top + new_height))
        return cropped.resize((width, height), Image.Resampling.LANCZOS)

    @staticmethod
    def pil_to_rgba(image):
        """PIL 图 → ``H×W×4`` 的 uint8 连续数组（``create_texture_gpu_from_rgba_data`` 的入参）。

        与旧 ``pil_to_dpg`` 的差异：不再逐像素除 255 转 float32。imgui 侧要的就是
        原始 8 位分量，省掉一次全图乘法（全屏图约 2.5k×1.6k，这一省是实打实的）。
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        width, height = image.size
        return np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(height, width, 4)
