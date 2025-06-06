import json
import os
from typing import Optional, List, Dict, Any
from collections import namedtuple # For NoteData if it's used by dialogs directly

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
    QApplication
)
from aqt.tagedit import TagEdit
from aqt.editor import Editor, EditorMode
from aqt.theme import theme_manager
from aqt.browser.browser import Browser
from aqt.deckchooser import DeckChooser
from aqt.notetypechooser import NotetypeChooser
from anki.notes import Note
from anki.models import NoteType, NotetypeId # Added NotetypeId
from anki.decks import DeckId # Added DeckId
from aqt.operations.note import add_note
from copy import deepcopy

# Imports from other modules in this addon
from .config_utils import get_config, get_logger, get_config_value, get_addon_name
from .anki_utils import (
    is_valid_cards_data, 
    is_valid_card_data, 
    NoteData, 
    json_to_namedtuples,
    replace_nulls_with_empty_strings,
    configure_generative_ai 
)
from .prompts import (
    generate_schema_class, 
    example_notes, 
    get_create_prompt, 
    get_complete_prompt
)
# from .core import ZikariaPrompts # Avoid circular import if core imports ui

# --- display_cards_confirmation_dialog ---
def display_cards_confirmation_dialog(
    cards_data_map: Dict[Note, List[Dict[str, Any]]], 
    parent: Optional[QWidget] = None, 
    global_tags: Optional[List[str]] = None,
    current_mw_ref: Optional[QMainWindow] = None 
) -> Dict[Note, List[Dict[str, Any]]]:
    current_mw_ref = current_mw_ref or mw
    logger = get_logger() # Use accessor
    
    class ConfirmationDialog(QDialog):
        def __init__(
            self, 
            cards_map: Dict[Note, List[Dict[str, Any]]], 
            parent_widget_ref=None, 
            global_tags_list_ref: Optional[List[str]] = None
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
                        lambda state, idx=card_global_idx: self.toggle_card_selection(idx, state)
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
                        value_str = card_data_item_dict.get(field_name, "") # Use .get for safety
                        if field_name in card_data_item_dict:
                             present_keys.add(field_name)

                        field_layout = QHBoxLayout()
                        field_label = QLabel(f"{field_name}:")
                        field_edit = QLineEdit(str(value_str) if value_str is not None else "")
                        field_layout.addWidget(field_label)
                        field_layout.addWidget(field_edit)
                        card_layout.addLayout(field_layout)
                        field_widgets_map[field_name] = field_edit
                    
                    tags_list = card_data_item_dict.get("tags", [])
                    if "tags" in card_data_item_dict:
                        present_keys.add("tags")

                    unused_data_keys = [key for key in card_data_item_dict if key not in present_keys]
                    if unused_data_keys:
                        rest_layout = QHBoxLayout()
                        rest_label = QLabel("Unused data:")
                        rest_edit = QLineEdit(
                            json.dumps({key: card_data_item_dict[key] for key in unused_data_keys})
                        )
                        rest_edit.setReadOnly(True)
                        rest_layout.addWidget(rest_label)
                        rest_layout.addWidget(rest_edit)
                        card_layout.addLayout(rest_layout)

                    card_tag_edit_widget = self._get_card_tag_edit(tags_list, card_layout)
                    self.card_widgets_list_internal.append(
                        (card_checkbox, field_widgets_map, note_template, card_tag_edit_widget)
                    )
                    card_frame.setLayout(card_layout)
                    scroll_layout.addWidget(card_frame)
                    card_global_idx += 1

            scroll_area.setWidget(scroll_content_widget)
            self.main_layout.addWidget(scroll_area)

            bulk_action_layout = QHBoxLayout()
            select_all_button = QPushButton("Select All")
            select_all_button.clicked.connect(self.select_all_cards) # Renamed method
            bulk_action_layout.addWidget(select_all_button)
            deselect_all_button = QPushButton("Select None")
            deselect_all_button.clicked.connect(self.deselect_all_cards) # Renamed method
            bulk_action_layout.addWidget(deselect_all_button)
            invert_selection_button = QPushButton("Invert Selection")
            invert_selection_button.clicked.connect(self.invert_card_selection) # Renamed method
            bulk_action_layout.addWidget(invert_selection_button)
            self.main_layout.addLayout(bulk_action_layout)

            self._setup_global_tag_edit(global_tags_list_ref or [])

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
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
            self.global_tag_edit_widget_internal = TagEdit(self) # Renamed
            self.global_tag_edit_widget_internal.setToolTip(shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt()))
            border = theme_manager.var(colors.BORDER) # Ensure theme_manager is available
            self.global_tag_edit_widget_internal.setStyleSheet(f"border: 1px solid {border}")
            tag_edit_layout.addWidget(self.global_tag_edit_widget_internal, 1, 1)
            tag_edit_frame.setLayout(tag_edit_layout)
            self.main_layout.addWidget(tag_edit_frame)
            if self.global_tag_edit_widget_internal.col != self.mw_instance.col:
                self.global_tag_edit_widget_internal.setCol(self.mw_instance.col)
            self.global_tag_edit_widget_internal.setText(self.mw_instance.col.tags.join(global_tags_list))

        def _get_card_tag_edit(self, tags_list: List[str], card_layout_ref: QVBoxLayout) -> TagEdit:
            tag_layout = QHBoxLayout()
            tag_label = QLabel(f"{tr.editing_tags()}:")
            tag_edit_widget = TagEdit(self)
            tag_edit_widget.setToolTip(shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt()))
            if tag_edit_widget.col != self.mw_instance.col:
                tag_edit_widget.setCol(self.mw_instance.col)
            tag_edit_widget.setText(self.mw_instance.col.tags.join(tags_list))
            tag_layout.addWidget(tag_label)
            tag_layout.addWidget(tag_edit_widget)
            card_layout_ref.addLayout(tag_layout)
            return tag_edit_widget

        def toggle_card_selection(self, index: int, state: int) -> None:
            self.selected_cards_flags[index] = (Qt.CheckState(state) == Qt.CheckState.Checked)

        def select_all_cards(self) -> None: # Renamed
            for checkbox, *_ in self.card_widgets_list_internal: checkbox.setChecked(True)
        def deselect_all_cards(self) -> None: # Renamed
            for checkbox, *_ in self.card_widgets_list_internal: checkbox.setChecked(False)
        def invert_card_selection(self) -> None: # Renamed
            for checkbox, *_ in self.card_widgets_list_internal: checkbox.setChecked(not checkbox.isChecked())

        def get_confirmed_cards_data(self) -> Dict[Note, List[Dict[str, Any]]]: # Renamed
            global_tags_set = set(self.mw_instance.col.tags.split(self.global_tag_edit_widget_internal.text()))
            confirmed_map = {}
            for idx, selected_flag in enumerate(self.selected_cards_flags):
                if selected_flag:
                    _cb, fields_map, note_tmpl, tag_edit_w = self.card_widgets_list_internal[idx]
                    tags_set = set(self.mw_instance.col.tags.split(tag_edit_w.text()))
                    tags_set.update(global_tags_set)
                    updated_data = {
                        field_name: widget.text() for field_name, widget in fields_map.items()
                    }
                    updated_data["tags"] = list(tags_set)
                    confirmed_map.setdefault(note_tmpl, []).append(updated_data)
            return confirmed_map

    dialog = ConfirmationDialog(
        cards_map=cards_data_map, 
        parent_widget_ref=parent, 
        global_tags_list_ref=global_tags
    )
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_confirmed_cards_data() # Use renamed method
    return {}

