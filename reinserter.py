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
    items.sort(key=lambda pair: pair[1]["start"])

    pieces = []
    last = 0
    for idx, span in items:
        pieces.append(html[last:span["start"]])
        pieces.append(translations[idx])
        last = span["end"]
    pieces.append(html[last:])
    return "".join(pieces)
