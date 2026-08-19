"""
负责挑战包的创建、加密存储、导入、导出和元数据查询。
挑战包是包含角色、地标、描述风格、副本配置等数据的加密压缩包，
通过秘钥保护内容安全。
"""

import os
import json
import zipfile
import tempfile
import datetime
import hashlib
import hmac
import secrets
import shutil
from dataclasses import asdict
from typing import Optional, List, Dict, Tuple


class ChallengeService:
    """
    挑战包管理器核心类

    管理挑战包的存储目录、秘钥文件、加密/解密逻辑以及各种操作。
    所有挑战包以 `.chal` 扩展名存储在指定目录，秘钥保存在同目录的 `keys.json` 中，
    并同步到环境变量以便跨会话使用。
    """

    # 环境变量名，用于存储所有挑战包的秘钥映射
    ENV_KEY = "GIANTESS_CHALLENGE_KEYS"
    # 加密文件中用于校验数据完整性的魔数（Magic Number）
    MAGIC = b"\x8a\xf3\x2b\x9c\x4d\x7e\x1f\x65\xa0\xd8\xbb\x31\x56\xe9\xc7\x42"

    def __init__(self, settings_repo, character_repo=None, landmark_repo=None, quip_repo=None, dungeon_repo=None, data_dir="data", world_state=None):
        """
        初始化挑战包管理器

        :param settings_repo: 设置仓库，用于读取/保存存储目录配置
        :param character_repo: 角色仓库，用于加载角色数据
        :param landmark_repo: 地标仓库，用于加载地标数据
        :param quip_repo: 描述风格仓库，用于加载描述数据
        :param dungeon_repo: 副本仓库，用于加载副本配置
        :param data_dir: 默认数据目录，用于拼接默认存储路径
        :param world_state: 世界包激活状态（None 表示无世界包）
        """
        self.settings_repo = settings_repo
        self.character_repo = character_repo
        self.landmark_repo = landmark_repo
        self.quip_repo = quip_repo
        self.dungeon_repo = dungeon_repo
        self.data_dir = data_dir
        self.world_state = world_state
        # 内部缓存存储目录，None 表示从 settings 中读取
        self._storage_dir = None

    @property
    def storage_dir(self) -> str:
        # 挑战包文件存储在 packs/challenges
        return os.path.join(self.data_dir, "packs", "challenges")

    def pack_challenges_dir(self) -> Optional[str]:
        """世界包附带挑战包目录；未激活或不附带时返回 None。"""
        ws = self.world_state
        if ws is None or not ws.active or not ws.owns("challenges"):
            return None
        return ws.pack_path("challenges")

    def is_bundled(self, filename: str) -> bool:
        """该文件名是否为世界包附带的挑战包。"""
        pack_dir = self.pack_challenges_dir()
        if pack_dir is None:
            return False
        return os.path.isfile(os.path.join(pack_dir, filename))

    def _is_bundled_path(self, file_path: str) -> bool:
        pack_dir = self.pack_challenges_dir()
        if pack_dir is None:
            return False
        return os.path.normpath(os.path.dirname(file_path)) == os.path.normpath(pack_dir)

    def _pack_keys(self) -> Dict[str, str]:
        """世界包附带挑战包目录内的 keys.json（包名基名 -> 秘钥）。"""
        pack_dir = self.pack_challenges_dir()
        if pack_dir is None:
            return {}
        path = os.path.join(pack_dir, "keys.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def resolve_pack_path(self, pack_name: str) -> Optional[str]:
        """按包名（可带或不带 .chal）解析完整路径：世界包附带目录优先，其次自由目录。"""
        if pack_name.endswith('.chal'):
            filename = pack_name
        else:
            filename = f"{pack_name}.chal"
        pack_dir = self.pack_challenges_dir()
        if pack_dir is not None:
            packed = os.path.join(pack_dir, filename)
            if os.path.exists(packed):
                return packed
        free = os.path.join(self.storage_dir, filename)
        if os.path.exists(free):
            return free
        return None

    def set_storage_dir(self, path: str):
        """
        永久设置存储目录并保存到设置仓库

        :param path: 新的存储目录路径
        """
        settings = self.settings_repo.load()
        settings["challenge_storage_dir"] = path
        self.settings_repo.save(settings)
        self._storage_dir = path

    def _ensure_storage_dir(self):
        """确保存储目录存在，若不存在则创建"""
        os.makedirs(self.storage_dir, exist_ok=True)

    def _keys_file_path(self) -> str:
        """返回秘钥文件的完整路径（challenge_keys.json），存储在 user/ 下"""
        return os.path.join(self.data_dir, "user", "challenge_keys.json")

    def generate_key(self) -> str:
        """生成一个 64 位十六进制随机秘钥（用于加密挑战包）"""
        return secrets.token_hex(32)

    def _load_keys(self) -> Dict[str, str]:
        """
        从 keys.json 加载秘钥映射

        :return: 字典 {包名（不含扩展名）: 秘钥字符串}
        """
        result = {}
        path = self._keys_file_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
            except Exception:
                pass
        return result

    def _save_keys(self, keys: Dict[str, str]):
        """
        保存秘钥映射到 keys.json，并更新环境变量

        :param keys: 秘钥映射字典
        """
        self._ensure_storage_dir()
        path = self._keys_file_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)
        # 同步到环境变量，方便其他进程读取
        os.environ[self.ENV_KEY] = json.dumps(keys, ensure_ascii=False)

    def _register_key(self, pack_name: str, key: str):
        """
        为挑战包注册秘钥

        :param pack_name: 挑战包文件名（含 .chal 扩展名）
        :param key: 对应的秘钥
        """
        base = os.path.splitext(pack_name)[0]
        keys = self._load_keys()
        keys[base] = key
        self._save_keys(keys)

    def _get_key(self, pack_name: str, file_path: str = None) -> str:
        """
        根据挑战包文件名获取对应秘钥

        世界包附带包优先查包内 keys.json（无需本地秘钥注册）；
        否则回退到本地秘钥文件。

        :param pack_name: 挑战包文件名（可带或不带 .chal）
        :param file_path: 可选的文件完整路径（用于识别附带包）
        :return: 秘钥字符串，若未找到则返回空字符串
        """
        # 仅剥离 .chal 扩展名：基名本身可能包含点号（如版本后缀 dup_W1_1.0）
        base = pack_name[:-5] if pack_name.endswith(".chal") else pack_name
        if file_path is None:
            file_path = self.resolve_pack_path(pack_name)
        if file_path and self._is_bundled_path(file_path):
            pack_keys = self._pack_keys()
            key = pack_keys.get(base, "")
            if key:
                return key
        keys = self._load_keys()
        return keys.get(base, "")

    def get_all_pack_keys(self) -> Dict[str, str]:
        """
        获取所有已注册的挑战包秘钥

        优先从环境变量读取，若不存在则从 keys.json 加载并同步到环境变量。

        :return: 字典 {包名（不含扩展名）: 秘钥}
        """
        env_val = os.environ.get(self.ENV_KEY, "")
        if env_val:
            try:
                return json.loads(env_val)
            except Exception:
                pass
        keys = self._load_keys()
        if keys:
            os.environ[self.ENV_KEY] = json.dumps(keys, ensure_ascii=False)
        return keys

    def get_available_packs(self) -> List[str]:
        """
        获取所有可用的挑战包文件名（世界包附带包在前，自由包在后）。

        附带包直接列出包目录内的 .chal 文件；
        自由包优先显示已注册秘钥的包，然后列出所有 .chal 文件。

        :return: 文件名列表（如 ['example.chal', ...]）
        """
        self._ensure_storage_dir()
        packs = []
        pack_dir = self.pack_challenges_dir()
        if pack_dir is not None and os.path.isdir(pack_dir):
            for f in sorted(os.listdir(pack_dir)):
                if f.endswith(".chal"):
                    packs.append(f)
        keys = self.get_all_pack_keys()
        # 先添加已注册秘钥的包（确保它们存在；附带包同名时不再重复）
        for pack_base in keys:
            filename = f"{pack_base}.chal"
            file_path = os.path.join(self.storage_dir, filename)
            if os.path.exists(file_path) and filename not in packs:
                packs.append(filename)
        # 再补充所有 .chal 文件（可能包含未注册秘钥的包）
        all_files = [f for f in os.listdir(self.storage_dir) if f.endswith(".chal")]
        for f in all_files:
            if f not in packs:
                packs.append(f)
        return packs

    def has_any_valid_key(self) -> bool:
        """
        检查是否有任何已注册秘钥且对应文件存在

        :return: True 表示至少有一个有效的挑战包
        """
        keys = self.get_all_pack_keys()
        if not keys:
            return False
        self._ensure_storage_dir()
        for pack_base in keys:
            filename = f"{pack_base}.chal"
            file_path = os.path.join(self.storage_dir, filename)
            if os.path.exists(file_path):
                return True
        return False

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """
        使用 PBKDF2 从密码派生出 32 字节加密密钥

        :param password: 用户密码（秘钥字符串）
        :param salt: 盐值（使用 IV）
        :return: 32 字节密钥
        """
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                   salt, 100000, dklen=32)

    def _encrypt(self, plaintext: bytes, password: str) -> bytes:
        """
        使用流加密（基于 HMAC-SHA256 的异或加密）加密明文

        加密结构：IV (16字节) + 密文
        密文由 MAGIC + 明文与密钥流逐字节异或得到。
        密钥流由 HMAC-SHA256(派生密钥, IV || 计数器) 生成。

        :param plaintext: 待加密的字节序列
        :param password: 秘钥字符串
        :return: 加密后的字节序列 (IV + 密文)
        """
        iv = os.urandom(16)
        key = self._derive_key(password, iv)
        # 在明文前添加 MAGIC 用于解密后校验
        payload = self.MAGIC + plaintext

        ciphertext = bytearray()
        for i, b in enumerate(payload):
            # 生成密钥流字节：使用 HMAC 对 iv + 4字节计数器做摘要，取第一个字节
            keystream_byte = hmac.digest(key, iv + i.to_bytes(4, 'big'), 'sha256')[0]
            ciphertext.append(b ^ keystream_byte)

        return iv + bytes(ciphertext)

    def _decrypt(self, data: bytes, password: str) -> Optional[bytes]:
        """
        解密加密数据，与 _encrypt 配套

        :param data: 加密数据（IV + 密文）
        :param password: 秘钥字符串
        :return: 解密后的明文字节序列，若校验失败则抛出异常
        :raises ValueError: 数据太短、秘钥不正确或数据损坏
        """
        if len(data) < 16:
            raise ValueError("数据太短")

        iv = data[:16]
        ciphertext = data[16:]
        key = self._derive_key(password, iv)

        # 异或解密得到 payload（包含 MAGIC + 明文）
        payload = bytearray()
        for i, b in enumerate(ciphertext):
            keystream_byte = hmac.digest(key, iv + i.to_bytes(4, 'big'), 'sha256')[0]
            payload.append(b ^ keystream_byte)

        payload = bytes(payload)
        # 校验 MAGIC，若不匹配说明秘钥错误或数据损坏
        if len(payload) < len(self.MAGIC) or payload[:len(self.MAGIC)] != self.MAGIC:
            raise ValueError("秘钥不正确或数据损坏")

        # 返回去除 MAGIC 后的真实明文
        return payload[len(self.MAGIC):]

    def create_challenge(self, character_id: str, landmark_styles: List[str],
                         quip_styles: List[str], dungeon_id: str,
                         intro: str, pack_name: str) -> str:
        """
        创建一个新的挑战包

        收集角色数据、地标数据、描述数据、副本配置，序列化后加密并打包为 .chal 文件。

        :param character_id: 角色ID
        :param landmark_styles: 地标风格组列表
        :param quip_styles: 描述风格组列表
        :param dungeon_id: 副本方案ID
        :param intro: 挑战包简介
        :param pack_name: 包名（不含扩展名或含扩展名均可）
        :return: 生成的秘钥字符串
        :raises ValueError: 缺少必要仓库、角色或副本加载失败等
        """
        self._ensure_storage_dir()
        if not all([self.character_repo, self.landmark_repo, self.quip_repo, self.dungeon_repo]):
            raise ValueError("创建挑战包需要提供所有仓库实例")

        # 生成随机秘钥
        key = self.generate_key()

        # 加载角色数据
        character_data = self.character_repo.load(character_id)
        if not character_data:
            raise ValueError(f"无法加载角色 '{character_id}'")

        # 加载地标数据
        landmark_data = {}
        for style in landmark_styles:
            landmarks = self.landmark_repo.load(style)
            landmark_data[style] = [asdict(lm) for lm in landmarks]

        # 加载描述风格数据（需要序列化为可JSON格式）
        quip_data = {}
        for style in quip_styles:
            data = self.quip_repo.load(style)
            # 将嵌套字典的键 (size_category, (index, direction)) 转为字符串
            serializable = {}
            for size_cat, matrix in data.items():
                serializable[size_cat] = {}
                for (i, d), qlist in matrix.items():
                    serializable[size_cat][f"{i}_{d}"] = qlist
            quip_data[style] = serializable

        # 加载副本配置
        dungeon_config = self.dungeon_repo.load_config(dungeon_id)
        if not dungeon_config:
            raise ValueError(f"无法加载副本 '{dungeon_id}'")

        # 构建挑战包数据字典
        pack_data = {
            "version": "1.0",
            "intro": intro,
            "character_id": character_id,
            "character_data": asdict(character_data),          # 角色数据（dataclass 转字典）
            "landmark_styles": landmark_styles,
            "landmark_data": landmark_data,                    # 地标数据字典
            "quip_styles": quip_styles,
            "quip_data": quip_data,                            # 描述风格数据字典
            "dungeon_id": dungeon_id,
            "dungeon_config": dungeon_config,                  # 副本配置字典
            "created_at": datetime.datetime.now().isoformat()
        }

        # 序列化为 JSON 字符串并加密
        plaintext = json.dumps(pack_data, ensure_ascii=False, indent=2).encode('utf-8')
        encrypted = self._encrypt(plaintext, key)

        # 确保包名有 .chal 扩展名
        if not pack_name.endswith('.chal'):
            pack_name += '.chal'
        output_path = os.path.join(self.storage_dir, pack_name)

        # 将加密数据写入临时文件，再压缩为 zip（单文件）
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix='.bin')
            os.close(fd)
            with open(tmp_path, 'wb') as f:
                f.write(encrypted)

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(tmp_path, 'challenge_data.bin')
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 注册秘钥
        self._register_key(pack_name, key)
        return key

    def open_challenge(self, file_path: str, password: str = None) -> Optional[dict]:
        """
        打开并解密挑战包文件

        若未提供 password，则自动从已注册秘钥中查找。

        :param file_path: 挑战包文件路径
        :param password: 可选秘钥，若为 None 则自动查找
        :return: 解密后的数据字典
        :raises ValueError: 未找到秘钥、读取失败、解密失败或数据损坏
        """
        if password is None:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            password = self._get_key(base_name, file_path=file_path)
            if not password:
                raise ValueError(f"未找到挑战包 '{base_name}' 的秘钥，请先创建或导入")

        # 从 zip 中读取加密数据
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                encrypted = zf.read('challenge_data.bin')
        except Exception as e:
            raise ValueError(f"读取挑战包失败: {e}")

        # 解密
        try:
            plaintext = self._decrypt(encrypted, password)
            if plaintext is None:
                raise ValueError("挑战包解密失败，秘钥不正确")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"挑战包解密失败，秘钥可能不正确: {e}")

        # 解析 JSON
        try:
            data = json.loads(plaintext.decode('utf-8'))
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("挑战包数据损坏或秘钥不正确")

    def open_challenge_by_name(self, pack_name: str) -> Optional[dict]:
        """
        根据包名（不含扩展名或含扩展名）打开挑战包

        世界包附带目录优先，其次自由存储目录。

        :param pack_name: 包名（可带或不带 .chal）
        :return: 解密后的数据字典
        :raises ValueError: 文件不存在或打开失败
        """
        file_path = self.resolve_pack_path(pack_name)
        if file_path is None:
            raise ValueError(f"挑战包文件不存在: {pack_name}")
        return self.open_challenge(file_path)

    def try_open_with_keys(self, file_path: str) -> Optional[Tuple[dict, str, str]]:
        """
        尝试使用所有已注册秘钥（含世界包附带包秘钥）打开挑战包，直到成功

        :param file_path: 挑战包文件路径
        :return: 若成功，返回 (数据字典, 秘钥, 包名)，否则返回 None
        """
        if not os.path.exists(file_path):
            return None
        keys = dict(self.get_all_pack_keys())
        if self._is_bundled_path(file_path):
            keys.update(self._pack_keys())
        for pack_base, key in keys.items():
            try:
                data = self.open_challenge(file_path, password=key)
                if data:
                    return (data, key, pack_base)
            except Exception:
                continue
        return None

    def get_challenge_meta(self, filename: str, file_path: str = None) -> Optional[dict]:
        """
        获取挑战包的元数据（简介、角色名、副本ID、风格等），无需完整加载所有数据

        :param filename: 挑战包文件名（含 .chal）
        :param file_path: 可选完整路径；缺省时按文件名解析（附带包优先）
        :return: 元数据字典（含 bundled/file_path），若无法打开则返回 None
        """
        if file_path is None:
            file_path = self.resolve_pack_path(filename)
        if file_path is None or not os.path.exists(file_path):
            return None
        result = self.try_open_with_keys(file_path)
        if result is None:
            return None
        data, key, pack_base = result
        return {
            "filename": filename,
            "file_path": file_path,
            "pack_base": pack_base,
            "bundled": self._is_bundled_path(file_path),
            "intro": data.get("intro", ""),
            "character_name": data.get("character_data", {}).get("name", "未知"),
            "dungeon_id": data.get("dungeon_id", ""),
            "landmark_styles": data.get("landmark_styles", []),
            "quip_styles": data.get("quip_styles", []),
            "created_at": data.get("created_at", "")
        }

    def get_all_metas(self) -> List[dict]:
        """
        获取全部挑战包的元数据列表（世界包附带包在前，自由包在后）

        :return: 元数据字典列表
        """
        self._ensure_storage_dir()
        metas = []
        pack_dir = self.pack_challenges_dir()
        if pack_dir is not None and os.path.isdir(pack_dir):
            for filename in sorted(os.listdir(pack_dir)):
                if not filename.endswith(".chal"):
                    continue
                meta = self.get_challenge_meta(
                    filename, file_path=os.path.join(pack_dir, filename))
                if meta:
                    metas.append(meta)
        for filename in self.get_available_packs():
            if self.is_bundled(filename):
                continue
            file_path = os.path.join(self.storage_dir, filename)
            if not os.path.exists(file_path):
                continue
            meta = self.get_challenge_meta(filename, file_path=file_path)
            if meta:
                metas.append(meta)
        return metas

    def import_pack(self, source_path: str, key: str) -> str:
        """
        导入一个挑战包到存储目录

        先验证秘钥是否正确（尝试打开），然后复制文件并注册秘钥。

        :param source_path: 源挑战包文件路径
        :param key: 对应的秘钥
        :return: 目标文件的完整路径
        :raises ValueError: 秘钥不正确或打开失败
        """
        self._ensure_storage_dir()
        # 验证秘钥是否正确
        try:
            data = self.open_challenge(source_path, password=key)
        except Exception as e:
            raise ValueError(f"导入失败，秘钥不正确: {e}")

        # 确定目标文件名（保留原文件名）
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        dest_path = os.path.join(self.storage_dir, f"{base_name}.chal")

        shutil.copy2(source_path, dest_path)
        self._register_key(f"{base_name}.chal", key)
        return dest_path