# --- ReviewAndEditNotesDialog ---
class ReviewAndEditNotesDialog(QDialog):
    def __init__(self, notes_data_list: List[NoteData], current_mw_ref: QMainWindow, parent: Optional[QWidget] = None):
        super().__init__(parent or current_mw_ref)
        self.notes_data_list = notes_data_list
        self.mw_instance = current_mw_ref
        self.logger = get_logger()
        self.edited_notes_data: List[NoteData] = []
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Review and Edit Notes (Zikaria)")
        self.resize(1000, 700) # Adjusted size

        self.main_layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_content_widget)

        self.note_editors = [] # List to store (checkbox, field_widgets_map, tag_edit_widget, original_note_data)

        for idx, note_data in enumerate(self.notes_data_list):
            note_frame = QFrame()
            note_frame.setFrameShape(QFrame.Shape.StyledPanel) # Add some visual separation
            note_layout = QVBoxLayout(note_frame)

            # Header for the note (e.g., Note 1, Note Type, Deck)
            header_label_text = f"Note {idx + 1}"
            if note_data.note_type_id:
                nt_model = self.mw_instance.col.models.get(NotetypeId(note_data.note_type_id))
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

            fields_group_box = QWidget() # Using a QWidget as a container
            fields_layout = QFormLayout(fields_group_box)
            field_widgets = {}

            for field_name, field_value in note_data.fields.items():
                field_label = QLabel(f"{field_name}:")
                field_edit = QLineEdit(str(field_value) if field_value is not None else "")
                fields_layout.addRow(field_label, field_edit)
                field_widgets[field_name] = field_edit
            note_layout.addWidget(fields_group_box)

            tags_label = QLabel("Tags:")
            tag_edit_widget = TagEdit(self) # Parent is the dialog
            if tag_edit_widget.col != self.mw_instance.col: # Important for correct tag handling
                tag_edit_widget.setCol(self.mw_instance.col)
            tag_edit_widget.setText(self.mw_instance.col.tags.join(note_data.tags or []))

            tags_layout = QHBoxLayout()
            tags_layout.addWidget(tags_label)
            tags_layout.addWidget(tag_edit_widget)
            note_layout.addLayout(tags_layout)

            scroll_layout.addWidget(note_frame)
            self.note_editors.append((checkbox, field_widgets, tag_edit_widget, note_data))

        scroll_area.setWidget(scroll_content_widget)
        self.main_layout.addWidget(scroll_area)

        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.on_accept)
        self.button_box.rejected.connect(self.reject) # QDialog's reject
        self.main_layout.addWidget(self.button_box)

    def on_accept(self):
        self.logger.debug("ReviewAndEditNotesDialog: on_accept called.")
        self.edited_notes_data = [] # Clear previous attempts if any
        for checkbox, field_widgets_map, tag_edit_widget, original_note_data in self.note_editors:
            if checkbox.isChecked():
                updated_fields = {name: qlineedit.text() for name, qlineedit in field_widgets_map.items()}
                updated_tags = self.mw_instance.col.tags.split(tag_edit_widget.text())

                # Create new NoteData, preserving original note_type_id and deck_id
                # as this dialog is for content review, not structural changes.
                new_note_data_entry = NoteData(
                    note_type_id=original_note_data.note_type_id,
                    deck_id=original_note_data.deck_id,
                    fields=updated_fields,
                    tags=updated_tags
                )
                self.edited_notes_data.append(new_note_data_entry)

        self.logger.info(f"Accepted review. {len(self.edited_notes_data)} notes will be processed.")
        self.accept() # This is QDialog.accept() which closes the dialog with Accepted code

