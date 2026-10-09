"""校验全仓 Markdown 相对链接与 `docs/...` 字符串引用是否指向存在的文件。"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {'.git', 'build', 'dist', '__pycache__', '.venv', '.workbuddy'}
LINK = re.compile(r'\]\(([^)\s]+?)(?:\s+"[^"]*")?\)')
PATHISH = re.compile(r'docs/[\w/\-]+\.md')

bad = []
for p in ROOT.rglob('*.md'):
    if SKIP & set(p.parts):
        continue
    for i, line in enumerate(p.read_text('utf-8', 'replace').splitlines(), 1):
        for m in LINK.finditer(line):
            t = m.group(1)
            if t.startswith(('http', '#', 'mailto:')):
                continue
            if not (p.parent / t.split('#')[0]).exists():
                bad.append(f'{p.relative_to(ROOT)}:{i} 链接失效 -> {t}')
        # 代码/文档里以字符串写死的 docs 路径
        for m in PATHISH.finditer(line):
            if not (ROOT / m.group(0)).exists():
                bad.append(f'{p.relative_to(ROOT)}:{i} 路径失效 -> {m.group(0)}')

for b in bad:
    print(b)
print(f'{"FAILED" if bad else "PASSED"}：{len(bad)} 处失效引用')
sys.exit(1 if bad else 0)
