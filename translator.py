import json

import llm_client

SYSTEM_PROMPT_TEMPLATE = """You are a professional game localization translator.
Translate each value in the given JSON object into {target_lang}.
Preserve placeholders, line breaks, and formatting markers exactly.
Respond with ONLY a JSON object that has the exact same keys as the input,
where each value is the translated text. No explanation, no extra keys."""


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


def translate_batch(api_key: str, provider_cfg: dict, texts: list, target_lang: str) -> dict:
    payload = {str(i): text for i, text in enumerate(texts)}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(target_lang=target_lang)},
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


def translate_all(api_key: str, provider_cfg: dict, spans: list, target_lang: str,
                   num_batches: int, progress_cb=None) -> dict:
    unique_texts, span_to_unique = dedup_spans(spans)
    batches = make_batches(unique_texts, num_batches)

    text_translations = {}
    failed_texts = []
    total = len(batches)
    for done, batch_indices in enumerate(batches):
        texts = [unique_texts[i] for i in batch_indices]
        try:
            result = translate_batch(api_key, provider_cfg, texts, target_lang)
            text_translations.update(result)
            for t in texts:
                if t not in result:
                    failed_texts.append(t)
        except Exception:
            failed_texts.extend(texts)
        if progress_cb:
            progress_cb(done + 1, total)

    translations = {}
    for span_idx, unique_idx in span_to_unique.items():
        text = unique_texts[unique_idx]
        if text in text_translations:
            translations[span_idx] = text_translations[text]

    return {"translations": translations, "failed_texts": failed_texts}
