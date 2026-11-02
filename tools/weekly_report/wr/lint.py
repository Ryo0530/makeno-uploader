"""公開前の表現チェック。

景品表示法・特定商取引法で問題になりやすい「確実に儲かる」系の表現を、
自動生成した文章に混ぜないための最終チェック。1つでも見つかったら出力を止める。
"""
from __future__ import annotations

import re

BANNED = (
    r"必ず(儲|勝|当た|稼)",
    r"絶対(に)?(儲|勝|当た|稼)",
    r"確実に(儲|勝|当た|稼)",
    r"100\s*%\s*(当た|的中|勝)",
    r"必勝",
    r"儲かる",
    r"稼げる",
    r"元本保証",
    r"利益を?保証(します|する)",
    r"負けない(方法|馬券)",
    r"誰でも(勝|儲|稼)",
)

_BANNED_RE = [re.compile(p) for p in BANNED]


def find_violations(text: str) -> list[str]:
    hits = []
    for rx in _BANNED_RE:
        for m in rx.finditer(text):
            start = max(0, m.start() - 12)
            hits.append(f"「{m.group(0)}」… {text[start:m.end() + 12].strip()}")
    return hits
