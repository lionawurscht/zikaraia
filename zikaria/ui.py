import json
import os
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, cast

from anki.collection import Collection, SearchNode
from anki.decks import DeckId  # Added DeckId
from anki.models import NotetypeId  # Added NotetypeId
from anki.notes import Note, NoteFieldsCheckResult
from aqt import QMainWindow, colors, gui_hooks, mw
from aqt.browser.browser import Browser
from aqt.deckchooser import DeckChooser
# from aqt.editor import Editor, EditorMode
from aqt.notetypechooser import NotetypeChooser
from aqt.operations import QueryOp
# from aqt.operations.note import add_note
from aqt.qt import (QAction, QApplication, QCheckBox, QCloseEvent, QComboBox,
                    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                    QFrame, QGridLayout, QGroupBox, QGuiApplication,
                    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                    QSizePolicy, QSpinBox, Qt, QTabWidget, QTextEdit, QTimer,
                    QVBoxLayout, QWidget, pyqtSignal, qconnect)
from aqt.studydeck import StudyDeck
from aqt.tagedit import TagEdit
from aqt.theme import theme_manager
from aqt.utils import getText, shortcut, showInfo, tooltip, tr

from .anki_utils import is_valid_notes_data, replace_nulls_with_empty_strings
# Imports from other modules in this addon
from .config_utils import (get_addon_name, get_config, get_effective_config,
                           logger)
from .prompt_store import load_saved_prompts, save_saved_prompts
from .prompts import (example_notes, generate_pydantic_class,
                      generate_schema_class, get_prompt_text,
                      get_prompt_text_by_mode, get_prompt_text_from_note)

# from .core import ZikariaPrompts # Avoid circular import if core imports ui


def create_confirmation_dialog(
    title: str,
    message: str,
    parent=None,
    buttons: dict[str, Any] | None = None,
) -> Any:
    """
    Creates and shows a confirmation dialog with a dynamic maximum width.
    The maximum width is set to the screen's width, with a configurable margin.

    Args:
        title (str): The title of the dialog.
        message (str): The message to display.
        parent (QWidget, optional): The parent widget. Defaults to None.
        buttons (dict[str, Any], optional): A dictionary of button labels and
                                            return values. Defaults to {"OK": True, "Cancel": False}.

    Returns:
        Any: The return value of the button that was clicked.
    """
    # Create the dialog with the provided parent
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)

    # Get the screen's width from the QGuiApplication instance
    screen = QGuiApplication.primaryScreen()
    if screen:
        screen_width = screen.geometry().width()
        margin = 100  # Adjust this margin as needed for spacing
        max_dialog_width = screen_width - margin
        dialog.setMaximumWidth(max_dialog_width)
    else:
        # Fallback if screen information is unavailable
        dialog.setMaximumWidth(600)

    # Create the main layout and message label
    layout = QVBoxLayout(dialog)
    message_label = QLabel(message)
    message_label.setWordWrap(True)  # Enable word wrapping for the message
    layout.addWidget(message_label)

    # Use default buttons if none are provided
    if buttons is None:
        buttons = {"OK": True, "Cancel": False}

    # Create the horizontal button layout
    button_layout = QHBoxLayout()
    for label, return_value in buttons.items():
        button = QPushButton(label)
        button.clicked.connect(lambda _, rv=return_value: dialog.done(rv))
        button_layout.addWidget(button)

    # Add the button layout to the main layout
    layout.addLayout(button_layout)

    # Execute the dialog and return the result
    return dialog.exec()


class ChoosersMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._close_event_has_cleaned_up = False  # For chooser cleanup

    def _setup_choosers(
        self,
        note_type_id=None,
        deck_id=None,
        show_prefix_label=True,
        on_notetype_changed=None,
        on_deck_changed=None,
    ) -> None:  # Renamed
        defaults = mw.col.defaults_for_adding(
            current_review_card=(mw.reviewer.card if mw.reviewer else None)
        )
        note_type_id = note_type_id or defaults.notetype_id
        deck_id = deck_id or defaults.deck_id

        self.notetype_chooser_instance = NotetypeChooser(
            mw=mw,
            widget=self.note_type_combo_widget,
            starting_notetype_id=NotetypeId(note_type_id),
            show_prefix_label=show_prefix_label,
            on_notetype_changed=on_notetype_changed,
        )
        self.deck_chooser_instance = DeckChooser(
            mw=mw,
            widget=self.deck_combo_widget,
            starting_deck_id=DeckId(deck_id),
            label=show_prefix_label,
            on_deck_changed=on_deck_changed,
        )

    def _cleanup_choosers(self):
        if not self._close_event_has_cleaned_up:
            logger.debug("%s: Cleaning up choosers.", self.__class__.__name__)

            if (
                hasattr(self, "notetype_chooser_instance")
                and self.notetype_chooser_instance
            ):
                self.notetype_chooser_instance.cleanup()
                logger.debug("Cleaned up notetype_chooser_instance.")
            if hasattr(self, "deck_chooser_instance") and self.deck_chooser_instance:
                self.deck_chooser_instance.cleanup()
                logger.debug("Cleaned up deck_chooser_instance.")
            self._close_event_has_cleaned_up = True


def get_duplicate_note_ids_by_checksum(
    note: Note, origin_note: Note | None = None
) -> list[int]:
    """
    Finds all duplicate Note IDs (NIDs) in the collection that match
    the first field and model type of the given note, using the official
    SearchNode structure for programmatic searching.
    """
    col: Collection = mw.col

    # 1. Get the Note Type ID and the value of the first field.
    mid = note.mid
    note_type = col.models.get(mid)

    if not note_type or not note_type["flds"]:
        return []

    # Get the value of the *first field*. This is what Anki uses for duplicate checking.
    # Note: We must ensure the field value used here is the *raw* value.
    first_field_ord = note_type["flds"][0]["ord"]
    first_field_value = note.fields[first_field_ord]

    # Clean the field value for the search. This cleaning is handled
    # implicitly by the SearchNode(dupe=...) logic in the Rust backend,
    # but we pass the raw content here.

    # 2. Construct the search using the official Dupe SearchNode structure.
    # This structure is specifically designed to perform the first-field
    # duplicate check, similar to the one used by the browser/editor (showDupes).
    dupe_search_node = SearchNode(
        dupe=SearchNode.Dupe(
            notetype_id=mid,
            first_field=first_field_value,
        )
    )

    # 3. Build the full search string and execute the search.
    # We explicitly exclude the Note ID of the note being checked (if it's an existing note)
    # to prevent it from finding itself.

    # Combine the dupe search with an exclusion search
    nids_to_exclude = []
    if note.id:
        nids_to_exclude.append(note.id)

    if origin_note and origin_note.id and origin_note.id != note.id:
        nids_to_exclude.append(origin_note.id)

    # Create the search query string
    search_query = col.build_search_string(
        dupe_search_node,
        *[f"-nid:{nid}" for nid in nids_to_exclude],  # Exclude the note being checked
    )

    logger.debug(f"Searching for duplicates using: {search_query}")

    # Execute the search and cast the result to list[int]
    duplicate_nids = cast(list[int], col.find_notes(search_query))

    return duplicate_nids


# Note: The usage of this function in _handle_dupe_result remains the same:
# op=lambda _: get_duplicate_note_ids_by_checksum(note),
# This ensures it runs in the background thread as intended.