# --- display_review_and_edit_notes_dialog ---
def display_review_and_edit_notes_dialog(
    notes_data_list: List[NoteData],
    current_mw_ref: Optional[QMainWindow] = None,
    parent_widget_ref: Optional[QWidget] = None
) -> List[NoteData]: # Return type changed to List[NoteData]
    effective_mw_ref = current_mw_ref or mw
    logger = get_logger()

    if not notes_data_list:
        logger.info("display_review_and_edit_notes_dialog: No notes data provided to review.")
        return []

    dialog = ReviewAndEditNotesDialog(
        notes_data_list=notes_data_list,
        current_mw_ref=effective_mw_ref,
        parent=parent_widget_ref
    )

    if dialog.exec() == QDialog.DialogCode.Accepted:
        logger.debug("ReviewAndEditNotesDialog accepted. Returning edited notes.")
        return dialog.edited_notes_data
    else:
        logger.debug("ReviewAndEditNotesDialog cancelled or closed without accepting.")
        return [] # Return empty list if cancelled

# --- JsonInputDialog ---
class JsonInputDialog(QDialog):
    def __init__(self, parent: Optional[QWidget]=None, current_mw_ref: Optional[QMainWindow]=None):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw # Use consistent naming
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
        self._setup_choosers() # Renamed
        layout.addWidget(self.note_type_combo_widget)
        layout.addWidget(self.deck_combo_widget)

        button_row_layout = QHBoxLayout()
        self.insert_template_button_widget = QPushButton("Insert JSON Template")
        self.insert_template_button_widget.clicked.connect(self.insert_json_template_handler)
        button_row_layout.addWidget(self.insert_template_button_widget)
        button_row_layout.addStretch()
        
        self.dialog_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.dialog_buttons.accepted.connect(self.accept)
        self.dialog_buttons.rejected.connect(self.reject)
        button_row_layout.addWidget(self.dialog_buttons)
        layout.addLayout(button_row_layout)


    def _setup_choosers(self) -> None: # Renamed
        defaults = self.mw_instance.col.defaults_for_adding(current_review_card=self.mw_instance.reviewer.card if self.mw_instance.reviewer else None)
        self.notetype_chooser_instance = NotetypeChooser(
            mw=self.mw_instance, widget=self.note_type_combo_widget, starting_notetype_id=NotetypeId(defaults.notetype_id)
        )
        self.deck_chooser_instance = DeckChooser(
            mw=self.mw_instance, widget=self.deck_combo_widget, starting_deck_id=DeckId(defaults.deck_id)
        )

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

    def _cleanup_choosers(self): # Added helper
        if not self._close_event_has_cleaned_up:
            if hasattr(self, 'notetype_chooser_instance') and self.notetype_chooser_instance:
                self.notetype_chooser_instance.cleanup()
            if hasattr(self, 'deck_chooser_instance') and self.deck_chooser_instance:
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
    config = get_config() # Get current config
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
            logger.error(f"JSON needs to be a list of dicts, was: {type(notes_json_list)}")
            showInfo("JSON data is not in the expected format (list of dictionaries).", parent=current_mw_ref)
            return
        if not notes_json_list:
            logger.info("No cards found in JSON, returning.")
            showInfo("No card data found in the JSON input.", parent=current_mw_ref)
            return

        json_tag_val = config.get("json_tag", "from_json")
        note_type_model = current_mw_ref.col.models.get(note_type_id)
        if not note_type_model:
            logger.error(f"Could not load note type model for ID: {note_type_id}")
            showInfo(f"Error: Could not load note type for ID {note_type_id}.", parent=current_mw_ref)
            return
        dummy_note_template = Note(current_mw_ref.col, note_type_model)
        
        confirmed_cards_map = display_cards_confirmation_dialog(
            cards_data_map={dummy_note_template: notes_json_list}, 
            global_tags=[json_tag_val],
            current_mw_ref=current_mw_ref
        )

        if not confirmed_cards_map:
            showInfo("No cards confirmed for import.", parent=current_mw_ref)
            return
        
        from .core import ZikariaPrompts # Import locally to avoid circularity at module level
        core_processor = ZikariaPrompts(mw=current_mw_ref, manual_execution=True)
        notes_added_count = 0
        for _note_tmpl, cards_to_add in confirmed_cards_map.items():
            # Call add_new_notes from the core processor
            # original_note, cards_data, deck_id=None, note_type=None, note_tag=None
            core_processor.add_new_notes(
                original_note=dummy_note_template, # Used as template
                cards_data_list=cards_to_add,
                deck_id_override=deck_id,
                note_type_override=note_type_model,
                default_tags=[] # Tags are already in cards_to_add from confirmation dialog
            )
            notes_added_count += len(cards_to_add)


        if notes_added_count > 0:
            showInfo(f"{notes_added_count} notes added from JSON.", parent=current_mw_ref)
            logger.info(f"Processed JSON input for deck ID {deck_id}, note type ID {note_type_id}. Added {notes_added_count} notes.")

