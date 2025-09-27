import json
import os
from typing import Optional, List, Dict, Any, Literal
from collections import namedtuple  # For NoteData if it's used by dialogs directly
import uuid

from aqt import QMainWindow, gui_hooks, mw, colors
from aqt.utils import showInfo, getText, tr, tooltip, shortcut
from aqt.qt import (
    QAction,
    qconnect,
    QDialog,
    QWidget,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QFormLayout,
    QVBoxLayout,
    QScrollArea,
    QDialogButtonBox,
    QFrame,
    Qt,
    QComboBox,
    QTabWidget,
    QApplication,
    QSizePolicy,
    pyqtSignal,
)
from aqt.studydeck import StudyDeck
from aqt.tagedit import TagEdit
from aqt.editor import Editor, EditorMode
from aqt.theme import theme_manager
from aqt.browser.browser import Browser
from aqt.deckchooser import DeckChooser
from aqt.notetypechooser import NotetypeChooser
from anki.notes import Note
from anki.models import NoteType, NotetypeId  # Added NotetypeId
from anki.decks import DeckId  # Added DeckId
from aqt.operations.note import add_note
from copy import deepcopy

# Imports from other modules in this addon
from .config_utils import (
    get_config,
    get_logger,
    # get_config_value,
    get_addon_name,
    score_custom_config_entry,
    filter_and_sort_custom_config_entries,
    get_effective_config,
)
from .anki_utils import (
    is_valid_cards_data,
    is_valid_card_data,
    NoteData,
    json_to_namedtuples,
    replace_nulls_with_empty_strings,
    configure_generative_ai,
)
from .prompts import (
    generate_schema_class,
    example_notes,
    get_prompt,
    get_create_prompt,
    get_complete_prompt,
)

from .prompt_store import load_saved_prompts, save_saved_prompts

# from .core import ZikariaPrompts # Avoid circular import if core imports ui

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
        defaults = self.mw_instance.col.defaults_for_adding(
            current_review_card=(
                self.mw_instance.reviewer.card if self.mw_instance.reviewer else None
            )
        )
        note_type_id = note_type_id or defaults.notetype_id
        deck_id = deck_id or defaults.deck_id

        self.notetype_chooser_instance = NotetypeChooser(
            mw=self.mw_instance,
            widget=self.note_type_combo_widget,
            starting_notetype_id=NotetypeId(note_type_id),
            show_prefix_label=show_prefix_label,
            on_notetype_changed=on_notetype_changed,
        )
        self.deck_chooser_instance = DeckChooser(
            mw=self.mw_instance,
            widget=self.deck_combo_widget,
            starting_deck_id=DeckId(deck_id),
            label=show_prefix_label,
            on_deck_changed=on_deck_changed,
        )

    def _cleanup_choosers(self):
        logger = getattr(self, "logger", get_logger())
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



