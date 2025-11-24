import logging
import pathlib
import uuid
from typing import Any, ClassVar, cast

from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import Note
from aqt import mw
from pydantic import BaseModel, Field, ValidationError

try:
    from rich.logging import RichHandler
    from rich.traceback import install as install_rich_traceback

    _ = install_rich_traceback(show_locals=True)
except ImportError:
    RichHandler = None

from collections.abc import Iterator, MutableMapping


class PromptTemplateId:
    def __init__(self, value: str | None):
        if value is None:
            self.value = None
        else:
            try:
                self.value = str(uuid.UUID(value))
            except ValueError:
                raise ValueError(f"Invalid UUID: {value}")

    def __str__(self):
        return self.value if self.value is not None else ""

    def __repr__(self):
        return f"PromptTemplateId({self.value})"

    def serialize(self) -> str | None:
        return self.value

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        # For introspection in config UI
        schema = handler(core_schema)
        schema["type"] = "string"
        schema["format"] = "uuid"
        schema["title"] = "Prompt Template UUID"
        return schema

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        # Use str as the schema type
        return handler.generate_schema(str)


class ZikariaBaseModel(BaseModel, MutableMapping):
    """BaseModel that exposes only fields via mapping interface."""

    class Config:
        extra = "ignore"

    def __getitem__(self, key: str) -> Any:
        if key in self.__class__.model_fields:
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self.__class__.model_fields:
            setattr(self, key, value)
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        if key in self.__class__.model_fields:
            setattr(self, key, None)
        else:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__class__.model_fields)

    def __len__(self) -> int:
        return len(self.__class__.model_fields)

    def keys(self) -> Iterator[str]:
        return self.__class__.model_fields.keys()

    def values(self) -> Iterator[Any]:
        for k in self.__class__.model_fields:
            yield getattr(self, k)

    def items(self) -> Iterator[tuple[str, Any]]:
        for k in self.__class__.model_fields:
            yield (k, getattr(self, k))

    def update(self, other: dict[str, Any]) -> None:
        for k, v in other.items():
            if k in self.__class__.model_fields:
                setattr(self, k, v)


# TODO: get avaiable models from google like:
#             for model in genai.list_models():
# if "generateContent" in model.supported_generation_methods:
#     print(model.name)
class ZikariaCustomConfig(ZikariaBaseModel):
    __hidden_attributes__: ClassVar[set[str]] = {"last_prompt_template_key"}

    last_prompt_template_key: PromptTemplateId | None = None

    create_prompt_template: PromptTemplateId | None = Field(
        None, description="Template for note creation prompt"
    )
    complete_prompt_template: PromptTemplateId | None = Field(
        None, description="Template for note completion prompt"
    )

    image_prompt_template: PromptTemplateId | None = Field(
        None, description="Template for image generation"
    )
    model_temperature: float | None = Field(
        None, description="Temperature for model generation"
    )
    model_name: str | None = Field(None, description="Model name for completions")
    image_model_name: str | None = Field(
        default="gemini-2.5-flash-image", description="Model name for image generation"
    )
    max_output_tokens: int | None = Field(
        None, description="Maximum output tokens for completions"
    )


type ZikariaCustomConfigEntry = tuple[
    NotetypeId | None, DeckId | None, ZikariaCustomConfig
]


