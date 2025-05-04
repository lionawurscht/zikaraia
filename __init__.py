#!/usr/bin/env python
from dataclasses import dataclass
from collections import namedtuple
import os
import random
import json
import logging
import functools
import inspect
from datetime import datetime
from anki.models import NoteType
from anki.decks import DeckId
from anki.models import NotetypeId
from aqt import QMainWindow, gui_hooks, mw, colors
from aqt.utils import showInfo, getText, tr, tooltip, shortcut
from aqt.qt import QAction, qconnect
from aqt.tagedit import TagEdit
from aqt.editor import Editor, EditorMode
from aqt.theme import theme_manager
from aqt.browser.browser import Browser
from aqt.deckchooser import DeckChooser
from aqt.notetypechooser import NotetypeChooser
from anki.notes import Note
from aqt.operations.note import add_note
from anki.decks import DeckDict
import google.generativeai as genai
from typing import Optional

from aqt.qt import (
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
)
from proto.message import copy

# Load user configuration
config = mw.addonManager.getConfig(__name__) or {}
logger = mw.addonManager.get_logger(__name__)

logger.info(config)
logger.setLevel(logging.DEBUG if config.get("debug", False) else logging.INFO)

# mw.addonManager.writeConfig(__name__, config)

API_KEY_MISSING = False
API_KEY = None


def is_valid_cards_data(data):
    """
    Validate if the data is a list of dicts with string keys and values.
    """
    if not isinstance(data, list):
        return False

    for item in data:
        if not is_valid_card_data(item):
            return False

    return True


def is_valid_card_data(item):
    """
    Validate if the data is a dict with string keys and values.
    """

    if not isinstance(item, dict):
        return False

    if not all(
        isinstance(k, str) and (isinstance(v, str) or v is None)
        for k, v in item.items()
    ):
        return False

    return True


# Define the Note namedtuple
NoteData = namedtuple("NoteData", ["note_type_id", "deck_id", "fields", "tags"])


def json_to_namedtuples(json_data, note_type_id, deck_id):
    """
    Convert a JSON list of dictionaries into a list of Note namedtuples.

    :param json_data: JSON list of dictionaries.
    :return: List of Note namedtuples.
    """
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


def ensure_api_key():
    """Ensures that the API key is set."""
    global API_KEY

    if API_KEY:
        return True

    api_key = os.getenv("GEMINI_API_KEY", config["api_key"])

    if not api_key:
        key, ok = getText("Enter your Generative AI API key:", title="API Key Required")
        if ok and key:
            API_KEY = key
        elif not ok:
            showInfo("API key is required for this add-on to function.")
            return False

    API_KEY = api_key

    return True


def ensure_tags_exist():
    """Ensures that the required tags are present in the collection."""
    col = mw.col
    if not col:
        logger.error("Collection is not available.")
        return

    required_tags = [config[tag] for tag in config if tag.endswith("_tag")]
    existing_tags = set(col.tags.all())

    defaults = col.defaults_for_adding(current_review_card=None)
    dummy_note = Note(col=col, model=defaults.notetype_id)
    missing_tags = []
    for tag in required_tags:
        if tag not in existing_tags:
            dummy_note.add_tag(tag)
            missing_tags.append(tag)

    if not missing_tags:
        return

    col.add_note(dummy_note, deck_id=defaults.deck_id)
    # col.update_note(dummy_note)
    col.remove_notes([dummy_note.id])
    logger.info("Tags '%s' added to the collection.", col.tags.join(missing_tags))


def configure_generative_ai():
    """Configures the Generative AI client using the provided API key."""
    global API_KEY_MISSING
    if ensure_api_key():
        genai.configure(api_key=API_KEY)
        API_KEY_MISSING = False

        return

    API_KEY_MISSING = True
    raise RuntimeError("Api key not set.")


def disable_running_on_sync():
    global DISABLE_ON_SYNC
    if not DISABLE_ON_SYNC:
        DISABLE_ON_SYNC = True
        logger.error("Running on sync will be disabled.")


def get_deck_ancestor_ids(did: int) -> list[int]:
    """
    Retrieve all ancestors of the given deck ID using Anki's API.
    :param did: Deck ID to find ancestors for.
    :return: List of ancestor deck dictionaries.
    """
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
    filtered_entries = (
        entry for entry in entries if entry[0] is not None or entry[1] is not None
    )
    sorted_entries = sorted(
        filtered_entries,
        reverse=False,
        key=lambda entry: score_custom_config_entry(entry, note_type_id, deck_id),
    )
    return sorted_entries