# --- display_cards_confirmation_dialog ---
def display_cards_confirmation_dialog(
    cards_data_map: Dict[Note, List[Dict[str, Any]]],
    parent: Optional[QWidget] = None,
    global_tags: Optional[List[str]] = None,
    current_mw_ref: Optional[QMainWindow] = None,
) -> Dict[Note, List[Dict[str, Any]]]:
    current_mw_ref = current_mw_ref or mw
    logger = get_logger()  # Use accessor

    class ConfirmationDialog(QDialog):
        def __init__(
            self,
            cards_map: Dict[Note, List[Dict[str, Any]]],
            parent_widget_ref=None,
            global_tags_list_ref: Optional[List[str]] = None,
        ):
            super().__init__(parent_widget_ref or current_mw_ref)
            self.mw_instance = current_mw_ref
            self.setWindowTitle("Confirm New Cards")
            self.resize(1200, 800)

            total_cards = sum(len(data_list) for data_list in cards_map.values())
            self.selected_cards_flags = [True] * total_cards

            self.setStyleSheet("QWidget { font-size: 16px; }")
            self.main_layout = QVBoxLayout()
            self.setLayout(self.main_layout)

            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_content_widget = QWidget()
            scroll_layout = QVBoxLayout()
            scroll_content_widget.setLayout(scroll_layout)

            self.card_widgets_list_internal = []
            card_global_idx = 0

            for note_template, card_data_list_item in cards_map.items():
                for card_data_item_dict in card_data_list_item:
                    card_frame = QFrame()
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
                    card_layout.addLayout(header_layout)

                    field_widgets_map = {}
                    note_type_model = note_template.note_type()
                    if not note_type_model:
                        logger.error("ConfirmationDialog: Note template has no model.")
                        continue

                    present_keys = set()
                    for field_def in note_type_model["flds"]:
                        field_name = field_def["name"]
                        value_str = card_data_item_dict.get(
                            field_name, ""
                        )  # Use .get for safety
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
                                {
                                    key: card_data_item_dict[key]
                                    for key in unused_data_keys
                                }
                            )
                        )
                        rest_edit.setReadOnly(True)
                        rest_layout.addWidget(rest_label)
                        rest_layout.addWidget(rest_edit)
                        card_layout.addLayout(rest_layout)

                    card_tag_edit_widget = self._get_card_tag_edit(
                        tags_list, card_layout
                    )
                    self.card_widgets_list_internal.append(
                        (
                            card_checkbox,
                            field_widgets_map,
                            note_template,
                            card_tag_edit_widget,
                        )
                    )
                    card_frame.setLayout(card_layout)
                    scroll_layout.addWidget(card_frame)
                    card_global_idx += 1

            scroll_area.setWidget(scroll_content_widget)
            self.main_layout.addWidget(scroll_area)

            bulk_action_layout = QHBoxLayout()
            select_all_button = QPushButton("Select All")
            select_all_button.clicked.connect(self.select_all_cards)  # Renamed method
            bulk_action_layout.addWidget(select_all_button)
            deselect_all_button = QPushButton("Select None")
            deselect_all_button.clicked.connect(
                self.deselect_all_cards
            )  # Renamed method
            bulk_action_layout.addWidget(deselect_all_button)
            invert_selection_button = QPushButton("Invert Selection")
            invert_selection_button.clicked.connect(
                self.invert_card_selection
            )  # Renamed method
            bulk_action_layout.addWidget(invert_selection_button)
            self.main_layout.addLayout(bulk_action_layout)

            self._setup_global_tag_edit(global_tags_list_ref or [])

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            self.main_layout.addWidget(buttons)

        def _setup_global_tag_edit(self, global_tags_list: List[str]) -> None:
            tag_edit_frame = QWidget(self)
            tag_edit_frame.setStyleSheet("border: 0")
            tag_edit_layout = QGridLayout()
            tag_edit_layout.setSpacing(12)
            tag_edit_layout.setContentsMargins(2, 6, 2, 6)
            tag_edit_label = QLabel(tr.editing_tags())
            tag_edit_layout.addWidget(tag_edit_label, 1, 0)
            self.global_tag_edit_widget_internal = TagEdit(self)  # Renamed
            self.global_tag_edit_widget_internal.setToolTip(
                shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
            )
            border = theme_manager.var(
                colors.BORDER
            )  # Ensure theme_manager is available
            self.global_tag_edit_widget_internal.setStyleSheet(
                f"border: 1px solid {border}"
            )
            tag_edit_layout.addWidget(self.global_tag_edit_widget_internal, 1, 1)
            tag_edit_frame.setLayout(tag_edit_layout)
            self.main_layout.addWidget(tag_edit_frame)
            if self.global_tag_edit_widget_internal.col != self.mw_instance.col:
                self.global_tag_edit_widget_internal.setCol(self.mw_instance.col)
            self.global_tag_edit_widget_internal.setText(
                self.mw_instance.col.tags.join(global_tags_list)
            )

        def _get_card_tag_edit(
            self, tags_list: List[str], card_layout_ref: QVBoxLayout
        ) -> TagEdit:
            tag_layout = QHBoxLayout()
            tag_label = QLabel(f"{tr.editing_tags()}:")
            tag_edit_widget = TagEdit(self)
            tag_edit_widget.setToolTip(
                shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
            )
            if tag_edit_widget.col != self.mw_instance.col:
                tag_edit_widget.setCol(self.mw_instance.col)
            tag_edit_widget.setText(self.mw_instance.col.tags.join(tags_list))
            tag_layout.addWidget(tag_label)
            tag_layout.addWidget(tag_edit_widget)
            card_layout_ref.addLayout(tag_layout)
            return tag_edit_widget

        def toggle_card_selection(self, index: int, state: int) -> None:
            self.selected_cards_flags[index] = (
                Qt.CheckState(state) == Qt.CheckState.Checked
            )

        def select_all_cards(self) -> None:  # Renamed
            for checkbox, *_ in self.card_widgets_list_internal:
                checkbox.setChecked(True)

        def deselect_all_cards(self) -> None:  # Renamed
            for checkbox, *_ in self.card_widgets_list_internal:
                checkbox.setChecked(False)

        def invert_card_selection(self) -> None:  # Renamed
            for checkbox, *_ in self.card_widgets_list_internal:
                checkbox.setChecked(not checkbox.isChecked())

        def get_confirmed_cards_data(
            self,
        ) -> Dict[Note, List[Dict[str, Any]]]:  # Renamed
            global_tags_set = set(
                self.mw_instance.col.tags.split(
                    self.global_tag_edit_widget_internal.text()
                )
            )
            confirmed_map = {}
            for idx, selected_flag in enumerate(self.selected_cards_flags):
                if selected_flag:
                    _cb, fields_map, note_tmpl, tag_edit_w = (
                        self.card_widgets_list_internal[idx]
                    )
                    tags_set = set(self.mw_instance.col.tags.split(tag_edit_w.text()))
                    tags_set.update(global_tags_set)
                    updated_data = {
                        field_name: widget.text()
                        for field_name, widget in fields_map.items()
                    }
                    updated_data["tags"] = list(tags_set)
                    confirmed_map.setdefault(note_tmpl, []).append(updated_data)
            return confirmed_map

    dialog = ConfirmationDialog(
        cards_map=cards_data_map,
        parent_widget_ref=parent,
        global_tags_list_ref=global_tags,
    )
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_confirmed_cards_data()  # Use renamed method
    return {}