# --- DebugDialog ---
class DebugDialog(QDialog):
    def __init__(self, note: Note, parent: Optional[QWidget]=None, current_mw_ref: Optional[QMainWindow]=None):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw
        self.note = note
        self.logger = get_logger() # Use accessor
        self.config = get_config() # Use accessor
        self.setWindowTitle("Debug Note (Zikaria)")
        self.resize(800, 1000)
        
        from .config_utils import get_effective_config # Import here
        self.effective_config = get_effective_config(self.note.mid, self.note.cards()[0].did if self.note.cards() else None)

        layout = QVBoxLayout(self)
        # ... (rest of DebugDialog implementation using self.logger, self.config, self.effective_config, self.mw_instance)
        # Ensure generate_schema_class, get_create_prompt, get_complete_prompt, example_notes are called correctly.
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Schema Tab
        self.schema_output_area = QTextEdit()
        self.schema_output_area.setReadOnly(True)
        schema_tab_widget = QWidget()
        schema_layout = QVBoxLayout(schema_tab_widget)
        # Add a button to refresh schema, useful if something changes
        schema_refresh_button = QPushButton("Refresh Schema")
        schema_refresh_button.clicked.connect(self.display_schema_handler)
        schema_layout.addWidget(schema_refresh_button)
        schema_layout.addWidget(self.schema_output_area)
        self.tabs.addTab(schema_tab_widget, "Schema")

        # Create Prompt Tab
        self.create_prompt_output_area = QTextEdit()
        self.create_prompt_output_area.setReadOnly(True)
        create_prompt_tab_widget = QWidget()
        create_prompt_layout = QVBoxLayout(create_prompt_tab_widget)
        # Add a button to refresh, useful if something changes
        create_refresh_button = QPushButton("Refresh Create Prompt")
        create_refresh_button.clicked.connect(self.display_create_prompt_handler)
        create_prompt_layout.addWidget(create_refresh_button)
        create_prompt_layout.addWidget(self.create_prompt_output_area)
        self.tabs.addTab(create_prompt_tab_widget, "Create Prompt")

        # Complete Prompt Tab
        self.complete_prompt_output_area = QTextEdit()
        self.complete_prompt_output_area.setReadOnly(True)
        complete_prompt_tab_widget = QWidget()
        complete_prompt_layout = QVBoxLayout(complete_prompt_tab_widget)
        # Add a button to refresh, useful if something changes
        complete_refresh_button = QPushButton("Refresh Complete Prompt")
        complete_refresh_button.clicked.connect(self.display_complete_prompt_handler)
        complete_prompt_layout.addWidget(complete_refresh_button)
        complete_prompt_layout.addWidget(self.complete_prompt_output_area)
        self.tabs.addTab(complete_prompt_tab_widget, "Complete Prompt")
        
        # Examples Tab
        self.examples_output_area = QTextEdit()
        self.examples_output_area.setReadOnly(True)
        examples_tab_widget = QWidget()
        examples_layout = QVBoxLayout(examples_tab_widget)
        # Add a button to refresh, useful if something changes
        examples_refresh_button = QPushButton("Refresh Examples")
        examples_refresh_button.clicked.connect(self.display_examples_handler)
        examples_layout.addWidget(examples_refresh_button)
        examples_layout.addWidget(self.examples_output_area)
        self.tabs.addTab(examples_tab_widget, "Examples")

        # Initial population of the tabs
        self.display_schema_handler()
        self.display_create_prompt_handler()
        self.display_complete_prompt_handler()
        self.display_examples_handler()

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def display_schema_handler(self):
        self.logger.debug("DebugDialog: Displaying schema.")
        note_model = self.note.note_type()
        if not note_model:
            self.schema_output_area.setPlainText("Error: Note has no model (note type).")
            self.logger.error("DebugDialog: Note has no model (note type).")
            return
        try:
            schema = generate_schema_class(note_model)
            self.schema_output_area.setPlainText(json.dumps(schema, indent=2, ensure_ascii=False))
        except Exception as e:
            self.schema_output_area.setPlainText(f"Error generating schema: {e}")
            self.logger.exception("DebugDialog: Error generating schema.")

    def display_create_prompt_handler(self):
        self.logger.debug("DebugDialog: Displaying create prompt.")
        try:
            prompt = get_create_prompt(self.note, self.effective_config)
            self.create_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.create_prompt_output_area.setPlainText(f"Error generating create prompt: {e}")
            self.logger.exception("DebugDialog: Error generating create prompt.")

    def display_complete_prompt_handler(self):
        self.logger.debug("DebugDialog: Displaying complete prompt.")
        try:
            prompt = get_complete_prompt(self.note, self.effective_config)
            self.complete_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.complete_prompt_output_area.setPlainText(f"Error generating complete prompt: {e}")
            self.logger.exception("DebugDialog: Error generating complete prompt.")

    def display_examples_handler(self):
        self.logger.debug("DebugDialog: Displaying examples.")
        try:
            note_type = self.note.note_type()
            if not note_type:
                self.examples_output_area.setPlainText("Error: Note has no model (note type) to generate examples from.")
                self.logger.error("DebugDialog: Note has no model (note type) for examples.")
                return

            # Ensure effective_config is a dict, as expected by example_notes
            config_for_examples = self.effective_config if isinstance(self.effective_config, dict) else {}
            if not isinstance(self.effective_config, dict):
                 self.logger.warning(f"DebugDialog: effective_config was not a dict ({type(self.effective_config)}), using empty dict for example_notes.")

            example_count = config_for_examples.get("example_notes_count", 3) # Default to 3 for debug dialog

            examples_str = example_notes(note_type, n=example_count)
            self.examples_output_area.setPlainText(examples_str)
        except Exception as e:
            self.examples_output_area.setPlainText(f"Error generating examples: {e}")
            self.logger.exception("DebugDialog: Error generating examples.")

