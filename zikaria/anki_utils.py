import os
from collections import namedtuple
from typing import Optional, List, Any, Dict # Added List, Any, Dict

from aqt import mw
from aqt.utils import showInfo, getText
from anki.notes import Note
from anki.models import NotetypeId # Ensure this is imported if used by NoteData or other funcs
from anki.decks import DeckId # Ensure this is imported
import google.generativeai as genai

# Use accessors for config and logger
from .config_utils import get_config, get_logger, get_config_value

# Globals related to API Key - these are module-level state, managed by functions below.
API_KEY_MISSING = False # Default state
API_KEY = None          # Default state

# Global related to sync disabling - managed by functions below.
DISABLE_ON_SYNC = False # Default state


def is_valid_cards_data(data: Any) -> bool:
    """Validate if the data is a list of dicts with string keys and values."""
    if not isinstance(data, list):
        return False
    for item in data:
        if not is_valid_card_data(item): # Relies on the other validation function
            return False
    return True


def is_valid_card_data(item: Any) -> bool:
    """Validate if the data is a dict with string keys and values."""
    if not isinstance(item, dict):
        return False
    if not all(
        isinstance(k, str) and 
        (isinstance(v, str) or v is None or 
         (k == "tags" and isinstance(v, list) and all(isinstance(it, str) for it in v)))
        for k, v in item.items()
    ):
        return False
    return True


# Define the Note namedtuple (remains as is)
NoteData = namedtuple("NoteData", ["note_type_id", "deck_id", "fields", "tags"])


def json_to_namedtuples(json_data: List[Dict[str, Any]], 
                        note_type_id: int, # Changed from NotetypeId to int for consistency
                        deck_id: int # Changed from DeckId to int
                       ) -> List[NoteData]:
    """Convert a JSON list of dictionaries into a list of Note namedtuples."""
    notes = []
    for entry in json_data:
        tags = entry.pop("__tags", []) 
        notes.append(
            NoteData(
                note_type_id=note_type_id,
                deck_id=deck_id,
                fields=entry,
                tags=tags,
            )
        )
    return notes


def ensure_api_key() -> bool:
    """Ensures that the API key is set. Modifies global API_KEY and API_KEY_MISSING."""
    global API_KEY, API_KEY_MISSING
    logger = get_logger() # Use accessor

    if API_KEY: # If already set in this session
        return True

    # Use get_config_value for consistency, or get_config().get()
    api_key_from_config = get_config_value("api_key") 
    # Allow override from environment variable (e.g. for testing or CI)
    # GEMINI_API_KEY is kept for backward compatibility if users have it set
    api_key_env = os.getenv("GEMINI_API_KEY", os.getenv("ZIKARIA_API_KEY")) 
    
    current_api_key = api_key_env or api_key_from_config

    if not current_api_key:
        if mw is None: # Should not happen in normal Anki environment
            logger.error("mw is None, cannot show API key dialog.")
            API_KEY_MISSING = True
            return False

        key, ok = getText("Enter your Generative AI API key:", title="API Key Required", parent=mw)
        if ok and key:
            API_KEY = key.strip()
            API_KEY_MISSING = False
            # Optionally, inform user this key is for the session and to save in config for persistence
            logger.info("API key entered manually for this session.")
            # It's generally better for ConfigDialog to handle saving the key to config.
        else: # Cancelled or empty
            showInfo("API key is required for Zikaria to function.", parent=mw)
            API_KEY_MISSING = True
            return False
    else:
        API_KEY = current_api_key.strip()
        API_KEY_MISSING = False
    
    logger.debug(f"API Key set: {'Yes' if API_KEY else 'No'}, Missing flag: {API_KEY_MISSING}")
    return not API_KEY_MISSING