# --- ReviewAndEditNotesDialog ---
class ReviewAndEditNotesDialog(QDialog):
    def __init__(
        self,
        notes_data_list: List[NoteData],
        current_mw_ref: QMainWindow,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent or current_mw_ref)
        self.notes_data_list = notes_data_list
        self.mw_instance = current_mw_ref
        self.logger = get_logger()
        self.edited_notes_data: List[NoteData] = []
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Review and Edit Notes (Zikaria)")
        self.resize(1000, 700)  # Adjusted size

        self.main_layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_content_widget)

        self.note_editors = (
            []
        )  # List to store (checkbox, field_widgets_map, tag_edit_widget, original_note_data)

        for idx, note_data in enumerate(self.notes_data_list):
            note_frame = QFrame()
            note_frame.setFrameShape(
                QFrame.Shape.StyledPanel
            )  # Add some visual separation
            note_layout = QVBoxLayout(note_frame)

            # Header for the note (e.g., Note 1, Note Type, Deck)
            header_label_text = f"Note {idx + 1}"
            if note_data.note_type_id:
                nt_model = self.mw_instance.col.models.get(
                    NotetypeId(note_data.note_type_id)
                )
                if nt_model:
                    header_label_text += f" (Type: {nt_model['name']})"
            if note_data.deck_id:
                dk_model = self.mw_instance.col.decks.get(DeckId(note_data.deck_id))
                if dk_model:
                    header_label_text += f" [Deck: {dk_model['name']}]"

            header_label = QLabel(header_label_text)
            header_label.setStyleSheet("font-weight: bold;")
            note_layout.addWidget(header_label)

            checkbox = QCheckBox("Include this note")
            checkbox.setChecked(True)
            note_layout.addWidget(checkbox)

            fields_group_box = QWidget()  # Using a QWidget as a container
            fields_layout = QFormLayout(fields_group_box)
            field_widgets = {}

            for field_name, field_value in note_data.fields.items():
                field_label = QLabel(f"{field_name}:")
                field_edit = QLineEdit(
                    str(field_value) if field_value is not None else ""
                )
                fields_layout.addRow(field_label, field_edit)
                field_widgets[field_name] = field_edit
            note_layout.addWidget(fields_group_box)

            tags_label = QLabel("Tags:")
            tag_edit_widget = TagEdit(self)  # Parent is the dialog
            if (
                tag_edit_widget.col != self.mw_instance.col
            ):  # Important for correct tag handling
                tag_edit_widget.setCol(self.mw_instance.col)
            tag_edit_widget.setText(
                self.mw_instance.col.tags.join(note_data.tags or [])
            )

            tags_layout = QHBoxLayout()
            tags_layout.addWidget(tags_label)
            tags_layout.addWidget(tag_edit_widget)
            note_layout.addLayout(tags_layout)

            scroll_layout.addWidget(note_frame)
            self.note_editors.append(
                (checkbox, field_widgets, tag_edit_widget, note_data)
            )

        scroll_area.setWidget(scroll_content_widget)
        self.main_layout.addWidget(scroll_area)

        # Dialog buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.on_accept)
        self.button_box.rejected.connect(self.reject)  # QDialog's reject
        self.main_layout.addWidget(self.button_box)

    def on_accept(self):
        self.logger.debug("ReviewAndEditNotesDialog: on_accept called.")
        self.edited_notes_data = []  # Clear previous attempts if any
        for (
            checkbox,
            field_widgets_map,
            tag_edit_widget,
            original_note_data,
        ) in self.note_editors:
            if checkbox.isChecked():
                updated_fields = {
                    name: qlineedit.text()
                    for name, qlineedit in field_widgets_map.items()
                }
                updated_tags = self.mw_instance.col.tags.split(tag_edit_widget.text())

                # Create new NoteData, preserving original note_type_id and deck_id
                # as this dialog is for content review, not structural changes.
                new_note_data_entry = NoteData(
                    note_type_id=original_note_data.note_type_id,
                    deck_id=original_note_data.deck_id,
                    fields=updated_fields,
                    tags=updated_tags,
                )
                self.edited_notes_data.append(new_note_data_entry)

        self.logger.info(
            f"Accepted review. {len(self.edited_notes_data)} notes will be processed."
        )
        self.accept()  # This is QDialog.accept() which closes the dialog with Accepted code


# --- display_review_and_edit_notes_dialog ---
def display_review_and_edit_notes_dialog(
    notes_data_list: List[NoteData],
    current_mw_ref: Optional[QMainWindow] = None,
    parent_widget_ref: Optional[QWidget] = None,
) -> List[NoteData]:  # Return type changed to List[NoteData]
    effective_mw_ref = current_mw_ref or mw
    logger = get_logger()

    if not notes_data_list:
        logger.info(
            "display_review_and_edit_notes_dialog: No notes data provided to review."
        )
        return []

    dialog = ReviewAndEditNotesDialog(
        notes_data_list=notes_data_list,
        current_mw_ref=effective_mw_ref,
        parent=parent_widget_ref,
    )

    if dialog.exec() == QDialog.DialogCode.Accepted:
        logger.debug("ReviewAndEditNotesDialog accepted. Returning edited notes.")
        return dialog.edited_notes_data
    else:
        logger.debug("ReviewAndEditNotesDialog cancelled or closed without accepting.")
        return []  # Return empty list if cancelled


# --- JsonInputDialog ---
class JsonInputDialog(QDialog, ChoosersMixin):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        current_mw_ref: Optional[QMainWindow] = None,
    ):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw  # Use consistent naming
        self._close_event_has_cleaned_up = False
        self.setWindowTitle("Process JSON Input")
        self.resize(800, 1000)
        # ... (rest of implementation using self.mw_instance)
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
        logger = get_logger()
        try:
            note_type_id = self.notetype_chooser_instance.selected_notetype_id
            note_type_model = self.mw_instance.col.models.get(note_type_id)
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