class ZikariaConfig(ZikariaBaseModel):
    __hidden_attributes__: ClassVar[set[str]] = {"last_prompt_template_key"}

    last_prompt_template_key: PromptTemplateId | None = None

    api_key: str = Field(
        "",
        description="The API key for accessing Generative AI services. If not provided, the user will be prompted to input it on first run.",
    )
    debug: bool = Field(False, description="Enable debug logging")
    create_prompt_template: PromptTemplateId | None = Field(
        None,
        description="The base prompt sent to the Generative AI when completing fields in an existing note. This is formatted dynamically.",
    )
    complete_prompt_template: PromptTemplateId | None = Field(
        None, description="Template for note completion prompt"
    )
    image_prompt_template: PromptTemplateId | None = Field(
        None, description="Template for image generation"
    )
    confirm_before_adding_notes: bool = Field(
        False, description="Require confirmation before adding notes"
    )
    model_temperature: float = Field(
        0.5,
        description="Temperature for model generation. Lower means more predictive, higher means more creative.",
    )
    model_name: str = Field(
        default="gemini-2.5-flash", description="Model name for completions"
    )
    image_model_name: str = Field(
        default="gemini-2.5-flash-image", description="Model name for image generation"
    )
    max_output_tokens: int = Field(
        512, description="Maximum output tokens for completions"
    )
    request_timeout: int = Field(
        1000,
        description="The request timeout in milliseconds for getting the gemini client.",
    )  # Default from original config
    note_tag: str = Field(
        "zikria_created", description="Tag to add to notes created by Gemini"
    )
    processed_tag: str = Field(
        "zikaria_processed", description="Tag to add to notes processed by Gemini"
    )
    prompt_tag: str = Field(
        "zikaria_prompt", description="Tag to add to notes with a prompt"
    )
    custom_prompt_tag: str = Field(
        "zikaria_custom_prompt", description="Tag to add to notes with a custom prompt"
    )
    complete_tag: str = Field(
        "zikaria_complete", description="Tag to add to notes that are completed"
    )
    json_tag: str = Field(
        "zikaria_from_json", description="Tag to add to notes created from JSON"
    )
    run_on_sync: bool = Field(False, description="Whether to run processing on sync")
    custom_config: list[ZikariaCustomConfigEntry] = Field(
        default_factory=list, description="List of custom configs for note types/decks"
    )


class ConfigProxy(MutableMapping):
    """Proxy for ZikariaConfig that can be swapped out."""

    def __init__(self, config: ZikariaConfig):
        self._config = config

    @property
    def config(self) -> ZikariaConfig:
        return self._config

    def swap(self, new_config: ZikariaConfig):
        self._config = new_config

    def __getattr__(self, attr):
        return self._config[attr]

    def __getitem__(self, key):
        return self._config[key]

    def __setitem__(self, key, value):
        self._config[key] = value

    def __delitem__(self, key):
        raise RuntimeError(
            "To delete a key from the config, access the ._config attribute directly."
        )
        del self._config[key]

    def __iter__(self):
        return iter(self._config)

    def __len__(self):
        return len(self._config)


def write_markdown_docs(model: type[BaseModel]) -> str:
    """Generate compact Markdown documentation for a config model."""
    lines = ["# Configuration Documentation\n"]
    for name, field_info in model.model_fields.items():
        if name in model.__hidden_attributes__:
            continue
        if name == "custom_config":
            overridable_keys = [
                k
                for k in ZikariaCustomConfig.model_fields
                if k not in ZikariaCustomConfig.__hidden_attributes__
            ]
            lines.append(
                "- `custom_config` (list): Per-deck and/or per-note-type configuration overrides. "
                "Allows setting prompt-related options for specific decks, note types, or combinations. "
                "Each entry is a tuple: (NotetypeId | None, DeckId | None, CustomConfig). "
                f"Overridable keys: {', '.join(overridable_keys)}."
            )
            continue
        desc = field_info.description or ""
        default = field_info.default if field_info.default is not None else "None"
        typ = getattr(field_info.annotation, "__name__", str(field_info.annotation))
        line = f"- `{name}` ({typ}, default: {default})"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)


type ConfigValue = int | float | bool | str
type CustomConfig = dict[str, ConfigValue]
type CustomConfigEntry = tuple[NotetypeId | None, DeckId | None, CustomConfig]
type Config = dict[str, ConfigValue | list[CustomConfigEntry]]

type ConfigType = ConfigProxy | Config

ADDON_NAME: str = mw.addonManager.addon_from_module(__name__)
addon_root_dir = pathlib.Path(mw.addonManager.addonsFolder(ADDON_NAME)).resolve()
user_files_dir = addon_root_dir / "user_files"

# Load user configuration
config_data = mw.addonManager.getConfig(ADDON_NAME)

try:
    new_config = ZikariaConfig(**config_data)
except ValidationError as e:
    raise RuntimeError(f"Config is invalid: {config_data}") from e

config = ConfigProxy(new_config)
# print(config_proxy.config)


config_docs_path = addon_root_dir / "config.md"
config_json_path = addon_root_dir / "config.json"

# Write config.md if changed
new_docs = write_markdown_docs(ZikariaConfig)
if config_docs_path.exists():
    with open(config_docs_path, "r", encoding="utf-8") as f:
        old_docs = f.read()
