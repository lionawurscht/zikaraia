from typing import Any, cast

from anki.collection import Collection, SearchNode
from anki.decks import DeckId  # Added DeckId
from anki.models import NotetypeId  # Added NotetypeId
from anki.notes import Note
from aqt import mw
from aqt.deckchooser import DeckChooser

# from aqt.editor import Editor, EditorMode
from aqt.notetypechooser import NotetypeChooser

# from aqt.operations.note import add_note
from aqt.qt import (
    QDialog,
    QGuiApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from aqt.studydeck import StudyDeck

# Imports from other modules in this addon
from .config_utils import logger


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
