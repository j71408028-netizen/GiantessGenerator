"""角色档案与生命周期：角色自身长时行为的域包（§4.2 步骤 6 自
``character_service/`` 改名归位）。

收窄后的边界（定版）：**只收角色自身的长时行为**——离线结算（``offline``）、
演化节奏（``rhythm``）、档案导入导出（``archive_export``）。

不属于本包的角色相关件：创建与状态两个核心服务是行为包 import 契约，留
``services/`` 顶层（``creation_service`` / ``state_service``）；近况动态是
附加数据源功能，独立成 ``services/news/``；聊天支撑（人设组装、经历事件）
在 ``services/chat/``。
"""
