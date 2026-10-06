"""服务层：跨界面复用的领域服务与流程编排。

依赖只能向下（core / dungeon / persistence / infra），由
``tests/check_import_graph.py`` 强制；ui 可直接引用本层，本层禁止
import tkinter / customtkinter / dearpygui。

**本包 ``__init__`` 不再充当中转门面**（§4.2 步骤 7）：调用点一律按
子域直连，别再 ``from services import X``。域分布——

====================  ==================================================
``creation_service``  角色创建（CreationService；行为包 import 契约，路径勿动）
``state_service``     角色状态演化（StateService；14 个行为钩子，同上）
``character/``        角色自身长时行为：档案导入导出、离线结算、节奏
``news/``             角色近况动态（附加功能）
``chat/``             聊天：协议、投递调度、人设组装、经历事件
``exploration/``      探索流程编排（ExplorationContext 及其职责子系统）
``worlds/``           世界包生命周期 + 在线地址注册
``challenges/``       挑战包：加密存储、秘钥、数据导入
``preview/``          身材剪影渲染（纯 PIL）
``ui_mode``           界面模式词表与启动界面偏好（3.2.2 判例）
====================  ==================================================
"""
