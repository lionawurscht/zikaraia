#!/usr/bin/env python

# Anki add-on boilerplate
from aqt import mw, gui_hooks
from aqt.qt import QAction, qconnect

# Imports from the new Zikaria modules
# Note: The ADDON_NAME in config_utils might need to be set to "zikaria" explicitly
# if __name__ within config_utils doesn't resolve correctly to the addon's root name.
# For now, we assume it's handled or will be adjusted if issues arise.
from .zikaria.config_utils import logger, config, update_config, ADDON_NAME
from .zikaria.anki_utils import (
    ensure_tags_exist,
    API_KEY_MISSING,
    DISABLE_ON_SYNC,
)
from .zikaria.core import ZikariaPrompts
from .zikaria.ui import (
    show_config_dialog_action,
    on_process_json_triggered_action,
    open_prompt_text_dialog_action,
    add_debug_menu_to_browser_action,
    on_browser_context_menu_action,
)

# --- Main Add-on Logic Setup ---


def zikaria_process_notes_manual_action():  # Renamed for clarity as an action handler
    """Manually trigger Zikaria note processing."""
    logger.debug(
        f"User manually triggered Zikaria note processing for addon: {ADDON_NAME}."
    )
    try:
        # mw is passed to ZikariaPrompts, ensuring it uses the correct instance
        processor = ZikariaPrompts(mw=mw, manual_execution=True)
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
    if not config.get("run_on_sync", False):
        logger.info(
            f"Zikaria ({ADDON_NAME}): Skipping note processing on sync (run_on_sync is false)."
        )
        return

    # Globals are imported from anki_utils
    if DISABLE_ON_SYNC:
        logger.info(
            f"Zikaria ({ADDON_NAME}): Note processing on sync is disabled due to a previous error."
        )
        return

    if API_KEY_MISSING:
        logger.info(
            f"Zikaria ({ADDON_NAME}): Note processing on sync is disabled due to missing API key."
        )
        return

    logger.info(
        f"Zikaria ({ADDON_NAME}): Starting automatic note processing after sync."
    )
    try:
        processor = ZikariaPrompts(mw=mw, manual_execution=False)  # Automatic execution
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
gui_hooks.addon_config_editor_will_update_json.append(update_config)

gui_hooks.main_window_did_init.append(on_main_window_did_init_hook)
gui_hooks.sync_did_finish.append(on_sync_did_finish_hook)

# Browser related hooks from ui.py
gui_hooks.browser_menus_did_init.append(add_debug_menu_to_browser_action)
gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu_action)


# --- Menu Item Setup ---

# Main action for manual processing
zikaria_manual_process_action_menu = QAction("Process Zikaria Notes", mw)
qconnect(
    zikaria_manual_process_action_menu.triggered, zikaria_process_notes_manual_action
)
mw.form.menuTools.addAction(zikaria_manual_process_action_menu)


# Configuration dialog action
# show_config_dialog_action is imported from ui.py
# It's designed to be called like: show_config_dialog_action(mw)
if mw.addonManager:
    # The setConfigAction is for the gear icon in addon list.
    # It expects a callable that takes no args or (parent_widget).
    # Our ui.show_config_dialog_action expects current_mw.
    mw.addonManager.setConfigAction(ADDON_NAME, lambda: show_config_dialog_action(mw))

    # Also add to Tools menu for easier access
    zikaria_config_menu_action = QAction(
        f"Zikaria Addon Configuration ({ADDON_NAME})...", mw
    )
    qconnect(
        zikaria_config_menu_action.triggered, lambda: show_config_dialog_action(mw)
    )
    mw.form.menuTools.addAction(zikaria_config_menu_action)


# "Process JSON Input" action
# on_process_json_triggered_action is imported from ui.py
zikaria_process_json_menu_action = QAction("Zikaria: Process JSON Input...", mw)
qconnect(
    zikaria_process_json_menu_action.triggered,
    lambda: on_process_json_triggered_action(mw),
)
mw.form.menuTools.addAction(zikaria_process_json_menu_action)

# "Create Cards from Prompt Text" action
# open_prompt_text_dialog_action is imported from ui.py
zikaria_create_from_text_menu_action = QAction(
    "Zikaria: Create Cards from Prompt Text...", mw
)
qconnect(
    zikaria_create_from_text_menu_action.triggered,
    lambda: open_prompt_text_dialog_action(mw),
)
mw.form.menuTools.addAction(zikaria_create_from_text_menu_action)


logger.info(
    f"Zikaria Addon ({ADDON_NAME if ADDON_NAME else __name__}) loaded successfully with modular structure."
)
# If ADDON_NAME is not set reliably from config_utils early enough, __name__ is a fallback.

# --- End of zikaria/__init__.py ---