@dataclass
class GeminiPrompts:
    mw: QMainWindow = mw
    manual_execution: bool = False

    def __post_init__(self):
        self._model = None
        self._error_messages = []

    def process_notes(self):
        """Processes notes with the prompt / complete tag."""
        manual_execution = self.manual_execution
        col = mw.col
        if not col:
            logger.error("Collection is not available.")
            if manual_execution:
                showInfo("Collection is not available.")
            return

        prompt_notes = col.find_notes(
            f"tag:{config['prompt_tag']} -tag:{config['processed_tag']}"
        )

        complete_notes = col.find_notes(
            f"tag:{config['complete_tag']} -tag:{config['processed_tag']}"
        )

        if not prompt_notes and not complete_notes:
            logger.info("No notes with the prompt / complete tag found.")
            if manual_execution:
                showInfo("No notes with the prompt / complete tag found.")
            return

        try:
            configure_generative_ai()
        except RuntimeError as e:
            logger.info("Failed to configure gemini api.")
            if manual_execution:
                showInfo("Failed to configure gemini api.")
            return

        if prompt_notes:
            self.process_prompt_notes(prompt_notes)

        if complete_notes:
            self.process_complete_notes(complete_notes)

    def process_prompt_notes(self, prompt_notes):
        """Processes notes with the prompt tag."""
        manual_execution = self.manual_execution
        col = mw.col

        new_cards = {}

        for note_id in prompt_notes:
            note = col.get_note(note_id)

            try:
                cards_data = self.process_prompt_note_with_generative_ai(note)
            except Exception as e:
                print(e)
                logger.error("Error processing note with Generative AI: %s", e)
                disable_running_on_sync()
                logger.error("Aborting execution due to an error.")
                break

            if cards_data is not None:
                new_cards[note] = cards_data

        if not new_cards:
            return

        if config["confirm_before_adding_notes"]:
            confirmed_cards = self.display_cards_confirmation_dialog(new_cards)
            if not confirmed_cards:
                return
            new_cards = confirmed_cards

        for note, cards_data in new_cards.items():
            self.add_new_notes(note, cards_data)
            self.finalize_prompt_note(note)

        logger.info("Finished processing prompt notes.")
        if manual_execution:
            showInfo("Finished processing notes.")

    def process_complete_notes(self, complete_notes):
        """Processes notes with the complete tag."""
        manual_execution = self.manual_execution
        col = mw.col

        updated_cards = {}

        for note_id in complete_notes:
            note = col.get_note(note_id)

            try:
                card_data = self.process_complete_note_with_generative_ai(note)
            except Exception as e:
                print(e)
                logger.error("Error processing note with Generative AI: %s", e)
                disable_running_on_sync()
                logger.error("Aborting execution due to an error.")
                break

            if card_data is not None:
                updated_cards[note] = [card_data]

        if not updated_cards:
            return

        if config["confirm_before_adding_notes"]:
            confirmed_cards = self.display_cards_confirmation_dialog(updated_cards)
            if not confirmed_cards:
                return
            updated_cards = confirmed_cards

        for note, cards_data in updated_cards.items():
            card_data = cards_data[0]

            self.update_completed_note(note, card_data)

        logger.info("Finished processing complete notes.")
        if manual_execution:
            showInfo("Finished processing notes.")

    def example_notes(self, note_type: NoteType) -> str:
        examples = (
            [
                {k: mw.col.media.strip(v) for k, v in mw.col.get_note(nid).items()}
                for nid in random.sample(mw.col.models.nids(note_type), 10)
            ],
        )
        return json.dumps(
            examples,
            indent=2,
            ensure_ascii=False,
        )

    def note_to_json(self, note):
        return json.dumps(
            {k: v if v != "_" else "" for k, v in note.items()},
            indent=4,
            ensure_ascii=False,
        )

    def get_create_prompt(self, note, schema=None, examples=None, custom_config=None):
        if custom_config is None:
            custom_config = config

        field_content = note.fields[0].strip()

        if custom_config["custom_prompt_tag"] in note.tags:
            base_prompt = field_content
        elif field_content.startswith("@prompt:"):
            base_prompt = field_content[len("@prompt:") :].strip()
        else:
            base_prompt = custom_config["create_prompt"]

        return self.get_prompt(
            note=note,
            base_prompt=base_prompt,
            schema=schema,
            examples=examples,
            custom_config=custom_config,
        )

    def get_complete_prompt(self, note, schema=None, examples=None, custom_config=None):
        if custom_config is None:
            custom_config = config

        return self.get_prompt(
            note=note,
            base_prompt=custom_config["complete_prompt"],
            schema=schema,
            examples=examples,
            custom_config=custom_config,
        )

    def get_prompt(
        self, note, base_prompt, schema=None, examples=None, custom_config=None
    ):
        if custom_config is None:
            custom_config = config

        note_type = note.note_type()

        return base_prompt.format(
            prompt=note.fields[0],
            json_note=self.note_to_json(note),
            schema=(
                schema if schema is not None else self.generate_schema_class(note_type)
            ),
            examples=(
                examples if examples is not None else self.example_notes(note_type)
            ),
        )

    def process_complete_note_with_generative_ai(self, note: Note):
        """Processes a single note with Generative AI."""

        custom_config = self.get_effective_config_for_note(note)

        # Send prompt to Generative AI
        response = self.send_prompt_to_generative_ai(
            prompt=self.get_complete_prompt(note, custom_config=custom_config),
            custom_config=custom_config,
        )

        if not response:
            return

        logger.debug("Returned raw text: %s", response.text)

        try:
            card_data = json.loads(response.text)
        except json.JSONDecodeError as e:
            logger.error("Failed to decode Generative AI response: %s", e)
            if self.manual_execution:
                showInfo("Error decoding response: %s" % e)
            return

        if not is_valid_card_data(card_data):
            logger.error("The provided json data isn't a valid card.")
            return

        logger.debug("Returned JSON object: %s", card_data)

        return card_data

    def process_prompt_note_with_generative_ai(self, note: Note):
        """Processes a single note with Generative AI."""

        custom_config = self.get_effective_config_for_note(note)

        # Send prompt to Generative AI
        response = self.send_prompt_to_generative_ai(
            prompt=self.get_create_prompt(note, custom_config=custom_config),
            custom_config=custom_config,
        )
        if not response:
            return

        logger.debug("Returned raw text: %s", response.text)

        try:
            cards_data = json.loads(response.text)
        except json.JSONDecodeError as e:
            logger.error("Failed to decode Generative AI response: %s", e)
            if self.manual_execution:
                showInfo("Error decoding response: %s" % e)
            return

        if not is_valid_cards_data(cards_data):
            logger.error("The provided json data isn't a list of valid card.")
            return

        logger.debug("Returned JSON object: %s", cards_data)

        return cards_data

    def generate_schema_class(self, note_type) -> dict:
        """
        Create a JSON schema with string fields based on a list of field names.

        Args:
            note_type (dict): A note type containing field definitions.

        Returns:
            dict: The generated JSON schema.
        """
        schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }

        col = mw.col  # Access the collection to query existing notes

        for field in note_type["flds"]:
            name = field["name"]
            schema["properties"][name] = property = {"type": "string"}

            # Add a description if present
            if desc := field.get("description"):
                property["description"] = desc

                # Special handling for "(category)" fields
                if "(category)" in desc:
                    # Find unique values for this field from notes with the same note type
                    # and without the prompt tag
                    note_ids = col.find_notes(
                        f'note:"{note_type["name"]}" -tag:{config["prompt_tag"]}'
                    )
                    unique_values = set()
                    for nid in note_ids:
                        note = col.get_note(nid)
                        if value := note[name]:
                            unique_values.add(value)

                    # Add the unique values as an enum to the schema
                    if unique_values:
                        property["enum"] = sorted(
                            unique_values
                        )  # Sorted for consistency

            # Assuming all fields are required
            schema["required"].append(name)

        logger.debug("Generated schema: %s", schema)

        return schema

    def model(self, custom_config=None):
        if custom_config is None:
            custom_config = config

        # if self._model:
        #     return self._model

        model = genai.GenerativeModel(
            model_name=custom_config["model_name"],
            generation_config=genai.GenerationConfig(
                max_output_tokens=custom_config["max_output_tokens"],
                temperature=custom_config["model_temperature"],
            ),
        )
        # self._model = model

        return model

    def send_prompt_to_generative_ai(self, prompt: str, custom_config=None):
        """Sends a prompt to Generative AI and returns the response."""
        logger.debug("Running Gemini AI with this prompt: %s", prompt)

        try:
            response = self.model(custom_config).generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    # response_schema=list[schema_class],
                ),
                request_options={"timeout": 600},
            )
            return response
        except Exception as e:
            print(e)
            logger.error("Error communicating with Generative AI: %s", e)
            return None

    def update_completed_note(self, original_note, card_data):
        """Adds new notes based on the cards_data."""
        col = mw.col
        deck_id = original_note.cards()[0].did

        for field_name, field_value in card_data.items():
            if field_name in original_note:
                original_note[field_name] = field_value

        original_note.tags.append(config["processed_tag"])
        col.update_note(note=original_note)
        logger.info("Updated note: %s", card_data)

    def add_new_notes(
        self, original_note, cards_data, deck_id=None, note_type=None, note_tag=None
    ):
        """Adds new notes based on the cards_data."""
        col = mw.col
        deck_id = original_note.cards()[0].did if deck_id is None else deck_id
        note_type = original_note.note_type() if note_type is None else note_type
        note_tag = config["note_tag"] if note_tag is None else note_tag

        new_notes = []
        for card_data in cards_data:
            new_note = Note(col, note_type)
            new_notes.append(new_note)
            for field_name, field_value in card_data.items():
                if field_name in new_note:
                    new_note[field_name] = field_value
            if note_tag:
                new_note.tags.append(note_tag)
            col.add_note(note=new_note, deck_id=deck_id)
            logger.info("Added new note: %s", card_data)

        # col.update_notes(notes=new_notes)

    def finalize_prompt_note(self, prompt_note):
        """Marks the prompt note as processed."""
        prompt_note.tags.append(config["processed_tag"])
        # prompt_note.tags.remove(PROMPT_TAG)
        # prompt_note.fields[0] += " (Processed)"
        # prompt_note.flush()
        mw.col.sched.suspend_notes([prompt_note.id])

        mw.col.update_note(note=prompt_note)

    def get_effective_config(self, note_type_id: int, deck_id: int):
        """
        Retrieve the effective configuration for a given note type and deck.

        Args:
            note_type (str): The name of the note type.
            deck (str): The name of the deck.

        Returns:
            dict: The effective configuration.
        """
        logger.debug(
            "Generating effective config for note type: %s, and deck: %s.",
            note_type_id,
            deck_id,
        )
        global_config = config.copy()

        # Custom configurations
        custom_config = config.get("custom_config", [])

        for i, entry in enumerate(
            filter_and_sort_custom_config_entries(custom_config, note_type_id, deck_id)
        ):
            logger.debug("Entry #%s: %s", i + 1, entry)
            settings = entry[2]

            global_config.update(settings)

        # Override global config with matching custom config
        final_config = global_config

        logger.debug("Got this config: %s", final_config)

        return final_config

    def get_effective_config_for_note(self, note: Note):
        return self.get_effective_config(note.note_type()["id"], note.cards()[0].did)

    def display_cards_confirmation_dialog(self, cards_data, parent=None):
        """
        Displays a custom dialog to confirm the addition of new cards.

        Args:
            cards_data (list): List of dictionaries representing the cards to be added.

        Returns:
            list: List of dictionaries representing the cards that were confirmed for addition.
        """
        outer_parent = parent or mw

        class ConfirmationDialog(QDialog):
            def __init__(self, cards, parent=None):
                super().__init__(parent or outer_parent)
                self.mw = mw
                self.setWindowTitle("Confirm New Cards")
                self.resize(1200, 800)
                self.selected_cards = [True] * sum(
                    len(cards_data) for cards_data in cards.values()
                )  # Default to all selected

                self.setStyleSheet("QWidget { font-size: 16px; }")

                # Main layout
                self.main_layout = main_layout = QVBoxLayout()
                self.setLayout(main_layout)

                # Scrollable content area
                scroll_area = QScrollArea()
                scroll_area.setWidgetResizable(True)
                scroll_content = QWidget()
                scroll_layout = QVBoxLayout()
                scroll_content.setLayout(scroll_layout)

                self.card_widgets = []

                for index, (note, card) in enumerate(
                    (note, card_data)
                    for note, cards_data in cards.items()
                    for card_data in cards_data
                ):
                    card_frame = QFrame()
                    card_layout = QVBoxLayout()

                    # Header with checkbox
                    header_layout = QHBoxLayout()
                    card_checkbox = QCheckBox(f"Card {index + 1}")
                    card_checkbox.setChecked(True)
                    card_checkbox.stateChanged.connect(
                        lambda state, idx=index: self.toggle_card_selection(idx, state)
                    )
                    header_layout.addWidget(card_checkbox)
                    card_layout.addLayout(header_layout)

                    field_widgets = {}

                    note_type = note.note_type()
                    found_keys = set()

                    for field_def in note_type["flds"]:
                        field = field_def["name"]
                        if field in card:
                            value = card[field]
                            found_keys.add(field)
                        else:
                            value = ""

                        field_layout = QHBoxLayout()
                        field_label = QLabel(f"{field}:")
                        logger.debug("Setting %s to %s", field, value)
                        field_edit = QLineEdit(value)
                        field_layout.addWidget(field_label)
                        field_layout.addWidget(field_edit)
                        card_layout.addLayout(field_layout)
                        field_widgets[field] = field_edit

                    unused_keys = [key for key in card if key not in found_keys]
                    if unused_keys:
                        rest_layout = QHBoxLayout()
                        rest_label = QLabel(f"Unused data:")
                        rest_edit = QLineEdit(
                            json.dumps({key: card[key] for key in unused_keys})
                        )
                        rest_edit.setReadOnly(True)
                        rest_layout.addWidget(rest_label)
                        rest_layout.addWidget(rest_edit)
                        card_layout.addLayout(rest_layout)

                    card_tag_edit = self.get_card_tag_edit(card_layout)

                    self.card_widgets.append(
                        (card_checkbox, field_widgets, note, card_tag_edit)
                    )
                    card_frame.setLayout(card_layout)
                    scroll_layout.addWidget(card_frame)

                scroll_area.setWidget(scroll_content)
                main_layout.addWidget(scroll_area)

                # Bulk action buttons
                bulk_action_layout = QHBoxLayout()
                select_all_button = QPushButton("Select All")
                select_all_button.clicked.connect(self.select_all)
                bulk_action_layout.addWidget(select_all_button)

                deselect_all_button = QPushButton("Select None")
                deselect_all_button.clicked.connect(self.deselect_all)
                bulk_action_layout.addWidget(deselect_all_button)

                invert_selection_button = QPushButton("Invert Selection")
                invert_selection_button.clicked.connect(self.invert_selection)
                bulk_action_layout.addWidget(invert_selection_button)

                main_layout.addLayout(bulk_action_layout)

                self.setup_global_tag_edit()

                # Buttons
                buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok
                    | QDialogButtonBox.StandardButton.Cancel
                )
                buttons.accepted.connect(self.accept)
                buttons.rejected.connect(self.reject)

                main_layout.addWidget(buttons)

            def setup_global_tag_edit(self) -> None:
                tag_edit_frame = QWidget(self)
                tag_edit_frame.setStyleSheet("border: 0")
                tag_edit_layout = QGridLayout()
                tag_edit_layout.setSpacing(12)
                tag_edit_layout.setContentsMargins(2, 6, 2, 6)
                # tags
                tag_edit_label = QLabel(tr.editing_tags())
                tag_edit_layout.addWidget(tag_edit_label, 1, 0)
                self.global_tag_edit = TagEdit(self)
                # qconnect(self.tags.lostFocus, self.on_tag_focus_lost)
                self.global_tag_edit.setToolTip(
                    shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
                )
                border = theme_manager.var(colors.BORDER)
                self.global_tag_edit.setStyleSheet(f"border: 1px solid {border}")
                tag_edit_layout.addWidget(self.global_tag_edit, 1, 1)
                tag_edit_frame.setLayout(tag_edit_layout)
                self.main_layout.addWidget(tag_edit_frame)

                if self.global_tag_edit.col != self.mw.col:
                    self.global_tag_edit.setCol(self.mw.col)

            def get_card_tag_edit(self, card_layout):
                tag_layout = QHBoxLayout()
                tag_label = QLabel(f"{tr.editing_tags()}:")

                tag_edit = TagEdit(self)
                # qconnect(self.tags.lostFocus, self.on_tag_focus_lost)
                tag_edit.setToolTip(
                    shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
                )

                if tag_edit.col != self.mw.col:
                    tag_edit.setCol(self.mw.col)

                tag_layout.addWidget(tag_label)
                tag_layout.addWidget(tag_edit)
                card_layout.addLayout(tag_layout)
                return tag_edit

            def toggle_card_selection(self, index, state):
                self.selected_cards[index] = (
                    Qt.CheckState(state) == Qt.CheckState.Checked
                )

            def select_all(self):
                for checkbox, *_ in self.card_widgets:
                    checkbox.setChecked(True)

            def deselect_all(self):
                for checkbox, *_ in self.card_widgets:
                    checkbox.setChecked(False)

            def invert_selection(self):
                for checkbox, *_ in self.card_widgets:
                    checkbox.setChecked(not checkbox.isChecked())

            def get_confirmed_cards(self):
                global_tags = mw.col.tags.split(self.global_tag_edit.text())
                confirmed_cards = {}
                for selected, (_, fields, note, tag_edit) in zip(
                    self.selected_cards, self.card_widgets
                ):
                    if selected:
                        updated_card = {
                            field: widget.text() for field, widget in fields.items()
                        }
                        confirmed_cards.setdefault(note, []).append(updated_card)
                return confirmed_cards

        dialog = ConfirmationDialog(cards_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.get_confirmed_cards()
        return []

    def display_review_and_edit_notes_dialog(
        self, notes_data: list[NoteData], mw=None, parent=None
    ):
        mw = mw or self.mw
        parent = parent or mw

        class ReviewAndEditNotesDialog(QDialog):
            def __init__(self, mw, notes_data: list[NoteData], parent=None):
                """
                Dialog for reviewing and editing notes before adding them to Anki.

                :param mw: Main Anki window object.
                :param notes: List of Note objects to review and edit.
                :param parent: Parent widget.
                """
                super().__init__(parent)
                self.mw = mw

                self.setWindowTitle("Review and Edit Notes")
                self.resize(1200, 800)

                self.setStyleSheet("QWidget { font-size: 16px; }")

                self.notes_data = notes_data

                # Main layout
                self.main_layout = main_layout = QVBoxLayout(self)

                # Scrollable content area
                scroll_area = QScrollArea()
                scroll_area.setWidgetResizable(True)
                scroll_content = QWidget()
                scroll_layout = QVBoxLayout()
                scroll_content.setLayout(scroll_layout)

                self.note_editors = []
                self.note_checkboxes = []

                # Populate note list
                for idx, note_data in enumerate(notes_data):
                    note_frame = QFrame()
                    note_layout = QVBoxLayout()

                    deck_name = mw.col.decks.get(note_data.deck_id)["name"]
                    note_type_name = mw.col.models.get(note_data.note_type_id)["name"]

                    # Header with checkbox
                    note_header_layout = QHBoxLayout()
                    note_checkbox = QCheckBox(
                        f"Card {idx + 1} (Deck: {deck_name} | Note Type: {note_type_name})"
                    )
                    note_checkbox.setChecked(True)

                    self.note_checkboxes.append(note_checkbox)

                    note_header_layout.addWidget(note_checkbox)
                    note_layout.addLayout(note_header_layout)

                    editor_widget = QWidget()

                    # Editor for note fields
                    note_editor = Editor(
                        mw=self.mw,
                        widget=editor_widget,
                        parentWindow=self,
                        editor_mode=EditorMode.ADD_CARDS,
                    )

                    new_note = self.mw.col.new_note(
                        self.mw.col.models.get(note_data.note_type_id)
                    )

                    for field_name, field_value in note_data.fields.items():
                        if field_name in new_note and field_value is not None:
                            new_note[field_name] = field_value

                    new_note.tags = note_data.tags[:]

                    note_editor.set_note(new_note, focusTo=0)

                    editor_widget.setMinimumSize(0, 200)

                    def set_editor_size():
                        try:
                            size = note_editor.web.page().mainFrame().size().toSize()
                        except Exception:
                            logger.debug("Couldn't get editor content size")
                            return

                        editor_widget.setMinimumSize(
                            size
                        )

                    note_editor.web.loadFinished.connect(
                        set_editor_size
                    )

                    note_layout.addWidget(editor_widget)

                    self.note_editors.append(note_editor)

                    note_frame.setLayout(note_layout)
                    scroll_layout.addWidget(note_frame)

                scroll_area.setWidget(scroll_content)
                main_layout.addWidget(scroll_area)

                # Bulk action buttons
                bulk_action_layout = QHBoxLayout()
                select_all_button = QPushButton("Select All")
                select_all_button.clicked.connect(self.select_all)
                bulk_action_layout.addWidget(select_all_button)

                deselect_all_button = QPushButton("Select None")
                deselect_all_button.clicked.connect(self.deselect_all)
                bulk_action_layout.addWidget(deselect_all_button)

                invert_selection_button = QPushButton("Invert Selection")
                invert_selection_button.clicked.connect(self.invert_selection)
                bulk_action_layout.addWidget(invert_selection_button)

                main_layout.addLayout(bulk_action_layout)

                self.setup_global_tag_edit()

                # Buttons
                buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok
                    | QDialogButtonBox.StandardButton.Cancel
                )
                buttons.accepted.connect(self.add_notes)
                buttons.rejected.connect(self.reject)

                main_layout.addWidget(buttons)

            def setup_global_tag_edit(self) -> None:
                tag_edit_frame = QWidget(self)
                tag_edit_frame.setStyleSheet("border: 0")
                tag_edit_layout = QGridLayout()
                tag_edit_layout.setSpacing(12)
                tag_edit_layout.setContentsMargins(2, 6, 2, 6)
                # tags
                tag_edit_label = QLabel(tr.editing_tags())
                tag_edit_layout.addWidget(tag_edit_label, 1, 0)
                self.global_tag_edit = TagEdit(self)
                # qconnect(self.tags.lostFocus, self.on_tag_focus_lost)
                self.global_tag_edit.setToolTip(
                    shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
                )
                border = theme_manager.var(colors.BORDER)
                self.global_tag_edit.setStyleSheet(f"border: 1px solid {border}")
                tag_edit_layout.addWidget(self.global_tag_edit, 1, 1)
                tag_edit_frame.setLayout(tag_edit_layout)
                self.main_layout.addWidget(tag_edit_frame)

                if self.global_tag_edit.col != self.mw.col:
                    self.global_tag_edit.setCol(self.mw.col)

            def select_all(self):
                """Select all notes."""
                for checkbox in self.note_checkboxes:
                    checkbox.setChecked(True)

            def deselect_all(self):
                """Deselect all notes."""
                for checkbox in self.note_checkboxes:
                    checkbox.setChecked(False)

            def invert_selection(self):
                """Invert the selection of notes."""
                for checkbox in self.note_checkboxes:
                    checkbox.setChecked(not checkbox.isChecked())

            def add_note(self, note_editor, deck_id) -> None:
                note_editor.call_after_note_saved(
                    lambda: self._add_note(note_editor, deck_id)
                )

            def _add_note(self, note_editor, deck_id) -> None:
                note = note_editor.note

                def on_success(changes) -> None:
                    tooltip(tr.adding_added(), period=500)

                add_note(parent=self, note=note, target_deck_id=deck_id).success(
                    on_success
                ).run_in_background()

            def add_notes(self):
                """Add the selected notes to the collection."""
                added_count = 0
                global_tags = self.mw.col.tags.split(self.global_tag_edit.text())

                for idx, note_data in enumerate(self.notes_data):
                    note_checkbox = self.note_checkboxes[idx]
                    if note_checkbox.isChecked():
                        # Apply global tags
                        note_editor = self.note_editors[idx]
                        deck_id = note_data.deck_id

                        note_editor.note.tags = list(
                            set(note_editor.note.tags + global_tags)
                        )

                        self.add_note(note_editor, deck_id)
                        added_count += 1

                tooltip(f"{added_count} notes added.")

                self.accept()

        dialog = ReviewAndEditNotesDialog(notes_data=notes_data, mw=mw, parent=parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return True

        return None


def on_main_window_did_init():
    """Ensures tags exist on main window initialization."""
    ensure_tags_exist()


DISABLE_ON_SYNC = False


def on_sync_complete():
    """Processes notes automatically after sync completion."""
    if not config["run_on_sync"]:
        return

    if DISABLE_ON_SYNC:
        logger.debug("Running on sync disabled.")
        return

    if API_KEY_MISSING is True:
        logger.debug("Running on sync disabled due to missing api key.")
        return

    GeminiPrompts(mw=mw, manual_execution=False).process_notes()


# Attach hooks and menu action
gui_hooks.main_window_did_init.append(on_main_window_did_init)
gui_hooks.sync_did_finish.append(on_sync_complete)

action = QAction("Process Gemini Notes", mw)
qconnect(
    action.triggered,
    lambda: GeminiPrompts(mw=mw, manual_execution=True).process_notes(),
)
mw.form.menuTools.addAction(action)


def update_config(text, _):
    try:
        new_config = json.loads(text)
    except json.JSONDecodeError:
        return

    global config
    config.update(new_config)

    return text


gui_hooks.addon_config_editor_will_update_json.append(update_config)


# Example function to show the configuration dialog
def show_config_dialog():
    dialog = ConfigDialog(mw)
    dialog.exec()


# Bind the configuration dialog to a menu item in Anki
def setup_addon_config_dialog():
    action = mw.form.menuTools.addAction("Addon Configuration")
    action.triggered.connect(show_config_dialog)

    mw.addonManager.setConfigAction(__name__, show_config_dialog)


setup_addon_config_dialog()


def on_process_json_triggered():
    """Displays the JSON input dialog and processes the input."""

    class JsonInputDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent or mw)
            self._close_event_has_cleaned_up = True
            self.setWindowTitle("Process JSON Input")
            self.resize(400, 300)

            # Layout setup
            layout = QVBoxLayout(self)
            self.json_input = QTextEdit(
                self
            )  # Replaced QLineEdit with QTextEdit for multiline input
            # text_option = QTextOption(Qt.AlignmentFlag.AlignLeft)
            # text_option.setTextDirection(Qt.LayoutDirection.RightToLeft)
            # self.json_input.document().setDefaultTextOption(text_option)
            self.json_input.setPlaceholderText("Enter JSON list here...")
            layout.addWidget(self.json_input)

            # Deck and note type selection
            self.notetype_combo = QWidget(self)
            self.deck_combo = QWidget(self)
            self.setup_choosers()

            # self.deck_combo = QComboBox(self)
            # self.deck_combo.addItems(
            #     [deck.name for deck in mw.col.decks.all_names_and_ids()]
            # )
            layout.addWidget(self.notetype_combo)
            layout.addWidget(self.deck_combo)

            # self.notetype_combo = QComboBox(self)
            # self.notetype_combo.addItems(
            #     [nt.name for nt in mw.col.models.all_names_and_ids()]
            # )


            # Action buttons
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel,
                self,
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            # layout.addWidget(buttons)

            # Add this just before layout.addWidget(buttons)
            button_row = QHBoxLayout()
            self.insert_template_button = QPushButton("Insert JSON Template")
            self.insert_template_button.clicked.connect(self.insert_json_template)
            button_row.addWidget(self.insert_template_button)
            button_row.addStretch()  # Pushes buttons to opposite ends
            button_row.addWidget(buttons)
            layout.addLayout(button_row)


        def setup_choosers(self) -> None:
            defaults = mw.col.defaults_for_adding(current_review_card=mw.reviewer.card)

            self.notetype_chooser = NotetypeChooser(
                mw=mw,
                widget=self.notetype_combo,
                starting_notetype_id=NotetypeId(defaults.notetype_id),
                # on_button_activated=lambda *_: None,
                # on_button_activated=self.show_notetype_selector,
                on_notetype_changed=lambda *_: None,
                # on_notetype_changed=self.on_notetype_change,
            )

            self.deck_chooser = DeckChooser(
                mw=mw,
                widget=self.deck_combo,
                starting_deck_id=DeckId(defaults.deck_id),
                on_deck_changed=lambda *_: None,
                # on_deck_changed=self.on_deck_changed,
            )

        def insert_json_template(self):
            """Populate the JSON input with a template for the current notetype."""
            try:
                note_type_id = self.notetype_chooser.selected_notetype_id
                note_type = mw.col.models.get(note_type_id)
                field_names = [f["name"] for f in note_type["flds"]]
                template = [{field: "" for field in field_names}]
                self.json_input.setPlainText(json.dumps(template, indent=4))
            except Exception as e:
                showInfo(f"Error generating template: {e}")

        def get_input_data(self):
            """Return the user input for JSON, deck, and note type."""
            return {
                "json_data": self.json_input.toPlainText(),  # Get the text from QTextEdit
                "deck": self.deck_chooser.selected_deck_id,  # self.deck_combo.currentText(),
                "notetype": self.notetype_chooser.selected_notetype_id,  # self.notetype_combo.currentText(),
            }

        # @log_method_calls
        def _close(self) -> None:
            self.notetype_chooser.cleanup()
            self.deck_chooser.cleanup()
            self._close_event_has_cleaned_up = True
            mw.deferred_delete_and_garbage_collect(self)
            self.close()

    dialog = JsonInputDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        input_data = dialog.get_input_data()
        dialog._close()

        json_data = input_data["json_data"]

        deck_id = input_data["deck"]
        deck_name = mw.col.decks.get(deck_id)["name"]

        note_type_id = input_data["notetype"]
        note_type = mw.col.models.get(note_type_id)
        note_type_name = note_type["name"]
        

        try:
            notes_json_data = json.loads(json_data)  # Parse the JSON input
        except json.JSONDecodeError:
            logger.error("Invalid JSON input.")
            return

        if not is_valid_cards_data(notes_json_data):
            logger.error(
                f"JSON needs to be list or dicts, was: {notes_json_data}, returning."
            )
            return

        if not notes_json_data:
            logger.info("No cards found, returning.")
            return

        logger.debug(
            "Got this python data:\n\n%r\n\nfrom this json input:\n\n%r",
            notes_json_data,
            json_data,
        )


        # Add the new tag from config value (json_tag is guaranteed to exist)
        json_tag = config["json_tag"]

        # Now, use the card confirmation dialog
        gp = GeminiPrompts(manual_execution=True)

        dummy_note = Note(mw.col, note_type)

        confirmed_cards = gp.display_cards_confirmation_dialog(
            {dummy_note: notes_json_data}
        )

        if not confirmed_cards:
            return

        new_cards = confirmed_cards

        for note, cards_data in new_cards.items():
            gp.add_new_notes(
                note,
                cards_data,
                deck_id=deck_id,
                note_type=note_type_id,
                note_tag=json_tag,
            )

            # Add the new tag to each note
            # for note in mw.col.find_notes(f"deck:{deck_name}"):
            #     note = mw.col.get_note(note)
            #     note.tags.append(json_tag)
            #     mw.col.update_note(note)

            logger.info(
                f"Processed JSON input for deck {deck_name} and note type {note_type_name}."
            )


