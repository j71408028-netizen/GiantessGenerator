"""世界包域：世界包的导入、导出、激活、卸载、解散与在线地址注册（§4.2 步骤 4 自
``world_service.py`` / ``address_registry.py`` 归位）。

世界包归档 ``<world_id>.world.zip``，解压部署到 ``data/worlds/<world_id>/``；
激活时包内资源按类型接管各数据源，自由数据不受影响。地址注册表
（``address_registry``）是世界地址的在线登记客户端，服务于世界包选址。
"""

from services.worlds.service import WorldManager
