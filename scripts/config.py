"""Configuration loader for Zotero Bridge."""
import yaml
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent
CONFIG_PATH = CONFIG_DIR / "config.yaml"
TEMPLATE_PATH = CONFIG_DIR / "references" / "config-template.yaml"


def load_config() -> dict:
    """Load config.yaml, error with guidance if missing."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config not found at {CONFIG_PATH.resolve()}.\n"
            f"Copy {TEMPLATE_PATH} to {CONFIG_PATH} and fill in your Zotero credentials."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _validate(config)
    return config


def _validate(config: dict):
    zotero = config.get("zotero", {})
    if not zotero.get("library_id"):
        raise ValueError("zotero.library_id is required in config.yaml")
    if not zotero.get("api_key"):
        raise ValueError("zotero.api_key is required in config.yaml")
    if zotero.get("library_type") not in ("user", "group"):
        raise ValueError("zotero.library_type must be 'user' or 'group'")


def get_data_dir(config: dict) -> Path:
    storage = config.get("storage", {})
    data_dir = storage.get("data_dir", "../zotero-data")
    path = Path(data_dir)
    if not path.is_absolute():
        path = (CONFIG_DIR / data_dir).resolve()
    return path
