"""Quick CLI to sanity-check extraction against a real HTML file without
running the full Gradio app or calling any LLM.

Usage:
    python test_extract.py path/to/game.html
    python test_extract.py path/to/game.html --profile <slug>
    python test_extract.py path/to/game.html --code path/to/extract_code.py
    python test_extract.py path/to/game.html -n 20
"""

import argparse
import sys
from pathlib import Path

import extractor_run
import profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", help="Path to the HTML file to test extraction on")
    parser.add_argument("--profile", default="__default__", help="Profile slug to use (default: __default__)")
    parser.add_argument("--code", help="Path to a .py file containing extract(html) instead of using a profile")
    parser.add_argument("-n", type=int, default=10, help="Number of matches to print (default: 10)")
    args = parser.parse_args()

    html = Path(args.html_file).read_text(encoding="utf-8", errors="replace")
    print(f"파일 크기: {len(html)}자")

    if args.code:
        code = Path(args.code).read_text(encoding="utf-8")
        source_desc = f"코드 파일: {args.code}"
    else:
        prof = profiles.load_profile(args.profile)
        code = prof["extraction_code"]
        source_desc = f"프로필: {prof['game_name']} ({args.profile})"
    print(source_desc)
    print("-" * 60)

    result = extractor_run.run_extraction(code, html)
    if result["error"]:
        print("오류:", result["error"])
        if result["raised"]:
            print(result["raised"])
        sys.exit(1)

    spans, warnings = extractor_run.validate_spans(result["spans"], html)
    for w in warnings:
        print("경고:", w)

    print(f"\n총 매치 수: {len(spans)}\n")
    for i, s in enumerate(spans[: args.n]):
        print(f"[{i}] ({s['start']}-{s['end']}) {s['text'][:200]!r}")


if __name__ == "__main__":
    main()