# New menu action to process JSON input
def _on_process_json_triggered(): # old version
    """Displays the JSON input dialog and processes the input."""

    # Configure logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    def log_method_calls(func):
        """
        Decorator to log when a method is called and when it finishes, including its return value.
        """

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get the caller details
            caller_frame = inspect.stack()[1]
            caller_name = caller_frame.function
            caller_file = caller_frame.filename
            caller_line = caller_frame.lineno

            logger.debug(
                "Called %s from %s:%d by %s",
                func.__name__,
                caller_file,
                caller_line,
                caller_name,
            )
            start_time = datetime.now()

            try:
                result = func(*args, **kwargs)
                return result
            finally:
                end_time = datetime.now()
                logging.debug(
                    "Finished %s with return value: %s (Duration: %s)",
                    func.__name__,
                    result,
                    end_time - start_time,
                )

        return wrapper

    class JsonInputDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent or mw)
            self._close_event_has_cleaned_up = True
            self.setWindowTitle("Process JSON Input")
            self.resize(400, 300)

            # Layout setup
            layout = QVBoxLayout(self)
            self.json_input = QTextEdit(
                self
            )  # Replaced QLineEdit with QTextEdit for multiline input
            # text_option = QTextOption(Qt.AlignmentFlag.AlignLeft)
            # text_option.setTextDirection(Qt.LayoutDirection.RightToLeft)
            # self.json_input.document().setDefaultTextOption(text_option)
            self.json_input.setPlaceholderText("Enter JSON list here...")
            layout.addWidget(self.json_input)

            # Deck and note type selection
            self.notetype_combo = QWidget(self)
            self.deck_combo = QWidget(self)
            self.setup_choosers()

            # self.deck_combo = QComboBox(self)
            # self.deck_combo.addItems(
            #     [deck.name for deck in mw.col.decks.all_names_and_ids()]
            # )
            layout.addWidget(self.notetype_combo)
            layout.addWidget(self.deck_combo)

            # self.notetype_combo = QComboBox(self)
            # self.notetype_combo.addItems(
            #     [nt.name for nt in mw.col.models.all_names_and_ids()]
            # )

            # Action buttons
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel,
                self,
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def setup_choosers(self) -> None:
            defaults = mw.col.defaults_for_adding(current_review_card=mw.reviewer.card)

            self.notetype_chooser = NotetypeChooser(
                mw=mw,
                widget=self.notetype_combo,
                starting_notetype_id=NotetypeId(defaults.notetype_id),
                # on_button_activated=lambda *_: None,
                # on_button_activated=self.show_notetype_selector,
                on_notetype_changed=lambda *_: None,
                # on_notetype_changed=self.on_notetype_change,
            )

            self.deck_chooser = DeckChooser(
                mw=mw,
                widget=self.deck_combo,
                starting_deck_id=DeckId(defaults.deck_id),
                on_deck_changed=lambda *_: None,
                # on_deck_changed=self.on_deck_changed,
            )

        def get_input_data(self):
            """Return the user input for JSON, deck, and note type."""
            return {
                "json_data": self.json_input.toPlainText(),  # Get the text from QTextEdit
                "deck": self.deck_chooser.selected_deck_id,  # self.deck_combo.currentText(),
                "notetype": self.notetype_chooser.selected_notetype_id,  # self.notetype_combo.currentText(),
            }

        # @log_method_calls
        def _close(self) -> None:
            self.notetype_chooser.cleanup()
            self.deck_chooser.cleanup()
            self._close_event_has_cleaned_up = True
            mw.deferred_delete_and_garbage_collect(self)
            self.close()

    dialog = JsonInputDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        input_data = dialog.get_input_data()
        json_data = input_data["json_data"]
        deck_id = input_data["deck"]
        note_type_id = input_data["notetype"]
        dialog._close()

        try:
            notes_json_data = json.loads(json_data)  # Parse the JSON input
        except json.JSONDecodeError:
            logger.error("Invalid JSON input.")
            return

        if not is_valid_cards_data(notes_json_data):
            logger.error(
                f"JSON needs to be list or dicts, was: {notes_json_data}, returning."
            )
            return

        if not notes_json_data:
            logger.info("No cards found, returning.")
            return

        logger.debug(
            "Got this python data:\n\n%r\n\nfrom this json input:\n\n%r",
            notes_json_data,
            json_data,
        )
        notes_data = json_to_namedtuples(
            notes_json_data, note_type_id=note_type_id, deck_id=deck_id
        )

        gp = GeminiPrompts(mw=mw, manual_execution=True)

        if gp.display_review_and_edit_notes_dialog(notes_data):
            logger.info("Success.")

        return

        # try:
        #     notes_json_data = json.loads(json_data)  # Parse the JSON input
        #
        #     if not is_valid_cards_data(notes_json_data):
        #         logger.error(
        #             f"JSON needs to be list or dicts, was: {type(notes_json_data)}, returning."
        #         )
        #         return
        #
        #     if not notes_json_data:
        #         logger.info("No cards found, returning.")
        #         return
        #
        #     logger.debug(
        #         "Got this python data:\n\n%r\n\nfrom this json input:\n\n%r",
        #         notes_json_data,
        #         json_data,
        #     )
        #     # Fetch the deck and notetype objects
        #     deck_name = mw.col.decks.get(deck_id)["name"]
        #     # deck_id = mw.col.decks.id(deck_name)
        #     # deck_id = mw.col.decks.by_name(deck_name)["id"]
        #     note_type_name = mw.col.models.get(note_type_id)["name"]
        #     note_type = mw.col.models.by_name(note_type_name)
        #     # note_type_id = note_type["id"]
        #
        #     # Add the new tag from config value (json_tag is guaranteed to exist)
        #     json_tag = config["json_tag"]
        #
        #     # Now, use the card confirmation dialog
        #     gp = GeminiPrompts(mw=mw, manual_execution=True)
        #
        #     dummy_note = Note(mw.col, note_type)
        #
        #     confirmed_cards = gp.display_cards_confirmation_dialog(
        #         {dummy_note: notes_json_data}
        #     )
        #
        #     if not confirmed_cards:
        #         return
        #
        #     new_cards = confirmed_cards
        #
        #     for note, notes_json_data in new_cards.items():
        #         gp.add_new_notes(
        #             note,
        #             notes_json_data,
        #             deck_id=deck_id,
        #             note_type=note_type_id,
        #             note_tag=json_tag,
        #         )
        #
        #         # Add the new tag to each note
        #         # for note in mw.col.find_notes(f"deck:{deck_name}"):
        #         #     note = mw.col.get_note(note)
        #         #     note.tags.append(json_tag)
        #         #     mw.col.update_note(note)
        #
        #         logger.info(
        #             f"Processed JSON input for deck {deck_name} and note type {note_type_name}."
        #         )
        #
        # except json.JSONDecodeError:
        #     logger.error("Invalid JSON input.")
    else:
        logger.debug("Called cancel on dialog")
        dialog._close()

    # dialog._close()