class DuplicateResolutionDialog(QDialog):
    def __init__(
        self,
        new_card_data: dict[str, Any],
        new_note_template: Note,
        duplicate_nids: list[int],
        parent: QWidget | None = None,
    ):
        super().__init__(parent or mw)
        self.setWindowTitle("Resolve Duplicates (Synchronized)")
        self.new_note_template = new_note_template

        # Central store for the new card data being edited
        self.current_new_data = new_card_data.copy()

        # Store original values for restore functionality
        self.original_field_values = new_card_data.copy()

        # Stores ALL QLineEdit instances, keyed by field_name, as a list
        # Example: {'Front': [QLineEdit@tab1, QLineEdit@tab2, ...]}
        self.field_widgets: dict[str, list[QLineEdit]] = {}

        self.duplicate_notes: list[Note] = [
            mw.col.get_note(nid) for nid in duplicate_nids if mw.col.get_note(nid)
        ]

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        self.resize(1200, 800)

        # Tab Widget for Duplicates
        self.tab_widget = QTabWidget(self)

        if not self.duplicate_notes:
            main_layout.addWidget(QLabel("Error: Could not load any duplicate notes."))
            return

        # Create a comparison tab for each duplicate
        for i, dupe_note in enumerate(self.duplicate_notes):
            dupe_tab_name = f"Dupe Note {i+1} (ID: {dupe_note.id})"
            tab_widget = self._create_comparison_tab(dupe_note)
            self.tab_widget.addTab(tab_widget, dupe_tab_name)

        main_layout.addWidget(self.tab_widget)

        # Dialog Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

    def _create_comparison_tab(self, dupe_note: Note) -> QWidget:
        """Creates a widget for comparing the new card against a single duplicate."""
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)

        comparison_group = QGroupBox("Field Comparison")
        comparison_layout = QGridLayout(comparison_group)

        # Headers
        comparison_layout.addWidget(QLabel("<b>F-Action</b>"), 0, 0)
        comparison_layout.addWidget(QLabel("<b>Field</b>"), 0, 1)
        comparison_layout.addWidget(QLabel("<b>New Card Value (Editable)</b>"), 0, 2)
        comparison_layout.addWidget(QLabel("<b>Duplicate Value</b>"), 0, 3)
        comparison_layout.addWidget(QLabel("<b>D-Action</b>"), 0, 4)

        # Fields
        for row, field_def in enumerate(self.new_note_template.note_type()["flds"]):
            field_name = field_def["name"]

            # --- New Card Field (Editable) ---
            # 1. Create a NEW QLineEdit instance for this specific tab
            new_value = self.current_new_data.get(field_name, "")
            new_edit = QLineEdit(new_value)
            new_edit.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

            # 2. Store the widget instance in the dictionary for synchronization
            self.field_widgets.setdefault(field_name, []).append(new_edit)

            # 3. Connect the signal to the synchronization slot
            new_edit.textEdited.connect(
                # Use default args to pass the field name correctly to the slot
                lambda text, name=field_name: self._update_field_value(name, text)
            )

            # --- Duplicate Field (Read Only) ---
            dupe_value = dupe_note.fields[field_def["ord"]]
            dupe_edit = QLineEdit(dupe_value)
            dupe_edit.setReadOnly(True)
            if theme_manager.night_mode:
                dupe_edit.setStyleSheet("background-color: #0a0a0a;")
            else:
                dupe_edit.setStyleSheet("background-color: #f0f0f0;")

            dupe_edit.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

            # --- Action Buttons Container ---
            # 1. Create the container widget and its layout
            f_action_widget = QWidget()
            f_action_layout = QHBoxLayout(f_action_widget)
            f_action_layout.setContentsMargins(
                0, 0, 0, 0
            )  # Remove margin/spacing around buttons
            f_action_layout.setSpacing(5)  # Set a small spacing between the buttons

            # "Restore" Button
            restore_btn = QPushButton("Restore")
            restore_btn.clicked.connect(
                lambda _, n=field_name, v=self.original_field_values[
                    field_name
                ]: self._update_field_value(n, v)
            )

            # "Selte" Button
            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(
                lambda _, n=field_name, v="": self._update_field_value(n, v)
            )

            f_action_layout.addWidget(restore_btn)
            f_action_layout.addWidget(clear_btn)

            # --- Action Buttons Container ---
            # 1. Create the container widget and its layout
            d_action_widget = QWidget()
            d_action_layout = QHBoxLayout(d_action_widget)
            d_action_layout.setContentsMargins(
                0, 0, 0, 0
            )  # Remove margin/spacing around buttons
            d_action_layout.setSpacing(5)  # Set a small spacing between the buttons

            # "Copy" Button
            copy_btn = QPushButton("Copy Dupe")
            # Connect the button to update the central data and ALL widgets
            copy_btn.clicked.connect(
                lambda _, n=field_name, v=dupe_value: self._update_field_value(n, v)
            )

            # "Copy" Button
            append_btn = QPushButton("Append Dupe")
            # Connect the button to update the central data and ALL widgets
            append_btn.clicked.connect(
                lambda _, n=field_name, v=dupe_value: self._append_field_value(n, v)
            )

            # Add buttons to the inner layout
            d_action_layout.addWidget(copy_btn)
            d_action_layout.addWidget(append_btn)

            # Add to layout
            comparison_layout.addWidget(f_action_widget, row + 1, 0)
            comparison_layout.addWidget(QLabel(f"<b>{field_name}</b>"), row + 1, 1)
            comparison_layout.addWidget(new_edit, row + 1, 2)
            comparison_layout.addWidget(dupe_edit, row + 1, 3)
            comparison_layout.addWidget(d_action_widget, row + 1, 4)

        tab_layout.addWidget(comparison_group)

        # Bulk Actions
        bulk_layout = QHBoxLayout()

        restore_all_btn = QPushButton(f"Restore All")
        restore_all_btn.clicked.connect(lambda: self._restore_all())
        bulk_layout.addWidget(restore_all_btn)

        overwrite_all_btn = QPushButton(f"Overwrite All Fields from Dupe")
        overwrite_all_btn.clicked.connect(lambda: self._copy_all_from_dupe(dupe_note))
        bulk_layout.addWidget(overwrite_all_btn)

        copy_nonempty_btn = QPushButton(f"Copy Non-Empty Fields from Dupe")
        copy_nonempty_btn.clicked.connect(
            lambda: self._copy_nonempty_from_dupe(dupe_note)
        )
        bulk_layout.addWidget(copy_nonempty_btn)

        open_dupe_btn = QPushButton(f"Open Dupe in Browser")
        open_dupe_btn.clicked.connect(lambda: self._open_dupe_in_browser(dupe_note.id))
        bulk_layout.addWidget(open_dupe_btn)

        tab_layout.addLayout(bulk_layout)
        tab_layout.addStretch(1)

        return tab_widget

    def _append_field_value(self, field_name: str, new_value: str) -> None:
        """Appends to a field, updates the central data store and synchronizes all widgets for a field."""

        # 1. Update the central data store
        old_value = self.current_new_data[field_name]
        new_value = "".join([old_value, new_value])
        self.current_new_data[field_name] = new_value

        # 2. Synchronize all QLineEdits for this field name
        # We temporarily block signals to prevent an infinite loop (A updates B, B updates A, etc.)
        if field_name in self.field_widgets:
            for widget in self.field_widgets[field_name]:
                if widget.text() != new_value:  # Only update if necessary
                    widget.blockSignals(True)
                    widget.setText(new_value)
                    widget.blockSignals(False)

    def _restore_all(self):
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            original_value = self.original_field_values[field_name]

            # Use the synchronized method to update all fields
            self._update_field_value(field_name, original_value)

    def _update_field_value(self, field_name: str, new_value: str) -> None:
        """Updates the central data store and synchronizes all widgets for a field."""

        # 1. Update the central data store
        self.current_new_data[field_name] = new_value

        # 2. Synchronize all QLineEdits for this field name
        # We temporarily block signals to prevent an infinite loop (A updates B, B updates A, etc.)
        if field_name in self.field_widgets:
            for widget in self.field_widgets[field_name]:
                if widget.text() != new_value:  # Only update if necessary
                    widget.blockSignals(True)
                    widget.setText(new_value)
                    widget.blockSignals(False)

    def _copy_all_from_dupe(self, dupe_note: Note) -> None:
        """Copies all field values from the selected duplicate by updating the central store."""
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            dupe_value = dupe_note.fields[field_def["ord"]]

            # Use the synchronized method to update all fields
            self._update_field_value(field_name, dupe_value)

    def _copy_nonempty_from_dupe(self, dupe_note: Note) -> None:
        """Copies only non-empty field values from the duplicate to the new card."""
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            dupe_value = dupe_note.fields[field_def["ord"]]
            if dupe_value:
                self._update_field_value(field_name, dupe_value)

    def _open_dupe_in_browser(self, dupe_nid: int) -> None:
        """Opens the Anki Browser filtered to the specific duplicate Note ID."""
        aqt.dialogs.open("Browser", mw, search=f"nid:{dupe_nid}")

    def get_resolved_data(self) -> dict[str, Any]:
        """Collects the final data from the central, authoritative data store."""
        return self.current_new_data


