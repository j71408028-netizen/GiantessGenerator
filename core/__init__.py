"""核心领域层：模型、尺寸逻辑、行为运行时与 AI 接入。

本包由「阶段 2（收编根目录）」从仓库根目录移入，对应关系::

    models.py            -> core/models.py
    address_model.py     -> core/address_model.py
    logic.py             -> core/logic.py
    behavior_runtime.py  -> core/behavior_runtime.py
    ai.py                -> core/ai.py

``context.py``（``ExplorationContext``）曾同样被搬进本包，但**阶段 3.1 已把它整层
迁走**：它跨越 core → persistence → services → ui 四层，在 ``core`` 里属于违规，守卫
曾为此开了一条全图唯一的双向豁免（``orchestration`` 层）。现在它连同拆出的职责子系统
一起住在 ``services/exploration/``，那条豁免也已删除；
``tests/check_import_graph.py`` 会拒绝任何把它放回 ``core/`` 的尝试。

**分层**：``core`` 只能依赖 ``infra``（``paths``），不得 import
``persistence`` / ``services`` / ``dungeon`` / ``ui``。唯一的越界例外是
``behavior_runtime.py`` 延迟导入 ``persistence.world_pack``（行为包解析器），
逐条列在 ``tests/check_import_graph.py`` 的 ``KNOWN_EXCEPTIONS`` 里。

**行为包 hook key 不是模块路径**：``logic.py`` 里的
``@behavior_hook("logic", "format_size")`` 拼出的 key 是 ``"logic.format_size"``，
这个 key 是**已部署世界包行为包的公开契约**（示例见
``data/static/behaviors/imperial_units/``）。重命名本包或重排文件时，**不得**
把它改成 ``"core.logic.format_size"``——那会让用户已安装的行为包静默失效。
"""
