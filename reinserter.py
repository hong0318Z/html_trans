def check_overlaps(spans: list) -> list:
    ordered = sorted(spans, key=lambda s: s["start"])
    pairs = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if b["start"] < a["end"]:
            pairs.append((a, b))
    return pairs


def reinsert(html: str, spans: list, translations: dict) -> str:
    items = [(i, s) for i, s in enumerate(spans) if i in translations]
    items.sort(key=lambda pair: pair[1]["start"], reverse=True)

    out = html
    for idx, span in items:
        out = out[:span["start"]] + translations[idx] + out[span["end"]:]
    return out
