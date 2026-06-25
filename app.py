from pathlib import Path

import gradio as gr

import extractor_gen
import extractor_run
import profiles
import reinserter
import sampler
import translator
from llm_client import PROVIDERS

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def on_upload(file):
    if file is None:
        return "", "파일을 업로드하세요."
    text = Path(file).read_text(encoding="utf-8", errors="replace")
    return text, f"{len(text.splitlines())}줄, {len(text)}자 로드됨."


def refresh_profile_list():
    names = [p["game_name"] for p in profiles.list_profiles()]
    return gr.update(choices=names)


def on_profile_select(game_name):
    if not game_name:
        return "", "", "English", None, gr.update(choices=[])
    for p in profiles.list_profiles():
        if p["game_name"] == game_name:
            prof = profiles.load_profile(p["slug"])
            preset_names = profiles.list_style_presets(prof)
            return (prof.get("rule_text", ""), prof.get("extraction_code", ""),
                    prof.get("target_lang", "English"), prof, gr.update(choices=preset_names))
    return "", "", "English", None, gr.update(choices=[])


def _table_to_examples(table) -> list:
    if table is None:
        return []
    rows = table.values.tolist() if hasattr(table, "values") else table
    examples = []
    for row in rows:
        if not row or not row[0] or str(row[0]).strip() == "":
            continue
        src = str(row[0]).strip()
        tgt = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        examples.append({"source": src, "target": tgt})
    return examples


def on_save_style(game_name, profile_state, table, preset_name):
    if not preset_name.strip():
        return "프리셋 이름을 입력하세요.", profile_state, gr.update()
    if not game_name.strip():
        return "게임 이름을 입력하세요.", profile_state, gr.update()

    if profile_state and profile_state.get("game_name") == game_name:
        prof = profile_state
    else:
        prof = profiles.new_profile(game_name)

    examples = _table_to_examples(table)
    profiles.save_style_preset(prof, preset_name, examples)
    profiles.save_profile(prof)
    preset_names = profiles.list_style_presets(prof)
    return f"프리셋 '{preset_name}' 저장됨.", prof, gr.update(choices=preset_names)


def on_load_style_preset(profile_state, preset_name):
    if not profile_state or not preset_name:
        return []
    preset = profiles.get_style_preset(profile_state, preset_name)
    if not preset:
        return []
    return [[ex["source"], ex["target"]] for ex in preset["examples"]]


def on_suggest_style(spans, provider_name, model_name, api_key, target_lang):
    if not spans:
        return "먼저 분석을 실행해 추출 결과를 만들어주세요.", gr.update()

    provider_cfg = {"base_url": PROVIDERS[provider_name]["base_url"], "model": model_name}
    unique_texts, _ = translator.dedup_spans(spans)
    examples = translator.suggest_style_examples(api_key, provider_cfg, unique_texts, target_lang)
    if not examples:
        return "추천할 문장이 없습니다.", gr.update()

    rows = [[ex["source"], ex["target"]] for ex in examples]
    status = (
        f"추출된 문장 중 {len(examples)}개로 번역투를 제안했습니다. "
        "표를 검토/수정한 뒤 마음에 들면 그대로 '번역 실행'을 누르세요."
    )
    return status, rows


def on_generate_code(html_text, rule_text, provider_name, model_name, api_key, prior_code, prior_status):
    if not html_text:
        return "HTML 파일을 먼저 업로드하세요.", prior_code or ""
    if not rule_text.strip():
        return "추출 규칙을 입력하세요.", prior_code or ""

    provider_cfg = {"base_url": PROVIDERS[provider_name]["base_url"], "model": model_name}
    sample = sampler.sample_html(html_text, rule_text)
    is_error_status = bool(prior_status) and "추출됨" not in prior_status
    prior_error = prior_status if is_error_status else None
    try:
        code = extractor_gen.generate_extraction_code(
            api_key, provider_cfg, sample, rule_text,
            prior_code=prior_code or None, prior_error=prior_error,
        )
    except Exception as e:
        return f"코드 생성 실패: {e}", prior_code or ""

    return "추출 코드가 생성되었습니다. 아래 '2단계: 추출 실행'을 눌러 결과를 확인하세요.", code


