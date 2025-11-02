from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

type NotesDataList = list[dict[str, str | list[str]]]
type NoteDict = dict[str, str | list[str]]


class PromptMode(StrEnum):
    CREATE = "create"
    COMPLETE = "complete"


@dataclass
class ZikariaRequestData:
    """Data required for a single network request."""

    # Used to pass data to the background thread
    notetype_dict: dict  # To pass to normalize_ai_response without collection access
    mode: PromptMode | None = None
    prompt_str: str | None = None
    effective_conf: dict | None = None
    original_note_id: int | None = None
    original_note_mod: int | None = None
    metadata: dict[str, int | float | str | bool] | None = None
    pydantic_class: type[BaseModel] | None = None
    # notetype_id: NotetypeId
    # deck_id: DeckId | None
    # tags: list[str] | None


@dataclass
class ZikariaResponseData:
    """Result from a single network request."""

    request_data: ZikariaRequestData
    notes_data_list: list[dict]  # The parsed, normalized data


# --- New Signals for Thread-to-Main-Thread Communication ---
