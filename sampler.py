import re

_KEYWORD_PATTERNS = [
    r'"([^"\n]{2,40})"',
    r"'([^'\n]{2,40})'",
    r"\bid=[\"']([\w-]+)[\"']",
    r"\bclass=[\"']([\w -]+)[\"']",
    r"\bdata-[\w-]+",
    r"<([a-zA-Z][\w-]*)\b",
]


def _extract_keywords(rule_text: str) -> list:
    keywords = []
    for pattern in _KEYWORD_PATTERNS:
        for match in re.finditer(pattern, rule_text):
            kw = match.group(1) if match.lastindex else match.group(0)
            kw = kw.strip()
            if kw and len(kw) >= 2 and kw not in keywords:
                keywords.append(kw)
    return keywords[:15]


def _window_around(lines: list, line_idx: int, context_lines: int, label: str) -> str:
    start = max(0, line_idx - context_lines)
    end = min(len(lines), line_idx + context_lines + 1)
    body = "\n".join(lines[start:end])
    return f"<!-- {label} around line {line_idx + 1} -->\n{body}\n"


def sample_html(html: str, rule_text: str, head_lines: int = 150, tail_lines: int = 100,
                 max_chars: int = 12000, context_lines: int = 8) -> str:
    lines = html.splitlines()
    total = len(lines)

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:]) if total > head_lines else ""

    keywords = _extract_keywords(rule_text)
    seen_ranges = []
    keyword_windows = []
    for kw in keywords:
        hits = 0
        for idx, line in enumerate(lines):
            if hits >= 3:
                break
            if kw in line:
                rng = (max(0, idx - context_lines), min(total, idx + context_lines + 1))
                if any(not (rng[1] <= s or rng[0] >= e) for s, e in seen_ranges):
                    continue
                seen_ranges.append(rng)
                keyword_windows.append(_window_around(lines, idx, context_lines, f"keyword '{kw}'"))
                hits += 1

    if not keyword_windows:
        for pct in (0, 25, 50, 75, 100):
            idx = min(total - 1, max(0, int(total * pct / 100)))
            keyword_windows.append(_window_around(lines, idx, 10, f"structural sample {pct}%"))

    parts = [
        f"<!-- HEAD (first {min(head_lines, total)} of {total} lines) -->",
        head,
        *keyword_windows,
        f"<!-- TAIL (last {min(tail_lines, total)} lines) -->",
        tail,
    ]
    sample = "\n\n".join(p for p in parts if p)

    if len(sample) > max_chars:
        # drop structural/keyword windows first, then trim head/tail as last resort
        sample = "\n\n".join([
            f"<!-- HEAD (first {min(head_lines, total)} of {total} lines) -->",
            head,
            f"<!-- TAIL (last {min(tail_lines, total)} lines) -->",
            tail,
        ])
        if len(sample) > max_chars:
            sample = sample[:max_chars]

    return sample
