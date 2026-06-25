import multiprocessing
import traceback


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


def run_extraction(code_str: str, html: str, timeout_sec: int = 15) -> dict:
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_worker, args=(code_str, html, queue))
    proc.start()
    proc.join(timeout_sec)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        return {"spans": [], "error": f"Extraction timed out after {timeout_sec}s.", "raised": None}

    if queue.empty():
        return {"spans": [], "error": f"Extraction process exited unexpectedly (code={proc.exitcode}).", "raised": None}

    status, payload = queue.get()
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
        valid.append({"start": start, "end": end, "text": text})

    if bad_offsets:
        warnings.append(f"{bad_offsets} of {len(spans)} spans had bad offsets and were dropped.")

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
