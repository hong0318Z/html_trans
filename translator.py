import json
from pathlib import Path

import llm_client

SYSTEM_PROMPT_TEMPLATE = """You are a professional game localization translator.
Translate each value in the given JSON object into {target_lang}.
Preserve placeholders, line breaks, and formatting markers exactly.
{style_block}Respond with ONLY a JSON object that has the exact same keys as the input,
where each value is the translated text. No explanation, no extra keys."""


def _style_block(style_examples: list) -> str:
    if not style_examples:
        return ""
    lines = "\n".join(f'- "{ex["source"]}" -> "{ex["target"]}"' for ex in style_examples if ex.get("source"))
    if not lines:
        return ""
    return f"Match this translation style/tone, as shown by these examples:\n{lines}\n\n"


def dedup_spans(spans: list) -> tuple:
    unique_texts = []
    text_to_idx = {}
    span_to_unique = {}
    for i, span in enumerate(spans):
        text = span["text"]
        if text not in text_to_idx:
            text_to_idx[text] = len(unique_texts)
            unique_texts.append(text)
        span_to_unique[i] = text_to_idx[text]
    return unique_texts, span_to_unique


def dedup_spans_by_speaker(spans: list) -> tuple:
    """Like dedup_spans, but keyed by (speaker, text) so the same line said by
    two different characters can get different translations/style."""
    unique = []
    key_to_idx = {}
    span_to_unique = {}
    for i, span in enumerate(spans):
        speaker = span.get("speaker") or "_"
        text = span["text"]
        key = (speaker, text)
        if key not in key_to_idx:
            key_to_idx[key] = len(unique)
            unique.append({"speaker": speaker, "text": text})
        span_to_unique[i] = key_to_idx[key]
    return unique, span_to_unique


def text_frequencies(spans: list) -> tuple:
    unique_texts, span_to_unique = dedup_spans(spans)
    counts = [0] * len(unique_texts)
    for unique_idx in span_to_unique.values():
        counts[unique_idx] += 1
    return unique_texts, counts


def recommended_batch_count(unique_texts: list, chars_per_batch: int = 4000) -> int:
    total_chars = sum(len(t) for t in unique_texts)
    if not unique_texts:
        return 1
    return max(1, min(len(unique_texts), -(-total_chars // chars_per_batch)))


def make_batches(unique_texts: list, num_batches: int) -> list:
    num_batches = max(1, min(num_batches, len(unique_texts))) if unique_texts else 1
    if not unique_texts:
        return []

    indexed = sorted(range(len(unique_texts)), key=lambda i: len(unique_texts[i]), reverse=True)
    batches = [[] for _ in range(num_batches)]
    batch_chars = [0] * num_batches
    for i in indexed:
        target = min(range(num_batches), key=lambda b: batch_chars[b])
        batches[target].append(i)
        batch_chars[target] += len(unique_texts[i])

    return [sorted(b) for b in batches if b]


def translate_batch(api_key: str, provider_cfg: dict, texts: list, target_lang: str,
                     style_examples: list = None) -> dict:
    payload = {str(i): text for i, text in enumerate(texts)}
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target_lang=target_lang, style_block=_style_block(style_examples),
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    content, _usage = llm_client.chat(
        api_key, messages, temperature=0.3,
        model=provider_cfg.get("model"), base_url=provider_cfg.get("base_url"),
        response_format={"type": "json_object"},
    )
    result = json.loads(content)
    if set(result.keys()) != set(payload.keys()):
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": (
            f"Your response had keys {sorted(result.keys())} but must have exactly "
            f"keys {sorted(payload.keys())}. Return the corrected JSON object only."
        )})
        content, _usage = llm_client.chat(
            api_key, messages, temperature=0.3,
            model=provider_cfg.get("model"), base_url=provider_cfg.get("base_url"),
            response_format={"type": "json_object"},
        )
        result = json.loads(content)

    return {texts[int(k)]: v for k, v in result.items() if k.isdigit() and int(k) < len(texts)}


def sample_for_style(spans: list, n: int = 10) -> list:
    """Pick representative texts to ask the user about, biased towards the
    most frequently repeated lines (these matter most for a consistent tone)."""
    unique_texts, counts = text_frequencies(spans)
    if len(unique_texts) <= n:
        return list(unique_texts)
    order = sorted(range(len(unique_texts)), key=lambda i: counts[i], reverse=True)
    top = sorted(order[:n])
    return [unique_texts[i] for i in top]


