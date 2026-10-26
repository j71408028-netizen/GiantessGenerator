# 行为包钩子：logic 与 profile

> 所属机制：[行为包](README.md)（玩家向）· [开发指南](dev-guide.md)。注册方式、
> 签名一致性与层级约定见开发指南的「注册 API」「Hook 目录与层级」。

## `logic`（模块函数与常量）

| 目标 | 签名 / 形态 | 说明                                                                                         |
|------|------|--------------------------------------------------------------------------------------------|
| `logic.PREDEFINED_TAGS` | `List[str]` | 预定义标签常量。覆盖可注册列表值，或返回列表的可调用对象；读取入口为 `logic.get_predefined_tags()`。                          |
| `apply_size_unlock_updates` | `(unlocks, updates, info_update_rate=0.5) -> Dict[str, str]` | 写入部位解锁信息。                                                                                  |
| `compute_environment_factor` | `(text) -> float` | 按文本中的环境关键词给出伤亡环境系数。                                                                        |
| `compute_casualty` | `(height, step, destruction, text, env_factor=None) -> float` | 计算一段文本的伤亡增量。                                                                               |
| `format_size` | `(size, base_size=None) -> str` | 尺寸格式化（米 / 千米，含小数位对齐），全流程共用。                                                                |
| `length_unit_label` | `() -> str` | 当前基础长度单位标签（默认“米”），供界面输入/单位标签显示。覆盖 `format_size` 换单位时，一般也覆盖本函数让输入标签一致。 |
| `replace_quip_tags` | `(quip_text, style, size_cat, detail_pools, quips_working=None, intr=None, dest=None, enable_confusion=False, allow_confusion_map=None) -> str` | 替换描述中的 `[类型:编号:文本]` 标记。                                                                    |
| `should_skip_by_part_tags` | `(part, selected_tags, p) -> bool` | 在构造对比时，按与部位展示有关的个性标签添加偏好。                                                                  |
| `contains_blocked_word` | `(text, blocked_words) -> bool` | 判断文本是否包含任一屏蔽词（大小写不敏感的子串匹配）。覆盖后地标匹配、描述匹配与副本提示词过滤都会生效。 |
| `get_comparisons` | `(landmarks, body_parts, order="match", limit=5, selected_tags=None, skip_base_prob=0.0, selected_parts=None, blocked_words=None) -> List[Dict]` | 寻找最接近的地标对比。名称含屏蔽词的地标会被跳过（内部调用 `contains_blocked_word`）。                                                                                |
| `build_size_description` | `(quip_result) -> str` | 由报告正文中的一条尺寸对比结果构造解锁描述。 |                                                                     |
| `select_quip_with_budget` | `(size_cat, intrusion_val, destruction_val, quips_working, locked_coords, cumulative_actual, cumulative_base, rate_factor=1.0, step_index=0, selected_tags=None, skip_base_prob=0.0, posture_list=None, blocked_words=None, lanes=None, env_estimate=None) -> (text, style, coord, actual_step, new_cumulative_actual, new_cumulative_base)` | 在预算内挑选事件描述 quip。含屏蔽词的描述会被跳过（内部调用 `contains_blocked_word`）。注意它会**就地弹出** `quips_working` 中被选中的 quip，覆盖实现必须保持该行为并返回 6 元组。`lanes` 是仲裁资格窗口（裁定级），`env_estimate` 是槽位环境估计（软权重）；覆盖实现必须接受这两个参数并遵循其语义。 |

## `profile`（档案派生层文本化，模块函数）

消费方：角色档案导出的「作息与活动范围观察」块、聊天人设与系统提示词。
返回约定：**纯文本多行（不含 HTML）**；实时提示在样本不足 / 零证据时返回**空串**
（消费方据此整行省略）。默认实现会按样本成熟度自动降级措辞，覆盖实现建议保留该约定。

| 目标 | 签名 | 说明 |
|------|------|------|
| `profile.describe_rhythm` | `(evolution, now=None) -> str` | 作息历史结论（活跃/安静时段、周末与工作日差异、成熟度措辞）；无样本返回空串。 |
| `profile.rhythm_now_hint` | `(evolution, now=None) -> str` | 作息实时提示：当前时刻活跃期 / 休整期各一行措辞，平常时段与样本不足时返回空串。 |
| `profile.describe_spatial` | `(profile, position="") -> str` | 空间偏好历史结论：常活动地带、环境偏好、手工活动区域。`profile` 是 `services.character.spatial.build_spatial_profile` 的装配结果。 |
| `profile.spatial_now_hint` | `(profile, position="") -> str` | 空间实时提示：当前位置相对偏好的评价 + 环境口语化描述；零证据返回空串。 |
