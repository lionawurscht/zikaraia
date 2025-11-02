import uuid
from typing import Callable, Literal, Optional

from aqt import QMainWindow, QMessageBox, mw
from aqt.qt import (
    QCloseEvent,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.studydeck import StudyDeck
from aqt.utils import getText, tr

from ..config_utils import config, logger, update_config
from ..prompt_store import load_saved_prompts, save_saved_prompts
from ..ui import ChoosersMixin


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
        logger.debug("Couldn't find entry for: %s", target_key)

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
        on_template_changed: Callable[[str], None] | None = None,
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
            logger.debug("TemplateChooser: Couldn't find entry for: %s", target_key)

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

            if config["last_prompt_template_key"] != (
                new_key := self.selected_template_key()
            ):
                logger.debug("Prompt template key changed, is: %s", new_key)
                config["last_prompt_template_key"] = new_key
                update_config(config)

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


# --- open_prompt_text_dialog_action ---
def open_saved_prompts_manager():
    dialog = SavedPromptManagerDialog()
    dialog.exec()


# --- Action functions for menu items (browser related) ---
