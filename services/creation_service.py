import hashlib
import random
from typing import Optional, Dict, Tuple

from models import Personality, BodyPreset
from persistence.name_repo import NameRepo, DEFAULT_NAME_TABLE
from behavior_runtime import behavior_hook


class CreationService:
    def __init__(self, name_repo: Optional[NameRepo] = None,
                 name_table: str = DEFAULT_NAME_TABLE):
        self._name_repo = name_repo or NameRepo()
        self.name_table = name_table or DEFAULT_NAME_TABLE
        self._name_data_cache = {}

    def set_name_table(self, table_name: str):
        self.name_table = table_name or DEFAULT_NAME_TABLE
        # 表切换后丢弃缓存
        self._name_data_cache.pop(self.name_table, None)
        self._name_data_cache.pop(DEFAULT_NAME_TABLE, None)

    def _load_name_data(self) -> Tuple[list, list, list, list]:
        if self.name_table not in self._name_data_cache:
            surnames, weights, names, nicks = self._name_repo.load(self.name_table)
            if not surnames and not names and not nicks and self.name_table != DEFAULT_NAME_TABLE:
                surnames, weights, names, nicks = self._name_repo.load(DEFAULT_NAME_TABLE)
            self._name_data_cache[self.name_table] = (surnames, weights, names, nicks)
        return self._name_data_cache[self.name_table]

    def generate_random_name_nick(self) -> Tuple[str, str]:
        surnames, weights, names, nicks = self._load_name_data()
        surname = random.choices(surnames, weights=weights, k=1)[0] if surnames and weights else "王"
        given = random.choice(names) if names else "静"
        name = surname + given
        nick = random.choice(nicks) if nicks else ""
        return name, nick

    @staticmethod
    @behavior_hook("CreationService", "generate_random_height")
    def generate_random_height() -> float:
        alpha = 256
        theta = 0.00625
        height = random.gammavariate(alpha, theta)
        formated_height = round(max(min(height, 2.2), 1.2), 2)
        return formated_height

    @staticmethod
    @behavior_hook("CreationService", "calculate_height")
    def calculate_height(option: str, custom_val: str,
                         min_slide: float, max_slide: float,
                         use_will: bool, greed: int,
                         rng: random.Random,
                         personality: Optional[Personality] = None
                         ) -> Tuple[float, Optional[str]]:
        if option == "custom":
            try:
                return max(0.1, float(custom_val)), None
            except ValueError:
                return 100.0, None

        if not use_will:
            return 10 ** random.uniform(min_slide, max_slide), None

        # 使用意愿时，在超额范围内采样 will
        will_lower, will_upper = -1.5, 1.5
        if personality is not None:
            diff = personality.init_destruction - 2.5 if personality.init_destruction != 0 else 0
            strength = personality.skip_base_prob
            if diff > 0:
                will_lower += diff * strength * 0.1
            else:
                will_upper += diff * strength * 0.1

        will = rng.uniform(will_lower, will_upper)

        center = (max_slide + min_slide) / 2
        half_range = (max_slide - min_slide) / 2

        attempt_prob = greed / 100 * abs(will)

        if -1.0 <= will <= 1.0:
            # -- A: will 不超范围，within --
            scale = will
            status = "within"

        else:
            attempt_succeeded = False

            # 第一次贪婪判定
            if greed > 0 and rng.random() < attempt_prob:
                attempt_succeeded = True
            else:
                # 线性映射 will: [-1.5, 1.5] → [remap_min, remap_max]
                if personality is not None:
                    remap_min = max(-1.5, -1 - greed / 200 + personality.sensitivity / 20)
                    remap_max = min(1.5, 1 + greed / 200 - personality.sensitivity / 20)
                    remapped_will = (will + 1.5) / 3.0 * (remap_max - remap_min) + remap_min
                    remap_attempt_prob = greed / 100 * abs(remapped_will)
                    # 第二次贪婪判定
                    if greed > 0 and rng.random() < remap_attempt_prob:
                        will = remapped_will
                        attempt_succeeded = True

            if attempt_succeeded:
                if will > 1.0:
                    # -- B: 向上突破成功，implemented  --
                    # 通过超1/4圆函数转换 will 为 scale
                    t = (will - 1) / 0.5
                    scale = 4 - 3 * (1 - t ** 1.7) ** 0.4
                    status = "implemented"
                elif will < -1.0:
                    # -- C: 向下突破成功，implemented  --
                    # 线性映射 will: [-1.5, -1] → [floor_scale, -1]
                    # floor_scale 为使 log_h 为 -0.5 时的 scale 值
                    floor_scale = (-0.5 - center) / half_range
                    t = (will + 1.5) / 0.5
                    scale = floor_scale + (-1 - floor_scale) * t
                    status = "implemented"
                else:
                    # -- D: 失败一次后妥协，failed  --
                    scale = will
                    status = "failed"
            else:
                # -- E: 两次突破失败，failed --
                if rng.random() < 0.5:
                    scale = 1.0 if will > 1.0 else -1.0
                else:
                    scale = (will + 1.5) / 3.0 * 2.0 - 1.0
                status = "failed"

        log_h = center + half_range * scale
        return 10 ** log_h, status

    @staticmethod
    def get_body_parts(height: float, preset: BodyPreset) -> Dict[str, float]:
        return {
            "身高": height,
            "腿长": height * preset.leg_ratio,
            "脚长": height * preset.foot_length_ratio,
            "臂长": height * preset.arm_span_ratio,
            "食指长度": height * preset.index_finger_ratio,
            "手掌长度": height * preset.palm_length_ratio,
            "胸宽": height * preset.chest_width_ratio,
            "大腿直径": height * preset.thigh_diameter_ratio,
            "小臂直径": height * preset.forearm_diameter_ratio,
            "膝盖高度": height * preset.knee_height_ratio,
            "脚踝高度": height * preset.ankle_height_ratio,
            "指缝宽度": height * preset.finger_gap_ratio,
            "步长": height * preset.stride_ratio,
            "食指直径": height * preset.index_finger_diameter_ratio,
            "指纹宽度": height * preset.fingerprint_width_ratio,
        }

    @staticmethod
    def core_from_params(params: dict, settings: dict,
                         preset_repo, personality_repo) -> dict:
        rng = CreationService.get_deterministic_rng(
            params["name"], extra_seed=settings.get("seed", 0)
        ) if params["will"] else random

        personality_obj = params["current_personality_obj"]
        preset_obj = params["current_preset_obj"]

        if personality_obj is None:
            personalities = personality_repo.load()
            personality_obj = rng.choice(personalities) if personalities else None
        if preset_obj is None:
            presets = preset_repo.load()
            preset_obj = rng.choice(presets) if presets else None

        height_option = params["height_option"]
        custom_height = params["custom_height"]
        min_val = params["min_slider"]
        max_val = params["max_slider"]
        will = params["will"]
        greed = params["greed"]
        world_setting = settings.get("world_setting", "appear")

        multiplier, will_status = CreationService.calculate_height(
            height_option, custom_height,
            min_val, max_val, will, greed, rng,
            personality=personality_obj
        )

        original_height = float(params["original_height"])
        if world_setting in ("abs_giant", "rel_giant"):
            height = original_height * multiplier if world_setting == "rel_giant" else multiplier
        else:
            height = multiplier

        body_parts = CreationService.get_body_parts(height, preset_obj)

        uploaded_image = params["uploaded_image_path"]
        if not uploaded_image and settings.get("use_preview_image_as_avatar", False):
            uploaded_image = CreationService._render_body_preview_to_temp(body_parts, height)

        return {
            "name": params["name"],
            "nick": params["nick"],
            "personality_obj": personality_obj,
            "preset_obj": preset_obj,
            "height": height,
            "original_height": original_height,
            "body_parts": body_parts,
            "base_intrusion": personality_obj.init_intrusion,
            "base_destruction": personality_obj.init_destruction,
            "greed": greed,
            "will": will,
            "will_status": will_status,
            "selected_tags": params["selected_tags"],
            "intro_hidden": params["intro_hidden"],
            "intro_visible": params["intro_visible"],
            "birthday": params.get("birthday", ""),
            "uploaded_image": uploaded_image,
            "multiplier": multiplier,
        }

    @staticmethod
    def _render_body_preview_to_temp(body_parts: dict, height: float) -> str:
        """未上传形象时把身材参数渲染为 PNG 临时文件，返回路径；失败返回 ''。"""
        import os
        import tempfile
        try:
            from ui.exploration.creation_params_dlg import render_body_preview_to_file
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            if render_body_preview_to_file(body_parts, height, tmp_path):
                return tmp_path
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        except Exception:
            pass
        return ""

    @staticmethod
    def get_deterministic_rng(name: str, extra_seed: int = 0) -> random.Random:
        """根据名字和额外种子创建确定性随机数生成器"""
        normalized = name.strip().lower()
        name_seed = int(hashlib.md5(normalized.encode('utf-8')).hexdigest(), 16) % (2 ** 32)
        combined = (name_seed + extra_seed) & 0xFFFFFFFF
        return random.Random(combined)