def suggest_style_examples(api_key: str, provider_cfg: dict, spans: list, target_lang: str,
                            n: int = 10) -> list:
    sample_texts = sample_for_style(spans, n)
    if not sample_texts:
        return []
    try:
        result = translate_batch(api_key, provider_cfg, sample_texts, target_lang)
    except Exception:
        result = {}
    return [{"source": t, "target": result.get(t, "")} for t in sample_texts]


def _checkpoint_key(speaker: str, text: str) -> str:
    return f"{speaker}\x1f{text}"


def load_checkpoint(checkpoint_path: str) -> dict:
    if not checkpoint_path:
        return {}
    path = Path(checkpoint_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_checkpoint(checkpoint_path: str, mapping: dict) -> None:
    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def translations_from_checkpoint(spans: list, checkpoint_path: str) -> dict:
    """Map whatever's already in the checkpoint back onto span indices, so a
    partial/interrupted run's progress can be reinserted without waiting for
    the rest of the translation to finish."""
    checkpoint = load_checkpoint(checkpoint_path)
    unique, span_to_unique = dedup_spans_by_speaker(spans)
    idx_translations = {}
    for i, u in enumerate(unique):
        key = _checkpoint_key(u["speaker"], u["text"])
        if key in checkpoint:
            idx_translations[i] = checkpoint[key]

    translations = {}
    for span_idx, unique_idx in span_to_unique.items():
        if unique_idx in idx_translations:
            translations[span_idx] = idx_translations[unique_idx]
    return translations


def translate_all(api_key: str, provider_cfg: dict, spans: list, target_lang: str,
                   num_batches: int, style_examples: list = None, char_style_examples: dict = None,
                   progress_cb=None, checkpoint_path: str = None) -> dict:
    """Translate spans, batching per speaker so each character's lines can use
    that character's own style examples (falling back to the global ones).

    If checkpoint_path is given, already-translated (speaker, text) pairs found
    there are reused (resuming a previously interrupted run), and progress is
    written back to the same file after every batch so an interruption never
    loses more than one batch's worth of work."""
    unique, span_to_unique = dedup_spans_by_speaker(spans)
    checkpoint = load_checkpoint(checkpoint_path)

    idx_translations = {}
    remaining_idx = []
    for i, u in enumerate(unique):
        key = checkpoint.get(_checkpoint_key(u["speaker"], u["text"]))
        if key is not None:
            idx_translations[i] = key
        else:
            remaining_idx.append(i)

    by_speaker = {}
    for i in remaining_idx:
        by_speaker.setdefault(unique[i]["speaker"], []).append(i)

    total_chars = sum(len(unique[i]["text"]) for i in remaining_idx) or 1
    batch_jobs = []  # (speaker, [unique_idx, ...])
    for speaker, idxs in by_speaker.items():
        texts = [unique[i]["text"] for i in idxs]
        share = sum(len(t) for t in texts) / total_chars
        speaker_batches = max(1, round(num_batches * share))
        for sub in make_batches(texts, speaker_batches):
            batch_jobs.append((speaker, [idxs[j] for j in sub]))

    failed_indices = []
    total = len(batch_jobs)
    for done, (speaker, idxs) in enumerate(batch_jobs):
        texts = [unique[i]["text"] for i in idxs]
        examples = (char_style_examples or {}).get(speaker) or style_examples
        try:
            result = translate_batch(api_key, provider_cfg, texts, target_lang, examples)
            for i, t in zip(idxs, texts):
                if t in result:
                    idx_translations[i] = result[t]
                    checkpoint[_checkpoint_key(speaker, t)] = result[t]
                else:
                    failed_indices.append(i)
        except Exception:
            failed_indices.extend(idxs)
        if checkpoint_path:
            save_checkpoint(checkpoint_path, checkpoint)
        if progress_cb:
            progress_cb(done + 1, total)

    translations = {}
    for span_idx, unique_idx in span_to_unique.items():
        if unique_idx in idx_translations:
            translations[span_idx] = idx_translations[unique_idx]

    failed_texts = [unique[i]["text"] for i in failed_indices]
    return {"translations": translations, "failed_texts": failed_texts}
