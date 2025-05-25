import logging
from typing import Optional
from aqt import mw

# Load user configuration
config = mw.addonManager.getConfig(__name__) if mw.addonManager else {} # Using __name__ might be an issue here. It should be the addon's name.
# For now, let's assume it needs to be the root module's __name__
# This will be fixed later when the main __init__.py is refactored.
logger = mw.addonManager.get_logger(__name__) if mw.addonManager else logging.getLogger(__name__)

if config:
    logger.info(config)
    logger.setLevel(logging.DEBUG if config.get("debug", False) else logging.INFO)
else: # Default logging if config is not loaded (e.g. testing)
    logger.setLevel(logging.DEBUG)


def update_config(text, _):
    import json
    try:
        new_config = json.loads(text)
    except json.JSONDecodeError:
        return text # Return original text if error

    global config
    config.update(new_config)
    # mw.addonManager.writeConfig(__name__, config) # This should be done by Anki
    return text


# Copied from anki_utils.py as it's used by score_custom_config_entry
def get_deck_ancestor_ids(did: int) -> list[int]:
    """
    Retrieve all ancestors of the given deck ID using Anki's API.
    :param did: Deck ID to find ancestors for.
    :return: List of ancestor deck dictionaries.
    """
    # This function relies on mw, which might not be ideal here.
    # Consider passing mw or col if this needs to be more independent.
    return [ancestor["id"] for ancestor in mw.col.decks.parents(did)]


def score_custom_config_entry(entry, note_type_id, deck_id):
    """
    Score an entry based on the given note type and deck using Anki's API.
    :param entry: The entry to score (note_type_id, deck_id, options).
    :param note_type_id: The target note type ID.
    :param deck_id: The target deck ID.
    :return: A tuple representing the score (higher is better).
    """
    entry_note_type_id, entry_deck_id, _ = entry

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
        if deck_id: # Ensure deck_id is not None before calling get_deck_ancestor_ids
            ancestor_ids = get_deck_ancestor_ids(deck_id)
            if entry_deck_id in ancestor_ids:
                return (1, -ancestor_ids.index(entry_deck_id))

    # If none match, return the lowest score
    return (0, 0)


def filter_and_sort_custom_config_entries(
    entries: list[tuple[Optional[int], Optional[int], dict]],
    note_type_id: int,
    deck_id: int,
) -> list[tuple[Optional[int], Optional[int], dict]]:
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


def get_effective_config(note_type_id: int, deck_id: int, global_config: dict, current_logger: logging.Logger):
    """
    Retrieve the effective configuration for a given note type and deck.
    This function is adapted from ZikariaPrompts.get_effective_config.
    """
    current_logger.debug(
        "Generating effective config for note type ID: %s, and deck ID: %s.",
        note_type_id,
        deck_id,
    )
    
    # Start with a copy of the global config
    effective_config = global_config.copy()

    # Custom configurations from the global_config (which should be the main `config` object)
    custom_config_entries = global_config.get("custom_config", [])

    # Ensure note_type_id and deck_id are not None for filtering and sorting
    # The filter_and_sort function expects int, not Optional[int] for these.
    # However, a note might not always have a deck (e.g., if it's a new note not yet added to a deck).
    # For now, we assume valid integer IDs are passed or the scoring handles None appropriately.
    # The original `score_custom_config_entry` handles `deck_id` being potentially None for matching.

    sorted_custom_entries = filter_and_sort_custom_config_entries(
        custom_config_entries, note_type_id, deck_id
    )

    for i, entry in enumerate(sorted_custom_entries):
        current_logger.debug("Applying custom config entry #%s: %s", i + 1, entry)
        # entry is (entry_note_type_id, entry_deck_id, settings_dict)
        settings_to_apply = entry[2]
        effective_config.update(settings_to_apply)

    current_logger.debug("Final effective config: %s", effective_config)
    return effective_config

def get_effective_config_for_note(note, global_config: dict, current_logger: logging.Logger):
    """
    Convenience function to get effective config directly from a note object.
    This function is adapted from ZikariaPrompts.get_effective_config_for_note.
    """
    note_model = note.note_type()
    if not note_model:
        current_logger.warning("Note has no model (note_type). Cannot determine effective config.")
        return global_config.copy() # Return a copy of global config

    note_type_id = note_model['id']
    
    # A note might not have cards, or might have multiple cards.
    # We need a robust way to get a deck_id.
    # If the note has cards, use the deck ID of the first card.
    # If it's a new note (no cards yet), it might not have a deck_id yet.
    # In such cases, deck_id might be considered None or a default.
    # The original code used `note.cards()[0].did`. This assumes the note has at least one card.
    cards = note.cards()
    deck_id = cards[0].did if cards else None # Use None if no cards

    if deck_id is None:
        current_logger.debug("Note has no cards or first card has no deck ID. Using None for deck_id in config resolution.")

    return get_effective_config(note_type_id, deck_id, global_config, current_logger)

# Ensure __name__ for getConfig/getLogger is correct when this module is imported.
# This will be addressed when refactoring the main __init__.py.
# For now, we assume that the add-on manager correctly resolves __name__
# to the main add-on package name.
ADDON_NAME = __name__.split('.')[0] if '.' in __name__ else __name__

def main_config():
    global config
    if not config and mw.addonManager:
        config = mw.addonManager.getConfig(ADDON_NAME) or {}
        if config:
            logger.info("Successfully loaded config for %s in main_config()", ADDON_NAME)
            logger.setLevel(logging.DEBUG if config.get("debug", False) else logging.INFO)
    return config

def main_logger():
    global logger
    # Ensure logger is configured even if initial load failed
    if not config and not logger.handlers: # Basic check if logger is unconfigured
        _logger = mw.addonManager.get_logger(ADDON_NAME) if mw.addonManager else logging.getLogger(ADDON_NAME)
        if _logger:
            logger = _logger
            logger.setLevel(logging.DEBUG if main_config().get("debug", False) else logging.INFO)
            logger.info("Logger re-initialized in main_logger for %s", ADDON_NAME)
    return logger

# Call them once to attempt loading if not already loaded.
# config = main_config()
# logger = main_logger()

# Test if mw is available, common issue in modularized Anki addons
if not hasattr(mw, "addonManager"):
    # This indicates a potential problem with Anki's environment not being fully available
    # when this module is loaded. This can happen with older Anki versions or specific load orders.
    # Fallback or warning:
    logger.warning("`mw.addonManager` is not available. Configuration and logger might not be Anki-managed.")
    # Set a default config if it's empty, to prevent errors if other parts of the code expect a dict.
    if not config:
        config = {}
        logger.info("Initialized config as empty dict due to missing mw.addonManager.")

def get_config_value(key: str, default=None):
    # Ensures that the config is loaded before trying to get a value.
    # This is a bit redundant if config is loaded at module level, but good for safety.
    # current_config = main_config()
    return config.get(key, default)

def get_logger():
    # return main_logger()
    return logger