# --- PromptFromTextDialog ---
class PromptFromTextDialog(QDialog):
    def __init__(self, parent: Optional[QWidget]=None, current_mw_ref: Optional[QMainWindow]=None):
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
        self._setup_choosers_promptdialog() # Renamed
        self.layout.addWidget(self.note_type_combo_widget)
        self.layout.addWidget(self.deck_combo_widget)
        
        self.prompt_input_area = QTextEdit()
        self.prompt_input_area.setPlaceholderText("Enter a list of words or a sentence...")
        self.layout.addWidget(QLabel("Prompt Text:"))
        self.layout.addWidget(self.prompt_input_area)

        self.generate_prompt_button = QPushButton("Generate Full Prompt")
        self.generate_prompt_button.clicked.connect(self.generate_full_prompt_handler)
        self.layout.addWidget(self.generate_prompt_button)

        self.prompt_edit_area = QTextEdit() # For the full generated prompt
        self.prompt_edit_area.setPlaceholderText("Prompt preview will appear here...")
        self.layout.addWidget(QLabel("Edit Prompt:"))
        self.layout.addWidget(self.prompt_edit_area)

        self.submit_ai_button = QPushButton("Send to AI")
        self.submit_ai_button.clicked.connect(self.send_prompt_to_ai_handler)
        self.layout.addWidget(self.submit_ai_button)
        
        self.global_tag_edit_widget = TagEdit(self)
        self.global_tag_edit_widget.setCol(self.mw_instance.col)
        self.layout.addWidget(self.global_tag_edit_widget)
        if note_tag_val := self.config.get("note_tag"): # Use self.config
            self.global_tag_edit_widget.setText(note_tag_val)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)


    def _setup_choosers_promptdialog(self) -> None: # Renamed
        defaults = self.mw_instance.col.defaults_for_adding(current_review_card=self.mw_instance.reviewer.card if self.mw_instance.reviewer else None)
        self.notetype_chooser_instance = NotetypeChooser(
            mw=self.mw_instance, widget=self.note_type_combo_widget, starting_notetype_id=NotetypeId(defaults.notetype_id)
        )
        self.deck_chooser_instance = DeckChooser(
            mw=self.mw_instance, widget=self.deck_combo_widget, starting_deck_id=DeckId(defaults.deck_id)
        )

    def generate_full_prompt_handler(self):
        self.logger.debug("Generating full prompt in PromptFromTextDialog.")
        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        note_model = self.mw_instance.col.models.get(note_type_id)
        if not note_model:
            self.status_label.setText("Error: Could not load note type.")
            return
            
        dummy_note = Note(self.mw_instance.col, note_model)
        dummy_note.fields[0] = self.prompt_input_area.toPlainText()
        
        from .config_utils import get_effective_config # Local import
        current_deck_id = self.deck_chooser_instance.selected_deck_id
        # Pass the global config from get_config()
        effective_config = get_effective_config(note_type_id, current_deck_id) 
        
        try:
            prompt_text = get_create_prompt(dummy_note, effective_config) # from prompts.py
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

        if not configure_generative_ai(): # From anki_utils
            self.status_label.setText("Failed to configure AI connection (API key?).")
            showInfo("Failed to configure AI. Please check your API key in the Addon Configuration.", parent=self)
            return

        self.status_label.setText("Sending prompt to AI...")
        QApplication.processEvents()
        
        from .core import ZikariaPrompts # Local import
        core_processor = ZikariaPrompts(mw=self.mw_instance, manual_execution=True)
        
        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        current_deck_id = self.deck_chooser_instance.selected_deck_id
        from .config_utils import get_effective_config # Local import
        effective_config = get_effective_config(note_type_id, current_deck_id)

        try:
            response = core_processor.send_prompt_to_generative_ai(full_prompt_text, custom_config=effective_config)
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
            self.logger.error(f"Error decoding AI JSON response: {e}\n\n{response.text}", exc_info=True)
            self.status_label.setText("Error decoding AI JSON response.")
            showInfo(f"Could not understand the AI's response (not valid JSON).\nDetails: {e}\nResponse:\n{response.text[:500]}...", parent=self)
            return

        if not is_valid_cards_data(cards_data_list):
            self.status_label.setText("AI response is not a valid card list.")
            showInfo("The AI's response was not a valid list of cards.", parent=self)
            return
        
        note_model = self.mw_instance.col.models.get(self.notetype_chooser_instance.selected_notetype_id)
        if not note_model:
            self.status_label.setText("Error: Selected note type not found.")
            return
        dummy_note_template = Note(self.mw_instance.col, note_model)
        global_tags_list = self.mw_instance.col.tags.split(self.global_tag_edit_widget.text())

        confirmed_cards_map = display_cards_confirmation_dialog(
            cards_data_map={dummy_note_template: cards_data_list},
            global_tags=global_tags_list,
            current_mw_ref=self.mw_instance,
            parent=self
        )

        if not confirmed_cards_map:
            self.status_label.setText("No cards confirmed for import.")
            return

        notes_added_count = 0
        for _template, cards_to_add in confirmed_cards_map.items():
            core_processor.add_new_notes( # Use core_processor instance
                original_note=dummy_note_template,
                cards_data_list=cards_to_add,
                deck_id_override=self.deck_chooser_instance.selected_deck_id,
                note_type_override=note_model,
                default_tags=[]
            )
            notes_added_count += len(cards_to_add)
        
        self.status_label.setText(f"{notes_added_count} cards added successfully.")
        tooltip(f"{notes_added_count} cards added.", parent=self.mw_instance)

    def closeEvent(self, event):
        if hasattr(self, 'notetype_chooser_instance') and self.notetype_chooser_instance:
            self.notetype_chooser_instance.cleanup()
        if hasattr(self, 'deck_chooser_instance') and self.deck_chooser_instance:
            self.deck_chooser_instance.cleanup()
        super().closeEvent(event)

# --- open_prompt_text_dialog_action ---
def open_prompt_text_dialog_action(current_mw_ref: QMainWindow):
    dialog = PromptFromTextDialog(current_mw_ref=current_mw_ref)
    dialog.exec()

