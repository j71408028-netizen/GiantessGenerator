"""quip 内容池构建：把描述矩阵解析成报告引擎可查的 detail_pools。

原 ``services/helpers.py`` 拆散归位（§4.2 步骤 3）：杂物间里它与挑战包导入
函数毫无关系，唯一消费者是 ``exploration/catalog``，故随消费者入包。
"""

import re


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