def ensure_tags_exist():
    """Ensures that the required tags (from config) are present in the collection."""
    logger = get_logger()
    current_config = get_config() # Use accessor

    if not mw or not mw.col:
        logger.error("Collection (mw.col) is not available for ensure_tags_exist.")
        return

    col = mw.col
    
    # Get all keys ending with '_tag' from the current config
    tag_keys_in_config = [key for key in current_config if key.endswith("_tag")]
    required_tag_values = [current_config[key] for key in tag_keys_in_config if current_config.get(key)]

    if not required_tag_values:
        logger.info("No tags configured with '_tag' suffix to ensure.")
        return

    existing_tags_in_anki = set(col.tags.all())
    
    # defaults_for_adding() does not take arguments in modern Anki versions
    defaults = col.defaults_for_adding(current_review_card=None) 
    
    default_model_id = defaults.notetype_id
    if default_model_id is None: # Fallback if no default notetype is set
        logger.warning("Default notetype ID is None. Attempting to find a fallback for dummy note.")
        all_notetypes = col.models.all_names_and_ids()
        if not all_notetypes:
            logger.error("No notetypes available. Cannot create dummy note for tags.")
            return
        default_model_id = all_notetypes[0].id # Use the first available notetype
        logger.info(f"Using fallback notetype ID for dummy note: {default_model_id}")

    note_model_for_dummy = col.models.get(default_model_id)
    if not note_model_for_dummy:
        logger.error(f"Failed to load model for notetype id {default_model_id}. Cannot create dummy note for tags.")
        return
    dummy_note = Note(col=col, model=note_model_for_dummy)

    missing_tags_to_add = []
    for tag_value in required_tag_values:
        if tag_value not in existing_tags_in_anki:
            dummy_note.add_tag(tag_value) # Add to the note object in memory
            missing_tags_to_add.append(tag_value)

    if not missing_tags_to_add:
        logger.info("All required tags (from config) already exist in the collection.")
        return

    # Determine deck for the dummy note
    deck_id_for_dummy = defaults.deck_id
    if deck_id_for_dummy is None: # Fallback if no default deck
        logger.warning("Default deck ID is None. Using first available deck for dummy note.")
        all_decks = col.decks.all_names_and_ids()
        if not all_decks:
            logger.error("No decks available. Cannot add dummy note for tags.")
            return # Cannot proceed if no deck to add to
        deck_id_for_dummy = all_decks[0].id
        logger.info(f"Using fallback deck ID for dummy note: {deck_id_for_dummy}")
        
    try:
        # Adding the note with tags effectively registers them in Anki's tag list
        col.add_note(dummy_note, deck_id=deck_id_for_dummy)
        logger.info("Tags '%s' added to the collection via temporary dummy note.", col.tags.join(missing_tags_to_add))
        col.remove_notes([dummy_note.id]) # Clean up the dummy note immediately
    except Exception as e:
        logger.error(f"Error adding/removing dummy note for ensuring tags: {e}", exc_info=True)


def configure_generative_ai() -> bool:
    """Configures the Generative AI client. Uses global API_KEY set by ensure_api_key."""
    global API_KEY_MISSING, API_KEY # We need API_KEY which is set by ensure_api_key
    logger = get_logger()

    if not ensure_api_key(): # This will prompt user if key is missing
        logger.info("API key not available or user cancelled. Generative AI configuration aborted.")
        # API_KEY_MISSING is set by ensure_api_key()
        return False 

    try:
        genai.configure(api_key=API_KEY) # API_KEY is now guaranteed to be set if ensure_api_key returned True
        API_KEY_MISSING = False # Explicitly reset if configuration is successful
        logger.info("Generative AI configured successfully.")
        return True
    except Exception as e:
        logger.error(f"Error configuring Generative AI library: {e}", exc_info=True)
        API_KEY_MISSING = True 
        return False


def disable_running_on_sync():
    """Disables automatic processing on sync for the current session, usually after an error."""
    global DISABLE_ON_SYNC
    logger = get_logger()
    if not DISABLE_ON_SYNC:
        DISABLE_ON_SYNC = True
        logger.info("Running on sync will be disabled for this session due to a runtime error.")


def get_deck_ancestor_ids(did: Optional[int]) -> List[int]: # did can be None
    """Retrieve all ancestor deck IDs for a given deck ID."""
    logger = get_logger()
    if not mw or not mw.col:
        logger.error("mw.col not available for get_deck_ancestor_ids.")
        return []
    if did is None: # Deck ID might be None for notes not yet in a deck
        logger.debug("get_deck_ancestor_ids called with None deck ID. Returning empty list.")
        return []
    return [ancestor["id"] for ancestor in mw.col.decks.parents(did)]


def replace_nulls_with_empty_strings(data: Any) -> Any:
    """Recursively replaces all None/null values in dicts/lists with empty strings."""
    if isinstance(data, dict):
        return {k: ("" if v is None else replace_nulls_with_empty_strings(v)) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_nulls_with_empty_strings(item) for item in data]
    return data
