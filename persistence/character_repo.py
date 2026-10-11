import datetime
import os
import shutil
from dataclasses import asdict
from typing import Optional

from core import imaging
from core.models import CharacterSnapshot
from .json_store import load_json_with_backup, write_json_atomic


class CharacterRepo:
    def __init__(self, data_dir: str = "data"):
        self._dir = os.path.join(data_dir, "archives")

    @property
    def states_dir(self) -> str:
        return self._dir

    def _character_path(self, giantess_id: str) -> str:
        return os.path.join(self._dir, giantess_id)

    def _data_file_path(self, giantess_id: str) -> str:
        return os.path.join(self._character_path(giantess_id), "info.json")

    def save(self, state: CharacterSnapshot):
        state.updated_at = datetime.datetime.now().isoformat()
        char_dir = self._character_path(state.giantess_id)
        os.makedirs(char_dir, exist_ok=True)
        # 角色档案与副本方案同待遇：原子写 + 覆盖前留 .bak，
        # 保存中途崩溃/断电不会把档案截断清零
        write_json_atomic(os.path.join(char_dir, "info.json"), asdict(state))

    def load(self, giantess_id: str) -> Optional[CharacterSnapshot]:
        data = load_json_with_backup(self._data_file_path(giantess_id))
        if data is None:
            return None
        return CharacterSnapshot.from_dict(data)

    def save_avatar(self, giantess_id: str, source_path: str, low_resolution: bool = False) -> str:
        char_dir = self._character_path(giantess_id)
        avatar_dir = os.path.join(char_dir, "avatar")
        os.makedirs(avatar_dir, exist_ok=True)
        existing = [f for f in os.listdir(avatar_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp'))]
        next_num = 1
        if existing:
            nums = []
            for f in existing:
                base = os.path.splitext(f)[0]
                if base.isdigit():
                    nums.append(int(base))
            if nums:
                next_num = max(nums) + 1
        ext = os.path.splitext(source_path)[1] or '.png'

        if low_resolution:
            try:
                from PIL import Image
                img = Image.open(source_path)
                if getattr(img, "is_animated", False):
                    img.seek(0)
                resized = imaging.resize_low_resolution(img)
                if resized.mode not in ("RGB", "RGBA"):
                    resized = resized.convert("RGB")
                dest = os.path.join(avatar_dir, f"{next_num}.png")
                resized.save(dest, "PNG")
                return f"avatar/{next_num}.png"
            except Exception:
                pass

        dest = os.path.join(avatar_dir, f"{next_num}{ext}")
        shutil.copy2(source_path, dest)
        return f"avatar/{next_num}{ext}"

    def get_avatar_abspath(self, giantess_id: str, avatar_path: str) -> str:
        if not avatar_path:
            return ""
        if os.path.isabs(avatar_path):
            return avatar_path
        return os.path.join(self._character_path(giantess_id), avatar_path)

    def delete(self, giantess_id: str) -> bool:
        char_dir = self._character_path(giantess_id)
        if not os.path.exists(char_dir):
            return False
        shutil.rmtree(char_dir)
        return True

    def list_ids(self):
        if not os.path.exists(self._dir):
            return []
        return [d for d in os.listdir(self._dir)
                if os.path.isdir(os.path.join(self._dir, d))
                and os.path.exists(os.path.join(self._dir, d, "info.json"))]