# --- ConfigDialog & CustomConfigDialog ---
class ConfigDialog(QDialog):
    def __init__(self, parent: Optional[QWidget]=None, config_data_override: Optional[Dict]=None, current_mw_ref: Optional[QMainWindow]=None, addon_name_param: str = ""):
        super().__init__(parent or current_mw_ref or mw)
        self.mw_instance = current_mw_ref or mw # Use consistent naming
        self.logger = get_logger()
        self.resize(1200, 1000)

        self.addon_name = addon_name_param
        if not self.addon_name: # Fallback if not provided, though __init__.py should provide it
            self.addon_name = get_addon_name() 
            self.logger.warning("ConfigDialog initialized without addon_name_param, using get_addon_name().")

        current_addon_config_obj = get_config() # Get the live config object
        self.config_to_edit = deepcopy(current_addon_config_obj) # Edit a deep copy

        self.setWindowTitle(f"Zikaria Addon Configuration ({self.addon_name})")
        # ... (rest of ConfigDialog UI setup as before, using self.mw_instance, self.logger, self.addon_name)
        self.main_layout = QHBoxLayout()
        self.setStyleSheet("QWidget { font-size: 16px; }") # Example style

        self.form_layout = QFormLayout()
        self.form_widget = QWidget()
        self.form_widget.setLayout(self.form_layout)
        self.main_layout.addWidget(self.form_widget)

        self._setup_docs_panel()
        self.config_widgets_map = {} # Renamed
        self.extra_config_data_map = {} # Renamed

        self._init_form_elements() # Renamed
        self.setLayout(self.main_layout)


    def _setup_docs_panel(self): # Renamed
        self.docs_panel_widget = QTextEdit()
        self.docs_panel_widget.setReadOnly(True)
        if self.mw_instance.addonManager:
            docs_path = os.path.join(self.mw_instance.addonManager.addonsFolder(), self.addon_name, "config.md")
            if os.path.exists(docs_path):
                with open(docs_path, "r", encoding="utf-8") as f:
                    self.docs_panel_widget.setMarkdown(f.read())
            else:
                self.docs_panel_widget.setMarkdown(f"# Configuration Documentation\n`config.md` not found at `{docs_path}`.")
        else:
            self.docs_panel_widget.setMarkdown("# Configuration Documentation\nAddon manager not available.")
        self.main_layout.addWidget(self.docs_panel_widget)

    def _init_form_elements(self): # Renamed
        default_config_keys = {}

        if self.mw_instance.addonManager:
            default_config_keys = self.mw_instance.addonManager.addonConfigDefaults(self.addon_name) or {}

        handled_keys = set()
        for key, default_value in default_config_keys.items():
            current_value = self.config_to_edit.get(key, default_value)
            if key == "custom_config": 
                handled_keys.add(key)
                continue
            
            widget_data = self._create_config_widget_ui(key, current_value) # Renamed
            if widget_data:
                self.form_layout.addRow(widget_data["label"], widget_data["widget"])
                self.config_widgets_map[key] = widget_data # Use renamed map
                handled_keys.add(key)
        
        self._add_remaining_config_ui_elements(handled_keys) # Renamed
        self._add_custom_config_ui_section() # Renamed
        self._add_action_buttons() # Renamed

    def _create_config_widget_ui(self, name: str, value: Any) -> Optional[Dict[str, Any]]: # Renamed
        self.logger.warning(f"ConfigDialog: Widget creation not fully implemented in this snippet for type {type(value)} (key: {name}).")
        """Create and add a widget for the given config key."""
        change_event = None
        if isinstance(value, bool):
            widget = QCheckBox()
            widget.setChecked(value)
            label = f"{name.replace('_', ' ').title()}:"
            get_fn = widget.isChecked
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
            return

        return {
            "widget": widget,
            "label": label,
            "row": widget,
            "get_fn": get_fn,
            "change_event": change_event,
        }

    def _add_remaining_config_ui_elements(self, handled_keys_set: set): # Renamed
        self.logger.debug("ConfigDialog: Adding UI for remaining configuration values (those not explicitly handled).")
        
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

    def _add_custom_config_ui_section(self): # Renamed
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
        self._update_custom_configs_display() # Changed from update_custom_configs_ui

        add_custom_config_button = QPushButton("Add Custom Config")
        add_custom_config_button.clicked.connect(
            lambda: self._add_or_edit_custom_config_entry() # Corrected method name
        )
        self.form_layout.addRow(add_custom_config_button)

    def _update_custom_configs_display(self): # Renamed
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
        self.custom_configs_content_grid_layout.addWidget(header_actions_label, 0, 2, 1, 2) # Span 2 columns for buttons

        custom_configs_list = self.config_to_edit.get("custom_config", [])
        self.logger.debug(f"Found {len(custom_configs_list)} custom configs to display.")

        for idx, custom_config_entry in enumerate(custom_configs_list):
            note_type_id, deck_id, settings = custom_config_entry # settings is a dict

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
            self.custom_configs_content_grid_layout.addWidget(note_type_display_label, idx + 1, 0)
            self.custom_configs_content_grid_layout.addWidget(deck_display_label, idx + 1, 1)
            self.custom_configs_content_grid_layout.addWidget(edit_button, idx + 1, 2)
            self.custom_configs_content_grid_layout.addWidget(delete_button, idx + 1, 3)

    def _add_or_edit_custom_config_entry(self, index: Optional[int] = None): # Renamed
        self.logger.debug(f"ConfigDialog: Add/Edit custom config entry at index {index}.")

        custom_configs_list = self.config_to_edit.get("custom_config", [])
        note_type_id_param: Optional[NotetypeId] = None
        deck_id_param: Optional[DeckId] = None
        custom_settings_data: Dict[str, Any] = {}

        if index is not None and index < len(custom_configs_list):
            entry = custom_configs_list[index]
            note_type_id_param = NotetypeId(entry[0]) if entry[0] else None
            deck_id_param = DeckId(entry[1]) if entry[1] else None
            custom_settings_data = entry[2] # This is the dictionary of settings
            self.logger.debug(f"Editing entry: NTID={note_type_id_param}, DID={deck_id_param}, Settings={custom_settings_data}")
        else:
            self.logger.debug("Creating new custom config entry.")

        dialog = CustomConfigDialog(
            parent_dialog=self,
            note_type_id_param=note_type_id_param,
            deck_id_param=deck_id_param,
            custom_settings_data=custom_settings_data, # Pass the dict here
            current_mw_ref=self.mw_instance,
            addon_name_param=self.addon_name
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
                new_settings
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
                self.logger.error(f"Error updating custom config: index {index} out of bounds for list of len {len(current_custom_configs)}")

            self._update_custom_configs_display()
        else:
            self.logger.debug("CustomConfigDialog cancelled or closed.")


    def _delete_custom_config_entry(self, index: int): # Renamed
        """Delete a custom configuration."""
        self.logger.debug(f"ConfigDialog: Attempting to delete custom config at index {index}.")
        custom_configs_list = self.config_to_edit.get("custom_config", [])
        if 0 <= index < len(custom_configs_list):
            custom_configs_list.pop(index)
            self.logger.info(f"Deleted custom config entry at index {index}.")
            self._update_custom_configs_display()
        else:
            self.logger.warning(f"Could not delete custom config at index {index}: index out of bounds.")

    def _add_action_buttons(self): # Renamed
        button_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_configuration_handler) # Renamed
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        self.form_layout.addRow(button_layout)

    def save_configuration_handler(self): # Renamed
        self.logger.info(f"Saving configuration for addon '{self.addon_name}'.")
        updated_data_from_widgets = {}
        for key, data in self.config_widgets_map.items():
            if data['get_fn']:
                updated_data_from_widgets[key] = data['get_fn']()
        
        # Update the copy we are editing
        self.config_to_edit.update(updated_data_from_widgets)
        # ... (Handle custom_config and extra_config from their UI elements) ...

        if self.mw_instance.addonManager:
            self.mw_instance.addonManager.writeConfig(self.addon_name, self.config_to_edit)
        
        # Update the live global config object used by the addon
        live_config = get_config() # Get the actual global config object
        live_config.clear()
        live_config.update(self.config_to_edit) # Update its content
        
        # Update logger level if debug status changed
        new_debug_level = logging.DEBUG if live_config.get("debug", False) else logging.INFO
        if self.logger.level != new_debug_level:
             self.logger.setLevel(new_debug_level)
             self.logger.info(f"Log level updated to {logging.getLevelName(new_debug_level)}.")
        
        self.logger.info(f"Configuration for '{self.addon_name}' saved and applied.")
        self.accept()

