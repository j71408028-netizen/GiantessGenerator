"""挂件版弹框：纯 tkinter，不碰 customtkinter。

副本窗口的收尾提示与询问（是否保存回放、加载回放出错等）经宿主端口
``TkHost.dialog`` 发出，宿主缺省用 ``ui.common.dialogs``——那是 customtkinter
实现，顶层就把 CTk 拉起来，在挂件（纯 ``tk.Tk`` 根窗口）下既违背挂件层的
依赖约定，实测也会卡在 CTk 的模态等待里。因此挂件侧注入本模块的实现。

对外只暴露 ``showinfo/showwarning/showerror/askyesno``，签名与
``ui.common.dialogs`` 的对应函数一致（``(title, message)``），这样
``TkHost`` 无需区分宿主种类。
"""

from tkinter import messagebox


class MiniDialogs:
    """把宿主弹框端口映射到原生 ``messagebox``。

    ``root`` 为挂件根窗口：弹框前若它处于隐藏状态（副本运行期间宿主被藏起）
    先恢复，否则原生对话框没有可见的父窗口。
    """

    def __init__(self, root=None):
        self.root = root

    def _parent(self):
        root = self.root
        if root is None:
            return None
        try:
            if not root.winfo_viewable():
                root.deiconify()
                root.lift()
        except Exception:
            pass
        return root

    def showinfo(self, title, message):
        messagebox.showinfo(title, message, parent=self._parent())

    def showwarning(self, title, message):
        messagebox.showwarning(title, message, parent=self._parent())

    def showerror(self, title, message):
        messagebox.showerror(title, message, parent=self._parent())

    def askyesno(self, title, message):
        return bool(messagebox.askyesno(title, message, parent=self._parent()))
