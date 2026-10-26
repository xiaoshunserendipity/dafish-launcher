"""自动探测路径：规则外置到 `detect_rules.json`，支持 `${APP}` 变量。

规则文件位置：与 `config.json` 同目录（默认 exe 同级）。
文件不存在时自动写一份默认规则，用户可以直接编辑，无需改代码。

规则结构：

```json
{
  "version": 1,
  "search_roots": ["${APP}", "${APP}/..", "D:/Some/Where"],
  "rules": [
    {"keywords": ["astrbot"], "patterns": ["AstrBot/astrbot-desktop-tauri.exe"]}
  ]
}
```

匹配逻辑：

1. 条目名称里包含 `keywords` 任意一个 → 命中这条规则（命中后不再往下试）；
2. 对每个 `patterns`：若含 `${APP}` 或本身是绝对路径，直接展开；否则依次拼到
   每个 `search_roots` 后面；
3. 路径里带 `*` / `?` 时按通配符展开，取第一个存在的文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import APP_VAR, data_dir, expand_vars

RULES_NAME = "detect_rules.json"
RULES_VERSION = 1

DEFAULT_RULES: dict[str, Any] = {
    "version": RULES_VERSION,
    "_comment": (
        "patterns 支持 * 和 ? 通配符；search_roots 里的 ${APP} 指启动器所在文件夹。"
        "把启动器和 AstrBot 放在同一个父目录下，${APP}/.. 就能自动找到它们。"
    ),
    "search_roots": [
        APP_VAR,
        APP_VAR + "/..",
    ],
    "rules": [
        {
            "name": "AstrBot 桌面版",
            "keywords": ["astrbot"],
            "patterns": [
                "AstrBot/astrbot-desktop-tauri.exe",
                "astrbot-desktop-tauri.exe",
            ],
        },
        {
            "name": "SnowLuma（QQ 桥）",
            "keywords": ["snowluma"],
            "patterns": ["SnowLuma*/launcher.bat"],
        },
        {
            "name": "GSVI 语音适配器",
            "keywords": ["gsvi", "语音"],
            "patterns": [
                "语音库模型/GPT-SoVITS*/*/启动GSVI适配器.bat",
                "GPT-SoVITS*/*/启动GSVI适配器.bat",
            ],
        },
    ],
}


def rules_path() -> Path:
    return data_dir() / RULES_NAME


def ensure_rules_file() -> Path:
    """规则文件不存在时写一份默认的，方便用户直接编辑。"""
    path = rules_path()
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return path


def load_rules() -> dict[str, Any]:
    """读规则；文件缺失/损坏/结构不对时回退到内置默认值。"""
    ensure_rules_file()
    try:
        raw = rules_path().read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except Exception:
        return DEFAULT_RULES
    if not isinstance(data, dict):
        return DEFAULT_RULES

    roots = data.get("search_roots")
    if not isinstance(roots, list) or not roots:
        data["search_roots"] = list(DEFAULT_RULES["search_roots"])

    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        data["rules"] = list(DEFAULT_RULES["rules"])
    return data


def _match_pattern(pattern: str) -> Path | None:
    """展开单个（可能带通配符的）路径模式。"""
    if not pattern:
        return None
    text = expand_vars(pattern)
    if not text:
        return None
    if not any(ch in text for ch in "*?"):
        candidate = Path(text)
        return candidate if candidate.exists() else None

    path = Path(text)
    parts = path.parts
    prefix: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?"):
            break
        candidate = Path(*prefix, part) if prefix else Path(part)
        if not candidate.exists():
            return None
        prefix.append(part)

    tail = parts[len(prefix):]
    if not tail:
        return None
    base = Path(*prefix) if prefix else Path(".")
    try:
        for found in base.glob(str(Path(*tail))):
            if found.exists():
                return found
    except Exception:
        return None
    return None


def find_for(name: str) -> Path | None:
    """按条目名称找可执行文件；找不到返回 None。"""
    lowered = (name or "").lower()
    if not lowered:
        return None

    data = load_rules()
    roots = [expand_vars(str(r)) for r in data.get("search_roots") or [] if str(r).strip()]

    for rule in data.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        keywords = [str(k).strip().lower() for k in rule.get("keywords") or []]
        keywords = [k for k in keywords if k]
        if not keywords or not any(k in lowered for k in keywords):
            continue

        for pattern in rule.get("patterns") or []:
            raw = str(pattern).strip()
            if not raw:
                continue
            if APP_VAR in raw or os.path.isabs(raw):
                candidates = [raw]
            else:
                candidates = [os.path.join(root, raw) for root in roots] or [raw]
            for candidate in candidates:
                found = _match_pattern(candidate)
                if found is not None:
                    return found
        # 命中关键词但没找到文件：不再尝试后面的规则，避免误填
        return None
    return None