# --- CustomConfigDialog ---
class CustomConfigDialog(ConfigDialog): # Inherits from ConfigDialog
    def __init__(self, parent_dialog: ConfigDialog,
                 note_type_id_param: Optional[NotetypeId]=None, deck_id_param: Optional[DeckId]=None,
                 custom_settings_data: Optional[Dict[str, Any]]=None,
                 current_mw_ref: Optional[QMainWindow]=None, addon_name_param: str=""):
        
        # Initialize with a deepcopy of the specific custom_settings_data for editing.
        # The superclass ConfigDialog's self.config_to_edit will be this specific dictionary.
        super().__init__(parent=parent_dialog,
                         config_data_override=deepcopy(custom_settings_data) if custom_settings_data is not None else {},
                         current_mw_ref=current_mw_ref,
                         addon_name_param=addon_name_param)

        self.setWindowTitle(f"Edit Custom Configuration for {self.addon_name}")
        self.note_type_id_param = note_type_id_param
        self.deck_id_param = deck_id_param

        # These will store the results when the dialog is accepted
        self.note_type_id_result: Optional[NotetypeId] = self.note_type_id_param
        self.deck_id_result: Optional[DeckId] = self.deck_id_param

        self._close_event_has_cleaned_up = False # For chooser cleanup

        # self.config_to_edit is already a deepcopy from super().__init__
        # if custom_settings_data is None, self.config_to_edit will be an empty dict {}
        # which is the desired behavior for a new custom config.

    def _init_form_elements(self): # Override to customize form for custom config
        self.logger.debug(f"CustomConfigDialog: Initializing form elements. Editing: {self.config_to_edit}")
        # Do not call super()._init_form_elements() as we have a different layout.
        self.config_widgets_map.clear() # Ensure it's empty

        # Chooser for Note Type
        self.note_type_combo_widget = QWidget(self) # Parent for NotetypeChooser's layout
        self.notetype_chooser_instance = NotetypeChooser(
            mw=self.mw_instance,
            widget=self.note_type_combo_widget,
            starting_notetype_id=self.note_type_id_param
        )
        self.form_layout.addRow(QLabel("Note Type:"), self.note_type_combo_widget)

        # Chooser for Deck
        self.deck_combo_widget = QWidget(self) # Parent for DeckChooser's layout
        self.deck_chooser_instance = DeckChooser(
            mw=self.mw_instance,
            widget=self.deck_combo_widget,
            starting_deck_id=self.deck_id_param,
            show_all_decks_button=True # Allow "Any Deck" concept if DeckChooser supports None
        )
        self.form_layout.addRow(QLabel("Deck:"), self.deck_combo_widget)

        # Define overridable keys
        overridable_keys = [
            "create_prompt", "complete_prompt", "model_temperature", "model_name",
            "max_output_tokens", "note_tag", "processed_tag", "prompt_tag",
            "custom_prompt_tag", "complete_tag", "json_tag"
        ]

        global_defaults = self.mw_instance.addonManager.addonConfigDefaults(self.addon_name) or {}

        for key in overridable_keys:
            # Value from existing custom config, or global default if not set
            current_value = self.config_to_edit.get(key, global_defaults.get(key))

            widget_data = self._create_config_widget_ui(key, current_value)
            if widget_data:
                # Optional: Add a checkbox to indicate if this setting is overridden or inherited
                # For now, any value set/present in the dialog will be saved as an override.
                self.form_layout.addRow(widget_data["label"], widget_data["widget"])
                self.config_widgets_map[key] = widget_data

        self._add_action_buttons() # Adds Save/Cancel buttons

    def save_configuration_handler(self): # Override for custom config saving
        self.logger.info(f"CustomConfigDialog: Saving custom configuration for {self.addon_name}.")

        # Update results from choosers
        self.note_type_id_result = self.notetype_chooser_instance.selected_notetype_id
        self.deck_id_result = self.deck_chooser_instance.selected_deck_id

        # The inherited save_configuration_handler from ConfigDialog updates self.config_to_edit
        # by iterating self.config_widgets_map. This is what we want.
        # However, the superclass method also writes to addonManager and updates global config,
        # which is NOT what we want here. We only want to populate self.config_to_edit.

        updated_data_from_widgets = {}
        for key, data in self.config_widgets_map.items():
            if data.get('get_fn'): # Check if get_fn exists
                try:
                    updated_data_from_widgets[key] = data['get_fn']()
                except Exception as e:
                    self.logger.error(f"Error getting value for key {key}: {e}", exc_info=True)

        self.config_to_edit.update(updated_data_from_widgets)
        # self.config_to_edit now contains the settings for this specific custom config.
        # No need to call mw.addonManager.writeConfig here, that's for the main config dialog.

        self.logger.debug(f"CustomConfigDialog: Updated config_to_edit: {self.config_to_edit}")
        self.accept() # Closes the dialog with QDialog.DialogCode.Accepted

    def _cleanup_choosers(self):
        if not self._close_event_has_cleaned_up:
            self.logger.debug("CustomConfigDialog: Cleaning up choosers.")
            if hasattr(self, 'notetype_chooser_instance') and self.notetype_chooser_instance:
                self.notetype_chooser_instance.cleanup()
                self.logger.debug("Cleaned up notetype_chooser_instance.")
            if hasattr(self, 'deck_chooser_instance') and self.deck_chooser_instance:
                self.deck_chooser_instance.cleanup()
                self.logger.debug("Cleaned up deck_chooser_instance.")
            self._close_event_has_cleaned_up = True

    def accept(self) -> None:
        self.logger.debug("CustomConfigDialog: Accepted.")
        self._cleanup_choosers()
        super().accept() # Call QDialog.accept()

    def reject(self) -> None:
        self.logger.debug("CustomConfigDialog: Rejected.")
        self._cleanup_choosers()
        super().reject() # Call QDialog.reject()

    def closeEvent(self, event): # Ensure cleanup on any close
        self.logger.debug("CustomConfigDialog: closeEvent triggered.")
        self._cleanup_choosers()
        super().closeEvent(event)