else:
    old_docs = ""

if old_docs != new_docs:
    with open(config_docs_path, "w", encoding="utf-8") as f:
        f.write(new_docs)

# Write config.json if changed
empty_config = ZikariaConfig()
new_json = empty_config.model_dump_json(indent=2)
if config_json_path.exists():
    with open(config_json_path, "r", encoding="utf-8") as f:
        old_json = f.read()
else:
    old_json = ""
if old_json != new_json:
    with open(config_json_path, "w", encoding="utf-8") as f:
        f.write(new_json)

logger = mw.addonManager.get_logger(__name__)
log_level = logging.DEBUG

log_level = logging.DEBUG if config.debug else logging.INFO

if RichHandler is not None:
    # Remove existing handlers
    logger.handlers.clear()
    # Add RichHandler
    rich_handler = RichHandler(rich_tracebacks=True)
    logger.addHandler(rich_handler)
    logger.propagate = False  # Prevent log records from propagating to parent handlers

logger.setLevel(log_level)
logger.info("Running with config:\n%r", config_data)


def get_config():
    return config


def get_addon_name():
    return mw.addonManager.addon_from_module(__name__)


# Copied from anki_utils.py as it's used by score_custom_config_entry
def get_deck_ancestor_ids(did: DeckId) -> list[DeckId]:
    """
    Retrieve all ancestors of the given deck ID using Anki's API.
    :param did: Deck ID to find ancestors for.
    :return: List of ancestor deck dictionaries.
    """
    # This function relies on mw, which might not be ideal here.
    # Consider passing mw or col if this needs to be more independent.
    return [ancestor["id"] for ancestor in mw.col.decks.parents(did)]


def score_custom_config_entry(
    entry: CustomConfigEntry, note_type_id: NotetypeId, deck_id: DeckId
):
    """
    Score an entry based on the given note type and deck using Anki's API.
    :param entry: The entry to score (note_type_id, deck_id, options).
    :param note_type_id: The target note type ID.
    :param deck_id: The target deck ID.
    :return: A tuple representing the score (higher is better).
    """
    entry_note_type_id, entry_deck_id, *_ = entry

    # Perfect match
    if entry_note_type_id == note_type_id and entry_deck_id == deck_id:
        return (4, 0)

    # Note type matches, deck is an ancestor
    if entry_note_type_id == note_type_id:
        if deck_id and entry_deck_id:
            ancestor_ids = get_deck_ancestor_ids(deck_id)
            if entry_deck_id in ancestor_ids:
                return (3, -ancestor_ids.index(entry_deck_id))
        return (2, 0)

    # Deck matches or is an ancestor, note type doesn't match
    if entry_deck_id:
        if entry_deck_id == deck_id:
            return (1, 0)
        if deck_id:  # Ensure deck_id is not None before calling get_deck_ancestor_ids
            ancestor_ids = get_deck_ancestor_ids(deck_id)
            if entry_deck_id in ancestor_ids:
                return (1, -ancestor_ids.index(entry_deck_id))

    # If none match, return the lowest score
    return (0, 0)


def filter_and_sort_custom_config_entries(
    entries: list[CustomConfigEntry],
    note_type_id: NotetypeId,
    deck_id: DeckId,
) -> list[CustomConfigEntry]:
    """
    Filter and sort a list of entries based on matching note type and deck IDs.
    :param entries: The list of entries to filter and sort.
    :param note_type_id: The target note type ID.
    :param deck_id: The target deck ID.
    :return: The filtered and sorted list of entries.
    """
    # Ensure entries with (None, None, ...) are not completely discarded if that's not intended
    # The original code implies that if both are None, they are filtered out.
    # If an entry has (None, deck_id, config) or (note_type_id, None, config) it should be scored.
    filtered_entries = (
        entry for entry in entries if entry[0] is not None or entry[1] is not None
    )

    # Sorting with reverse=False means lower scores come first.
    # The original key lambda entry: score_custom_config_entry(...) will give higher scores for better matches.
    # To have better matches come first (which is typical for overrides), we should sort in reverse.
    # However, the original code had reverse=False. I will keep it as is, but this might be a bug.
    # If the intention is that more specific configs (higher score) override less specific ones,
    # then they should be applied in that order, meaning they should appear later in the sorted list
    # if we iterate and update. Or, if we take the first match, then reverse=True would be needed.
    # Given the current logic in ZikariaPrompts.get_effective_config that iterates and updates,
    # having more specific entries later (reverse=False) is correct.
    sorted_entries = sorted(
        filtered_entries,
        reverse=False,
        key=lambda entry: score_custom_config_entry(entry, note_type_id, deck_id),
    )
    return sorted_entries


