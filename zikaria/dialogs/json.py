import json
from typing import Any, Dict

from anki.notes import Note
from aqt import mw

# from aqt.operations.note import add_note
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo

# Imports from other modules in this addon
from ..config_utils import config, get_effective_config, logger
from ..prompts import generate_pydantic_class
from ..types import NotesDataList, ZikariaRequestData, ZikariaResponseData
from ..ui import ChoosersMixin

# from aqt.editor import Editor, EditorMode


# --- JsonInputDialog ---
class JsonInputDialog(QDialog, ChoosersMixin):
    def __init__(
        self,
        parent: QWidget | None = None,
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


# --- on_process_json_triggered_action ---
def on_process_json_triggered_action():
    dialog = JsonInputDialog()

    from ..core import (
        ZikariaPrompts,
    )  # Import locally to avoid circularity at module level

    if dialog.exec() == QDialog.DialogCode.Accepted:
        input_data = dialog.get_input_data()
        json_text_data = input_data["json_data"]
        deck_id = input_data["deck_id"]
        note_type_id = input_data["notetype_id"]

        effective_config = get_effective_config(note_type_id, deck_id)

        note_type = mw.col.models.get(note_type_id)

        core_processor = ZikariaPrompts(manual_execution=True)

        pydantic_class = generate_pydantic_class(note_type, enforce_enum=False)

        request_data = ZikariaRequestData(
            mode="create",
            # prompt_str=prompt,
            effective_conf=effective_config,
            notetype_dict=note_type,
            metadata={},
            pydantic_class=pydantic_class,
        )

        notes_data: NotesDataList | None = core_processor._parse_text_into_notes_data(
            response_text=json_text_data,
            request_data=request_data,
        )

        logger.debug(
            "Got this notes_data from _parse_text_into_notes_data: %s", notes_data
        )

        notes_response = ZikariaResponseData(
            notes_data_list=notes_data, request_data=request_data
        )

        json_tag_val = config.json_tag

        def per_notes_response_fn(notes_response: ZikariaResponseData):
            added_notes.extend(notes_response.notes_data_list)
            if notes_response.notes_data_list:
                core_processor._add_notes(
                    note_type=note_type,
                    notes_data_list=notes_response.notes_data_list,
                    default_tags=[json_tag_val],
                    deck_id=deck_id,
                )

        core_processor._finalize_notes_responses(
            notes_responses=[notes_response],
            per_notes_response_fn=per_notes_response_fn,
            finished_msg="create notes",
            global_tags=None,
        )
