import datetime
import json
import os
import shutil
from dataclasses import asdict
from typing import Optional

from models import CharacterSnapshot

from services.image_service import ImageService


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
        path = os.path.join(char_dir, "info.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(asdict(state), f, ensure_ascii=False, indent=2)

    def load(self, giantess_id: str) -> Optional[CharacterSnapshot]:
        path = self._data_file_path(giantess_id)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
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
                resized = ImageService.resize_low_resolution(img)
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
