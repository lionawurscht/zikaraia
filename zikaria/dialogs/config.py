import json
import os

# --- ConfigDialog & CustomConfigDialog ---
from copy import deepcopy
from typing import Any, Dict, Optional

from anki.decks import DeckId  # Added DeckId
from anki.models import NotetypeId  # Added NotetypeId
from aqt import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    mw,
)

# from aqt.operations.note import add_note
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo

# Imports from other modules in this addon
from ..config_utils import ADDON_NAME, config, logger, update_config
from ..ui import ChoosersMixin
from .saved_prompts_manager import PromptTemplateChooser

# from aqt.editor import Editor, EditorMode


class ConfigDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        config_data_override: dict | None = None,
        addon_name_param: str = "",
    ):
        super().__init__(parent or mw)
        self.resize(1200, 1000)

        self.addon_name = ADDON_NAME

        if config_data_override is None:
            current_addon_config_obj = config.config.model_dump()
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

        default_config_keys = mw.addonManager.addonConfigDefaults(self.addon_name) or {}

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
    ) -> dict[str, Any] | None:  # Renamed
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
            widget.setRange(0, 100000)
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

        update_config(self.config_to_edit)

        self.accept()


# --- CustomConfigDialog ---
class CustomConfigDialog(ConfigDialog, ChoosersMixin):  # Inherits from ConfigDialog
    def __init__(
        self,
        parent: ConfigDialog,
        note_type_id_param: NotetypeId | None = None,
        deck_id_param: DeckId | None = None,
        custom_settings_data: dict[str, Any] | None = None,
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