# --- on_process_json_triggered_action ---
def on_process_json_triggered_action(current_mw_ref: QMainWindow):
    logger = get_logger()
    config = get_config()  # Get current config
    dialog = JsonInputDialog(current_mw_ref=current_mw_ref)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        input_data = dialog.get_input_data()
        json_text_data = input_data["json_data"]
        deck_id = input_data["deck_id"]
        note_type_id = input_data["notetype_id"]

        try:
            notes_json_list = json.loads(json_text_data)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON input: {e}", exc_info=True)
            showInfo("Invalid JSON provided.", parent=current_mw_ref)
            return

        notes_json_list = replace_nulls_with_empty_strings(notes_json_list)
        if not is_valid_cards_data(notes_json_list):
            logger.error(
                f"JSON needs to be a list of dicts, was: {type(notes_json_list)}"
            )
            showInfo(
                "JSON data is not in the expected format (list of dictionaries).",
                parent=current_mw_ref,
            )
            return
        if not notes_json_list:
            logger.info("No cards found in JSON, returning.")
            showInfo("No card data found in the JSON input.", parent=current_mw_ref)
            return

        json_tag_val = config.get("json_tag", "from_json")
        note_type_model = current_mw_ref.col.models.get(note_type_id)
        if not note_type_model:
            logger.error(f"Could not load note type model for ID: {note_type_id}")
            showInfo(
                f"Error: Could not load note type for ID {note_type_id}.",
                parent=current_mw_ref,
            )
            return
        dummy_note_template = Note(current_mw_ref.col, note_type_model)

        confirmed_cards_map = display_cards_confirmation_dialog(
            cards_data_map={dummy_note_template: notes_json_list},
            global_tags=[json_tag_val],
            current_mw_ref=current_mw_ref,
        )

        if not confirmed_cards_map:
            showInfo("No cards confirmed for import.", parent=current_mw_ref)
            return

        from .core import (
            ZikariaPrompts,
        )  # Import locally to avoid circularity at module level

        core_processor = ZikariaPrompts(mw=current_mw_ref, manual_execution=True)
        notes_added_count = 0
        for _note_tmpl, cards_to_add in confirmed_cards_map.items():
            # Call add_new_notes from the core processor
            # original_note, cards_data, deck_id=None, note_type=None, note_tag=None
            core_processor.add_new_notes(
                original_note=dummy_note_template,  # Used as template
                cards_data_list=cards_to_add,
                deck_id_override=deck_id,
                note_type_override=note_type_model,
                default_tags=[],  # Tags are already in cards_to_add from confirmation dialog
            )
            notes_added_count += len(cards_to_add)

        if notes_added_count > 0:
            showInfo(
                f"{notes_added_count} notes added from JSON.", parent=current_mw_ref
            )
            logger.info(
                f"Processed JSON input for deck ID {deck_id}, note type ID {note_type_id}. Added {notes_added_count} notes."
            )