class ConfirmationDialog(QDialog):
    def __init__(
        self,
        notes_data_map: dict[Note, list[dict[str, Any]]],
        parent: QWidget | None = None,
        global_tags: list[str] | None = None,
    ):
        super().__init__(parent or mw)
        self.setWindowTitle("Confirm New Cards")
        self.resize(1200, 800)

        total_cards = sum(len(data_list) for data_list in notes_data_map.values())
        self.selected_cards_flags = [True] * total_cards

        # Global Card Index -> List of Dupe NIDs (int)
        self.duplicate_nids_map: dict[int, list[int]] = {}
        # (Note, Data, Frame, G-Index) for async check setup
        self.widgets_to_check: list[tuple[Note, dict[str, Any], QWidget, int]] = []

        self.setStyleSheet("QWidget { font-size: 16px; }")
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_content_widget.setLayout(scroll_layout)

        # (CheckBox, FieldsMap, NoteTemplate, TagEdit, DupeButton)
        self.card_widgets_list_internal: list[
            tuple[QCheckBox, dict[str, QLineEdit], Note, TagEdit, QPushButton]
        ] = []

        card_global_idx = 0

        for note_template, card_data_list_item in notes_data_map.items():
            for card_data_item_dict in card_data_list_item:
                card_frame = QFrame()
                # card_frame.setStyleSheet("border: 1px solid gray;") # Default border
                card_layout = QVBoxLayout()
                header_layout = QHBoxLayout()
                card_checkbox = QCheckBox(f"Card {card_global_idx + 1}")
                card_checkbox.setChecked(True)
                card_checkbox.stateChanged.connect(
                    lambda state, idx=card_global_idx: self.toggle_card_selection(
                        idx, state
                    )
                )
                header_layout.addWidget(card_checkbox)

                # Duplicate Resolution Button
                resolve_dupe_btn = QPushButton("Checking Duplicates...")
                resolve_dupe_btn.setToolTip(
                    "Opens a dialog to resolve field-level conflicts."
                )
                resolve_dupe_btn.setEnabled(False)
                resolve_dupe_btn.clicked.connect(
                    lambda _, idx=card_global_idx: self._show_resolve_dialog(idx)
                )
                header_layout.addWidget(resolve_dupe_btn)

                card_layout.addLayout(header_layout)

                field_widgets_map: dict[str, QLineEdit] = {}
                note_type_model = note_template.note_type()
                if not note_type_model:
                    logger.error("ConfirmationDialog: Note template has no model.")
                    continue

                present_keys: set[str] = set()
                temp_note = Note(mw.col, note_template.mid)

                for field_def in note_type_model["flds"]:
                    field_name = field_def["name"]
                    value_str = card_data_item_dict.get(field_name, "")
                    if field_name in card_data_item_dict:
                        present_keys.add(field_name)

                    field_layout = QHBoxLayout()
                    field_label = QLabel(f"{field_name}:")
                    field_edit = QLineEdit(
                        str(value_str) if value_str is not None else ""
                    )
                    field_layout.addWidget(field_label)
                    field_layout.addWidget(field_edit)
                    card_layout.addLayout(field_layout)
                    field_widgets_map[field_name] = field_edit

                    # Populate temp_note for async check
                    temp_note.fields[field_def["ord"]] = field_edit.text()

                tags_list = card_data_item_dict.get("tags", [])
                if "tags" in card_data_item_dict:
                    present_keys.add("tags")

                unused_data_keys = [
                    key for key in card_data_item_dict if key not in present_keys
                ]
                if unused_data_keys:
                    rest_layout = QHBoxLayout()
                    rest_label = QLabel("Unused data:")
                    rest_edit = QLineEdit(
                        json.dumps(
                            {key: card_data_item_dict[key] for key in unused_data_keys}
                        )
                    )
                    rest_edit.setReadOnly(True)
                    rest_layout.addWidget(rest_label)
                    rest_layout.addWidget(rest_edit)
                    card_layout.addLayout(rest_layout)

                card_tag_edit_widget = self._get_card_tag_edit(tags_list, card_layout)

                self.card_widgets_list_internal.append(
                    (
                        card_checkbox,
                        field_widgets_map,
                        note_template,
                        card_tag_edit_widget,
                        resolve_dupe_btn,
                    )
                )

                # Prepare for async check
                self.widgets_to_check.append(
                    (temp_note, card_data_item_dict, card_frame, card_global_idx)
                )

                card_frame.setLayout(card_layout)
                scroll_layout.addWidget(card_frame)
                card_global_idx += 1

        # Add functionality for duplicate checking on text change
        self._first_field_timers = {}  # global_idx -> QTimer

        for idx, (
            _,
            field_widgets_map,
            note_template,
            _,
            resolve_dupe_btn,
        ) in enumerate(self.card_widgets_list_internal):
            # Get the first field QLineEdit
            first_field_name = next(iter(field_widgets_map))
            first_field_edit = field_widgets_map[first_field_name]
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(500)  # 500ms debounce
            timer.timeout.connect(lambda idx=idx: self._check_dupe_for_card(idx))
            self._first_field_timers[idx] = timer

            def on_first_field_changed(text, idx=idx):
                self._first_field_timers[idx].start()

            first_field_edit.textChanged.connect(on_first_field_changed)

        scroll_area.setWidget(scroll_content_widget)
        self.main_layout.addWidget(scroll_area)

        bulk_action_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(self.select_all_cards)
        bulk_action_layout.addWidget(select_all_button)
        deselect_all_button = QPushButton("Select None")
        deselect_all_button.clicked.connect(self.deselect_all_cards)
        bulk_action_layout.addWidget(deselect_all_button)
        invert_selection_button = QPushButton("Invert Selection")
        invert_selection_button.clicked.connect(self.invert_card_selection)
        bulk_action_layout.addWidget(invert_selection_button)

        remove_all_tags_button = QPushButton("Remove All Tags")
        remove_all_tags_button.setToolTip(
            "Remove all tags from all cards (does not affect global tags)"
        )
        remove_all_tags_button.clicked.connect(self.remove_all_card_tags)
        bulk_action_layout.addWidget(remove_all_tags_button)

        self.main_layout.addLayout(bulk_action_layout)

        self._setup_global_tag_edit(global_tags or [])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(buttons)

        # Start the async check
        self._check_dupes_on_open()

    def _setup_global_tag_edit(self, global_tags_list: list[str]) -> None:
        tag_edit_frame = QWidget(self)
        tag_edit_frame.setStyleSheet("border: 0")
        tag_edit_layout = QGridLayout()
        tag_edit_layout.setSpacing(12)
        tag_edit_layout.setContentsMargins(2, 6, 2, 6)
        tag_edit_label = QLabel(tr.editing_tags())
        tag_edit_layout.addWidget(tag_edit_label, 1, 0)
        self.global_tag_edit_widget_internal = TagEdit(self)
        self.global_tag_edit_widget_internal.setToolTip(
            shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
        )
        border = theme_manager.var(colors.BORDER)
        self.global_tag_edit_widget_internal.setStyleSheet(
            f"border: 1px solid {border}"
        )
        tag_edit_layout.addWidget(self.global_tag_edit_widget_internal, 1, 1)
        tag_edit_frame.setLayout(tag_edit_layout)
        self.main_layout.addWidget(tag_edit_frame)
        if self.global_tag_edit_widget_internal.col != mw.col:
            self.global_tag_edit_widget_internal.setCol(mw.col)
        self.global_tag_edit_widget_internal.setText(mw.col.tags.join(global_tags_list))

    def _get_card_tag_edit(
        self, tags_list: list[str], card_layout_ref: QVBoxLayout
    ) -> TagEdit:
        tag_layout = QHBoxLayout()
        tag_label = QLabel(f"{tr.editing_tags()}:")
        tag_edit_widget = TagEdit(self)
        tag_edit_widget.setToolTip(
            shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
        )
        if tag_edit_widget.col != mw.col:
            tag_edit_widget.setCol(mw.col)
        tag_edit_widget.setText(mw.col.tags.join(tags_list))
        tag_layout.addWidget(tag_label)
        tag_layout.addWidget(tag_edit_widget)
        card_layout_ref.addLayout(tag_layout)
        return tag_edit_widget

    def remove_all_card_tags(self):
        for _, _, _, tag_edit_widget, _ in self.card_widgets_list_internal:
            tag_edit_widget.setText("")

    def _check_dupes_on_open(self) -> None:
        """Start asynchronous duplicate checks for all cards."""
        for temp_note, _, _, global_idx in self.widgets_to_check:
            _, _, note_template, _, dupe_btn = self.card_widgets_list_internal[
                global_idx
            ]

            QueryOp(
                parent=self,
                op=lambda _, temp_note=temp_note, note_template=note_template: get_duplicate_note_ids_by_checksum(
                    temp_note, note_template
                ),
                success=lambda nids, idx=global_idx, btn=dupe_btn: self._store_nids_and_update_button(
                    nids, idx, btn
                ),
            ).run_in_background()

    def _check_dupe_for_card(self, global_idx: int) -> None:
        temp_note, _, _, _ = self.widgets_to_check[global_idx]

        # Update temp_note's first field to current value
        field_widgets_map = self.card_widgets_list_internal[global_idx][1]
        note_template = self.card_widgets_list_internal[global_idx][2]
        dupe_btn = self.card_widgets_list_internal[global_idx][4]
        first_field_name = next(iter(field_widgets_map))
        first_field_edit = field_widgets_map[first_field_name]

        # Update temp_note's first field
        note_type_model = note_template.note_type()

        if not note_type_model:
            return

        first_field_ord = note_type_model["flds"][0]["ord"]
        temp_note.fields[first_field_ord] = first_field_edit.text()
        QueryOp(
            parent=self,
            op=lambda _: get_duplicate_note_ids_by_checksum(temp_note, note_template),
            success=lambda nids, idx=global_idx, btn=dupe_btn: self._store_nids_and_update_button(
                nids, idx, btn
            ),
        ).run_in_background()

    def _store_nids_and_update_button(
        self, nids: list[int], global_idx: int, dupe_btn: QPushButton
    ) -> None:
        """Store the found NIDs and update the button's state and text."""
        if nids:
            self.duplicate_nids_map[global_idx] = nids
            dupe_btn.setStyleSheet("border: 2px solid red;")
            dupe_btn.setText(f"Resolve Duplicate ({len(nids)})")
            dupe_btn.setEnabled(True)
        else:
            # Should not happen if fields_check() returned DUPLICATE, but safe to handle
            dupe_btn.setStyleSheet("border: 2px solid green;")
            dupe_btn.setText("No Duplicates Found")
            dupe_btn.setEnabled(False)

    def _show_resolve_dialog(self, global_idx: int) -> None:
        """Opens the comparison dialog for a specific card."""
        dupe_nids = self.duplicate_nids_map.get(global_idx)
        if not dupe_nids:
            # If the button was enabled but lookup failed, offer to open browser
            self._open_browser_to_dupes_by_first_field(global_idx)
            return

        _cb, fields_map, note_tmpl, tag_edit_w, dupe_btn = (
            self.card_widgets_list_internal[global_idx]
        )

        # Reconstruct the current card data from the QLineEdits
        current_card_data: dict[str, Any] = {
            field_name: widget.text() for field_name, widget in fields_map.items()
        }
        current_card_data["tags"] = mw.col.tags.split(tag_edit_w.text())

        # Open the new resolution dialog
        resolution_dialog = DuplicateResolutionDialog(
            new_card_data=current_card_data,
            new_note_template=note_tmpl,
            duplicate_nids=dupe_nids,
            parent=self,
        )

        if resolution_dialog.exec() == QDialog.DialogCode.Accepted:
            resolved_data = resolution_dialog.get_resolved_data()

            # ... (Update fields logic remains the same) ...
            for field_name, new_value in resolved_data.items():
                if field_name != "tags":
                    fields_map[field_name].setText(new_value)

            if "tags" in resolved_data:
                new_tags_text = mw.col.tags.join(resolved_data["tags"])
                tag_edit_w.setText(new_tags_text)

            dupe_btn.setText("Duplicates Resolved (Manual)")
            # dupe_btn.setEnabled(False)
            self.card_widgets_list_internal[global_idx][4].setStyleSheet(
                "border: 2px solid blue;"
            )

    # --- New Method for Browser Opener ---
    def _open_browser_to_dupes_by_first_field(self, global_idx: int) -> None:
        """Opens the Anki Browser using the official SearchNode structure."""
        _cb, fields_map, note_tmpl, tag_edit_w, dupe_btn = (
            self.card_widgets_list_internal[global_idx]
        )

        note_type_id = note_tmpl.note_type()["id"]
        # The first field is always index 0 in the fields_map keys (due to iteration order)
        first_field_value = next(iter(fields_map.values())).text()

        aqt.dialogs.open(
            "Browser",
            mw,
            search=(
                SearchNode(
                    dupe=SearchNode.Dupe(
                        notetype_id=note_type_id,
                        first_field=first_field_value,
                    )
                ),
            ),
        )

    def toggle_card_selection(self, index: int, state: int) -> None:
        self.selected_cards_flags[index] = Qt.CheckState(state) == Qt.CheckState.Checked

    def select_all_cards(self) -> None:
        for checkbox, *_ in self.card_widgets_list_internal:
            checkbox.setChecked(True)

    def deselect_all_cards(self) -> None:
        for checkbox, *_ in self.card_widgets_list_internal:
            checkbox.setChecked(False)

    def invert_card_selection(self) -> None:
        for checkbox, *_ in self.card_widgets_list_internal:
            checkbox.setChecked(not checkbox.isChecked())

    def get_confirmed_notes_data(
        self,
    ) -> dict[Note, list[dict[str, Any]]]:
        global_tags_set = set(
            mw.col.tags.split(self.global_tag_edit_widget_internal.text())
        )
        confirmed_map: dict[Note, list[dict[str, Any]]] = {}
        for idx, selected_flag in enumerate(self.selected_cards_flags):
            if selected_flag:
                _cb, fields_map, note_tmpl, tag_edit_w, _dupe_btn = (
                    self.card_widgets_list_internal[idx]
                )
                tags_set = set(mw.col.tags.split(tag_edit_w.text()))
                tags_set.update(global_tags_set)
                updated_data = {
                    field_name: widget.text()
                    for field_name, widget in fields_map.items()
                }
                updated_data["tags"] = list(tags_set)
                confirmed_map.setdefault(note_tmpl, []).append(updated_data)
        return confirmed_map


# TODO: (Maybe) Maintain optional card data and add per-note restore capabilities
def display_notes_confirmation_dialog(
    notes_data_map: dict[Note, list[dict[str, str | list[str]]]],
    parent: QWidget | None = None,
    global_tags: list[str] | None = None,
) -> dict[Note, list[dict[str, Any]]]:

    dialog = ConfirmationDialog(
        notes_data_map=notes_data_map,
        global_tags=global_tags,
        parent=parent,
    )

    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_confirmed_notes_data()
    return {}


# --- JsonInputDialog ---
class JsonInputDialog(QDialog, ChoosersMixin):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent or mw)
        self._close_event_has_cleaned_up = False
        self.setWindowTitle("Process JSON Input")
        self.resize(800, 1000)
        # ... (rest of implementation using mw)
        layout = QVBoxLayout(self)
        self.json_input_area = QTextEdit(self)
        self.json_input_area.setPlaceholderText("Enter JSON list here...")
        layout.addWidget(self.json_input_area)

        self.note_type_combo_widget = QWidget(self)
        self.deck_combo_widget = QWidget(self)
        self._setup_choosers()  # Renamed
        layout.addWidget(self.note_type_combo_widget)
        layout.addWidget(self.deck_combo_widget)

        button_row_layout = QHBoxLayout()
        self.insert_template_button_widget = QPushButton("Insert JSON Template")
        self.insert_template_button_widget.clicked.connect(
            self.insert_json_template_handler
        )
        button_row_layout.addWidget(self.insert_template_button_widget)
        button_row_layout.addStretch()

        self.dialog_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.dialog_buttons.accepted.connect(self.accept)
        self.dialog_buttons.rejected.connect(self.reject)
        button_row_layout.addWidget(self.dialog_buttons)
        layout.addLayout(button_row_layout)

    def insert_json_template_handler(self):
        try:
            note_type_id = self.notetype_chooser_instance.selected_notetype_id
            note_type_model = mw.col.models.get(note_type_id)
            field_names = [f["name"] for f in note_type_model["flds"]]
            note_template = {field: "" for field in field_names}
            note_template["tags"] = []
            self.json_input_area.setPlainText(json.dumps([note_template], indent=4))
        except Exception as e:
            logger.error(f"Error generating template: {e}", exc_info=True)
            showInfo(f"Error generating template: {e}", parent=self)

    def get_input_data(self) -> Dict[str, Any]:
        return {
            "json_data": self.json_input_area.toPlainText(),
            "deck_id": self.deck_chooser_instance.selected_deck_id,
            "notetype_id": self.notetype_chooser_instance.selected_notetype_id,
        }

    def _cleanup_choosers(self):  # Added helper
        if not self._close_event_has_cleaned_up:
            if (
                hasattr(self, "notetype_chooser_instance")
                and self.notetype_chooser_instance
            ):
                self.notetype_chooser_instance.cleanup()
            if hasattr(self, "deck_chooser_instance") and self.deck_chooser_instance:
                self.deck_chooser_instance.cleanup()
            self._close_event_has_cleaned_up = True

    def accept(self) -> None:
        self._cleanup_choosers()
        super().accept()

    def reject(self) -> None:
        self._cleanup_choosers()
        super().reject()

    def closeEvent(self, event):
        self._cleanup_choosers()
        super().closeEvent(event)

