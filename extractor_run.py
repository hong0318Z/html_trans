import multiprocessing
import queue as queue_module
import re
import traceback

# Matches a Twine/Harlowe-style macro *closing* tag, both literal
# (`<</name>>`) and HTML-entity-escaped (`&lt;&lt;/name&gt;&gt;`) forms. If
# extracted "text" still contains one of these, it swallowed an entire
# open+close macro pair (e.g. <<widget>>...<<nobr>>...<</nobr>>...<</widget>>)
# as plain text -- translating that risks rewording the tag syntax itself and
# desyncing the pair, which breaks the game.
#
# A lone self-closing/inline macro with no closing tag (e.g. <<s $familyName>>)
# is intentionally NOT matched here: it has no pairing to desync, and the
# default rule_text explicitly tells the LLM to preserve such placeholders
# verbatim while translating the surrounding sentence.
_MACRO_TAG_RE = re.compile(r"<<\s*/\s*[A-Za-z_]\w*|&lt;&lt;\s*/\s*[A-Za-z_]\w*")


def _contains_macro_tag(text: str) -> bool:
    return bool(_MACRO_TAG_RE.search(text))


def _worker(code_str: str, html: str, queue: multiprocessing.Queue) -> None:
    try:
        ns = {}
        exec(code_str, ns)
        fn = ns.get("extract")
        if fn is None:
            queue.put(("error", "Generated code has no function named 'extract'."))
            return
        spans = fn(html)
        queue.put(("ok", spans))
    except Exception:
        queue.put(("error", traceback.format_exc()))


def run_extraction(code_str: str, html: str, timeout_sec: int = 90) -> dict:
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_worker, args=(code_str, html, queue))
    proc.start()

    # Read from the queue *before* joining: a child producing a large payload
    # (many spans) can block on queue.put() once the pipe buffer fills, and
    # join()-ing first would deadlock both sides until the timeout fires.
    try:
        status, payload = queue.get(timeout=timeout_sec)
    except queue_module.Empty:
        proc.terminate()
        proc.join()
        return {"spans": [], "error": f"Extraction timed out after {timeout_sec}s.", "raised": None}

    proc.join()

    if status == "error":
        return {"spans": [], "error": "Extraction code raised an exception.", "raised": payload}

    if not isinstance(payload, list):
        return {"spans": [], "error": "extract() must return a list of spans.", "raised": None}

    return {"spans": payload, "error": None, "raised": None}


def validate_spans(spans: list, html: str) -> tuple:
    valid = []
    warnings = []
    bad_offsets = 0
    for span in spans:
        try:
            start, end, text = span["start"], span["end"], span["text"]
        except (KeyError, TypeError):
            bad_offsets += 1
            continue
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(html)):
            bad_offsets += 1
            continue
        if html[start:end] != text:
            bad_offsets += 1
            continue
        clean = {"start": start, "end": end, "text": text}
        if span.get("speaker"):
            clean["speaker"] = span["speaker"]
        valid.append(clean)

    if bad_offsets:
        warnings.append(f"{bad_offsets} of {len(spans)} spans had bad offsets and were dropped.")

    embedded_tags = [s for s in valid if _contains_macro_tag(s["text"])]
    if embedded_tags:
        valid = [s for s in valid if not _contains_macro_tag(s["text"])]
        examples = "; ".join(repr(s["text"][:60]) for s in embedded_tags[:3])
        warnings.append(
            f"{len(embedded_tags)} span(s) contained an embedded <<macro>> tag inside their text and were "
            "dropped (translating them risks corrupting the tag itself, e.g. a mismatched/missing closing "
            "tag). The extraction rule likely needs to stop at the nested tag instead of swallowing it. "
            f"Examples: {examples}"
        )

    overlap_pairs = check_overlaps(valid)
    if overlap_pairs:
        drop_starts = {b["start"] for _a, b in overlap_pairs}
        valid = [s for s in valid if s["start"] not in drop_starts]
        warnings.append(f"{len(overlap_pairs)} overlapping span pair(s) found; later-starting span in each pair was dropped.")

    return valid, warnings


def check_overlaps(spans: list) -> list:
    ordered = sorted(spans, key=lambda s: s["start"])
    pairs = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if b["start"] < a["end"]:
            pairs.append((a, b))
    return pairs
