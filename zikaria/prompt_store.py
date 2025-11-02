import json

from .config_utils import config, user_files_dir

PROMPT_FILE = user_files_dir / "saved_prompts.json"


def load_saved_prompts() -> dict[str, dict]:
    if not PROMPT_FILE.exists():
        return []
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        from .log_utils import get_logger

        get_logger().exception("Error loading saved prompts")
        return {}


def save_saved_prompts(prompts: dict[str, dict]):
    PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)


def get_default_prompt_template_key():
    return config.last_prompt_template_key
