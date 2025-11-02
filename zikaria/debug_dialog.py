import json
from copy import deepcopy

from anki.notes import Note
from aqt import mw
from aqt.browser.browser import Browser

# from aqt.operations.note import add_note
from aqt.qt import (
    QAction,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo

# Imports from other modules in this addon
from .config_utils import config, get_effective_config, logger
from .prompt_store import get_default_prompt_template_key
from .prompts import (
    example_notes,
    generate_pydantic_class,
    generate_schema_class,
    get_prompt_text_from_note,
    get_prompt_text_from_note_by_mode,
)
from .saved_prompts_manager_dialog import PromptTemplateChooser

# from aqt.editor import Editor, EditorMode


class DebugDialog(QDialog):
    def __init__(
        self,
        note: Note,
        parent: QWidget | None = None,
    ):
        super().__init__(parent or mw)
        self.note = note
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
            starting_template=get_default_prompt_template_key(),
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
            # TODO: Add this function back!
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
            prompt = get_prompt_text_from_note_by_mode(
                note=self.note,
                mode="create",
                **self.generate_get_prompt_text_args("create"),
            )
            self.create_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.create_prompt_output_area.setPlainText(
                f"Error generating create prompt: {e}"
            )
            logger.exception("DebugDialog: Error generating create prompt.")

    def display_complete_prompt_handler(self):
        logger.debug("DebugDialog: Displaying complete prompt.")

        try:
            prompt = get_prompt_text_from_note_by_mode(
                note=self.note,
                mode="complete",
                **self.generate_get_prompt_text_args("complete"),
            )
            self.complete_prompt_output_area.setPlainText(prompt)
        except Exception as e:
            self.complete_prompt_output_area.setPlainText(
                f"Error generating complete prompt: {e}"
            )
            logger.exception("DebugDialog: Error generating complete prompt.")

    def display_prompt_handler(self):
        logger.debug("DebugDialog: Displaying prompt.")

        dummy_keys: set[str] = set()
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


def add_debug_menu_to_browser_action(browser_instance: Browser):
    if config.debug:
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
    if not config.debug:
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
