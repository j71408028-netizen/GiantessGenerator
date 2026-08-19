import os
import re

from services.challenge_service import ChallengeService


def build_detail_pools(quips):
    pattern = r'\[([a-e]):(\d+):([^\]]+)\]'
    pools = {}
    for size_cat, matrix in quips.items():
        pools[size_cat] = {}
        for (i, d), quip_dicts in matrix.items():
            for qd in quip_dicts:
                text = qd["text"]
                style = qd["style"]
                if style not in pools[size_cat]:
                    pools[size_cat][style] = {}
                matches = re.findall(pattern, text)
                for letter, num, content in matches:
                    if content.strip().upper() == "MARK":
                        continue
                    if letter not in pools[size_cat][style]:
                        pools[size_cat][style][letter] = {}
                    if num not in pools[size_cat][style][letter]:
                        pools[size_cat][style][letter][num] = set()
                    pools[size_cat][style][letter][num].add(content)
        for style, letters in pools[size_cat].items():
            for letter, nums in letters.items():
                for num, cont_set in nums.items():
                    pools[size_cat][style][letter][num] = list(cont_set)
    return pools


def get_challenge_packs(settings_repo):
    cm = ChallengeService(settings_repo)
    if not cm.has_any_valid_key():
        return []
    return cm.get_available_packs()


def _open_challenge_pack(settings_repo, pack_name):
    cm = ChallengeService(settings_repo)
    file_path = os.path.join(cm.storage_dir, pack_name)
    result = cm.try_open_with_keys(file_path)
    if result is None:
        raise ValueError(f"无法读取挑战包 '{pack_name}'，未找到对应秘钥")
    data, _, _ = result
    if not data:
        raise ValueError("挑战包数据为空")
    return data


def import_landmark_challenge_pack(settings_repo, pack_name, repo):
    from models import Landmark
    data = _open_challenge_pack(settings_repo, pack_name)
    styles = data.get("landmark_styles", [])
    raw_data = data.get("landmark_data", {})
    if not styles or not raw_data:
        raise ValueError("该挑战包中没有地标数据")
    imported_count = 0
    for style in styles:
        raw = raw_data.get(style, [])
        if not raw:
            continue
        landmarks = [Landmark(**item) for item in raw]
        repo.save(style, landmarks)
        imported_count += len(landmarks)
        if style not in repo.get_styles():
            repo.create_style(style)
    return imported_count


def import_quip_challenge_pack(settings_repo, pack_name, repo):
    data = _open_challenge_pack(settings_repo, pack_name)
    styles = data.get("quip_styles", [])
    raw_data = data.get("quip_data", {})
    if not styles or not raw_data:
        raise ValueError("该挑战包中没有描述数据")
    imported_count = 0
    for style in styles:
        raw = raw_data.get(style, {})
        if not raw:
            continue
        converted = {}
        for size_cat, matrix in raw.items():
            converted[size_cat] = {}
            for key, qlist in matrix.items():
                if "_" in key:
                    parts = key.split("_")
                    if len(parts) == 2:
                        converted[size_cat][(int(parts[0]), int(parts[1]))] = qlist
                    else:
                        converted[size_cat][key] = qlist
                else:
                    converted[size_cat][key] = qlist
            imported_count += sum(len(qlist) for qlist in converted[size_cat].values())
        repo.save(style, converted)
        if style not in repo.get_styles():
            repo.create_style(style)
    return imported_count