# --- show_config_dialog_action ---
def show_config_dialog_action(current_mw_ref: QMainWindow, addon_name_str: str = None):
    logger = get_logger()
    logger.debug(f"Showing config dialog for addon: {addon_name_str}")
    dialog = ConfigDialog(current_mw_ref=current_mw_ref, addon_name_param=addon_name_str)
    dialog.exec()

# --- Action functions for menu items (browser related) ---
def add_debug_menu_to_browser_action(browser_instance: Browser):
    logger = get_logger()
    config = get_config()
    if config.get("debug", False): 
        action = QAction("Zikaria Debug Note", browser_instance)
        action.triggered.connect(lambda: _debug_selected_note_browser_action(browser_instance))
        
        # Ensure menu exists before adding
        if not hasattr(browser_instance.form, "menuZikaria"):
            browser_instance.form.menuZikaria = browser_instance.form.menuEdit.addMenu("Zikaria")
        browser_instance.form.menuZikaria.addAction(action)
        logger.debug("Added Zikaria debug menu to browser.")

def _debug_selected_note_browser_action(browser_instance: Browser): # Helper
    logger = get_logger()
    selected_nids = browser_instance.selected_notes()
    if len(selected_nids) != 1:
        showInfo("Please select exactly one note for Zikaria debugging.", parent=browser_instance)
        return
    note_object = browser_instance.mw.col.get_note(selected_nids[0])
    if note_object:
        logger.debug(f"Opening DebugDialog for note ID: {note_object.id}")
        dialog = DebugDialog(note_object, parent=browser_instance, current_mw_ref=browser_instance.mw)
        dialog.exec()

def on_browser_context_menu_action(browser_instance: Browser, menu: QWidget): # menu is QMenu
    logger = get_logger()
    config = get_config()
    if not config.get("debug", False): return

    selected_nids = browser_instance.selected_notes()
    if len(selected_nids) == 1: # Only for single selection for simplicity
        note_object = browser_instance.mw.col.get_note(selected_nids[0])
        if note_object:
            action = QAction("Zikaria Debug Note...", menu)
            action.triggered.connect(lambda: _debug_selected_note_browser_action(browser_instance))
            menu.addAction(action)
            logger.debug("Added Zikaria debug context menu action.")
    elif not selected_nids:
        logger.debug("No notes selected, not adding Zikaria debug context menu.")
    else: # Multiple notes selected
        logger.debug("Multiple notes selected, Zikaria debug context menu not added for this case.")