def run_extraction_only(html_text, code):
    if not html_text or not code:
        return "HTML과 추출 코드가 모두 필요합니다.", code or "", None, [], "", None

    result = extractor_run.run_extraction(code, html_text)
    if result["error"]:
        msg = result["error"]
        if result["raised"]:
            msg += f"\n\n{result['raised']}"
        return msg, code, None, [], "", None

    spans, warnings = extractor_run.validate_spans(result["spans"], html_text)
    if not spans:
        msg = "매치된 텍스트가 없습니다. 규칙 설명을 더 구체적으로 작성한 뒤 재생성하세요."
        if warnings:
            msg += "\n" + "\n".join(warnings)
        return msg, code, None, [], "", None

    unique_texts, _ = translator.dedup_spans(spans)
    recommended = translator.recommended_batch_count(unique_texts)
    status = f"{len(spans)}개 추출됨 (고유 문장 {len(unique_texts)}개)."
    if warnings:
        status += "\n" + "\n".join(warnings)

    preview = [[s["text"][:200]] for s in spans[:30]]
    return status, code, spans, preview, str(len(spans)), str(recommended)


def on_save_profile(game_name, rule_text, code, target_lang, existing_profile):
    if not game_name.strip():
        return "게임 이름을 입력하세요.", existing_profile
    if existing_profile and existing_profile.get("game_name") == game_name:
        prof = existing_profile
    else:
        prof = profiles.new_profile(game_name)
    prof["rule_text"] = rule_text
    prof["extraction_code"] = code
    prof["target_lang"] = target_lang
    profiles.save_profile(prof)
    return f"프로필 '{game_name}' 저장됨.", prof


def on_translate(html_text, spans, provider_name, model_name, api_key, target_lang, num_batches,
                  style_table, progress=gr.Progress()):
    if not html_text or not spans:
        return None, "먼저 분석을 실행해 추출 결과를 만들어주세요."

    provider_cfg = {"base_url": PROVIDERS[provider_name]["base_url"], "model": model_name}
    style_examples = _table_to_examples(style_table)

    def cb(done, total):
        progress((done, total))

    result = translator.translate_all(
        api_key, provider_cfg, spans, target_lang, int(num_batches),
        style_examples=style_examples, progress_cb=cb,
    )
    translated_html = reinserter.reinsert(html_text, spans, result["translations"])

    OUTPUTS_DIR.mkdir(exist_ok=True)
    out_path = OUTPUTS_DIR / "translated.html"
    out_path.write_text(translated_html, encoding="utf-8")

    summary = f"{len(result['translations'])}개 번역 완료, {len(result['failed_texts'])}개 실패(원문 유지)."
    return str(out_path), summary


