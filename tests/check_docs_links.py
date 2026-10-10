"""校验全仓 Markdown 相对链接与 `docs/...` 字符串引用是否指向存在的文件。

**大小写必须是精确匹配**——这是本守卫的核心约束，不是顺手加的严格性。Windows /
macOS 的文件系统不区分大小写，``Path.exists()`` 对 ``docs/Releases/`` 和
``docs/releases/`` 会给同一个答案；Ubuntu 区分，两者是**两个目录**。只按
``exists()`` 判会导致「本机全绿、CI 全红」：曾有一版把目录提交成 ``docs/Releases/``
（大写 R），而三处引用写成 ``docs/releases/``，Windows 上检查通过、Ubuntu 上报
3 处失效引用。

因此这里不直接用 ``exists()``，而是逐段比对**真实目录项的名字**（``os.listdir``），
在 Windows 上也能复现 Linux 的判定。跨平台共享的路径一律用全小写。
"""
import os, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {'.git', 'build', 'dist', '__pycache__', '.venv', '.workbuddy'}
LINK = re.compile(r'\]\(([^)\s]+?)(?:\s+"[^"]*")?\)')
PATHISH = re.compile(r'docs/[\w/\-]+\.md')


def _resolve_exact(start: pathlib.Path, target: str) -> str | None:
    """按 Linux 语义解析相对路径：每段都必须在 ``os.listdir`` 中精确出现。

    返回 ``None`` 表示完全命中；否则返回第一处失配的说明（用于报错）。
    这样可以区分三种情况——真正不存在、只有大小写不同、路径里有 ``..`` 越界。
    """
    parts = [p for p in target.replace('\\', '/').split('/') if p not in ('', '.')]
    cur = start
    for part in parts:
        if part == '..':
            cur = cur.parent
            continue
        try:
            entries = os.listdir(cur)
        except OSError:
            return f'{cur} 不可读'
        if part in entries:
            cur = cur / part
            continue
        # 精确名不存在——看是不是只差大小写（Windows 上 exists() 会骗人）
        lowered = part.lower()
        near = [e for e in entries if e.lower() == lowered]
        if near:
            return f'大小写不符：引用 {part!r}，实际是 {near[0]!r}'
        return f'{part!r} 不存在'
    return None


bad = []
for p in ROOT.rglob('*.md'):
    if SKIP & set(p.parts):
        continue
    for i, line in enumerate(p.read_text('utf-8', 'replace').splitlines(), 1):
        for m in LINK.finditer(line):
            t = m.group(1)
            if t.startswith(('http', '#', 'mailto:')):
                continue
            why = _resolve_exact(p.parent, t.split('#')[0])
            if why:
                bad.append(f'{p.relative_to(ROOT)}:{i} 链接失效 -> {t}（{why}）')
        # 代码/文档里以字符串写死的 docs 路径
        for m in PATHISH.finditer(line):
            why = _resolve_exact(ROOT, m.group(0))
            if why:
                bad.append(f'{p.relative_to(ROOT)}:{i} 路径失效 -> {m.group(0)}（{why}）')

for b in bad:
    print(b)
print(f'{"FAILED" if bad else "PASSED"}：{len(bad)} 处失效引用')
sys.exit(1 if bad else 0)
