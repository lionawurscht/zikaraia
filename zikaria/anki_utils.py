import os
from collections import namedtuple
from typing import Any

from anki.decks import DeckId  # Ensure this is imported
from anki.models import (  # Ensure this is imported if used by NoteData or other funcs
    NoteType,
    NotetypeId,
)
from anki.notes import Note
from aqt import mw
from aqt.utils import getText, showInfo
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

# Use accessors for config and logger
from .config_utils import get_config, get_config_value, logger
from .prompts import generate_pydantic_class

# Globals related to API Key - these are module-level state, managed by functions below.
API_KEY_MISSING = False  # Default state
API_KEY = None  # Default state

# Global related to sync disabling - managed by functions below.
DISABLE_ON_SYNC = False  # Default state


# --- Helper Function for Recursive Processing ---
def _process_value(
    value: Any, pydantic_note_model: type[BaseModel]
) -> list[dict[str, Any]]:
    """
    Recursively processes a value which could be a single note dict,
    a list of note dicts, or a dictionary containing nested notes.
    """
    normalized_notes = []

    if isinstance(value, dict):
        # 1. Check if the dictionary itself is a valid single note
        try:
            # Validate it against the Pydantic model
            validated_note = pydantic_note_model.model_validate(value)
            # Add the validated, clean dictionary representation
            # normalized_notes.append(value)
            normalized_notes.append(validated_note.model_dump())
        except ValidationError:
            logger.exception("Error Validating, trying values:\n%s", value)
            # 2. If not a single note, it's the undesired 'key-grouped' format.
            # Recursively process all values inside this dict.
            for nested_value in value.values():
                normalized_notes.extend(
                    _process_value(nested_value, pydantic_note_model)
                )

    elif isinstance(value, list):
        # 3. If it's a list, process each item in the list
        for item in value:
            normalized_notes.extend(_process_value(item, pydantic_note_model))

    # 4. Any other type is ignored (e.g., a non-note string or number that snuck in)
    return normalized_notes


# ----------------------------------------------------------------------
# --- Main Normalization Function ---
# ----------------------------------------------------------------------


def normalize_ai_response(data: Any, note_type: NoteType) -> list[dict[str, Any]]:
    """
    Normalizes a flexible AI JSON response into a flat list of valid note dictionaries.

    It accepts:
    1. A list of valid note dictionaries (the desired format).
    2. A single dictionary representing one valid note.
    3. A dict where keys map to either a list of valid notes or a single valid note
       (the 'stubborn AI' format: {"word": {... or [...]}, ...}).
    4. A nested list/dict structure containing the above elements.

    Args:
        data: The raw data returned by the AI (can be a list or a dict).
        NoteModel: The Pydantic model class for a single valid note.

    Returns:
        A list of dictionaries, where each dict is a validated note.
    """
    pydantic_note_model = generate_pydantic_class(note_type, enforce_enum=False)

    if isinstance(data, list):
        # Input is a list (Case 1), process all elements
        return _process_value(data, pydantic_note_model)

    elif isinstance(data, dict):
        # Input is a dict (Case 2 or 3), process it via the recursive helper
        return _process_value(data, pydantic_note_model)

    else:
        # Input is not a list or dict, return empty list
        return []


def is_valid_notes_data(data: Any) -> bool:
    """Validate if the data is a list of dicts with string keys and values."""
    if not isinstance(data, list):
        return False
    for item in data:
        if not is_valid_note_data(item):  # Relies on the other validation function
            return False
    return True


def is_valid_note_data(item: Any) -> bool:
    """Validate if the data is a dict with string keys and values."""
    if not isinstance(item, dict):
        return False
    if not all(
        isinstance(k, str)
        and (
            isinstance(v, str)
            or v is None
            or (
                k == "tags"
                and isinstance(v, list)
                and all(isinstance(it, str) for it in v)
            )
        )
        for k, v in item.items()
    ):
        return False
    return True


# Define the Note namedtuple (remains as is)
# NoteData = namedtuple("NoteData", ["note_type_id", "deck_id", "fields", "tags"])


# def json_to_namedtuples(
#     json_data: list[dict[str, Any]],
#     note_type_id: int,  # Changed from NotetypeId to int for consistency
#     deck_id: int,  # Changed from DeckId to int
# ) -> list[NoteData]:
#     """Convert a JSON list of dictionaries into a list of Note namedtuples."""
#     notes = []
#     for entry in json_data:
#         tags = entry.pop("__tags", [])
#         notes.append(
#             NoteData(
#                 note_type_id=note_type_id,
#                 deck_id=deck_id,
#                 fields=entry,
#                 tags=tags,
#             )
#         )
#     return notes


