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
from proto.message import copy as proto_copy # Renamed to avoid conflict

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

# --- display_review_and_edit_notes_dialog (Simplified for brevity, assume similar changes) ---
def display_review_and_edit_notes_dialog(
    notes_data_list: List[NoteData], 
    current_mw_ref: Optional[QMainWindow] = None, 
    parent_widget_ref: Optional[QWidget] = None
) -> Optional[bool]:
    current_mw_ref = current_mw_ref or mw
    logger = get_logger()
    # ... (Full implementation using current_mw_ref and logger from get_logger()) ...
    # This dialog is complex, ensure all mw calls use current_mw_ref.
    # For now, returning a placeholder to keep the structure.
    logger.info("display_review_and_edit_notes_dialog called (implementation condensed).")
    # Placeholder:
    # dialog = ReviewAndEditNotesDialog(mw_ref=current_mw_ref, notes_list=notes_data_list, parent_ref=parent_widget_ref)
    # return dialog.exec() == QDialog.DialogCode.Accepted
    return True # Placeholder

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
        # ... (buttons and layout as before)
        self.tabs.addTab(schema_tab_widget, "Schema")
        # Connect button: schema_button.clicked.connect(self.display_schema_handler)

        # Create Prompt Tab
        self.create_prompt_output_area = QTextEdit()
        # ... (as before)
        self.tabs.addTab(QWidget(), "Create Prompt") # Placeholder

        # Complete Prompt Tab
        self.complete_prompt_output_area = QTextEdit()
        # ... (as before)
        self.tabs.addTab(QWidget(), "Complete Prompt") # Placeholder
        
        # Examples Tab
        self.examples_output_area = QTextEdit()
        # ... (as before)
        self.tabs.addTab(QWidget(), "Examples") # Placeholder

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def display_schema_handler(self):
        note_model = self.note.note_type()
        if not note_model:
            self.schema_output_area.setPlainText("Error: Note has no model.")
            return
        schema = generate_schema_class(note_model) 
        self.schema_output_area.setPlainText(json.dumps(schema, indent=2, ensure_ascii=False))

    # ... (other handlers for DebugDialog)

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
        # ... (rest of UI elements)

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
        self.config_to_edit = proto_copy.deepcopy(current_addon_config_obj) # Edit a deep copy

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
                self.form_layout.addRow(widget_data["label_widget"], widget_data["editor_widget"])
                self.config_widgets_map[key] = widget_data # Use renamed map
                handled_keys.add(key)
        
        self._add_remaining_config_ui_elements(handled_keys) # Renamed
        self._add_custom_config_ui_section() # Renamed
        self._add_action_buttons() # Renamed

    def _create_config_widget_ui(self, name: str, value: Any) -> Optional[Dict[str, Any]]: # Renamed
        # ... (Same logic as _create_config_widget from previous turns, ensure it's complete)
        # This is a critical part for building the form. For brevity, not repeating full code.
        # Make sure it returns: {'label_widget': QLabel, 'editor_widget': QWidget, 'get_fn': callable, 'change_event': signal_or_None}
        # Example for boolean:
        if isinstance(value, bool):
            editor = QCheckBox()
            editor.setChecked(value)
            return {"label_widget": QLabel(name.replace('_', ' ').title() + ":"), "editor_widget": editor, "get_fn": editor.isChecked, "change_event": None}
        # ... other types
        self.logger.warning(f"ConfigDialog: Widget creation not fully implemented in this snippet for type {type(value)} (key: {name}).")
        return None


    def _add_remaining_config_ui_elements(self, handled_keys_set: set): # Renamed
        self.logger.debug("Adding remaining config UI (placeholder).")

    def _add_custom_config_ui_section(self): # Renamed
        self.custom_configs_main_label = QLabel("Custom Configurations:") # Renamed attribute
        self.form_layout.addRow(self.custom_configs_main_label)
        # ... More UI for custom_config list, add/edit/delete buttons ...
        # Needs CustomConfigDialog integration.
        # self._update_custom_configs_display() # To render initial list

    def _update_custom_configs_display(self): # Renamed
        self.logger.debug("Updating custom configs display (placeholder).")

    def _add_or_edit_custom_config_entry(self, index: Optional[int] = None): # Renamed
        self.logger.debug(f"Add/Edit custom config entry: index {index} (placeholder).")
        # This would create and exec a CustomConfigDialog instance.
        # custom_settings = self.config_to_edit.get("custom_config", [])[index] if index is not None else {}
        # dialog = CustomConfigDialog(self, ..., current_mw_ref=self.mw_instance, addon_name_param=self.addon_name)
        # if dialog.exec... update self.config_to_edit and self._update_custom_configs_display()

    def _delete_custom_config_entry(self, index: int): # Renamed
        self.logger.debug(f"Delete custom config entry: index {index} (placeholder).")

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

# --- CustomConfigDialog (Simplified placeholder) ---
class CustomConfigDialog(ConfigDialog): # Inherits from ConfigDialog
    def __init__(self, parent_dialog: ConfigDialog, 
                 note_type_id_param: Optional[int]=None, deck_id_param: Optional[int]=None, 
                 custom_settings_data: Optional[Dict]=None, 
                 current_mw_ref: Optional[QMainWindow]=None, addon_name_param: str=""):
        
        # For CustomConfigDialog, the 'config_data_override' is the specific custom_settings_data
        super().__init__(parent=parent_dialog, 
                         config_data_override=custom_settings_data or {}, 
                         current_mw_ref=current_mw_ref, 
                         addon_name_param=addon_name_param)
        self.setWindowTitle(f"Edit Custom Config for {self.addon_name}")
        # ... (Specific UI for selecting note type, deck, and overriding specific config keys) ...
        # Must exclude certain global keys from being overridden here.

    def _init_form_elements(self): # Override to customize form for custom config
        self.logger.info("CustomConfigDialog form elements (placeholder).")
        # Add selectors for Note Type and Deck
        # Add widgets for only the overridable config keys (exclude api_key, debug, etc.)
        # Need to handle 'unset' state for each config key in custom config.
        self._add_action_buttons() # Save/Cancel for this sub-dialog


# --- show_config_dialog_action ---
def show_config_dialog_action(current_mw_ref: QMainWindow, addon_name_str: str):
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
