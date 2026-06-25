import json
import re
import time
from pathlib import Path

PROFILES_DIR = Path(__file__).parent / "profiles"


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
    return profiles


def load_profile(slug: str) -> dict:
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
    }