type NotesDataList = list[dict[str, str | list[str]]]

# --- on_process_json_triggered_action ---
def on_process_json_triggered_action():
    config = get_config()  # Get current config
    dialog = JsonInputDialog()

    from .core import \
        ZikariaPrompts  # Import locally to avoid circularity at module level

    if dialog.exec() == QDialog.DialogCode.Accepted:
        input_data = dialog.get_input_data()
        json_text_data = input_data["json_data"]
        deck_id = input_data["deck_id"]
        note_type_id = input_data["notetype_id"]

        note_type = mw.col.models.get(note_type_id)

        core_processor = ZikariaPrompts(manual_execution=True)

        notes_data: NotesDataList | None = core_processor._parse_json_into_notes_data(
            json_string=json_text_data, note_type=note_type
        )


        json_tag_val = config.get("json_tag", "from_json")


        dummy_note = Note(mw.col, note_type)

        def per_note_fn(original_note, notes_data_list):
            core_processor._add_notes(
                note_type=note_type,
                notes_data_list=notes_data_list,
                deck_id=deck_id,
            )

        core_processor._finalize_notes(
            notes_map={dummy_note: notes_data}, per_note_fn=per_note_fn, finished_msg="create notes", global_tags=[json_tag_val]
        )


class DebugDialog(QDialog):
    def __init__(
        self,
        note: Note,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent or mw)
        self.note = note
        self.config = get_config()
        self.setWindowTitle("Debug Note (Zikaria)")
        self.resize(800, 1000)

        self.effective_config = get_effective_config(
            self.note.mid, self.note.cards()[0].did if self.note.cards() else None
        )

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._init_schema_tab()
        self._init_pydantic_schema_tab()
        self._init_examples_tab()
        # --- NEW TAB INITIALIZATION ---
        self._init_note_json_tab()
        # ------------------------------
        self._init_create_prompt_tab()
        self._init_complete_prompt_tab()
        self._init_prompt_tab()

        # Initial content
        self.display_schema_handler()
        self.display_pydantic_schema_handler()
        self.display_examples_handler()
        # --- NEW TAB DISPLAY CALL ---
        self.display_note_json_handler()
        # ----------------------------
        self.display_create_prompt_handler()
        self.display_complete_prompt_handler()
        self.display_prompt_handler()

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def _add_output_area_with_controls(self, text_edit, refresh_func):
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        copy_btn = QPushButton("Copy to Clipboard")
        refresh_btn.clicked.connect(refresh_func)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(text_edit))
        controls.addWidget(refresh_btn)
        controls.addWidget(copy_btn)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(text_edit)
        return wrapper

    # --- NEW METHOD: Tab Initialization ---
    def _init_note_json_tab(self):
        self.note_json_output_area = QTextEdit()
        self.note_json_output_area.setReadOnly(True)
        tab = self._add_output_area_with_controls(
            self.note_json_output_area, self.display_note_json_handler
        )
        self.tabs.addTab(tab, "Note JSON")

    # --------------------------------------

    def _init_schema_tab(self):
        self.schema_output_area = QTextEdit()
        self.schema_output_area.setReadOnly(True)
        tab = self._add_output_area_with_controls(
            self.schema_output_area, self.display_schema_handler
        )
        self.tabs.addTab(tab, "Schema")

    def _init_pydantic_schema_tab(self):
        self.pydantic_schema_output_area = QTextEdit()
        self.pydantic_schema_output_area.setReadOnly(True)
        tab = self._add_output_area_with_controls(
            self.pydantic_schema_output_area, self.display_pydantic_schema_handler
        )
        self.tabs.addTab(tab, "Pydantic Schema")

    def _init_create_prompt_tab(self):
        self.create_prompt_output_area = QTextEdit()
        self.create_prompt_output_area.setReadOnly(True)

        self.include_schema_create = QCheckBox("Include Schema")
        self.include_examples_create = QCheckBox("Include Examples")

        for checkbox in [self.include_schema_create, self.include_examples_create]:
            checkbox.stateChanged.connect(
                lambda __: self.display_create_prompt_handler()
            )

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.addWidget(self.include_schema_create)
        layout.addWidget(self.include_examples_create)

        controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Create Prompt")
        copy_btn = QPushButton("Copy to Clipboard")
        refresh_btn.clicked.connect(self.display_create_prompt_handler)
        copy_btn.clicked.connect(
            lambda: self.copy_to_clipboard(self.create_prompt_output_area)
        )
        controls.addWidget(refresh_btn)
        controls.addWidget(copy_btn)
        controls.addStretch()

        layout.addLayout(controls)
        layout.addWidget(self.create_prompt_output_area)

        self.tabs.addTab(wrapper, "Create Prompt")

    def _init_complete_prompt_tab(self):
        self.complete_prompt_output_area = QTextEdit()
        self.complete_prompt_output_area.setReadOnly(True)

        self.include_schema_complete = QCheckBox("Include Schema")
        self.include_examples_complete = QCheckBox("Include Examples")
        for checkbox in [self.include_schema_complete, self.include_examples_complete]:
            checkbox.stateChanged.connect(
                lambda __: self.display_complete_prompt_handler()
            )

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.addWidget(self.include_schema_complete)
        layout.addWidget(self.include_examples_complete)

        controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Complete Prompt")
        copy_btn = QPushButton("Copy to Clipboard")
        refresh_btn.clicked.connect(self.display_complete_prompt_handler)
        copy_btn.clicked.connect(
            lambda: self.copy_to_clipboard(self.complete_prompt_output_area)
        )
        controls.addWidget(refresh_btn)
        controls.addWidget(copy_btn)
        controls.addStretch()

        layout.addLayout(controls)
        layout.addWidget(self.complete_prompt_output_area)

        self.tabs.addTab(wrapper, "Complete Prompt")

    def _init_prompt_tab(self):
        self.prompt_output_area = QTextEdit()
        self.prompt_output_area.setReadOnly(True)

        self.prompt_template_combo_widget = QWidget(self)
        self.prompt_template_chooser_instance = PromptTemplateChooser(
            mw=mw,
            widget=self.prompt_template_combo_widget,
            # starting_prompt = None,
            # note_type_id = self.note.note_type().id,
            # deck_id = self.deck_chooser_instance.selected_deck_id,
            # on_prompt_changed = None,
        )

        self.include_schema_prompt = QCheckBox("Include Schema")
        self.include_examples_prompt = QCheckBox("Include Examples")

        for checkbox in [self.include_schema_prompt, self.include_examples_prompt]:
            checkbox.stateChanged.connect(lambda __: self.display_prompt_handler())

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.addWidget(self.prompt_template_combo_widget)
        layout.addWidget(self.include_schema_prompt)
        layout.addWidget(self.include_examples_prompt)

        controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Create Prompt")
        copy_btn = QPushButton("Copy to Clipboard")
        refresh_btn.clicked.connect(self.display_prompt_handler)
        copy_btn.clicked.connect(
            lambda: self.copy_to_clipboard(self.prompt_output_area)
        )
        controls.addWidget(refresh_btn)
        controls.addWidget(copy_btn)
        controls.addStretch()

        layout.addLayout(controls)
        layout.addWidget(self.prompt_output_area)

        self.tabs.addTab(wrapper, "Prompt")

    def _init_examples_tab(self):
        self.examples_output_area = QTextEdit()
        self.examples_output_area.setReadOnly(True)

        self.example_count = QSpinBox()
        self.example_count.setMinimum(1)
        self.example_count.setMaximum(100)
        self.example_count.setValue(3)
        self.example_count.setSuffix(" examples")

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Count:"))
        controls.addWidget(self.example_count)

        refresh_btn = QPushButton("Refresh Examples")
        copy_btn = QPushButton("Copy to Clipboard")
        refresh_btn.clicked.connect(self.display_examples_handler)
        copy_btn.clicked.connect(
            lambda: self.copy_to_clipboard(self.examples_output_area)
        )
        controls.addWidget(refresh_btn)
        controls.addWidget(copy_btn)
        controls.addStretch()

        layout.addLayout(controls)
        layout.addWidget(self.examples_output_area)
        self.tabs.addTab(wrapper, "Examples")

    def copy_to_clipboard(self, text_edit: QTextEdit):
        QApplication.clipboard().setText(text_edit.toPlainText())

    def display_schema_handler(self):
        logger.debug("DebugDialog: Displaying schema.")
        note_model = self.note.note_type()
        if not note_model:
            self.schema_output_area.setPlainText(
                "Error: Note has no model (note type)."
            )
            logger.error("DebugDialog: Note has no model (note type).")
            return
        try:
            schema = generate_schema_class(note_model)
            self.schema_output_area.setPlainText(
                json.dumps(schema, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            self.schema_output_area.setPlainText(f"Error generating schema: {e}")
            logger.exception("DebugDialog: Error generating schema.")

    def display_pydantic_schema_handler(self):
        logger.debug("DebugDialog: Displaying pydantic schema.")
        note_model = self.note.note_type()
        if not note_model:
            self.schema_output_area.setPlainText(
                "Error: Note has no model (note type)."
            )
            logger.error("DebugDialog: Note has no model (note type).")
            return
        try:
            pydantic_schema = generate_pydantic_class(note_model)
            self.pydantic_schema_output_area.setPlainText(
                json.dumps(
                    pydantic_schema.model_json_schema(), indent=2, ensure_ascii=False
                )
            )
        except Exception as e:
            self.pydantic_schema_output_area.setPlainText(
                f"Error generating pydantic schema: {e}"
            )
            logger.exception("DebugDialog: Error generating pydantic schema.")

    # --- NEW METHOD: Display Note JSON ---
    def display_note_json_handler(self):
        logger.debug("DebugDialog: Displaying note JSON.")
        try:
            # Assuming note_to_json is imported at the top-level as requested
            json_str = note_to_json_string(self.note)
            self.note_json_output_area.setPlainText(json_str)
        except Exception as e:
            self.note_json_output_area.setPlainText(f"Error generating note JSON: {e}")
            logger.exception("DebugDialog: Error generating note JSON.")

    # --------------------------------------

    def generate_get_prompt_text_args(self, mode: str) -> dict:
        kwargs = {"dummy_keys": set()}
        temporary_config = deepcopy(self.effective_config)

        if not getattr(self, f"include_schema_{mode}").isChecked():
            kwargs["dummy_keys"].add("schema")

        if not getattr(self, f"include_examples_{mode}").isChecked():
            kwargs["dummy_keys"].add("examples")

        kwargs["config_"] = temporary_config
        return kwargs

    def display_create_prompt_handler(self):
        logger.debug("DebugDialog: Displaying create prompt.")

        try:
            prompt = get_prompt_text_by_mode(note=self.note, mode="create", **self.generate_get_prompt_text_args("create"))
            self.create_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.create_prompt_output_area.setPlainText(
                f"Error generating create prompt: {e}"
            )
            logger.exception("DebugDialog: Error generating create prompt.")

    def display_complete_prompt_handler(self):
        logger.debug("DebugDialog: Displaying complete prompt.")

        try:
            prompt = get_prompt_text_by_mode(note=self.note, mode="complete", **self.generate_get_prompt_text_args("complete"))
            self.complete_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.complete_prompt_output_area.setPlainText(
                f"Error generating complete prompt: {e}"
            )
            logger.exception("DebugDialog: Error generating complete prompt.")

    def display_prompt_handler(self):
        logger.debug("DebugDialog: Displaying prompt.")

        dummy_keys : set[str] = set()
        base_prompt = self.prompt_template_chooser_instance.selected_template_text()

        if base_prompt is None:
            self.prompt_output_area.setPlainText("Selected prompt template is empty")
            logger.warning("Selected prompt template is empty")
            return
            
        # logger.debug("DebugDialog: selected_template: %s", base_prompt)

        if not self.include_schema_prompt.isChecked():
            dummy_keys.add("schema")

        if not self.include_examples_prompt.isChecked():
            dummy_keys.add("examples")

        try:
            prompt = get_prompt_text_from_note(
                self.note,
                base_prompt=base_prompt,
                config_=self.effective_config,
                dummy_keys=dummy_keys,
            )
            self.prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.prompt_output_area.setPlainText(f"Error generating prompt: {e}")
            logger.exception("DebugDialog: Error generating prompt.")

    def display_examples_handler(self):
        logger.debug("DebugDialog: Displaying examples.")
        try:
            note_type = self.note.note_type()
            if not note_type:
                self.examples_output_area.setPlainText(
                    "Error: Note has no model (note type) to generate examples from."
                )
                logger.error("DebugDialog: Note has no model (note type) for examples.")
                return

            n = self.example_count.value()
            examples_str = example_notes(note_type, n=n)
            self.examples_output_area.setPlainText(examples_str)
        except Exception as e:
            self.examples_output_area.setPlainText(f"Error generating examples: {e}")
            logger.exception("DebugDialog: Error generating examples.")


from typing import Callable, Optional


class MyStudyDeck(StudyDeck):
    def accept(self):
        row = self.form.list.currentRow()
        if row < 0:
            self.name = None
            # showInfo(tr.decks_please_select_something())
            # return
        else:
            self.name = self.names[self.form.list.currentRow()]
        self.accept_with_callback()


class PromptTemplateChooser(QHBoxLayout):
    template_changed = pyqtSignal(str)

    def __init__(
        self,
        mw: QMainWindow,
        widget: QWidget,
        show_prefix_label: bool = True,
        starting_template: str | None | Literal[False] = False,
        # note_type_id: Optional[int] = None,
        # deck_id: Optional[int] = None,
        on_template_changed: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()

        self._widget = widget  # type: ignore
        self.mw = mw
        # self.note_type_id = note_type_id
        # self.deck_id = deck_id
        self.on_template_changed = on_template_changed
        self.study_deck = None

        self.prompt_templates = load_saved_prompts()
        self.selected_template: tuple | None | Literal[False] = None

        self._setup_ui(show_prefix_label=show_prefix_label)
        self._widget.setLayout(self)

        if starting_template is False:
            self.refresh_template_list()
        elif starting_template is None:
            self._update_button_label()
        else:
            self.set_prompt_by_key(starting_template)

    def _setup_ui(self, show_prefix_label: bool) -> None:
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

        if show_prefix_label:
            self.templateLabel = QLabel("Prompt Template")
            self.addWidget(self.templateLabel)

        self.button = QPushButton()
        self.button.setAutoDefault(False)
        self.button.setToolTip("Choose prompt template")
        self.button.setSizePolicy(QSizePolicy.Policy(7), QSizePolicy.Policy(0))
        self.button.clicked.connect(self._open_template_chooser_dialog)
        self.addWidget(self.button)

    def set_prompt_by_key(self, target_key):
        for key, entry in self.prompt_templates.items():
            if key == target_key:
                self.selected_template = key, entry
                break
        else:
            self.selected_template = False

        self._update_button_label()

    def get_key_by_name(self, target_name):
        for key, entry in self.prompt_templates.items():
            if entry["name"] == target_name:
                return key
        return None

    def _open_template_chooser_dialog(self):
        def template_names() -> list[str]:
            return sorted(t["name"] for t in self.prompt_templates.values())

        def callback(ret: StudyDeck) -> None:
            logger.debug(
                "PromptTemplateChooser: study deck returned:\n- row: %s\n- item: %s\n- index: %s",
                ret.form.list.currentRow(),
                ret.form.list.currentItem(),
                ret.form.list.currentIndex(),
            )

            selected_item = ret.form.list.currentItem()

            if selected_item is None:
                self.selected_template = None
            else:
                key = self.get_key_by_name(selected_item.text())

                self.set_prompt_by_key(key)

                logger.debug(
                    "New key and template: %s, %s",
                    self.selected_template_key(),
                    self.selected_template,
                )

            self._update_button_label()
            if self.on_template_changed:
                self.on_template_changed(self.selected_template)
            self.template_changed.emit(self.selected_template_key() or "")

        manage_button = QPushButton(tr.qt_misc_manage())

        def open_manage():
            dialog = SavedPromptManagerDialog(
                parent=self._widget,
                starting_index=self.study_deck.form.list.currentRow(),
                # note_type_id=self.note_type_id,
                # deck_id=self.deck_id,
            )

            dialog.exec()
            template_index = dialog.prompt_list.currentIndex()
            logger.debug(
                "Prompt manager finished with prompt index: %s", template_index
            )

            self.prompt_templates = load_saved_prompts()
            self.refresh_template_list()
            if self.study_deck:
                self.study_deck.nameFunc = template_names
                self.study_deck.origNames = template_names()
                self.study_deck.onReset()
                self.study_deck.form.list.setCurrentRow(template_index)
                # self.study_deck.redraw("", None)

        qconnect(manage_button.clicked, open_manage)

        unset_button = QPushButton("Unset")
        qconnect(
            unset_button.clicked, lambda: self.study_deck.form.list.setCurrentRow(-1)
        )

        self.study_deck = MyStudyDeck(
            mw=mw,
            names=template_names,
            accept=tr.actions_choose(),
            title="Choose Prompt Template",
            help=None,
            current=self.selected_template_name(),
            parent=self._widget,
            buttons=[manage_button, unset_button],
            cancel=True,
            geomKey="selectPromptTemplate",
            callback=callback,
        )

    def refresh_template_list(self):
        if self.prompt_templates:
            self.selected_template = next(iter(self.prompt_templates.items()))
        else:
            self.selected_template = None

        self._update_button_label()

    def _update_button_label(self):
        if self.selected_template is None:
            name = "(none)"
        elif self.selected_template is False:
            name = "(Error: unknown template)"
        else:
            name = self.selected_template_name() or "(none)"
        self.button.setText(name.replace("&", "&&"))

    def selected_template_text(self) -> Optional[str]:
        if self.selected_template:
            return self.selected_template[1]["template"]

    def selected_template_name(self) -> Optional[str]:
        if self.selected_template:
            return self.selected_template[1]["name"]

    def selected_template_key(self) -> Optional[str]:
        if self.selected_template:
            return self.selected_template[0]

    def selected_template_index(self) -> int:
        if self.selected_template:
            return list(self.prompt_templates).index(self.selected_template[0])
        return -1

    # def set_note_type_id(self, note_type_id: int):
    #     self.note_type_id = note_type_id
    #     self.refresh_template_list()
    #
    # def set_deck_id(self, deck_id: int):
    #     self.deck_id = deck_id
    #     self.refresh_template_list()

    def show(self) -> None:
        self._widget.show()  # type: ignore

    def hide(self) -> None:
        self._widget.hide()  # type: ignore


# --- PromptFromTextDialog ---
class PromptFromTextDialog(QDialog, ChoosersMixin):

    def __init__(
        self,
        parent: QWidget | None = None,
    ):
        super().__init__(parent or mw)
        self.config = get_config()
        self.setWindowTitle("Create Cards from Prompt Text (Zikaria)")
        self.resize(1000, 800)
        self.layout = QVBoxLayout(self)
        # ... (rest of implementation using logger, self.config, mw)
        # Needs access to ZikariaPrompts instance or its methods for AI call.
        # For now, we'll instantiate ZikariaPrompts locally in send_prompt_to_ai_handler.

        self.note_type_combo_widget = QWidget(self)
        self.deck_combo_widget = QWidget(self)
        self._setup_choosers()  # Renamed
        self.layout.addWidget(self.note_type_combo_widget)
        self.layout.addWidget(self.deck_combo_widget)

        self.prompt_template_combo_widget = QWidget(self)
        self.prompt_template_chooser_instance = PromptTemplateChooser(
            mw=mw,
            widget=self.prompt_template_combo_widget,
            # starting_prompt = None,
            # note_type_id = self.notetype_chooser_instance.selected_notetype_id,
            # deck_id = self.deck_chooser_instance.selected_deck_id,
            # on_prompt_changed = None,
        )
        self.layout.addWidget(self.prompt_template_combo_widget)

        self.prompt_input_area = QTextEdit()
        self.prompt_input_area.setPlaceholderText(
            "Enter a list of words or a sentence..."
        )
        self.layout.addWidget(QLabel("Prompt Text:"))
        self.layout.addWidget(self.prompt_input_area)

        # self.select_prompt_button = QPushButton("Select Saved Prompt")
        # self.select_prompt_button.clicked.connect(self.select_saved_prompt_handler)
        # self.layout.addWidget(self.select_prompt_button)

        self.generate_prompt_button = QPushButton("Generate Full Prompt")
        self.generate_prompt_button.clicked.connect(self.generate_full_prompt_handler)
        self.layout.addWidget(self.generate_prompt_button)

        self.prompt_edit_area = QTextEdit()  # For the full generated prompt
        self.prompt_edit_area.setPlaceholderText("Prompt preview will appear here...")
        self.layout.addWidget(QLabel("Edit Prompt:"))
        self.layout.addWidget(self.prompt_edit_area)

        self.submit_ai_button = QPushButton("Send to AI")
        self.submit_ai_button.clicked.connect(self.send_prompt_to_ai_handler)
        self.layout.addWidget(self.submit_ai_button)

        self.global_tag_edit_widget = TagEdit(self)
        self.global_tag_edit_widget.setCol(mw.col)
        self.layout.addWidget(self.global_tag_edit_widget)
        if note_tag_val := self.config.get("note_tag"):  # Use self.config
            self.global_tag_edit_widget.setText(note_tag_val)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

    def generate_full_prompt_handler(self):
        logger.debug("Generating full prompt in PromptFromTextDialog.")
        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        note_type = mw.col.models.get(note_type_id)

        current_deck_id = self.deck_chooser_instance.selected_deck_id
        effective_config = get_effective_config(note_type_id, current_deck_id)
        temporary_config = deepcopy(effective_config)

        base_prompt = (
            self.prompt_template_chooser_instance.selected_template_text()
        )
        if base_prompt is None:
            self.prompt_output_area.setPlainText("Selected prompt template is empty")
            logger.warning("Selected prompt template is empty")
            return

        try:
            prompt_text = get_prompt_text(
                note_type = note_type,
                prompt = self.prompt_input_area.toPlainText(),
                base_prompt=base_prompt,
                config_ =temporary_config,
            )

            self.prompt_edit_area.setPlainText(prompt_text)
            self.status_label.setText("Prompt generated.")
        except Exception as e:
            self.status_label.setText(f"Error generating prompt: {e}")
            logger.exception("Failed to generate prompt in PromptFromTextDialog")

    def send_prompt_to_ai_handler(self):
        from .core import ZikariaPrompts  # Local import

        logger.debug("Sending prompt to AI from PromptFromTextDialog.")
        full_prompt_text = self.prompt_edit_area.toPlainText()

        if not full_prompt_text.strip():
            self.status_label.setText("Prompt is empty.")
            return

        self.status_label.setText("Sending prompt to AI...")
        QApplication.processEvents()

        core_processor = ZikariaPrompts(manual_execution=True)

        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        note_type = mw.col.models.get(note_type_id)

        deck_id = self.deck_chooser_instance.selected_deck_id

        global_tags = mw.col.tags.split(self.global_tag_edit_widget.text())

        effective_config = get_effective_config(note_type_id, deck_id)

        # 1. Send prompt
        response = core_processor._send_prompt_to_ai(
            full_prompt_text, custom_config=effective_config, note_type=note_type
        )
        # 2. Parse and validate response
        notes_data : NotesDataList | None = core_processor._parse_response_into_notes_data(
            response, note_type=note_type
        )

        if not notes_data:
            logger.debug("Response parsing returned no notes data.")
            self.status_label.setText("Response parsing returned no notes data.")
            return

        dummy_note = Note(mw.col, note_type)

        added_notes = []

        def per_note_fn(original_note, notes_data_list):
            added_notes.extend(notes_data_list)
            core_processor._add_notes(
                note_type=note_type,
                notes_data_list=notes_data_list,
                default_tags=global_tags,
                deck_id=deck_id,
            )

        core_processor._finalize_notes(
            notes_map={dummy_note: notes_data}, per_note_fn=per_note_fn, finished_msg="create notes", global_tags=global_tags
        )

        if added_notes:
            self.status_label.setText(f"{len(notes_data)} notes added successfully.")
            tooltip(f"{len(notes_data)} notes added successfully.")
            return 

        self.status_label.setText(f"No notes were added.")
        tooltip(f"No notes were added.")

    def closeEvent(self, event):
        if (
            hasattr(self, "notetype_chooser_instance")
            and self.notetype_chooser_instance
        ):
            self.notetype_chooser_instance.cleanup()
        if hasattr(self, "deck_chooser_instance") and self.deck_chooser_instance:
            self.deck_chooser_instance.cleanup()
        super().closeEvent(event)


# --- SavedPromptManagerDialog ---
class SavedPromptManagerDialog(QDialog, ChoosersMixin):
    def __init__(
        self,
        parent: QWidget | None = None,
        starting_index: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Manage Saved Prompts")
        self.resize(900, 500)

        self.prompt_templates = load_saved_prompts()
        self.selected_prompt = None
        # Flag to track unsaved edits in the text area
        self.is_edited = False

        # Layouts
        main_layout = QHBoxLayout(self)
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()
        main_layout.addLayout(left_col, 4)
        main_layout.addLayout(right_col, 1)

        # Prompt List
        self.prompt_list = QComboBox()
        self.prompt_list.currentIndexChanged.connect(self.update_preview)
        right_col.addWidget(self.prompt_list)

        # Buttons
        self.add_button = QPushButton("Add")
        self.rename_button = QPushButton("Rename")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.ok_button = QPushButton("Close")
        for b in [
            self.add_button,
            self.rename_button,
            self.edit_button,
            self.delete_button,
        ]:
            right_col.addWidget(b)
        right_col.addStretch()

        # OK / Cancel
        right_col.addWidget(self.ok_button)

        self.add_button.clicked.connect(self.add_prompt)
        self.rename_button.clicked.connect(self.rename_prompt)
        self.edit_button.clicked.connect(self.enter_edit_mode)
        self.delete_button.clicked.connect(self.delete_prompt)
        self.ok_button.clicked.connect(self.accept)

        # Prompt Editor
        self.preview_area = QTextEdit()
        self.preview_area.setReadOnly(True)
        # Connect text change to update the is_edited state and button
        self.preview_area.textChanged.connect(self._on_editor_text_changed)
        left_col.addWidget(self.preview_area)

        self.save_edit_button = QPushButton("Save")
        self.save_edit_button.setVisible(False)
        self.save_edit_button.setEnabled(False)  # Start disabled
        self.save_edit_button.clicked.connect(self.save_edited_prompt)
        left_col.addWidget(self.save_edit_button)

        self.refresh_prompt_list()

        if starting_index is not None:
            self.prompt_list.setCurrentIndex(starting_index)
            self.update_preview()

    def _on_editor_text_changed(self):
        """Updates the is_edited flag and save button's enabled state."""
        # Only check if the editor is NOT read-only (i.e., in edit mode)
        if not self.preview_area.isReadOnly():
            current_text = self.preview_area.toPlainText().strip()
            # Retrieve the original text from the stored data
            original_text = (
                self.selected_prompt[1]["template"].strip()
                if self.selected_prompt
                else ""
            )

            # Update the is_edited flag
            self.is_edited = current_text != original_text

            # Enable the Save button only if the text has been edited
            self.save_edit_button.setEnabled(self.is_edited)

    def refresh_prompt_list(self):
        self.prompt_list.clear()

        for key, entry in self.prompt_templates.items():
            self.prompt_list.addItem(entry["name"], userData=(key, entry))

        self.update_preview()

    def update_preview(self):
        self.selected_prompt = self.prompt_list.currentData()

        if self.selected_prompt:
            template = self.selected_prompt[1]["template"]
        else:
            template = ""

        self.preview_area.setPlainText(template)

        # Reset editor to read-only state
        self.preview_area.setReadOnly(True)
        self.save_edit_button.setVisible(False)
        self.save_edit_button.setEnabled(False)
        self.is_edited = False  # No edits upon viewing a new/existing template

    def set_current_prompt_by_key(self, target_key):
        for index in range(self.prompt_list.count()):
            user_data = self.prompt_list.itemData(index)
            if user_data and user_data[0] == target_key:
                self.prompt_list.setCurrentIndex(index)
                return

    def enter_edit_mode(self):
        if not self.selected_prompt:
            return

        self.preview_area.setReadOnly(False)
        self.save_edit_button.setVisible(True)
        # Check if the text is already edited (e.g. if entering edit mode for a new prompt)
        self._on_editor_text_changed()

    def save_edited_prompt(self):
        new_text = self.preview_area.toPlainText().strip()
        if not new_text:
            # showInfo is likely an Anki utility function you'd use here
            return

        # Update the selected prompt's template
        self.selected_prompt[1]["template"] = new_text

        # Save to disk
        save_saved_prompts(self.prompt_templates)

        # Reset editor state after saving
        self.preview_area.setReadOnly(True)
        self.save_edit_button.setVisible(False)
        self.save_edit_button.setEnabled(False)
        self.is_edited = False

    def add_prompt(self):
        name, ok = getText("Enter a name for the new prompt:", parent=self)
        if not ok or not name.strip():
            return

        new_entry = {"name": name, "template": ""}
        key = str(uuid.uuid4())
        self.prompt_templates[key] = new_entry

        save_saved_prompts(self.prompt_templates)
        self.refresh_prompt_list()
        self.set_current_prompt_by_key(key)

        self.enter_edit_mode()

    def rename_prompt(self):
        if not self.selected_prompt:
            return

        # self.selected_prompt[2] is likely the display text, use the name from the dict
        current_name = self.selected_prompt[1]["name"]
        new_name, ok = getText("Rename prompt:", default=current_name, parent=self)
        if not ok or not new_name.strip():
            return

        self.selected_prompt[1]["name"] = new_name.strip()

        save_saved_prompts(self.prompt_templates)
        self.refresh_prompt_list()

    def delete_prompt(self):
        if not self.selected_prompt:
            return

        # Confirmation dialog is highly recommended here, but not requested

        del self.prompt_templates[self.selected_prompt[0]]

        save_saved_prompts(self.prompt_templates)
        self.refresh_prompt_list()

    def get_selected_prompt_template(self) -> Optional[str]:
        if self.selected_prompt:
            return self.selected_prompt[1]["template"]
        return

    def closeEvent(self, event: QCloseEvent):
        """Handles the dialog closing event, checking for unsaved changes."""
        if not self.is_edited:
            # No unsaved changes, close normally
            event.accept()
            return

        # Unsaved changes, prompt user
        msg = QMessageBox(self)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText("The current prompt template has unsaved changes.")
        msg.setInformativeText(
            "Do you want to save the changes, discard them, or cancel closing?"
        )

        # Define buttons
        save_btn = msg.addButton("Save", QMessageBox.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.RejectRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.DestructiveRole)

        msg.exec()

        if msg.clickedButton() == save_btn:
            # User chose Save: save and then accept closing
            self.save_edited_prompt()
            event.accept()
        elif msg.clickedButton() == discard_btn:
            # User chose Discard: accept closing without saving
            event.accept()
        elif msg.clickedButton() == cancel_btn:
            # User chose Cancel: ignore the close event
            event.ignore()
        else:
            # Fallback for unexpected closure
            event.ignore()


# --- open_prompt_text_dialog_action ---
def open_saved_prompts_manager():
    dialog = SavedPromptManagerDialog()
    dialog.exec()


# --- open_prompt_text_dialog_action ---
def open_prompt_text_dialog_action():
    dialog = PromptFromTextDialog()
    dialog.exec()


# --- ConfigDialog & CustomConfigDialog ---
class ConfigDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        config_data_override: dict | None = None,
        addon_name_param: str = "",
    ):
        super().__init__(parent or mw)
        self.resize(1200, 1000)

        self.addon_name = addon_name_param
        if (
            not self.addon_name
        ):  # Fallback if not provided, though __init__.py should provide it
            self.addon_name = get_addon_name()
            logger.warning(
                "ConfigDialog initialized without addon_name_param, using get_addon_name()."
            )

        if config_data_override is None:
            current_addon_config_obj = get_config()  # Get the live config object
            self.config_to_edit = deepcopy(current_addon_config_obj)  # Edit a deep copy
        else:
            self.config_to_edit = deepcopy(config_data_override)  # Edit a deep copy

        self.setWindowTitle(f"Zikaria Addon Configuration ({self.addon_name})")
        # ... (rest of ConfigDialog UI setup as before, using mw, logger, self.addon_name)
        self.main_layout = QVBoxLayout()

        self.content_layout = QHBoxLayout()
        self.content_widget = QWidget()
        self.content_widget.setLayout(self.content_layout)
        self.main_layout.addWidget(self.content_widget)

        self.setStyleSheet("QWidget { font-size: 16px; }")  # Example style

        self.form_scroll_area = QScrollArea()
        self.form_layout = QFormLayout()
        self.form_widget = QWidget()

        self.form_widget.setLayout(self.form_layout)

        self.form_scroll_area.setWidgetResizable(True)

        self.form_scroll_area.setWidget(self.form_widget)

        self.content_layout.addWidget(self.form_scroll_area)

        self._setup_docs_panel()
        self.config_widgets_map = {}  # Renamed
        self.extra_config_data_map = {}  # Renamed

        self._init_form_elements()  # Renamed
        self.setLayout(self.main_layout)

    def _setup_docs_panel(self):  # Renamed
        self.docs_panel_widget = QTextEdit()
        self.docs_panel_widget.setReadOnly(True)
        if mw.addonManager:
            docs_path = os.path.join(
                mw.addonManager.addonsFolder(),
                self.addon_name,
                "config.md",
            )
            if os.path.exists(docs_path):
                with open(docs_path, "r", encoding="utf-8") as f:
                    self.docs_panel_widget.setMarkdown(f.read())
            else:
                self.docs_panel_widget.setMarkdown(
                    f"# Configuration Documentation\n`config.md` not found at `{docs_path}`."
                )
        else:
            self.docs_panel_widget.setMarkdown(
                "# Configuration Documentation\nAddon manager not available."
            )
        self.content_layout.addWidget(self.docs_panel_widget)

    def _init_form_elements(self):  # Renamed
        default_config_keys = {}

        if mw.addonManager:
            default_config_keys = (
                mw.addonManager.addonConfigDefaults(self.addon_name) or {}
            )

        handled_keys = set()
        for key, default_value in default_config_keys.items():
            current_value = self.config_to_edit.get(key, default_value)
            if key == "custom_config":
                handled_keys.add(key)
                continue

            widget_data = self._create_config_widget_ui(key, current_value)  # Renamed
            if widget_data:
                self.form_layout.addRow(widget_data["label"], widget_data["widget"])
                self.config_widgets_map[key] = widget_data  # Use renamed map
                handled_keys.add(key)

        self._add_remaining_config_ui_elements(handled_keys)  # Renamed
        self._add_custom_config_ui_section()  # Renamed
        self._add_action_buttons()  # Renamed

    def _create_config_widget_ui(
        self, name: str, value: Any
    ) -> Optional[Dict[str, Any]]:  # Renamed
        """Create and add a widget for the given config key."""
        change_event = None
        if name in {"create_prompt_template", "complete_prompt_template"}:
            widget = QWidget()
            prompt_template_chooser_instance = PromptTemplateChooser(
                mw=mw,
                widget=widget,
                show_prefix_label=False,
                starting_template=value,
                # starting_prompt = None,
                # note_type_id = self.note.note_type().id,
                # deck_id = self.deck_chooser_instance.selected_deck_id,
                # on_prompt_changed = None,
            )
            label = f"{name.replace('_', ' ').title()}:"

            def get_fn():
                return prompt_template_chooser_instance.selected_template_key()

            change_event = prompt_template_chooser_instance.template_changed

        elif isinstance(value, bool):
            widget = QCheckBox()
            widget.setChecked(value)
            label = f"{name.replace('_', ' ').title()}:"
            get_fn = widget.isChecked
            change_event = widget.stateChanged
        elif isinstance(value, int):
            widget = QSpinBox()
            widget.setRange(0, 10000)
            widget.setValue(value)
            label = f"{name.replace('_', ' ').title()}:"
            get_fn = widget.value
            change_event = widget.textChanged
        elif isinstance(value, float):
            widget = QDoubleSpinBox()
            widget.setRange(0.0, 100.0)
            widget.setValue(value)
            label = f"{name.replace('_', ' ').title()}:"
            get_fn = widget.value
            change_event = widget.textChanged
        elif isinstance(value, list):
            widget = QLineEdit(", ".join(map(str, value)))
            change_event = widget.textChanged
            label = f"{name.replace('_', ' ').title()} (comma-separated):"
            get_fn = lambda: [item.strip() for item in widget.text().split(",")]
        elif isinstance(value, dict):
            widget = QTextEdit(
                json.dumps(
                    value,
                    indent=4,
                    ensure_ascii=False,
                )
            )
            change_event = widget.textChanged
            label = f"{name.replace('_', ' ').title()} (JSON):"

            def get_fn():
                success, data = self._load_json_dict(widget.toPlainText())
                if success:
                    return data

        elif isinstance(value, str):
            if "\n" in value or len(value) > 80:
                widget = QTextEdit(value)
                get_fn = widget.toPlainText
            else:
                widget = QLineEdit(value)
                get_fn = widget.text
            change_event = widget.textChanged
            label = f"{name.replace('_', ' ').title()}:"
        else:
            logger.warning(
                f"ConfigDialog: Widget creation not fully implemented in this snippet for type {type(value)} (key: {name})."
            )
            return

        return {
            "widget": widget,
            "label": label,
            "row": widget,
            "get_fn": get_fn,
            "change_event": change_event,
        }

    def _add_remaining_config_ui_elements(self, handled_keys_set: set):  # Renamed
        logger.debug(
            "ConfigDialog: Adding UI for remaining configuration values (those not explicitly handled)."
        )

        self.extra_config_edit = QTextEdit()
        self.form_layout.addRow(
            QLabel("Other Configuration Values (JSON):"), self.extra_config_edit
        )
        remaining_config = {
            k: v for k, v in self.config_to_edit.items() if k not in handled_keys_set
        }
        if remaining_config:
            self.extra_config_data_map = remaining_config
            self.extra_config_edit.setPlainText(
                json.dumps(
                    remaining_config,
                    indent=4,
                    ensure_ascii=False,
                )
            )

    def _add_custom_config_ui_section(self):  # Renamed
        """Add the UI for managing custom configurations."""
        self.custom_configs_label = QLabel("Custom Configurations:")
        self.form_layout.addRow(self.custom_configs_label)

        self.custom_configs_list = QScrollArea()
        self.custom_configs_list.setWidgetResizable(True)

        self.custom_configs_content = QWidget()

        self.custom_configs_content_layout = QVBoxLayout()
        # self.custom_configs_content_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_configs_content.setLayout(self.custom_configs_content_layout)

        self.custom_configs_content_grid = QWidget()

        self.custom_configs_content_grid_layout = QGridLayout()
        self.custom_configs_content_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_configs_content_grid.setLayout(
            self.custom_configs_content_grid_layout
        )

        self.custom_configs_content_layout.addWidget(self.custom_configs_content_grid)
        self.custom_configs_content_layout.addStretch()

        self.custom_configs_list.setWidget(self.custom_configs_content)

        self.form_layout.addRow(self.custom_configs_list)
        self._update_custom_configs_display()  # Changed from update_custom_configs_ui

        add_custom_config_button = QPushButton("Add Custom Config")
        add_custom_config_button.clicked.connect(
            lambda: self._add_or_edit_custom_config_entry()  # Corrected method name
        )
        self.form_layout.addRow(add_custom_config_button)

    def _update_custom_configs_display(self):  # Renamed
        """Refresh the display of custom configurations."""
        logger.debug("ConfigDialog: Updating custom configs display.")

        # Clear existing widgets in the grid
        while self.custom_configs_content_grid_layout.count():
            child = self.custom_configs_content_grid_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()

        header_note_label = QLabel("Note Type")
        header_note_label.setStyleSheet("font-weight: bold;")
        header_deck_label = QLabel("Deck")
        header_deck_label.setStyleSheet("font-weight: bold;")
        header_actions_label = QLabel("Actions")
        header_actions_label.setStyleSheet("font-weight: bold;")

        self.custom_configs_content_grid_layout.addWidget(header_note_label, 0, 0)
        self.custom_configs_content_grid_layout.addWidget(header_deck_label, 0, 1)
        self.custom_configs_content_grid_layout.addWidget(
            header_actions_label, 0, 2, 1, 2
        )  # Span 2 columns for buttons

        custom_configs_list = self.config_to_edit.get("custom_config", [])
        logger.debug(f"Found {len(custom_configs_list)} custom configs to display.")

        for idx, custom_config_entry in enumerate(custom_configs_list):
            note_type_id, deck_id, settings = custom_config_entry  # settings is a dict

            note_type_name = "Any Note Type"
            if note_type_id:
                nt = mw.col.models.get(NotetypeId(note_type_id))
                if nt:
                    note_type_name = nt["name"]
                else:
                    note_type_name = f"Missing NT ID: {note_type_id}"

            deck_name = "Any Deck"
            if deck_id:
                dk = mw.col.decks.get(DeckId(deck_id))
                if dk:
                    deck_name = dk["name"]
                else:
                    deck_name = f"Missing Deck ID: {deck_id}"

            note_type_display_label = QLabel(note_type_name)
            deck_display_label = QLabel(deck_name)

            edit_button = QPushButton("Edit")
            # Corrected lambda to pass index 'idx'
            edit_button.clicked.connect(
                lambda checked=False, i=idx: self._add_or_edit_custom_config_entry(i)
            )

            delete_button = QPushButton("Delete")
            # Corrected lambda to pass index 'idx'
            delete_button.clicked.connect(
                lambda checked=False, i=idx: self._delete_custom_config_entry(i)
            )

            # Add widgets to grid
            self.custom_configs_content_grid_layout.addWidget(
                note_type_display_label, idx + 1, 0
            )
            self.custom_configs_content_grid_layout.addWidget(
                deck_display_label, idx + 1, 1
            )
            self.custom_configs_content_grid_layout.addWidget(edit_button, idx + 1, 2)
            self.custom_configs_content_grid_layout.addWidget(delete_button, idx + 1, 3)

    def _add_or_edit_custom_config_entry(self, index: Optional[int] = None):  # Renamed
        logger.debug(f"ConfigDialog: Add/Edit custom config entry at index {index}.")

        custom_configs_list = self.config_to_edit.get("custom_config", [])
        note_type_id_param: Optional[NotetypeId] = None
        deck_id_param: Optional[DeckId] = None
        custom_settings_data: Dict[str, Any] = {}

        if index is not None and index < len(custom_configs_list):
            entry = custom_configs_list[index]
            note_type_id_param = NotetypeId(entry[0]) if entry[0] else None
            deck_id_param = DeckId(entry[1]) if entry[1] else None
            custom_settings_data = entry[2]  # This is the dictionary of settings
            logger.debug(
                f"Editing entry: NTID={note_type_id_param}, DID={deck_id_param}, Settings={custom_settings_data}"
            )
        else:
            logger.debug("Creating new custom config entry.")

        dialog = CustomConfigDialog(
            parent=self,
            note_type_id_param=note_type_id_param,
            deck_id_param=deck_id_param,
            custom_settings_data=custom_settings_data,  # Pass the dict here
            addon_name_param=self.addon_name,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            logger.debug("CustomConfigDialog accepted.")
            new_note_type_id = dialog.note_type_id_result
            new_deck_id = dialog.deck_id_result
            # dialog.config_to_edit contains the settings edited in CustomConfigDialog
            new_settings = dialog.config_to_edit

            updated_entry = [
                new_note_type_id if new_note_type_id is not None else None,
                new_deck_id if new_deck_id is not None else None,
                new_settings,
            ]
            logger.debug(f"Updated entry data: {updated_entry}")

            current_custom_configs = self.config_to_edit.setdefault("custom_config", [])
            if index is None:
                current_custom_configs.append(updated_entry)
                logger.debug("Appended new custom config.")
            elif index < len(current_custom_configs):
                current_custom_configs[index] = updated_entry
                logger.debug(f"Updated custom config at index {index}.")
            else:
                logger.error(
                    f"Error updating custom config: index {index} out of bounds for list of len {len(current_custom_configs)}"
                )

            self._update_custom_configs_display()
        else:
            logger.debug("CustomConfigDialog cancelled or closed.")

    def _delete_custom_config_entry(self, index: int):  # Renamed
        """Delete a custom configuration."""
        logger.debug(
            f"ConfigDialog: Attempting to delete custom config at index {index}."
        )
        custom_configs_list = self.config_to_edit.get("custom_config", [])
        if 0 <= index < len(custom_configs_list):
            custom_configs_list.pop(index)
            logger.info(f"Deleted custom config entry at index {index}.")
            self._update_custom_configs_display()
        else:
            logger.warning(
                f"Could not delete custom config at index {index}: index out of bounds."
            )

    def _add_action_buttons(self):  # Renamed
        button_widget = QWidget()
        button_layout = QHBoxLayout()
        button_widget.setLayout(button_layout)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_configuration_handler)  # Renamed
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        self.main_layout.addWidget(button_widget)

    def save_configuration_handler(self):  # Renamed
        logger.info(f"Saving configuration for addon '{self.addon_name}'.")
        updated_data_from_widgets = {}
        for key, data in self.config_widgets_map.items():
            if data["get_fn"]:
                updated_data_from_widgets[key] = data["get_fn"]()

        # Update the copy we are editing
        self.config_to_edit.update(updated_data_from_widgets)
        # ... (Handle custom_config and extra_config from their UI elements) ...

        if mw.addonManager:
            mw.addonManager.writeConfig(self.addon_name, self.config_to_edit)

        # Update the live global config object used by the addon
        live_config = get_config()  # Get the actual global config object
        live_config.clear()
        live_config.update(self.config_to_edit)  # Update its content

        # Update logger level if debug status changed
        import logging

        new_debug_level = (
            logging.DEBUG if live_config.get("debug", False) else logging.INFO
        )
        if logger.level != new_debug_level:
            logger.setLevel(new_debug_level)
            logger.info(
                f"Log level updated to {logging.getLevelName(new_debug_level)}."
            )

        logger.info(f"Configuration for '{self.addon_name}' saved and applied.")
        self.accept()


# --- CustomConfigDialog ---
class CustomConfigDialog(ConfigDialog, ChoosersMixin):  # Inherits from ConfigDialog
    def __init__(
        self,
        parent: ConfigDialog,
        note_type_id_param: NotetypeId | None = None,
        deck_id_param: DeckId | None = None,
        custom_settings_data: dict[str, Any] = None,
        addon_name_param: str = "",
    ):
        self.note_type_id_param = note_type_id_param
        self.deck_id_param = deck_id_param

        super().__init__(
            parent=parent,
            config_data_override=custom_settings_data,
            addon_name_param=addon_name_param,
        )

        self.setWindowTitle(f"Edit Custom Configuration for {self.addon_name}")

        self.note_type_id_result: Optional[NotetypeId] = self.note_type_id_param
        self.deck_id_result: Optional[DeckId] = self.deck_id_param

    def _setup_docs_panel(self):
        """Remove the documentation panel for the custom configuration dialog."""
        pass

    def _init_form_elements(self):
        logger.debug(
            f"CustomConfigDialog: Initializing form elements. Editing: {self.config_to_edit}"
        )
        self.config_widgets_map.clear()

        self.note_type_combo_widget = QWidget(self)
        self.form_layout.addRow(QLabel("Note Type:"), self.note_type_combo_widget)

        self.deck_combo_widget = QWidget(self)
        self.form_layout.addRow(QLabel("Deck:"), self.deck_combo_widget)

        self._setup_choosers(
            note_type_id=self.note_type_id_param,
            deck_id=self.deck_id_param,
            show_prefix_label=False,
        )

        overridable_keys = [
            "create_prompt",
            "complete_prompt",
            "complete_prompt_template",
            "create_prompt_template",
            "confirm_before_adding_notes",
            "model_temperature",
            "model_name",
            "max_output_tokens",
        ]

        global_defaults = mw.addonManager.addonConfigDefaults(self.addon_name) or {}

        for key in overridable_keys:
            current_value = self.config_to_edit.get(key, global_defaults.get(key))

            widget_data = self._create_config_widget_ui(key, current_value)
            if widget_data:
                row_layout = QHBoxLayout()
                is_active_checkbox = QCheckBox()
                is_active_checkbox.setChecked(key in self.config_to_edit)

                row_layout.addWidget(is_active_checkbox)
                row_layout.addWidget(widget_data["widget"])

                change_event = widget_data.get("change_event")
                if change_event:
                    change_event.connect(
                        lambda checked=False, k=key, g=widget_data[
                            "get_fn"
                        ], c=is_active_checkbox: self._sync_checkbox_with_widget(
                            k, g, c
                        )
                    )

                widget_data["row"] = row_layout
                widget_data["is_active_checkbox"] = is_active_checkbox
                self.form_layout.addRow(widget_data["label"], row_layout)
                self.config_widgets_map[key] = widget_data

        self._add_action_buttons()

    def save_configuration_handler(self):
        logger.info(
            f"CustomConfigDialog: Saving custom configuration for {self.addon_name}."
        )

        selected_note_type_id = self.notetype_chooser_instance.selected_notetype_id
        selected_deck_id = self.deck_chooser_instance.selected_deck_id

        if selected_note_type_id is None and selected_deck_id is None:
            showInfo("Please select at least a Note Type, a Deck, or both.")
            return

        if self._note_type_and_deck_combination_exists(
            selected_note_type_id, selected_deck_id
        ):
            showInfo("This Note Type and Deck combination already exists.")
            return

        updated_data_from_widgets = {}
        for key, data in self.config_widgets_map.items():
            if (
                data.get("is_active_checkbox")
                and not data["is_active_checkbox"].isChecked()
            ):
                continue

            if data.get("get_fn"):
                try:
                    updated_data_from_widgets[key] = data["get_fn"]()
                except Exception as e:
                    logger.error(
                        f"Error getting value for key {key}: {e}", exc_info=True
                    )

        self.config_to_edit.clear()
        self.config_to_edit.update(updated_data_from_widgets)

        self.note_type_id_result = selected_note_type_id
        self.deck_id_result = selected_deck_id

        logger.debug(
            f"CustomConfigDialog: Updated config_to_edit: {self.config_to_edit}"
        )
        self.accept()

    def _sync_checkbox_with_widget(self, key, get_fn, checkbox):
        value = get_fn()
        if isinstance(value, str):
            checkbox.setChecked(bool(value.strip()))
        else:
            checkbox.setChecked(bool(value))

    def _note_type_and_deck_combination_exists(self, note_type_id, deck_id):
        current_custom_configs = self.parent().config_to_edit.get("custom_config", [])

        for entry in current_custom_configs:
            existing_note_type_id, existing_deck_id, _ = entry
            if existing_note_type_id == note_type_id and existing_deck_id == deck_id:
                if (
                    note_type_id == self.note_type_id_param
                    and deck_id == self.deck_id_param
                ):
                    continue  # Editing current entry
                return True
        return False

    def accept(self) -> None:
        logger.debug("CustomConfigDialog: Accepted.")
        self._cleanup_choosers()
        super().accept()  # Call QDialog.accept()

    def reject(self) -> None:
        logger.debug("CustomConfigDialog: Rejected.")
        self._cleanup_choosers()
        super().reject()  # Call QDialog.reject()

    def closeEvent(self, event):  # Ensure cleanup on any close
        logger.debug("CustomConfigDialog: closeEvent triggered.")
        self._cleanup_choosers()
        super().closeEvent(event)


# --- show_config_dialog_action ---
def show_config_dialog_action(addon_name_str: str = None):
    logger.debug(f"Showing config dialog for addon: {addon_name_str}")
    dialog = ConfigDialog()
    dialog.exec()


# --- Action functions for menu items (browser related) ---
def add_debug_menu_to_browser_action(browser_instance: Browser):
    config = get_config()
    if config.get("debug", False):
        action = QAction("Zikaria Debug Note", browser_instance)
        action.triggered.connect(
            lambda: _debug_selected_note_browser_action(browser_instance)
        )

        # Ensure menu exists before adding
        if not hasattr(browser_instance.form, "menuZikaria"):
            browser_instance.form.menuZikaria = browser_instance.form.menuEdit.addMenu(
                "Zikaria"
            )
        browser_instance.form.menuZikaria.addAction(action)
        logger.debug("Added Zikaria debug menu to browser.")


def _debug_selected_note_browser_action(
    browser_instance: Browser, selected_nids=None
):  # Helper
    if selected_nids is None:
        selected_nids = browser_instance.selected_notes()
        if not are_nids_one_notetype(selected_nids, browser_instance):
            showInfo(
                "Please select notes of exactly one note type for Zikaria debugging.",
                parent=browser_instance,
            )
            return

    note_object = browser_instance.mw.col.get_note(selected_nids[0])
    if note_object:
        logger.debug(f"Opening DebugDialog for note ID: {note_object.id}")
        dialog = DebugDialog(note_object, parent=browser_instance)
        dialog.exec()


def are_nids_one_notetype(nids, browser_instance: Browser):
    col = browser_instance.mw.col
    notes = [col.get_note(nid) for nid in nids]
    note_types = {note.mid for note in notes}

    return len(note_types) == 1


def on_browser_context_menu_action(
    browser_instance: Browser, menu: QWidget
):  # menu is QMenu
    config = get_config()
    if not config.get("debug", False):
        return

    selected_nids = browser_instance.selected_notes()

    if not selected_nids:
        logger.debug("No notes selected, not adding Zikaria debug context menu.")
        return

    if are_nids_one_notetype(
        selected_nids, browser_instance
    ):  # Only for single selection for simplicity
        note_object = browser_instance.mw.col.get_note(selected_nids[0])
        if note_object:
            action = QAction("Zikaria Debug Note...", menu)
            action.triggered.connect(
                lambda: _debug_selected_note_browser_action(browser_instance)
            )
            menu.addAction(action)
            logger.debug("Added Zikaria debug context menu action.")
    else:  # Multiple notes selected
        logger.debug(
            "Multiple notes selected, Zikaria debug context menu not added for this case."
        )
