#!/usr/bin/env python

# Anki add-on boilerplate
from aqt import gui_hooks, mw
from aqt.qt import QAction, qconnect

from .zikaria import anki_utils
from .zikaria.anki_utils import ensure_tags_exist
from .zikaria.config_utils import ADDON_NAME, config, logger
from .zikaria.core import ZikariaPrompts
from .zikaria.dialogs.config import show_config_dialog_action
from .zikaria.dialogs.debug import (
    add_debug_menu_to_browser_action,
    on_browser_context_menu_action,
)
from .zikaria.dialogs.format_string import add_wordlist_export_context_menu
from .zikaria.dialogs.image_from_prompt import open_image_from_prompt_dialog_action
from .zikaria.dialogs.json import on_process_json_triggered_action
from .zikaria.dialogs.notes_from_prompt import open_notes_from_prompt_dialog_action
from .zikaria.dialogs.saved_prompts_manager import open_saved_prompts_manager

# --- Main Add-on Logic Setup ---


def zikaria_process_notes_manual_action():  # Renamed for clarity as an action handler
    """Manually trigger Zikaria note processing."""
    logger.debug(
        f"User manually triggered Zikaria note processing for addon: {ADDON_NAME}."
    )
    try:
        # mw is passed to ZikariaPrompts, ensuring it uses the correct instance
        processor = ZikariaPrompts(manual_execution=True)
        processor.process_notes()
    except Exception as e:
        logger.error(f"Error during manual Zikaria processing: {e}", exc_info=True)
        from aqt.utils import (
            showCritical,
        )  # Keep UI import local to UI-related function

        showCritical(
            f"An unexpected error occurred during Zikaria processing: {e}", parent=mw
        )


def on_main_window_did_init_hook():
    """
    Called when the main Anki window is initialized.
    Ensures necessary tags exist.
    """
    logger.debug(
        f"Zikaria ({ADDON_NAME}): Main window initialized. Ensuring tags exist."
    )
    ensure_tags_exist()  # This function is now in anki_utils


def on_sync_did_finish_hook():
    """
    Called after Anki synchronization is complete.
    Processes notes automatically if configured.
    """
    logger.debug(
        f"Zikaria ({ADDON_NAME}): Sync finished. Checking run_on_sync configuration."
    )

    # Access config via the imported config object
    if not config.run_on_sync:
        logger.info(
            f"Zikaria ({ADDON_NAME}): Skipping note processing on sync (run_on_sync is false)."
        )
        return

    # Globals are imported from anki_utils
    if anki_utils.DISABLE_ON_SYNC:
        logger.info(
            f"Zikaria ({ADDON_NAME}): Note processing on sync is disabled due to a previous error."
        )
        return

    if anki_utils.API_KEY_MISSING:
        logger.info(
            f"Zikaria ({ADDON_NAME}): Note processing on sync is disabled due to missing API key."
        )
        return

    logger.info(
        f"Zikaria ({ADDON_NAME}): Starting automatic note processing after sync."
    )
    try:
        processor = ZikariaPrompts(manual_execution=False)  # Automatic execution
        processor.process_notes()
        logger.info(
            f"Zikaria ({ADDON_NAME}): Automatic note processing finished successfully."
        )
    except Exception as e:
        logger.error(
            f"Error during automatic Zikaria processing after sync: {e}", exc_info=True
        )


# --- Hook Registrations ---
# update_config is directly imported from config_utils and can be used by the hook.
# gui_hooks.addon_config_editor_will_update_json.append(update_config)

gui_hooks.main_window_did_init.append(on_main_window_did_init_hook)
gui_hooks.sync_did_finish.append(on_sync_did_finish_hook)

# Browser related hooks from ui.py
gui_hooks.browser_menus_did_init.append(add_debug_menu_to_browser_action)
gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu_action)
gui_hooks.browser_will_show_context_menu.append(add_wordlist_export_context_menu)


# --- Menu Item Setup ---

# Main action for manual processing
mw.form.menuZikaria = menu_zikaria = mw.form.menubar.addMenu("Zikaria")

zikaria_manual_process_action_menu = QAction("Process Notes", mw)
qconnect(
    zikaria_manual_process_action_menu.triggered, zikaria_process_notes_manual_action
)

menu_zikaria.addAction(zikaria_manual_process_action_menu)

zikaria_create_image_menu_action = QAction("Create Image from Prompt", mw)
qconnect(
    zikaria_create_image_menu_action.triggered,
    lambda: open_image_from_prompt_dialog_action(),
)
menu_zikaria.addAction(zikaria_create_image_menu_action)

# Configuration dialog action
# show_config_dialog_action is imported from ui.py
# It's designed to be called like: show_config_dialog_action(mw)
if mw.addonManager:
    # The setConfigAction is for the gear icon in addon list.
    # It expects a callable that takes no args or (parent_widget).
    # Our ui.show_config_dialog_action expects current_mw.
    mw.addonManager.setConfigAction(ADDON_NAME, lambda: show_config_dialog_action())

    # Also add to Tools menu for easier access
    zikaria_config_menu_action = QAction("Configuration", mw)
    qconnect(zikaria_config_menu_action.triggered, lambda: show_config_dialog_action())
    menu_zikaria.addAction(zikaria_config_menu_action)


# "Process JSON Input" action
# on_process_json_triggered_action is imported from ui.py
zikaria_process_json_menu_action = QAction("Import from JSON", mw)
qconnect(
    zikaria_process_json_menu_action.triggered,
    lambda: on_process_json_triggered_action(),
)
menu_zikaria.addAction(zikaria_process_json_menu_action)

# "Create Cards from Prompt Text" action
# open_prompt_text_dialog_action is imported from ui.py
zikaria_create_from_text_menu_action = QAction("Create Card from Prompt", mw)
qconnect(
    zikaria_create_from_text_menu_action.triggered,
    lambda: open_notes_from_prompt_dialog_action(),
)
menu_zikaria.addAction(zikaria_create_from_text_menu_action)

zikaria_manage_saved_prompts = QAction("Manage Saved Prompts", mw)
qconnect(
    zikaria_manage_saved_prompts.triggered,
    lambda: open_saved_prompts_manager(),
)
menu_zikaria.addAction(zikaria_manage_saved_prompts)

logger.info(
    f"Zikaria Addon ({ADDON_NAME if ADDON_NAME else __name__}) loaded successfully with modular structure."
)
# If ADDON_NAME is not set reliably from config_utils early enough, __name__ is a fallback.

# --- End of zikaria/__init__.py ---
