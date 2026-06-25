import re

import llm_client

SYSTEM_PROMPT = """You write Python extraction code for HTML game localization.
You will be given a SAMPLE excerpt of a much larger HTML file (not the full file) and a rule
describing where translatable text lives in that file.

Respond with ONLY a single fenced python code block containing exactly one function:

def extract(html: str) -> list[dict]:
    # Return [{"start": int, "end": int, "text": str, "speaker": str}, ...]
    # "speaker" is optional: include it only when the rule describes per-character/
    # per-speaker dialogue, using the speaker identifier as it appears in the markup
    # (e.g. a Twine macro/tag name). Omit it entirely if there is no such concept.
    # html[start:end] must equal text exactly.
    # Return [] if nothing matches. Never raise; skip anything that doesn't fit cleanly.
    ...

Rules:
- Use only Python standard library (re, etc.). No third-party imports.
- `extract` receives the FULL original file content at run time, not the sample shown to you.
- Only include spans whose text is actually meant to be translated (per the rule below),
  never markup/attribute names/JS keywords.
- No explanation text outside the code block.
"""


def build_prompt(sample: str, rule_text: str, prior_code: str = None, prior_error: str = None) -> list:
    user_parts = [
        f"Rule describing what to extract:\n{rule_text}\n",
        f"Sample excerpt of the HTML file:\n```html\n{sample}\n```",
    ]
    if prior_code:
        user_parts.append(f"Previous attempt:\n```python\n{prior_code}\n```")
    if prior_error:
        user_parts.append(f"That attempt failed with this error, fix it:\n{prior_error}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def extract_code_block(llm_output: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", llm_output, re.DOTALL)
    if match:
        return match.group(1).strip()
    return llm_output.strip()


def generate_extraction_code(api_key: str, provider_cfg: dict, sample: str, rule_text: str,
                              prior_code: str = None, prior_error: str = None) -> str:
    messages = build_prompt(sample, rule_text, prior_code, prior_error)
    content, _usage = llm_client.chat(
        api_key, messages, temperature=0.2,
        model=provider_cfg.get("model"), base_url=provider_cfg.get("base_url"),
    )
    return extract_code_block(content)