# --- DebugDialog ---
# --- Enhanced DebugDialog ---
class DebugDialog(QDialog):
    def __init__(
        self,
        note: Note,
        parent: Optional[QWidget] = None,
        current_mw_ref: Optional[QMainWindow] = None,
    ):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw
        self.note = note
        self.logger = get_logger()
        self.config = get_config()
        self.setWindowTitle("Debug Note (Zikaria)")
        self.resize(800, 1000)

        from .config_utils import get_effective_config

        self.effective_config = get_effective_config(
            self.note.mid, self.note.cards()[0].did if self.note.cards() else None
        )

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._init_schema_tab()
        self._init_examples_tab()
        self._init_create_prompt_tab()
        self._init_complete_prompt_tab()
        self._init_prompt_tab()

        # Initial content
        self.display_schema_handler()
        self.display_examples_handler()
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

    def _init_schema_tab(self):
        self.schema_output_area = QTextEdit()
        self.schema_output_area.setReadOnly(True)
        tab = self._add_output_area_with_controls(
            self.schema_output_area, self.display_schema_handler
        )
        self.tabs.addTab(tab, "Schema")

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
            mw=self.mw_instance,
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
        self.logger.debug("DebugDialog: Displaying schema.")
        note_model = self.note.note_type()
        if not note_model:
            self.schema_output_area.setPlainText(
                "Error: Note has no model (note type)."
            )
            self.logger.error("DebugDialog: Note has no model (note type).")
            return
        try:
            schema = generate_schema_class(note_model)
            self.schema_output_area.setPlainText(
                json.dumps(schema, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            self.schema_output_area.setPlainText(f"Error generating schema: {e}")
            self.logger.exception("DebugDialog: Error generating schema.")

    def get_prompt_args(self, mode: str) -> dict:
        kwargs = {"dummy_keys": set()}
        temporary_config = deepcopy(self.effective_config)
        base_prompt = temporary_config.get(f"{mode}_prompt", "")

        if not getattr(self, f"include_schema_{mode}").isChecked():
            kwargs["dummy_keys"].add("schema")

        if not getattr(self, f"include_examples_{mode}").isChecked():
            kwargs["dummy_keys"].add("examples")

        temporary_config[f"{mode}_prompt"] = base_prompt
        kwargs["current_config"] = temporary_config
        return kwargs

    def display_create_prompt_handler(self):
        self.logger.debug("DebugDialog: Displaying create prompt.")

        try:
            prompt = get_create_prompt(self.note, **self.get_prompt_args("create"))
            self.create_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.create_prompt_output_area.setPlainText(
                f"Error generating create prompt: {e}"
            )
            self.logger.exception("DebugDialog: Error generating create prompt.")

    def display_complete_prompt_handler(self):
        self.logger.debug("DebugDialog: Displaying complete prompt.")

        try:
            prompt = get_complete_prompt(self.note, **self.get_prompt_args("complete"))
            self.complete_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.complete_prompt_output_area.setPlainText(
                f"Error generating complete prompt: {e}"
            )
            self.logger.exception("DebugDialog: Error generating complete prompt.")

    def display_prompt_handler(self):
        self.logger.debug("DebugDialog: Displaying prompt.")

        dummy_keys = set()
        base_prompt = self.prompt_template_chooser_instance.selected_template_text()
        # self.logger.debug("DebugDialog: selected_template: %s", base_prompt)

        if not self.include_schema_prompt.isChecked():
            dummy_keys.add("schema")

        if not self.include_examples_prompt.isChecked():
            dummy_keys.add("examples")

        try:
            prompt = get_prompt(
                self.note,
                base_prompt=base_prompt,
                current_config=self.effective_config,
                dummy_keys=dummy_keys,
            )
            self.prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.prompt_output_area.setPlainText(f"Error generating prompt: {e}")
            self.logger.exception("DebugDialog: Error generating prompt.")

    def display_examples_handler(self):
        self.logger.debug("DebugDialog: Displaying examples.")
        try:
            note_type = self.note.note_type()
            if not note_type:
                self.examples_output_area.setPlainText(
                    "Error: Note has no model (note type) to generate examples from."
                )
                self.logger.error(
                    "DebugDialog: Note has no model (note type) for examples."
                )
                return

            n = self.example_count.value()
            examples_str = example_notes(note_type, n=n)
            self.examples_output_area.setPlainText(examples_str)
        except Exception as e:
            self.examples_output_area.setPlainText(f"Error generating examples: {e}")
            self.logger.exception("DebugDialog: Error generating examples.")



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
        self.logger = get_logger()

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

    def _open_template_chooser_dialog(self):
        def template_names() -> list[str]:
            return sorted(t["name"] for t in self.prompt_templates.values())

        def callback(ret: StudyDeck) -> None:
            self.logger.debug("PromptTemplateChooser: study deck returned: %s", ret.form.list.currentRow())
            template_index = ret.form.list.currentRow()
            if template_index < 0:
                self.selected_template = None

            else:
                key = list(self.prompt_templates)[template_index]

                self.selected_template = key, self.prompt_templates[key]
                self.logger.debug("New key and template: %s, %s", key, self.prompt_templates[key])

            self._update_button_label()
            if self.on_template_changed:
                self.on_template_changed(self.selected_template)
            self.template_changed.emit(self.selected_template_key() or "")

        manage_button = QPushButton(tr.qt_misc_manage())

        def open_manage():
            dialog = SavedPromptManagerDialog(
                parent=self._widget,
                current_mw_ref=self.mw,
                starting_index = self.study_deck.form.list.currentRow()
                # note_type_id=self.note_type_id,
                # deck_id=self.deck_id,
            )

            dialog.exec()
            template_index = dialog.prompt_list.currentIndex()
            self.logger.debug("Prompt manager finished with prompt index: %s", template_index)

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
        qconnect(unset_button.clicked, lambda: self.study_deck.form.list.setCurrentRow(-1))

        self.study_deck = MyStudyDeck(
            mw=self.mw,
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
        parent: Optional[QWidget] = None,
        current_mw_ref: Optional[QMainWindow] = None,
    ):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw
        self.logger = get_logger()
        self.config = get_config()
        self.setWindowTitle("Create Cards from Prompt Text (Zikaria)")
        self.resize(1000, 800)
        self.layout = QVBoxLayout(self)
        # ... (rest of implementation using self.logger, self.config, self.mw_instance)
        # Needs access to ZikariaPrompts instance or its methods for AI call.
        # For now, we'll instantiate ZikariaPrompts locally in send_prompt_to_ai_handler.

        self.note_type_combo_widget = QWidget(self)
        self.deck_combo_widget = QWidget(self)
        self._setup_choosers()  # Renamed
        self.layout.addWidget(self.note_type_combo_widget)
        self.layout.addWidget(self.deck_combo_widget)

        self.prompt_template_combo_widget = QWidget(self)
        self.prompt_template_chooser_instance = PromptTemplateChooser(
            mw=self.mw_instance,
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
        self.global_tag_edit_widget.setCol(self.mw_instance.col)
        self.layout.addWidget(self.global_tag_edit_widget)
        if note_tag_val := self.config.get("note_tag"):  # Use self.config
            self.global_tag_edit_widget.setText(note_tag_val)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

    # def select_saved_prompt_handler(self):
    #     note_type_id = self.notetype_chooser_instance.selected_notetype_id
    #     deck_id = self.deck_chooser_instance.selected_deck_id
    #
    #     dialog = SavedPromptManagerDialog(
    #         parent=self,
    #         current_mw_ref=self.mw_instance,
    #         note_type_id=note_type_id,
    #         deck_id=deck_id,
    #     )
    #     if dialog.exec():
    #         selected_prompt_text = dialog.get_selected_prompt_template()
    #         if selected_prompt_text:
    #             self.prompt_edit_area.setPlainText(selected_prompt_text)
    #             self.status_label.setText("Loaded saved prompt.")

    def generate_full_prompt_handler(self):
        self.logger.debug("Generating full prompt in PromptFromTextDialog.")
        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        note_model = self.mw_instance.col.models.get(note_type_id)
        if not note_model:
            self.status_label.setText("Error: Could not load note type.")
            return

        dummy_note = Note(self.mw_instance.col, note_model)
        dummy_note.fields[0] = self.prompt_input_area.toPlainText()

        current_deck_id = self.deck_chooser_instance.selected_deck_id
        effective_config = get_effective_config(note_type_id, current_deck_id)
        temporary_config = deepcopy(effective_config)

        selected_template = (
            self.prompt_template_chooser_instance.selected_template_text()
        )
        # dialog = SavedPromptManagerDialog(
        #     parent=self,
        #     current_mw_ref=self.mw_instance,
        #     note_type_id=note_type_id,
        #     deck_id=current_deck_id,
        # )
        # if dialog.exec():
        #     selected_template = dialog.get_selected_prompt_template()

        if selected_template:
            temporary_config["create_prompt"] = selected_template

        try:
            prompt_text = get_create_prompt(
                dummy_note,
                temporary_config,
            )
            self.prompt_edit_area.setPlainText(prompt_text)
            self.status_label.setText("Prompt generated.")
        except Exception as e:
            self.status_label.setText(f"Error generating prompt: {e}")
            self.logger.exception("Failed to generate prompt in PromptFromTextDialog")

    def send_prompt_to_ai_handler(self):
        self.logger.debug("Sending prompt to AI from PromptFromTextDialog.")
        full_prompt_text = self.prompt_edit_area.toPlainText()
        if not full_prompt_text.strip():
            self.status_label.setText("Prompt is empty.")
            return

        self.status_label.setText("Configuring AI connection...")
        QApplication.processEvents()

        if not configure_generative_ai():  # From anki_utils
            self.status_label.setText("Failed to configure AI connection (API key?).")
            showInfo(
                "Failed to configure AI. Please check your API key in the Addon Configuration.",
                parent=self,
            )
            return

        self.status_label.setText("Sending prompt to AI...")
        QApplication.processEvents()

        from .core import ZikariaPrompts  # Local import

        core_processor = ZikariaPrompts(mw=self.mw_instance, manual_execution=True)

        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        current_deck_id = self.deck_chooser_instance.selected_deck_id
        from .config_utils import get_effective_config  # Local import

        effective_config = get_effective_config(note_type_id, current_deck_id)

        try:
            response = core_processor.send_prompt_to_generative_ai(
                full_prompt_text, custom_config=effective_config
            )
            if not response or not response.text:
                self.status_label.setText("No response received from AI.")
                return
            self.status_label.setText("Response received. Validating...")
            QApplication.processEvents()
        except Exception as e:
            self.status_label.setText(f"Error from AI: {e}")
            self.logger.exception("AI returned an error in PromptFromTextDialog")
            return

        try:
            cards_data_list = json.loads(response.text)
        except json.JSONDecodeError as e:
            self.logger.error(
                f"Error decoding AI JSON response: {e}\n\n{response.text}",
                exc_info=True,
            )
            self.status_label.setText("Error decoding AI JSON response.")
            showInfo(
                f"Could not understand the AI's response (not valid JSON).\nDetails: {e}\nResponse:\n{response.text[:500]}...",
                parent=self,
            )
            return

        if not is_valid_cards_data(cards_data_list):
            self.status_label.setText("AI response is not a valid card list.")
            showInfo("The AI's response was not a valid list of cards.", parent=self)
            return

        note_model = self.mw_instance.col.models.get(
            self.notetype_chooser_instance.selected_notetype_id
        )
        if not note_model:
            self.status_label.setText("Error: Selected note type not found.")
            return
        dummy_note_template = Note(self.mw_instance.col, note_model)
        global_tags_list = self.mw_instance.col.tags.split(
            self.global_tag_edit_widget.text()
        )

        confirmed_cards_map = display_cards_confirmation_dialog(
            cards_data_map={dummy_note_template: cards_data_list},
            global_tags=global_tags_list,
            current_mw_ref=self.mw_instance,
            parent=self,
        )

        if not confirmed_cards_map:
            self.status_label.setText("No cards confirmed for import.")
            return

        notes_added_count = 0
        for _template, cards_to_add in confirmed_cards_map.items():
            core_processor.add_new_notes(  # Use core_processor instance
                original_note=dummy_note_template,
                cards_data_list=cards_to_add,
                deck_id_override=self.deck_chooser_instance.selected_deck_id,
                note_type_override=note_model,
                default_tags=[],
            )
            notes_added_count += len(cards_to_add)

        self.status_label.setText(f"{notes_added_count} cards added successfully.")
        tooltip(f"{notes_added_count} cards added.", parent=self.mw_instance)

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
        parent: Optional[QWidget] = None,
        current_mw_ref: QMainWindow | None = None,
        starting_index: int | None = None
        # note_type_id: Optional[int] = None,
        # deck_id: Optional[int] = None,
    ):
        super().__init__(parent)
        self.mw_instance = current_mw_ref or mw  # Use consistent naming
        self.setWindowTitle("Manage Saved Prompts")
        self.resize(900, 500)

        # self.note_type_id = note_type_id
        # self.deck_id = deck_id
        self.prompt_templates = load_saved_prompts()
        self.selected_prompt = None

        # Layouts
        main_layout = QHBoxLayout(self)
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()
        # right_col = QVBoxLayout()
        main_layout.addLayout(left_col, 4)
        main_layout.addLayout(right_col, 1)

        # Choosers
        # self.note_type_combo_widget = QWidget(self)
        # self.deck_combo_widget = QWidget(self)
        # self._setup_choosers(
        #     self.note_type_id,
        #     self.deck_id,
        #     on_notetype_changed=self.on_chooser_change,
        #     on_deck_changed=self.on_chooser_change,
        # )
        # top_layout = QHBoxLayout()
        # top_layout.addWidget(self.note_type_combo_widget)
        # top_layout.addWidget(self.deck_combo_widget)
        # center_col.addLayout(top_layout)

        # Show All Checkbox
        # self.show_all_checkbox = QCheckBox("Show all prompts")
        # self.show_all_checkbox.setChecked(False)
        # self.show_all_checkbox.stateChanged.connect(self.refresh_prompt_list)
        # left_col.addWidget(self.show_all_checkbox)

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
        left_col.addWidget(self.preview_area)

        self.save_edit_button = QPushButton("Save")
        self.save_edit_button.setVisible(False)
        self.save_edit_button.clicked.connect(self.save_edited_prompt)
        left_col.addWidget(self.save_edit_button)

        self.refresh_prompt_list()

        if starting_index is not None:
            self.prompt_list.setCurrentIndex(starting_index)
            self.update_preview()

    # def on_chooser_change(self, *__):
    #     self.note_type_id = self.notetype_chooser_instance.selected_notetype_id
    #     self.deck_id = self.deck_chooser_instance.selected_deck_id
    #     self.refresh_prompt_list()

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

        self.preview_area.setReadOnly(True)
        self.save_edit_button.setVisible(False)

    def set_current_prompt_by_key(self, target_key):
        for index in range(self.prompt_list.count()):
            user_data = self.prompt_list.itemData(index)
            if user_data and user_data[0] == target_key:
                self.prompt_list.setCurrentIndex(index)
                return  # Stop after finding the first match

    def enter_edit_mode(self):
        if not self.selected_prompt:
            return
        self.preview_area.setReadOnly(False)
        self.save_edit_button.setVisible(True)

    def save_edited_prompt(self):
        new_text = self.preview_area.toPlainText().strip()
        if not new_text:
            return
        self.selected_prompt[1]["template"] = new_text

        save_saved_prompts(self.prompt_templates)
        self.preview_area.setReadOnly(True)
        self.save_edit_button.setVisible(False)

    def add_prompt(self):
        name, ok = getText("Enter a name for the new prompt:", parent=self)
        if not ok or not name.strip():
            return
        # text, ok = getText("Enter the prompt text:", parent=self)
        # if not ok:
        #     return
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
        new_name, ok = getText(
            "Rename prompt:", default=self.selected_prompt[2], parent=self
        )
        if not ok or not new_name.strip():
            return
        self.selected_prompt[1]["name"] = new_name.strip()

        save_saved_prompts(self.prompt_templates)
        self.refresh_prompt_list()

    def delete_prompt(self):
        if not self.selected_prompt:
            return

        del self.prompt_templates[self.selected_prompt[0]]

        save_saved_prompts(self.prompt_templates)
        self.refresh_prompt_list()

    def get_selected_prompt_template(self) -> Optional[str]:
        if self.selected_prompt:
            return self.selected_prompt[1]["template"]
        return None


# --- open_prompt_text_dialog_action ---
def open_saved_prompts_manager(current_mw_ref: QMainWindow, parent: QWidget):
    dialog = SavedPromptManagerDialog(current_mw_ref=current_mw_ref, parent=parent)
    dialog.exec()


# --- open_prompt_text_dialog_action ---
def open_prompt_text_dialog_action(current_mw_ref: QMainWindow):
    dialog = PromptFromTextDialog(current_mw_ref=current_mw_ref)
    dialog.exec()


# --- ConfigDialog & CustomConfigDialog ---
class ConfigDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        config_data_override: Optional[Dict] = None,
        current_mw_ref: Optional[QMainWindow] = None,
        addon_name_param: str = "",
    ):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw  # Use consistent naming
        self.logger = get_logger()
        self.resize(1200, 1000)

        self.addon_name = addon_name_param
        if (
            not self.addon_name
        ):  # Fallback if not provided, though __init__.py should provide it
            self.addon_name = get_addon_name()
            self.logger.warning(
                "ConfigDialog initialized without addon_name_param, using get_addon_name()."
            )

        if config_data_override is None:
            current_addon_config_obj = get_config()  # Get the live config object
            self.config_to_edit = deepcopy(current_addon_config_obj)  # Edit a deep copy
        else:
            self.config_to_edit = deepcopy(config_data_override)  # Edit a deep copy


        self.setWindowTitle(f"Zikaria Addon Configuration ({self.addon_name})")
        # ... (rest of ConfigDialog UI setup as before, using self.mw_instance, self.logger, self.addon_name)
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
        if self.mw_instance.addonManager:
            docs_path = os.path.join(
                self.mw_instance.addonManager.addonsFolder(),
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

        if self.mw_instance.addonManager:
            default_config_keys = (
                self.mw_instance.addonManager.addonConfigDefaults(self.addon_name) or {}
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
                mw=self.mw_instance,
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
            change_event=widget.stateChanged
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
            self.logger.warning(
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
        self.logger.debug(
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
        self.logger.debug("ConfigDialog: Updating custom configs display.")

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
        self.logger.debug(
            f"Found {len(custom_configs_list)} custom configs to display."
        )

        for idx, custom_config_entry in enumerate(custom_configs_list):
            note_type_id, deck_id, settings = custom_config_entry  # settings is a dict

            note_type_name = "Any Note Type"
            if note_type_id:
                nt = self.mw_instance.col.models.get(NotetypeId(note_type_id))
                if nt:
                    note_type_name = nt["name"]
                else:
                    note_type_name = f"Missing NT ID: {note_type_id}"

            deck_name = "Any Deck"
            if deck_id:
                dk = self.mw_instance.col.decks.get(DeckId(deck_id))
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
        self.logger.debug(
            f"ConfigDialog: Add/Edit custom config entry at index {index}."
        )

        custom_configs_list = self.config_to_edit.get("custom_config", [])
        note_type_id_param: Optional[NotetypeId] = None
        deck_id_param: Optional[DeckId] = None
        custom_settings_data: Dict[str, Any] = {}

        if index is not None and index < len(custom_configs_list):
            entry = custom_configs_list[index]
            note_type_id_param = NotetypeId(entry[0]) if entry[0] else None
            deck_id_param = DeckId(entry[1]) if entry[1] else None
            custom_settings_data = entry[2]  # This is the dictionary of settings
            self.logger.debug(
                f"Editing entry: NTID={note_type_id_param}, DID={deck_id_param}, Settings={custom_settings_data}"
            )
        else:
            self.logger.debug("Creating new custom config entry.")

        dialog = CustomConfigDialog(
            parent_dialog=self,
            note_type_id_param=note_type_id_param,
            deck_id_param=deck_id_param,
            custom_settings_data=custom_settings_data,  # Pass the dict here
            current_mw_ref=self.mw_instance,
            addon_name_param=self.addon_name,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.logger.debug("CustomConfigDialog accepted.")
            new_note_type_id = dialog.note_type_id_result
            new_deck_id = dialog.deck_id_result
            # dialog.config_to_edit contains the settings edited in CustomConfigDialog
            new_settings = dialog.config_to_edit

            updated_entry = [
                new_note_type_id if new_note_type_id is not None else None,
                new_deck_id if new_deck_id is not None else None,
                new_settings,
            ]
            self.logger.debug(f"Updated entry data: {updated_entry}")

            current_custom_configs = self.config_to_edit.setdefault("custom_config", [])
            if index is None:
                current_custom_configs.append(updated_entry)
                self.logger.debug("Appended new custom config.")
            elif index < len(current_custom_configs):
                current_custom_configs[index] = updated_entry
                self.logger.debug(f"Updated custom config at index {index}.")
            else:
                self.logger.error(
                    f"Error updating custom config: index {index} out of bounds for list of len {len(current_custom_configs)}"
                )

            self._update_custom_configs_display()
        else:
            self.logger.debug("CustomConfigDialog cancelled or closed.")

    def _delete_custom_config_entry(self, index: int):  # Renamed
        """Delete a custom configuration."""
        self.logger.debug(
            f"ConfigDialog: Attempting to delete custom config at index {index}."
        )
        custom_configs_list = self.config_to_edit.get("custom_config", [])
        if 0 <= index < len(custom_configs_list):
            custom_configs_list.pop(index)
            self.logger.info(f"Deleted custom config entry at index {index}.")
            self._update_custom_configs_display()
        else:
            self.logger.warning(
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
        self.logger.info(f"Saving configuration for addon '{self.addon_name}'.")
        updated_data_from_widgets = {}
        for key, data in self.config_widgets_map.items():
            if data["get_fn"]:
                updated_data_from_widgets[key] = data["get_fn"]()

        # Update the copy we are editing
        self.config_to_edit.update(updated_data_from_widgets)
        # ... (Handle custom_config and extra_config from their UI elements) ...

        if self.mw_instance.addonManager:
            self.mw_instance.addonManager.writeConfig(
                self.addon_name, self.config_to_edit
            )

        # Update the live global config object used by the addon
        live_config = get_config()  # Get the actual global config object
        live_config.clear()
        live_config.update(self.config_to_edit)  # Update its content

        # Update logger level if debug status changed
        import logging

        new_debug_level = (
            logging.DEBUG if live_config.get("debug", False) else logging.INFO
        )
        if self.logger.level != new_debug_level:
            self.logger.setLevel(new_debug_level)
            self.logger.info(
                f"Log level updated to {logging.getLevelName(new_debug_level)}."
            )

        self.logger.info(f"Configuration for '{self.addon_name}' saved and applied.")
        self.accept()


# --- CustomConfigDialog ---
class CustomConfigDialog(ConfigDialog, ChoosersMixin):  # Inherits from ConfigDialog
    def __init__(
        self,
        parent_dialog: ConfigDialog,
        note_type_id_param: Optional[NotetypeId] = None,
        deck_id_param: Optional[DeckId] = None,
        custom_settings_data: Optional[Dict[str, Any]] = None,
        current_mw_ref: Optional[QMainWindow] = None,
        addon_name_param: str = "",
    ):
        self.note_type_id_param = note_type_id_param
        self.deck_id_param = deck_id_param

        super().__init__(
            parent=parent_dialog,
            config_data_override=custom_settings_data,
            current_mw_ref=current_mw_ref,
            addon_name_param=addon_name_param,
        )

        self.setWindowTitle(f"Edit Custom Configuration for {self.addon_name}")

        self.note_type_id_result: Optional[NotetypeId] = self.note_type_id_param
        self.deck_id_result: Optional[DeckId] = self.deck_id_param

    def _setup_docs_panel(self):
        """Remove the documentation panel for the custom configuration dialog."""
        pass

    def _init_form_elements(self):
        self.logger.debug(
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

        global_defaults = (
            self.mw_instance.addonManager.addonConfigDefaults(self.addon_name) or {}
        )

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
                        lambda checked=False, k=key, g=widget_data["get_fn"], c=is_active_checkbox: self._sync_checkbox_with_widget(
                            k, g, c
                        )
                    )

                widget_data["row"] = row_layout
                widget_data["is_active_checkbox"] = is_active_checkbox
                self.form_layout.addRow(widget_data["label"], row_layout)
                self.config_widgets_map[key] = widget_data

        self._add_action_buttons()

    def save_configuration_handler(self):
        self.logger.info(
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
            if data.get("is_active_checkbox") and not data["is_active_checkbox"].isChecked():
                continue

            if data.get("get_fn"):
                try:
                    updated_data_from_widgets[key] = data["get_fn"]()
                except Exception as e:
                    self.logger.error(
                        f"Error getting value for key {key}: {e}", exc_info=True
                    )

        self.config_to_edit.clear()
        self.config_to_edit.update(updated_data_from_widgets)

        self.note_type_id_result = selected_note_type_id
        self.deck_id_result = selected_deck_id

        self.logger.debug(
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
        self.logger.debug("CustomConfigDialog: Accepted.")
        self._cleanup_choosers()
        super().accept()  # Call QDialog.accept()

    def reject(self) -> None:
        self.logger.debug("CustomConfigDialog: Rejected.")
        self._cleanup_choosers()
        super().reject()  # Call QDialog.reject()

    def closeEvent(self, event):  # Ensure cleanup on any close
        self.logger.debug("CustomConfigDialog: closeEvent triggered.")
        self._cleanup_choosers()
        super().closeEvent(event)


# --- show_config_dialog_action ---
def show_config_dialog_action(current_mw_ref: QMainWindow, addon_name_str: str = None):
    logger = get_logger()
    logger.debug(f"Showing config dialog for addon: {addon_name_str}")
    dialog = ConfigDialog(
        current_mw_ref=current_mw_ref, addon_name_param=addon_name_str
    )
    dialog.exec()


# --- Action functions for menu items (browser related) ---
def add_debug_menu_to_browser_action(browser_instance: Browser):
    logger = get_logger()
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
    logger = get_logger()
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
        dialog = DebugDialog(
            note_object, parent=browser_instance, current_mw_ref=browser_instance.mw
        )
        dialog.exec()


def are_nids_one_notetype(nids, browser_instance: Browser):
    col = browser_instance.mw.col
    notes = [col.get_note(nid) for nid in nids]
    note_types = {note.mid for note in notes}

    return len(note_types) == 1


def on_browser_context_menu_action(
    browser_instance: Browser, menu: QWidget
):  # menu is QMenu
    logger = get_logger()
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