with gr.Blocks(title="HTML 게임 번역 도구") as demo:
    html_state = gr.State("")
    spans_state = gr.State(None)
    profile_state = gr.State(None)

    gr.Markdown("## HTML 게임 번역 도구")

    with gr.Row():
        file_input = gr.File(label="HTML 파일 업로드", file_types=[".html", ".htm"])
        upload_status = gr.Textbox(label="상태", interactive=False)

    with gr.Row():
        profile_dropdown = gr.Dropdown(label="기존 프로필", choices=[], allow_custom_value=True)
        refresh_btn = gr.Button("프로필 목록 새로고침")
        game_name_input = gr.Textbox(label="게임 이름")

    with gr.Row():
        provider_dropdown = gr.Dropdown(label="제공자", choices=list(PROVIDERS.keys()), value=list(PROVIDERS.keys())[0])
        model_dropdown = gr.Dropdown(label="모델", choices=PROVIDERS[list(PROVIDERS.keys())[0]]["models"])
        api_key_input = gr.Textbox(label="API 키", type="password")
        target_lang_input = gr.Dropdown(label="목표 언어", choices=["English", "한국어", "日本語", "中文"], value="English", allow_custom_value=True)

    gr.Markdown("### 1단계: 추출 규칙 만들기 (AI)")
    gr.Markdown("어떤 텍스트를 추출할지 자연어로 설명하면, AI가 그 설명을 보고 파이썬이 실제로 문서에서 텍스트를 긁어올 추출 코드를 만듭니다.")
    rule_text_input = gr.Textbox(label="추출 규칙 (자연어로 설명)", lines=6)
    generate_code_btn = gr.Button("1단계: AI로 추출 코드 생성")
    code_box = gr.Code(label="생성된 추출 코드 (직접 수정 가능)", language="python")
    save_profile_btn = gr.Button("프로필로 저장 (규칙 + 코드 재사용)")
    save_status = gr.Textbox(label="저장 상태", interactive=False)

    gr.Markdown("### 2단계: 추출 실행 (번역할 문장 뽑기)")
    gr.Markdown("AI 호출 없이 파이썬이 위 코드를 그대로 실행해 문서 전체에서 텍스트를 긁어옵니다.")
    extract_btn = gr.Button("2단계: 추출 실행")
    analysis_status = gr.Textbox(label="상태 (규칙 생성 / 추출 결과)", interactive=False, lines=4)
    preview_table = gr.Dataframe(headers=["추출된 텍스트"], label="추출 미리보기 (최대 30개)")
    total_spans_box = gr.Textbox(label="전체 추출 문장 수", interactive=False)

    gr.Markdown("### 3단계: 번역투 협의")
    gr.Markdown("추출된 문장 중 일부를 AI에게 보여주고 번역 결과를 미리 받아본 뒤, 검토/수정해서 전체 번역의 스타일로 사용합니다.")
    gr.Markdown("예: 원문 `hi` -> 번역 `안녕` 처럼 원하는 말투/스타일의 예시 몇 개를 적어두면 번역할 때 참고합니다.")
    suggest_style_btn = gr.Button("3단계: 추출된 문장으로 번역투 추천받기")
    style_table = gr.Dataframe(
        headers=["원문", "번역"], datatype=["str", "str"],
        row_count=(5, "dynamic"), column_count=(2, "fixed"),
        label="스타일 예시",
    )
    with gr.Row():
        style_preset_name = gr.Textbox(label="프리셋 이름 (예: 1번 - 캐쥬얼 번역)")
        save_style_btn = gr.Button("프리셋으로 저장")
        style_preset_dropdown = gr.Dropdown(label="저장된 프리셋 불러오기", choices=[])
    style_status = gr.Textbox(label="프리셋 상태", interactive=False)

    gr.Markdown("### 4단계: 번역 실행")
    batch_count_input = gr.Number(label="번역 호출을 나눌 횟수 (배치 개수)", value=1, precision=0)
    translate_btn = gr.Button("4단계: 번역 실행")
    translate_summary = gr.Textbox(label="번역 결과", interactive=False)
    download_file = gr.File(label="번역된 HTML 다운로드")

    file_input.upload(on_upload, inputs=file_input, outputs=[html_state, upload_status]).then(
        run_extraction_only, inputs=[html_state, code_box],
        outputs=[analysis_status, code_box, spans_state, preview_table, total_spans_box, batch_count_input],
    )

    refresh_btn.click(refresh_profile_list, outputs=profile_dropdown)
    demo.load(refresh_profile_list, outputs=profile_dropdown)

    profile_dropdown.change(
        on_profile_select, inputs=profile_dropdown,
        outputs=[rule_text_input, code_box, target_lang_input, profile_state, style_preset_dropdown],
    ).then(
        run_extraction_only, inputs=[html_state, code_box],
        outputs=[analysis_status, code_box, spans_state, preview_table, total_spans_box, batch_count_input],
    )
    profile_dropdown.change(lambda name: name, inputs=profile_dropdown, outputs=game_name_input)

    save_style_btn.click(
        on_save_style,
        inputs=[game_name_input, profile_state, style_table, style_preset_name],
        outputs=[style_status, profile_state, style_preset_dropdown],
    )

    style_preset_dropdown.change(
        on_load_style_preset, inputs=[profile_state, style_preset_dropdown], outputs=style_table,
    )

    suggest_style_btn.click(
        on_suggest_style,
        inputs=[spans_state, provider_dropdown, model_dropdown, api_key_input, target_lang_input],
        outputs=[style_status, style_table],
    )

    provider_dropdown.change(
        lambda name: gr.update(choices=PROVIDERS[name]["models"]),
        inputs=provider_dropdown, outputs=model_dropdown,
    )

    generate_code_btn.click(
        on_generate_code,
        inputs=[html_state, rule_text_input, provider_dropdown, model_dropdown, api_key_input, code_box, analysis_status],
        outputs=[analysis_status, code_box],
    )

    extract_btn.click(
        run_extraction_only,
        inputs=[html_state, code_box],
        outputs=[analysis_status, code_box, spans_state, preview_table, total_spans_box, batch_count_input],
    )

    save_profile_btn.click(
        on_save_profile,
        inputs=[game_name_input, rule_text_input, code_box, target_lang_input, profile_state],
        outputs=[save_status, profile_state],
    )

    translate_btn.click(
        on_translate,
        inputs=[html_state, spans_state, provider_dropdown, model_dropdown, api_key_input,
                target_lang_input, batch_count_input, style_table],
        outputs=[download_file, translate_summary],
    )

if __name__ == "__main__":
    demo.queue().launch()