# Attach the new menu item to the Tools menu
action = QAction("Process JSON Input", mw)
qconnect(action.triggered, on_process_json_triggered)
mw.form.menuTools.addAction(action)


class DebugDialog(QDialog):
    def __init__(self, note, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Debug Note")
        self.resize(800, 1200)
        self.note = note

        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Generate Schema Button
        self.schema_output = QTextEdit()
        self.schema_output.setReadOnly(True)

        generate_schema_button = QPushButton("Generate Schema")
        generate_schema_button.clicked.connect(self.display_schema)
        main_layout.addWidget(generate_schema_button)
        main_layout.addWidget(self.schema_output)

        # Generate Create Prompt Button
        self.create_prompt_output = QTextEdit()
        self.create_prompt_output.setReadOnly(True)

        generate_create_prompt_button = QPushButton("Generate Create Prompt")
        generate_create_prompt_button.clicked.connect(self.display_create_prompt)
        main_layout.addWidget(generate_create_prompt_button)
        main_layout.addWidget(self.create_prompt_output)

        # Generate complete Prompt Button
        self.complete_prompt_output = QTextEdit()
        self.complete_prompt_output.setReadOnly(True)

        generate_complete_prompt_button = QPushButton("Generate Complete Prompt")
        generate_complete_prompt_button.clicked.connect(self.display_complete_prompt)
        main_layout.addWidget(generate_complete_prompt_button)
        main_layout.addWidget(self.complete_prompt_output)

        # Close Button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        main_layout.addWidget(close_button)

    def display_schema(self):
        """Generate and display the schema for the note."""
        schema = GeminiPrompts(mw=mw).generate_schema_class(self.note.note_type())
        self.schema_output.setPlainText(
            json.dumps(
                schema,
                indent=2,
                ensure_ascii=False,
            )
        )

    def display_create_prompt(self):
        """Generate and display the prompt for the note."""
        gp = GeminiPrompts(mw=mw)
        prompt = gp.get_create_prompt(
            self.note,
            examples="",
            schema="",
            custom_config=gp.get_effective_config_for_note(self.note),
        )
        self.create_prompt_output.setPlainText(prompt)

    def display_complete_prompt(self):
        """Generate and display the prompt for the note."""
        gp = GeminiPrompts(mw=mw)
        prompt = gp.get_complete_prompt(
            self.note,
            examples="",
            schema="",
            custom_config=gp.get_effective_config_for_note(self.note),
        )
        self.complete_prompt_output.setPlainText(prompt)


def add_debug_menu_action(browser):
    """Adds a menu action in the browser for debugging a single card."""

    def debug_selected_card(browser: Browser):
        if not config.get("debug", False):
            return

        selected_notes = browser.selected_notes()
        if len(selected_notes) != 1:
            showInfo("Please select exactly one note for debugging.")
            return

        note_id = selected_notes[0]
        note = mw.col.get_note(note_id)

        dialog = DebugDialog(note, parent=browser)
        dialog.exec()

    # Add the action to the browser menu
    if config.get("debug", False):
        action = QAction("Debug Note", mw)
        action.triggered.connect(lambda: debug_selected_card(browser))

        browser.form.menuEdit.addAction(action)


# Hook to add the action when the browser is initialized
gui_hooks.browser_menus_did_init.append(add_debug_menu_action)


class ConfigDialog(QDialog):
    def __init__(self, parent=None, config_data=None):
        super().__init__(parent)
        self.resize(1200, 1000)

        if config_data is None:
            config_data = config

        self.config = copy.deepcopy(config_data)

        self.setWindowTitle("Addon Configuration")
        self.main_layout = QHBoxLayout()
        self.setStyleSheet("QWidget { font-size: 16px; }")

        self.form_layout = QFormLayout()
        self.form_widget = QWidget()
        self.form_widget.setLayout(self.form_layout)
        self.main_layout.addWidget(self.form_widget)

        self.setup_docs_panel()

        self.widgets = {}
        self.extra_config = {}

        self.init_ui()

        self.setLayout(self.main_layout)

    def setup_docs_panel(self):
        # Right side: Documentation panel
        self.docs_panel = QTextEdit()
        self.docs_panel.setReadOnly(True)
        self.docs_panel.setMarkdown(self.load_docs())
        self.main_layout.addWidget(self.docs_panel)

    def init_ui(self):
        """Initialize the configuration UI."""

        handled_keys = self.add_config_ui()

        self.add_remaining_config(handled_keys)
        self.add_custom_config_ui()
        self.add_action_buttons()

    def add_config_ui(self):
        handled_keys = set()
        default_config = mw.addonManager.addonConfigDefaults(__name__) or {}

        for key in default_config:
            value = self.config.get(key, default_config[key])

            if key == "custom_config":
                handled_keys.add(key)
                continue  # Handled separately

            widget_data = self.get_config_widget_data(key, value)

            if widget_data is None:
                continue

            self.form_layout.addRow(widget_data["label"], widget_data["row"])

            self.widgets[key] = widget_data
            handled_keys.add(key)

        return handled_keys

    def get_config_widget_data(self, name, value):
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

    def add_remaining_config(self, handled_keys):
        """Handle remaining config keys."""
        self.extra_config_edit = QTextEdit()
        self.form_layout.addRow(
            QLabel("Other Configuration Values (JSON):"), self.extra_config_edit
        )
        remaining_config = {
            k: v for k, v in self.config.items() if k not in handled_keys
        }
        if remaining_config:
            self.extra_config = remaining_config
            self.extra_config_edit.setPlainText(
                json.dumps(
                    remaining_config,
                    indent=4,
                    ensure_ascii=False,
                )
            )

    def add_custom_config_ui(self):
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
        self.update_custom_configs_ui()

        add_custom_config_button = QPushButton("Add Custom Config")
        add_custom_config_button.clicked.connect(
            lambda _: self.add_or_edit_custom_config()
        )
        self.form_layout.addRow(add_custom_config_button)

    def update_custom_configs_ui(self):
        """Refresh the display of custom configurations."""
        while self.custom_configs_content_grid_layout.count():
            child = self.custom_configs_content_grid_layout.takeAt(0)
            if widget := child.widget():
                widget.deleteLater()

        note_label = QLabel("Note Type")
        note_label.setStyleSheet("font-weight: bold;")

        deck_label = QLabel("Deck")
        deck_label.setStyleSheet("font-weight: bold;")

        for column, widget in enumerate([note_label, deck_label]):
            self.custom_configs_content_grid_layout.addWidget(widget, 0, column, 1, 1)

        for idx, custom_config in enumerate(self.config.get("custom_config", [])):
            note_type_id, deck_id, _ = custom_config
            note_type_name = mw.col.models.get(note_type_id)["name"]
            deck_name = mw.col.decks.get(deck_id)["name"]

            note_type_label = QLabel(note_type_name)
            deck_label = QLabel(deck_name)

            edit_button = QPushButton("Edit")
            edit_button.clicked.connect(
                lambda _, i=idx: self.add_or_edit_custom_config(i)
            )

            delete_button = QPushButton("Delete")
            delete_button.clicked.connect(lambda _, i=idx: self.delete_custom_config(i))

            # entry_layout = QHBoxLayout()
            # entry_layout.addWidget(entry_label)
            # entry_layout.addWidget(edit_button)
            # entry_layout.addWidget(delete_button)
            #
            # entry_widget = QWidget()
            # entry_widget.setLayout(entry_layout)

            for column, widget in enumerate(
                [note_type_label, deck_label, edit_button, delete_button]
            ):
                self.custom_configs_content_grid_layout.addWidget(
                    widget, idx + 1, column, 1, 1
                )

        # self.custom_configs_content_layout.addStretch()

    def add_or_edit_custom_config(self, index=None):
        """Add or edit a custom config."""
        if index is None:
            logger.debug("Adding new custom config.")
            note_type_id, deck_id, custom_settings = None, None, {}
        else:
            logger.debug("Editing existing custom config.")
            note_type_id, deck_id, custom_settings = self.config["custom_config"][index]

        dialog = CustomConfigDialog(self, note_type_id, deck_id, custom_settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated_entry = [dialog.note_type, dialog.deck, dialog.config]
            if index is None:
                self.config.setdefault("custom_config", []).append(updated_entry)
            else:
                self.config["custom_config"][index] = updated_entry
            self.update_custom_configs_ui()

    def delete_custom_config(self, index):
        """Delete a custom configuration."""
        self.config["custom_config"].pop(index)
        self.update_custom_configs_ui()

    def add_action_buttons(self):
        """Add Save and Cancel buttons."""
        button_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        self.form_layout.addRow(button_layout)

    def save(self):
        """Save the updated configuration."""
        update_config = {}
        delete_keys = set()
        for key, widget_data in self.widgets.items():
            update_config[key] = widget_data["get_fn"]()

        if self.extra_config:
            text = self.extra_config_edit.toPlainText().strip()

            if not text:
                success, data = True, {}
            else:
                success, data = self._load_json_dict(
                    self.extra_config_edit.toPlainText()
                )

            if success:
                data = {k: v for k, v in data.items() if k not in self.widgets}

                update_config.update(data)

                for key in self.extra_config:
                    if key not in data:
                        delete_keys.add(key)
            else:
                return

        self.config.update(update_config)

        for key in delete_keys:
            if key in self.config:
                del self.config[key]

        global config
        config = self.config

        mw.addonManager.writeConfig(__name__, self.config)
        self.accept()

    def _load_json_dict(self, data):
        """Helper method to validate JSON data."""
        try:
            parsed_data = json.loads(data)
            if not isinstance(parsed_data, dict):
                raise ValueError()
        except Exception:
            return False, "Invalid JSON in the configuration section."

        return True, parsed_data

    def load_docs(self):
        """Load the documentation from the config.md file."""
        docs_path = os.path.join(mw.addonManager.addonsFolder(), __name__, "config.md")
        if os.path.exists(docs_path):
            with open(docs_path, "r", encoding="utf-8") as f:
                return f.read()
        return "# Configuration Documentation\nDocumentation not found."


class CustomConfigDialog(ConfigDialog):
    excluded_keys = {
        "api_key",
        "debug",
        "note_tag",
        "processed_tag",
        "prompt_tag",
        "complete_tag",
        "run_on_sync",
        "confirm_before_adding_notes",
    }

    def __init__(
        self, parent: ConfigDialog, note_type=None, deck=None, custom_settings=None
    ):
        """
        Initialize the custom config dialog.

        Args:
            parent (ConfigDialog): The parent configuration dialog.
            note_type (str): The note type ID.
            deck (str): The deck ID.
            custom_settings (dict): The initial custom settings.
        """
        self.note_type = self.original_note_type = note_type
        self.deck = self.original_deck = deck

        if custom_settings is None:
            custom_settings = {}

        self.original_config = custom_settings
        self.config = copy.deepcopy(self.original_config)
        self.parent_config = parent.config

        super().__init__(parent=parent, config_data=self.config)

        self.setWindowTitle("Edit Custom Config")

    def init_ui(self):
        """Initialize the configuration UI."""

        self.init_note_type_and_deck_selectors()
        super().init_ui()

    def add_config_ui(self):
        handled_keys = set()
        for key, value in config.items():
            if key == "custom_config":
                handled_keys.add(key)
                continue  # Handled separately
            elif key in self.excluded_keys:
                handled_keys.add(key)
                continue

            if key in self.config:
                value = self.config[key]
                unset = False
            else:
                unset = True

            widget_data = self.get_config_widget_data(key, value, unset)

            if widget_data is None:
                continue

            self.form_layout.addRow(widget_data["label"], widget_data["row"])

            self.widgets[key] = widget_data
            handled_keys.add(key)

        return handled_keys

    def setup_docs_panel(self):
        """Remove the documentation panel for the custom configuration dialog."""
        pass

    def init_note_type_and_deck_selectors(self):
        """Initialize selectors for note type and deck."""
        # Note Type Selector
        self.note_type_selector = QComboBox()
        self.note_type_selector.addItem("")
        self.note_type_selector.addItems(
            [nt.name for nt in mw.col.models.all_names_and_ids()]
        )
        self.update_note_type_selector()
        self.note_type_selector.currentTextChanged.connect(self.check_note_type)
        self.form_layout.addRow("Note Type:", self.note_type_selector)

        # Deck Selector
        self.deck_selector = QComboBox()
        self.deck_selector.addItem("")
        self.deck_selector.addItems(
            [deck.name for deck in mw.col.decks.all_names_and_ids()]
        )
        self.deck_selector.currentTextChanged.connect(self.check_deck)
        self.update_deck_selector()
        self.form_layout.addRow("Deck: ", self.deck_selector)

    def update_note_type_selector(self):
        if self.note_type:
            note_type_name = mw.col.models.get(self.note_type)["name"]
        else:
            note_type_name = ""

        self.note_type_selector.setCurrentText(note_type_name)

    def update_deck_selector(self):
        if self.deck:
            deck_name = mw.col.decks.get(self.deck)["name"]
        else:
            deck_name = ""

        self.deck_selector.setCurrentText(deck_name)

    def get_config_widget_data(self, name, value, unset=False):
        """
        Override to add checkboxes for active/inactive status of each field.
        """
        widget_data = super().get_config_widget_data(name, value)

        if widget_data is None:
            return

        widget = widget_data["widget"]

        row_layout = QHBoxLayout()
        is_active_widget = QCheckBox()
        is_active_widget.setChecked(not unset)

        row_layout.addWidget(is_active_widget)
        row_layout.addWidget(widget)

        # Automatically activate/deactivate the checkbox based on user interaction
        change_event = widget_data["change_event"]
        if change_event is not None:
            change_event.connect(
                lambda: self.sync_checkbox_with_widget(
                    name, widget_data["get_fn"], is_active_widget
                )
            )
        # is_active_widget.stateChanged.connect(lambda: self.sync_widget_with_checkbox(name, widget, is_active_widget))

        widget_data["row"] = row_layout
        widget_data["is_active"] = is_active_widget

        return widget_data

    def sync_checkbox_with_widget(self, name, get_fn, checkbox):
        """
        Synchronize the checkbox state with the widget's content.

        - Activate the checkbox if the widget has a value.
        - Deactivate the checkbox if the widget is cleared.
        """
        value = get_fn()
        checkbox.setChecked(bool(value.strip()))

    # def sync_widget_with_checkbox(self, name, widget, checkbox):
    #     """
    #     Synchronize the widget state with the checkbox.
    #
    #     - Clear the widget if the checkbox is deactivated.
    #     """
    #     if not checkbox.isChecked():
    #         if isinstance(widget, QLineEdit):
    #             widget.clear()
    #         elif isinstance(widget, QTextEdit):
    #             widget.clear()
    #         elif isinstance(widget, QSpinBox) or isinstance(widget, QDoubleSpinBox):
    #             widget.setValue(0)

    def currently_selected_note_type(self):
        if note_type_name := self.note_type_selector.currentText():
            return mw.col.models.by_name(note_type_name)["id"]

        return None

    def check_note_type(self):
        note_type = self.currently_selected_note_type()

        # Nothing to do if note type hasn't changed
        if note_type == self.note_type:
            logger.debug("Chosen note type hasn't changed.")
            return

        if self.note_type_and_deck_combination_exists_in_parent_config(
            note_type, self.deck
        ):
            self.update_note_type_selector()
            logger.debug("Combination of note_type and deck already exists, returning.")
            return

        logger.debug(
            "Combination of note_type and deck does not exist. Changing note type."
        )

        self.note_type = note_type

    def currently_selected_deck(self):
        if deck_name := self.deck_selector.currentText():
            return mw.col.decks.by_name(deck_name)["id"]

        return None

    def check_deck(self):
        deck = self.currently_selected_deck()

        # Noting to do if deck hasn't changed
        if deck == self.deck:
            logger.debug("Chosen deck hasn't changed.")
            return

        if self.note_type_and_deck_combination_exists_in_parent_config(
            self.note_type, deck
        ):
            self.update_deck_selector()
            logger.debug("Combination of note_type and deck already exists, returning.")
            return

        logger.debug("Combination of note_type and deck does not exist. Changing deck.")

        self.deck = deck

    def note_type_and_deck_combination_exists_in_parent_config(self, note_type, deck):
        if note_type == self.original_note_type and deck == self.original_deck:
            logger.debug(
                "Combination of note_type and deck is the same as original. -> False"
            )
            return False

        custom_config = self.parent_config.get("custom_config", [])

        for nt, d, _ in custom_config:
            if (note_type == nt) and (deck == d):
                return True

        return False

    def save(self):
        """
        Save the custom configuration back to the parent's configuration.

        - Include only active fields in the custom configuration.
        - Remove inactive fields from the custom configuration.
        """

        if self.note_type is None and self.deck is None:
            showInfo("Please select either a note type, a deck or both.")
            return

        updated_config = {}
        for key, widget_data in self.widgets.items():

            if not widget_data["is_active"].isChecked():
                continue

            updated_config[key] = widget_data["get_fn"]()

        # Update only the relevant custom configuration entry
        self.original_config.clear()
        self.original_config.update(updated_config)
        self.config = self.original_config

        # Close the dialog
        self.accept()