def get_effective_config(note_type_id: NotetypeId, deck_id: DeckId) -> ZikariaConfig:
    """
    Retrieve the effective configuration for a given note type and deck.
    This function is adapted from ZikariaPrompts.get_effective_config.
    """
    logger.debug(
        "Generating effective config for note type ID: %s, and deck ID: %s, original config:\n%s",
        note_type_id,
        deck_id,
        config.config.model_dump(),
    )

    # Start with a copy of the global config
    #
    effective_config: Config = config.config.model_dump()

    # Custom configurations from the global_config (which should be the main `config` object)
    custom_config_entries = effective_config.get("custom_config", [])

    # Ensure note_type_id and deck_id are not None for filtering and sorting
    # The filter_and_sort function expects int, not Optional[int] for these.
    # However, a note might not always have a deck (e.g., if it's a new note not yet added to a deck).
    # For now, we assume valid integer IDs are passed or the scoring handles None appropriately.
    # The original `score_custom_config_entry` handles `deck_id` being potentially None for matching.

    sorted_custom_entries = filter_and_sort_custom_config_entries(
        custom_config_entries, note_type_id, deck_id
    )

    for i, entry in enumerate(sorted_custom_entries):
        logger.debug("Applying custom config entry #%s: %s", i + 1, entry)
        # entry is (entry_note_type_id, entry_deck_id, settings_dict)
        settings_to_apply = {k: v for k, v in entry[2].items() if v is not None}
        effective_config.update(settings_to_apply)

    logger.debug("Final effective config: %s", effective_config)

    return ZikariaConfig(**effective_config)


def get_effective_config_for_note(note: Note) -> ZikariaConfig | None:
    """
    Convenience function to get effective config directly from a note object.
    This function is adapted from ZikariaPrompts.get_effective_config_for_note.
    """
    note_type = note.note_type()
    if not note_type:
        logger.warning(
            "Note has no model (note_type). Cannot determine effective config."
        )
        return config_data.copy()  # Return a copy of global config

    note_type_id = cast(NotetypeId, note_type["id"])

    # A note might not have cards, or might have multiple cards.
    # We need a robust way to get a deck_id.
    # If the note has cards, use the deck ID of the first card.
    # If it's a new note (no cards yet), it might not have a deck_id yet.
    # In such cases, deck_id might be considered None or a default.
    # The original code used `note.cards()[0].did`. This assumes the note has at least one card.
    cards = note.cards()
    deck_id: DeckId | None = cards[0].did if cards else None  # Use None if no cards

    if deck_id is None:
        raise RuntimeError("Note has no cards or first card has no deck ID")

    return get_effective_config(note_type_id, deck_id)


# TODO: remove this and the hook in __init__.py and rely on the builtin Addon Config management or replace this function with a custom validity check (Pydantic?), which seems to be what it was meant for.
def update_config(updated_config_data: ZikariaConfig | Config | ConfigProxy) -> None:
    if isinstance(updated_config_data, ConfigProxy):
        updated_config_data = updated_config_data.config

    if isinstance(updated_config_data, ZikariaConfig):
        updated_config_data = updated_config_data.model_dump()

    try:
        updated_config = ZikariaConfig(**updated_config_data)
    except ValidationError as e:
        raise RuntimeError(f"Updated config is invalid: {updated_config_data}") from e

    config.swap(updated_config)

    # Update logger level if debug status changed

    new_debug_level = logging.DEBUG if config.debug else logging.INFO
    if logger.level != new_debug_level:
        logger.setLevel(new_debug_level)
        logger.info(f"Log level updated to {logging.getLevelName(new_debug_level)}.")

    mw.addonManager.writeConfig(
        ADDON_NAME, updated_config.model_dump()
    )  # This should be done by Anki