class GeminiClientProxy:
    def __init__(self):
        self._client = None
        self._api_key = None

    def _get_api_key(self):
        # Try config
        api_key = get_config_value("api_key")

        # Try environment
        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY"))

        # Validate type
        if api_key and not isinstance(api_key, str):
            raise TypeError(f"Gemini API key should be a string, is: {type(api_key)}")

        # Prompt user if missing
        if not api_key:
            if mw is None:
                logger.error("mw is None, cannot show API key dialog.")
                raise RuntimeError(
                    "No Gemini API key set in config or env and user input not possible, since Anki isn't running."
                )
            key, ok = getText(
                "Enter your Generative AI API key:", title="API Key Required", parent=mw
            )
            if ok and key:
                api_key = key.strip()
                logger.info("API key entered manually for this session.")
        if not api_key:
            showInfo("API key is required for Zikaria to function.", parent=mw)
            return None
        return api_key

    def _setup_client(self):
        if self._client is not None:
            return True
        api_key = self._get_api_key()
        if not api_key:
            logger.info(
                "API key not available or user cancelled. Generative AI configuration aborted."
            )
            return False
        timeout = get_config_value("request_timeout", 60000)
        logger.debug("Configuring client with a http timeout of %s seconds.", timeout)

        try:
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(timeout)),
            )
            logger.info("Generative AI configured successfully.")
            return True
        except Exception as e:
            logger.error(f"Error creating gemini client: {e}", exc_info=True)
            return False

    @property
    def client(self):
        if self._client is None:
            self._setup_client()
        return self._client

    def __bool__(self):
        if self._client is None:
            return self._setup_client()
        return self._client is not None


gemini_client_proxy = GeminiClientProxy()


def ensure_tags_exist():
    """Ensures that the required tags (from config) are present in the collection."""
    current_config = get_config()  # Use accessor

    if not mw or not mw.col:
        logger.error("Collection (mw.col) is not available for ensure_tags_exist.")
        return

    col = mw.col

    # Get all keys ending with '_tag' from the current config
    tag_keys_in_config = [key for key in current_config if key.endswith("_tag")]
    required_tag_values = [
        current_config[key] for key in tag_keys_in_config if current_config.get(key)
    ]

    if not required_tag_values:
        logger.info("No tags configured with '_tag' suffix to ensure.")
        return

    existing_tags_in_anki = set(col.tags.all())

    # defaults_for_adding() does not take arguments in modern Anki versions
    defaults = col.defaults_for_adding(current_review_card=None)

    default_model_id = defaults.notetype_id
    if default_model_id is None:  # Fallback if no default notetype is set
        logger.warning(
            "Default notetype ID is None. Attempting to find a fallback for dummy note."
        )
        all_notetypes = col.models.all_names_and_ids()
        if not all_notetypes:
            logger.error("No notetypes available. Cannot create dummy note for tags.")
            return
        default_model_id = all_notetypes[0].id  # Use the first available notetype
        logger.info(f"Using fallback notetype ID for dummy note: {default_model_id}")

    note_model_for_dummy = col.models.get(default_model_id)
    if not note_model_for_dummy:
        logger.error(
            f"Failed to load model for notetype id {default_model_id}. Cannot create dummy note for tags."
        )
        return
    dummy_note = Note(col=col, model=note_model_for_dummy)

    missing_tags_to_add = []
    for tag_value in required_tag_values:
        if tag_value not in existing_tags_in_anki:
            dummy_note.add_tag(tag_value)  # Add to the note object in memory
            missing_tags_to_add.append(tag_value)

    if not missing_tags_to_add:
        logger.info("All required tags (from config) already exist in the collection.")
        return

    # Determine deck for the dummy note
    deck_id_for_dummy = defaults.deck_id
    if deck_id_for_dummy is None:  # Fallback if no default deck
        logger.warning(
            "Default deck ID is None. Using first available deck for dummy note."
        )
        all_decks = col.decks.all_names_and_ids()
        if not all_decks:
            logger.error("No decks available. Cannot add dummy note for tags.")
            return  # Cannot proceed if no deck to add to
        deck_id_for_dummy = all_decks[0].id
        logger.info(f"Using fallback deck ID for dummy note: {deck_id_for_dummy}")

    try:
        # Adding the note with tags effectively registers them in Anki's tag list
        col.add_note(dummy_note, deck_id=deck_id_for_dummy)
        logger.info(
            "Tags '%s' added to the collection via temporary dummy note.",
            col.tags.join(missing_tags_to_add),
        )
        col.remove_notes([dummy_note.id])  # Clean up the dummy note immediately
    except Exception as e:
        logger.error(
            f"Error adding/removing dummy note for ensuring tags: {e}", exc_info=True
        )


def disable_running_on_sync():
    """Disables automatic processing on sync for the current session, usually after an error."""
    global DISABLE_ON_SYNC
    if not DISABLE_ON_SYNC:
        DISABLE_ON_SYNC = True
        logger.info(
            "Running on sync will be disabled for this session due to a runtime error."
        )


def get_deck_ancestor_ids(did: int | None) -> list[int]:  # did can be None
    """Retrieve all ancestor deck IDs for a given deck ID."""
    if not mw or not mw.col:
        logger.error("mw.col not available for get_deck_ancestor_ids.")
        return []
    if did is None:  # Deck ID might be None for notes not yet in a deck
        logger.debug(
            "get_deck_ancestor_ids called with None deck ID. Returning empty list."
        )
        return []
    return [ancestor["id"] for ancestor in mw.col.decks.parents(did)]


def replace_nulls_with_empty_strings(data: Any) -> Any:
    """Recursively replaces all None/null values in dicts/lists with empty strings."""
    if isinstance(data, dict):
        return {
            k: ("" if v is None else replace_nulls_with_empty_strings(v))
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [replace_nulls_with_empty_strings(item) for item in data]
    return data
