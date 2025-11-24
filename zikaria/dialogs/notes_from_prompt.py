import json
import uuid
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import replace

from anki.models import NotetypeDict
from anki.notes import Note
from aqt import mw

# from aqt.operations.note import add_note
from aqt.qt import (
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.tagedit import TagEdit
from aqt.utils import tooltip
from pydantic import BaseModel

# Imports from other modules in this addon
from ..config_utils import config, get_effective_config, logger, update_config
from ..prompt_store import get_default_prompt_template_key
from ..prompts import (
    generate_pydantic_class,
    get_prompt_text,
    get_prompt_text_from_string_by_mode,
)
from ..types import PromptMode, ZikariaRequestData, ZikariaResponseData
from ..ui import ChoosersMixin
from .saved_prompts_manager import PromptTemplateChooser

# from aqt.editor import Editor, EditorMode


# --- PromptFromTextDialog ---
class PromptFromTextDialog(QDialog, ChoosersMixin):

    def __init__(
        self,
        parent: QWidget | None = None,
    ):
        super().__init__(parent or mw)
        self.done_string = " ✔️"

        self.setWindowTitle("Create Cards from Prompt Text (Zikaria)")
        self.resize(1000, 800)
        self.layout = QVBoxLayout(self)
        # ... (rest of implementation using logger, config_proxy, mw)
        # Needs access to ZikariaPrompts instance or its methods for AI call.
        # For now, we'll instantiate ZikariaPrompts locally in send_prompt_to_ai_handler.

        self.note_type_combo_widget = QWidget(self)
        self.deck_combo_widget = QWidget(self)
        self._setup_choosers()  # Renamed
        self.layout.addWidget(self.note_type_combo_widget)
        self.layout.addWidget(self.deck_combo_widget)
        self.layout.addSpacing(16)

        # TODO: Use a custom config entry if notetype of deck are set through the choosers and alter the last_ormpt_template_key in that custom config instead of the global one.
        def on_prompt_template_changed(prompt_template: tuple[str, str] | None):
            new_key = prompt_template[0]

            if config["last_prompt_template_key"] != new_key:
                logger.debug("Prompt template key changed, is: %s", new_key)
                config["last_prompt_template_key"] = new_key
                update_config(config)

        self.prompt_template_combo_widget = QWidget(self)
        self.prompt_template_chooser_instance = PromptTemplateChooser(
            mw=mw,
            widget=self.prompt_template_combo_widget,
            starting_template=get_default_prompt_template_key(),
            # starting_prompt = None,
            # note_type_id = self.notetype_chooser_instance.selected_notetype_id,
            # deck_id = self.deck_chooser_instance.selected_deck_id,
            on_prompt_template_changed=on_prompt_template_changed,
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

        # if config.debug:
        #     self.generate_prompt_button = QPushButton("Generate Full Prompt")
        #     self.generate_prompt_button.clicked.connect(self.generate_full_prompt_handler)
        #     self.layout.addWidget(self.generate_prompt_button)
        #
        #     self.prompt_edit_area = QTextEdit()  # For the full generated prompt
        #     self.prompt_edit_area.setPlaceholderText("Prompt preview will appear here...")
        #     self.layout.addWidget(QLabel("Edit Prompt:"))
        #     self.layout.addWidget(self.prompt_edit_area)
        self.remove_done_lines_button = QPushButton("Remove Done Lines")
        self.remove_done_lines_button.clicked.connect(self.remove_done_lines)
        self.layout.addWidget(self.remove_done_lines_button)

        self.layout.addSpacing(16)

        self.submit_ai_button = QPushButton("Send to AI")
        self.submit_ai_button.clicked.connect(self.send_prompt_to_ai_handler)
        self.layout.addWidget(self.submit_ai_button)

        self.submit_ai_button.setFocus()

        self.global_tag_edit_widget = TagEdit(self)
        self.global_tag_edit_widget.setCol(mw.col)
        self.layout.addWidget(self.global_tag_edit_widget)
        if note_tag_val := config.get("note_tag"):  # Use config_proxy
            self.global_tag_edit_widget.setText(note_tag_val)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

    def remove_done_lines(self):
        """
        Removes all lines from the input area that end with self.done_string.
        """
        lines = self.prompt_input_area.toPlainText().splitlines()
        filtered = [
            line for line in lines if not line.rstrip().endswith(self.done_string)
        ]
        self.prompt_input_area.setPlainText("\n".join(filtered))

    def generate_full_prompt_handler(self):
        logger.debug("Generating full prompt in PromptFromTextDialog.")
        note_type_id = self.notetype_chooser_instance.selected_notetype_id
        note_type = mw.col.models.get(note_type_id)

        current_deck_id = self.deck_chooser_instance.selected_deck_id
        effective_config = get_effective_config(note_type_id, current_deck_id)
        temporary_config = deepcopy(effective_config)

        base_prompt = self.prompt_template_chooser_instance.selected_template_text()
        if base_prompt is None:
            self.prompt_output_area.setPlainText("Selected prompt template is empty")
            logger.warning("Selected prompt template is empty")
            return

        try:
            prompt_text = get_prompt_text(
                note_type=note_type,
                prompt=self.prompt_input_area.toPlainText(),
                base_prompt=base_prompt,
                config_=temporary_config,
            )

            self.prompt_edit_area.setPlainText(prompt_text)
            self.status_label.setText("Prompt generated.")
        except Exception as e:
            self.status_label.setText(f"Error generating prompt: {e}")
            logger.exception("Failed to generate prompt in PromptFromTextDialog")

    @staticmethod
    def _create_requests_with_origin_index(
        base_prompt: str,
        # mode: PromptMode,
        prompt_str: str,
        effective_config: dict,
        pydantic_class: type[BaseModel],
        notetype_dict: NotetypeDict,
    ) -> list[ZikariaRequestData]:
        lines = str(prompt_str).splitlines()
        chunk_size = effective_config.get("chunk_size", 3)
        chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]

        requests = []
        for idx, chunk in enumerate(chunks):
            chunk_dict = {}
            uuid_to_origin = {}
            for line_idx, line in enumerate(chunk):
                line_uuid = str(uuid.uuid4())
                chunk_dict[line_uuid] = line
                uuid_to_origin[line_uuid] = idx * chunk_size + line_idx
            chunked_prompt = json.dumps(chunk_dict)
            meta = {}
            # meta["origin_index"] = idx
            meta["uuid_to_origin"] = uuid_to_origin
            # TODO: change chunked prompt into json dictionary with uuid for note tracking (so each line gets a uuid) and add a metadata dict, which maps the uuids to the origin indices
            prompt = get_prompt_text(
                base_prompt=base_prompt,
                prompt=chunked_prompt,
                config_=effective_config,
                note_type=notetype_dict,
            )
            # Store the origin index in metadata
            req_data = ZikariaRequestData(
                mode=PromptMode.CREATE,
                prompt_str=prompt,
                effective_conf=effective_config,
                notetype_dict=notetype_dict,
                metadata=meta,
                pydantic_class=pydantic_class,
            )
            requests.append(req_data)

        return requests

    def send_prompt_to_ai_handler(self):
        base_prompt = self.prompt_template_chooser_instance.selected_template_text()
        if base_prompt is None:
            self.prompt_output_area.setPlainText("Selected prompt template is empty")
            logger.warning("Selected prompt template is empty")
            return

        from ..core import ZikariaPrompts, ZikariaTaskManager

        logger.debug("Sending prompt to AI from PromptFromTextDialog.")

        prompt = self.prompt_input_area.toPlainText()

        if not prompt.strip():
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

        pydantic_class = generate_pydantic_class(note_type, with_uuid=True)

        requests = self._create_requests_with_origin_index(
            base_prompt=base_prompt,
            prompt_str=prompt,
            notetype_dict=note_type,
            effective_config=effective_config,
            pydantic_class=pydantic_class,
        )

        added_notes = []
        processed_indices = set()

        def per_notes_response_fn(notes_response: ZikariaResponseData):
            added_notes.extend(notes_response.notes_data_list)
            if notes_response.notes_data_list:
                metadata = notes_response.request_data.metadata
                logger.debug(
                    "Got origin_index: %s from metadata %s",
                    metadata["origin_index"],
                    metadata,
                )
                processed_indices.add(
                    notes_response.request_data.metadata["origin_index"]
                )

                core_processor._add_notes(
                    note_type=note_type,
                    notes_data_list=notes_response.notes_data_list,
                    default_tags=global_tags,
                    deck_id=deck_id,
                )

        def finalize_notes_responses(notes_responses):
            new_notes_responses = []
            for notes_response in notes_responses:
                for note_data in notes_response.notes_data_list:
                    uuid_ = note_data.pop("zikaria__uuid")
                    metadata = deepcopy(notes_response.request_data.metadata)
                    origin_index = metadata["uuid_to_origin"].get(uuid_)
                    metadata["origin_index"] = origin_index
                    logger.debug("Got uuid and origin: %s -> %s", uuid_, origin_index)

                    new_notes_responses.append(
                        ZikariaResponseData(
                            request_data=replace(
                                notes_response.request_data, metadata=metadata
                            ),
                            notes_data_list=[note_data],
                        )
                    )

            core_processor._finalize_notes_responses(
                notes_responses=new_notes_responses,
                per_notes_response_fn=per_notes_response_fn,
                finished_msg="create notes",
                global_tags=global_tags,
            )

            if added_notes:
                self.append_value_to_lines_by_indices(
                    indices=processed_indices,
                    value=self.done_string,
                )
                self.status_label.setText(
                    f"{len(added_notes)} notes added successfully."
                )
                tooltip(f"{len(added_notes)} notes added successfully.")
                return

            self.status_label.setText("No notes were added.")
            tooltip("No notes were added.")

        # 2. Start the concurrent manager
        if requests:
            # NOTE: We use _finalize_create_notes for the entire batch as it covers
            # both note creation and updates to the original note (processing tags, suspending).
            manager = ZikariaTaskManager(requests, finalize_notes_responses)
            manager.start_tasks()

    def append_value_to_lines_by_indices(
        self, indices: Iterable[int], value: str = " "
    ):
        """
        Appends `value` to every line in the input field whose index is in `indices`.
        """

        lines = self.prompt_input_area.toPlainText().splitlines()
        for idx in indices:
            if 0 <= idx < len(lines):
                lines[idx] = lines[idx] + value
        self.prompt_input_area.setPlainText("\n".join(lines))

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


# --- open_prompt_text_dialog_action ---
def open_notes_from_prompt_dialog_action():
    dialog = PromptFromTextDialog()
    dialog.exec()
