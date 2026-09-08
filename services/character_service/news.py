import csv
import datetime
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from logic import get_size_category
from paths import data_dir


NEWS_COLUMNS = (
    "尺寸等级", "纪念", "介入度", "破坏性", "正文",
    "替换队列1", "替换队列2",
)
DEFAULT_NEWS_TABLE = "default"
_REPLACEMENT_KEYS = {
    1: ("{a}", "{1}"),
    2: ("{b}", "{2}"),
}


_FESTIVAL = {
    "0101": "元旦",
    "0401": "愚人节",
    "0601": "儿童节",
    "1031": "万圣节",
}
# 兼容日期：Excel 打开会把前导零吞掉（0101→101、0401→401、0601→601）。
_LEGACY_FESTIVAL = {value: name for code, name in _FESTIVAL.items() for value in {code, code.lstrip("0")}}


def _normalize_memorial(value: str) -> str:
    """将旧版日期代码（0101/101 等）归一化为节日名；其他值原样返回。"""
    value = (value or "").strip()
    return _LEGACY_FESTIVAL.get(value, value)


@dataclass
class NewsArticle:
    text: str
    indexed: bool


class NewsService:
    """加载静态新闻表，并按角色当前状态选择新闻。"""

    def __init__(self, news_dir: Optional[str] = None, news_table: str = DEFAULT_NEWS_TABLE,
                 world_state=None):
        self._world_state = world_state
        self._free_dir = news_dir or os.path.join(data_dir(), "static", "news")
        self._table = news_table or DEFAULT_NEWS_TABLE
        self._cache: Optional[List[Dict[str, str]]] = None

    def _read_dir(self) -> str:
        if self._world_state is not None and self._world_state.owns("news"):
            return self._world_state.pack_path("news")
        return self._free_dir

    def get_tables(self) -> List[str]:
        """返回可用新闻表名（文件名不含 .csv 后缀）。"""
        read_dir = self._read_dir()
        if not os.path.isdir(read_dir):
            return []
        return sorted(
            os.path.splitext(filename)[0]
            for filename in os.listdir(read_dir)
            if filename.lower().endswith(".csv")
        )

    def resolve(self, table_name: str) -> str:
        """返回指定新闻表 CSV 的完整路径。"""
        name = table_name or DEFAULT_NEWS_TABLE
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self._read_dir(), name)

    def set_table(self, table_name: str):
        self._table = table_name or DEFAULT_NEWS_TABLE
        self._cache = None

    def _load(self) -> List[Dict[str, str]]:
        if self._cache is not None:
            return self._cache
        path = self.resolve(self._table)
        rows: List[Dict[str, str]] = []
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    row = {key: (raw.get(key) or "").strip() for key in NEWS_COLUMNS}
                    if row["正文"]:
                        rows.append(row)
        except Exception as e:
            print(f"加载新闻表失败: {e}")
        self._cache = rows
        return rows

    @staticmethod
    def _is_birthday(birthday: str, day: datetime.date) -> bool:
        value = (birthday or "").strip().replace("/", "-")
        try:
            parts = value[:10].split("-")
            if len(parts) == 2:
                month, date = map(int, parts)
            elif len(parts) == 3:
                _, month, date = map(int, parts)
            else:
                return False
            return (month, date) == (day.month, day.day)
        except (TypeError, ValueError):
            return False

    @classmethod
    def _memorial_for_day(cls, birthday: str, day: datetime.date) -> str:
        if cls._is_birthday(birthday, day):
            return "生日"
        memorial = f"{day.month:02d}{day.day:02d}"
        return _FESTIVAL.get(memorial, "无")

    @staticmethod
    def _int_grid(value: float) -> int:
        return max(0, min(4, int(round(float(value)))))

    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        try:
            return NewsService._int_grid(float(value)) if value != "" else None
        except (TypeError, ValueError):
            return None

    def _garbage_rows(self, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        return [row for row in rows if not row["尺寸等级"]]

    def _pick_different(self, pool: List[Dict[str, str]], state) -> Dict[str, str]:
        """优先选取与上一次新闻正文不同的条目，全部相同才复用，避免主干完全一致。"""
        different = [row for row in pool if row["正文"] != state.last_news]
        return random.choice(different) if different else random.choice(pool)

    def _make_article(self, row: Dict[str, str], state, indexed: bool) -> NewsArticle:
        state.last_news = row["正文"]
        return NewsArticle(self._render(row, state), indexed=indexed)

    def _indexed_article(
            self, rows: List[Dict[str, str]], state, day: datetime.date
    ) -> Optional[NewsArticle]:
        candidates = [row for row in rows if row["尺寸等级"]]
        size = get_size_category(state.height)
        if not size:
            return None

        # 表头顺序即索引优先级：尺寸必须精确命中，其余字段按规则逐层收窄。
        for column in NEWS_COLUMNS:
            if column == "尺寸等级":
                candidates = [row for row in candidates if row[column] == size]
                if not candidates:
                    return None
            elif column == "纪念":
                expected = _normalize_memorial(self._memorial_for_day(state.birthday, day))
                exact = [row for row in candidates if _normalize_memorial(row[column]) == expected]
                if exact:
                    candidates = exact
                # 纪念无法命中时按要求忽略该要求。
            elif column in ("介入度", "破坏性"):
                target = self._int_grid(getattr(state, "intrusion" if column == "介入度" else "destruction", 0))
                numeric = [(row, self._parse_int(row[column])) for row in candidates]
                numeric = [(row, value) for row, value in numeric if value is not None]
                if numeric:
                    nearest = min(abs(value - target) for _, value in numeric)
                    candidates = [row for row, value in numeric if abs(value - target) == nearest]
            # 正文及替换队列不是索引字段。

        if not candidates:
            return None
        return self._make_article(self._pick_different(candidates, state), state, indexed=True)

    @staticmethod
    def _render(row: Dict[str, str], state) -> str:
        text = row["正文"].replace("{name}", state.name).replace("{nick}", state.nick or "")
        for index in range(1, 3):
            values = [item.strip() for item in row[f"替换队列{index}"].split(",") if item.strip()]
            replacement = random.choice(values) if values else ""
            for key in _REPLACEMENT_KEYS[index]:
                text = text.replace(key, replacement)
        return text

    def choose_for_load(self, state, info_update_rate: float,
                        saved_at: Optional[str] = None,
                        now: Optional[datetime.datetime] = None) -> Optional[NewsArticle]:
        """按角色加载时选择新闻。saved_at 为本次加载前的保存时间（updated_at）。"""
        now = now or datetime.datetime.now()
        if now.hour < 9:
            return None

        # 每日9点后首次加载：今天已弹过新闻则跳过。
        last_check = None
        if state.news_checked_at:
            try:
                last_check = datetime.datetime.fromisoformat(state.news_checked_at)
            except ValueError:
                pass
        if last_check is not None and last_check.date() == now.date():
            return None

        # 七天判断：使用保存时间（本次加载前的 updated_at）。
        last_saved = None
        if saved_at:
            try:
                last_saved = datetime.datetime.fromisoformat(saved_at)
            except ValueError:
                pass
        elapsed_days = (now - last_saved).total_seconds() / 86400 if last_saved else None
        force_garbage = elapsed_days is not None and elapsed_days >= 7

        rows = self._load()
        garbage = self._garbage_rows(rows)
        if not garbage:
            return None

        # 已确认本次将弹出新闻，记录当日门控时间。
        state.news_checked_at = now.isoformat()

        if force_garbage or random.random() >= max(0.0, min(1.0, float(info_update_rate))):
            return self._make_article(self._pick_different(garbage, state), state, indexed=False)

        return self._indexed_article(rows, state, now.date()) or self._make_article(
            self._pick_different(garbage, state), state, indexed=False
        )
