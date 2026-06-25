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
        return "", "", "English", None
    for p in profiles.list_profiles():
        if p["game_name"] == game_name:
            prof = profiles.load_profile(p["slug"])
            return prof.get("rule_text", ""), prof.get("extraction_code", ""), prof.get("target_lang", "English"), prof
    return "", "", "English", None


def on_analyze(html_text, rule_text, provider_name, model_name, api_key, prior_code, prior_error):
    if not html_text:
        return "HTML 파일을 먼저 업로드하세요.", "", None, [], "", ""
    if not rule_text.strip():
        return "추출 규칙을 입력하세요.", "", None, [], "", ""

    provider_cfg = {"base_url": PROVIDERS[provider_name]["base_url"], "model": model_name}
    sample = sampler.sample_html(html_text, rule_text)
    try:
        code = extractor_gen.generate_extraction_code(
            api_key, provider_cfg, sample, rule_text,
            prior_code=prior_code or None, prior_error=prior_error or None,
        )
    except Exception as e:
        return f"코드 생성 실패: {e}", prior_code or "", None, [], "", ""

    return run_extraction_only(html_text, code)


def run_extraction_only(html_text, code):
    if not html_text or not code:
        return "HTML과 추출 코드가 모두 필요합니다.", code or "", None, [], "", ""

    result = extractor_run.run_extraction(code, html_text)
    if result["error"]:
        msg = result["error"]
        if result["raised"]:
            msg += f"\n\n{result['raised']}"
        return msg, code, None, [], "", ""

    spans, warnings = extractor_run.validate_spans(result["spans"], html_text)
    if not spans:
        msg = "매치된 텍스트가 없습니다. 규칙 설명을 더 구체적으로 작성한 뒤 재생성하세요."
        if warnings:
            msg += "\n" + "\n".join(warnings)
        return msg, code, None, [], "", ""

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


def on_translate(html_text, spans, provider_name, model_name, api_key, target_lang, num_batches, progress=gr.Progress()):
    if not html_text or not spans:
        return None, "먼저 분석을 실행해 추출 결과를 만들어주세요."

    provider_cfg = {"base_url": PROVIDERS[provider_name]["base_url"], "model": model_name}

    def cb(done, total):
        progress((done, total))

    result = translator.translate_all(api_key, provider_cfg, spans, target_lang, int(num_batches), progress_cb=cb)
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

    rule_text_input = gr.Textbox(label="추출 규칙 (자연어로 설명)", lines=6)

    with gr.Row():
        provider_dropdown = gr.Dropdown(label="제공자", choices=list(PROVIDERS.keys()), value=list(PROVIDERS.keys())[0])
        model_dropdown = gr.Dropdown(label="모델", choices=PROVIDERS[list(PROVIDERS.keys())[0]]["models"])
        api_key_input = gr.Textbox(label="API 키", type="password")
        target_lang_input = gr.Dropdown(label="목표 언어", choices=["English", "한국어", "日本語", "中文"], value="English", allow_custom_value=True)

    with gr.Row():
        analyze_btn = gr.Button("분석 (규칙 -> 추출 코드 생성 및 실행)")
        rerun_btn = gr.Button("코드만 재실행")

    analysis_status = gr.Textbox(label="분석 상태", interactive=False, lines=4)
    code_box = gr.Code(label="생성된 추출 코드 (직접 수정 가능)", language="python")
    preview_table = gr.Dataframe(headers=["추출된 텍스트"], label="추출 미리보기 (최대 30개)")

    save_profile_btn = gr.Button("프로필 저장")
    save_status = gr.Textbox(label="저장 상태", interactive=False)

    gr.Markdown("### 번역")
    with gr.Row():
        total_spans_box = gr.Textbox(label="전체 추출 문장 수", interactive=False)
        batch_count_input = gr.Number(label="번역 호출을 나눌 횟수 (배치 개수)", value=1, precision=0)

    translate_btn = gr.Button("번역 실행")
    translate_summary = gr.Textbox(label="번역 결과", interactive=False)
    download_file = gr.File(label="번역된 HTML 다운로드")

    file_input.change(on_upload, inputs=file_input, outputs=[html_state, upload_status])

    refresh_btn.click(refresh_profile_list, outputs=profile_dropdown)
    demo.load(refresh_profile_list, outputs=profile_dropdown)

    profile_dropdown.change(
        on_profile_select, inputs=profile_dropdown,
        outputs=[rule_text_input, code_box, target_lang_input, profile_state],
    )
    profile_dropdown.change(lambda name: name, inputs=profile_dropdown, outputs=game_name_input)

    provider_dropdown.change(
        lambda name: gr.update(choices=PROVIDERS[name]["models"]),
        inputs=provider_dropdown, outputs=model_dropdown,
    )

    prior_code_state = gr.State("")
    prior_error_state = gr.State("")

    analyze_btn.click(
        on_analyze,
        inputs=[html_state, rule_text_input, provider_dropdown, model_dropdown, api_key_input, code_box, prior_error_state],
        outputs=[analysis_status, code_box, spans_state, preview_table, total_spans_box, batch_count_input],
    )

    rerun_btn.click(
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
        inputs=[html_state, spans_state, provider_dropdown, model_dropdown, api_key_input, target_lang_input, batch_count_input],
        outputs=[download_file, translate_summary],
    )

if __name__ == "__main__":
    demo.queue().launch()
