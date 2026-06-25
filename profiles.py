import json
import re
import time
from pathlib import Path

PROFILES_DIR = Path(__file__).parent / "profiles"

_DEFAULT_RULE_TEXT = (
    "텍스트는 `<<이름>>대사 내용<</이름>>` 형태로 감싸여 있다. 여는 태그와 닫는 태그의 "
    "'이름'은 항상 동일한 화자 식별자(예: Amy, Mc)다. 이 화자 이름표(<<이름>> 자체)는 "
    "절대 추출하지 말고, 태그 사이의 대사 텍스트만 추출한다. `<<button ...>>...<</button>>`, "
    "`<<nextStage ...>>`, `<<editcycle ...>>`, `<<s $변수>>`, `<<if ...>>...<</if>>`, "
    "`<<set ...>>` 등 인자가 있거나 게임 로직/변수를 다루는 매크로는 절대 추출 대상이 아니다. "
    "대사 텍스트 안에 `<<s $brotherName>>` 같은 변수 매크로가 섞여 있으면 그 부분은 그대로 "
    "보존하고 나머지 자연어 부분만 번역 대상으로 포함한다. Twine으로 컴파일된 HTML은 "
    "`<tw-passagedata>` 안에 원본 텍스트가 HTML 엔티티로 escape되어 `&lt;&lt;이름&gt;&gt;대사"
    "&lt;&lt;/이름&gt;&gt;` 형태로 저장되어 있을 수도 있다 (literal `<<`가 아니라 `&lt;&lt;`). "
    "두 형태(escape됨/안 됨) 모두 처리해야 한다."
)

_DEFAULT_EXTRACTION_CODE = '''import re

def extract(html: str) -> list:
    spans = []
    patterns = [
        r'&lt;&lt;([A-Za-z_]\\w*)&gt;&gt;(.*?)&lt;&lt;/\\1&gt;&gt;',
        r'<<([A-Za-z_]\\w*)>>(.*?)<</\\1>>',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, html, re.DOTALL):
            body = m.group(2)
            stripped = body.strip()
            if not stripped:
                continue
            offset = body.find(stripped)
            start = m.start(2) + offset
            spans.append({"start": start, "end": start + len(stripped), "text": stripped})
        if spans:
            break
    return spans
'''

DEFAULT_SLUG = "__default__"


def _default_profile() -> dict:
    return {
        "slug": DEFAULT_SLUG,
        "game_name": "기본 프리셋 (Twine 화자 대사)",
        "rule_text": _DEFAULT_RULE_TEXT,
        "extraction_code": _DEFAULT_EXTRACTION_CODE,
        "target_lang": "English",
        "style_presets": [],
        "created_at": "",
        "updated_at": "",
    }


def slugify(game_name: str) -> str:
    slug = re.sub(r"[^a-z0-9가-힣]+", "-", game_name.strip().lower()).strip("-")
    slug = slug or "game"
    candidate = slug
    n = 2
    while (PROFILES_DIR / f"{candidate}.json").exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def list_profiles() -> list:
    PROFILES_DIR.mkdir(exist_ok=True)
    profiles = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        profiles.append({
            "slug": data.get("slug", path.stem),
            "game_name": data.get("game_name", path.stem),
            "updated_at": data.get("updated_at", ""),
        })
    profiles.sort(key=lambda p: p["updated_at"], reverse=True)

    default_entry = {
        "slug": DEFAULT_SLUG,
        "game_name": _default_profile()["game_name"],
        "updated_at": "",
    }
    return [default_entry] + profiles


def load_profile(slug: str) -> dict:
    if slug == DEFAULT_SLUG:
        return _default_profile()
    path = PROFILES_DIR / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_profile(profile: dict) -> None:
    PROFILES_DIR.mkdir(exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    profile.setdefault("created_at", now)
    profile["updated_at"] = now
    path = PROFILES_DIR / f"{profile['slug']}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def new_profile(game_name: str, rule_text: str = "") -> dict:
    return {
        "slug": slugify(game_name),
        "game_name": game_name,
        "rule_text": rule_text,
        "extraction_code": "",
        "target_lang": "English",
        "style_presets": [],
    }


def list_style_presets(profile: dict) -> list:
    return [p["name"] for p in profile.get("style_presets", [])]


def get_style_preset(profile: dict, name: str) -> dict:
    for preset in profile.get("style_presets", []):
        if preset["name"] == name:
            return preset
    return None


def save_style_preset(profile: dict, name: str, examples: list) -> dict:
    profile.setdefault("style_presets", [])
    profile["style_presets"] = [p for p in profile["style_presets"] if p["name"] != name]
    profile["style_presets"].append({"name": name, "examples": examples})
    return profile
