"""核心领域层：模型、尺寸逻辑、行为运行时与 AI 接入。

本包由「阶段 2（收编根目录）」从仓库根目录移入，对应关系::

    models.py            -> core/models.py
    address_model.py     -> core/address_model.py
    logic.py             -> core/logic.py
    behavior_runtime.py  -> core/behavior_runtime.py
    ai.py                -> core/ai.py

``imaging.py``（2026-10-06 阶段 3.2.3）与 ``scale_reference.py``（同日自 services/ 上浮，
提示词参考数据，将来副本与外部工具也要读）是后加的两个模块：前者是纯图像处理
（裁剪 / 缩放 / 缩略图 / base64），原先混在 ``services/image_service.py`` 里，只依赖
PIL 与标准库；对应地，``PIL`` 已退出守卫的「下层禁 UI 框架」整包禁列，只禁
``PIL.ImageTk`` / ``PIL.ImageGrab``。

``context.py``（``ExplorationContext``）曾同样被搬进本包，但**阶段 3.1 已把它整层
迁走**：它跨越 core → persistence → services → ui 四层，在 ``core`` 里属于违规，守卫
曾为此开了一条全图唯一的双向豁免（``orchestration`` 层）。现在它连同拆出的职责子系统
一起住在 ``services/exploration/``，那条豁免也已删除；
``tests/check_import_graph.py`` 会拒绝任何把它放回 ``core/`` 的尝试。

**分层**：``core`` 只能依赖 ``infra``（``paths``），不得 import
``persistence`` / ``services`` / ``dungeon`` / ``ui``。**2026-10-06 阶段 3.2 起本包
零例外**（原先 ``behavior_runtime.py`` 延迟导入 ``persistence.world_pack`` 的那条已随
``resolve_behavior_source`` 下移而删除）；例外逐条登记在
``tests/check_import_graph.py`` 的 ``KNOWN_EXCEPTIONS`` 里。

``appearance.py``（外观模式的唯一来源）也住在这里：它零 import，却被两套界面与服务层
同时读取，放最底层是唯一能让所有调用方都合法引用它的位置。别因为它「像界面概念」而
挪回 ``ui/``。

**行为包 hook key 不是模块路径**：``core/logic/``（原 ``logic.py``，2026-10-06 §4.1
按职责拆为 ``sizing`` / ``quips`` / ``simulation`` / ``text`` 四个子模块，
``core/logic/__init__.py`` 薄壳再导出全部公开名）里的
``@behavior_hook("logic", "format_size")`` 拼出的 key 是 ``"logic.format_size"``，
这个 key 是**已部署世界包行为包的公开契约**（示例见
``data/static/behaviors/imperial_units/``）。重命名本包或重排文件时，**不得**
把它改成 ``"core.logic.format_size"``——那会让用户已安装的行为包静默失效。
"""
