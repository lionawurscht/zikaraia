import json
from dataclasses import dataclass
from typing import Any

import aqt
from anki.collection import SearchNode
from anki.notes import Note
from aqt import QFrame, QScrollArea, Qt, QTimer, mw
from aqt.operations import QueryOp
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from aqt.tagedit import TagEdit
from aqt.theme import colors, theme_manager
from aqt.utils import shortcut, tr

from .config_utils import logger
from .types import ZikariaResponseData
from .ui import get_duplicate_note_ids_by_checksum


class DuplicateResolutionDialog(QDialog):
    def __init__(
        self,
        new_card_data: dict[str, Any],
        new_note_template: Note,
        duplicate_nids: list[int],
        parent: QWidget | None = None,
    ):
        super().__init__(parent or mw)
        self.setWindowTitle("Resolve Duplicates (Synchronized)")
        self.new_note_template = new_note_template

        # Central store for the new card data being edited
        self.current_new_data = new_card_data.copy()

        # Store original values for restore functionality
        self.original_field_values = new_card_data.copy()

        # Stores ALL QLineEdit instances, keyed by field_name, as a list
        # Example: {'Front': [QLineEdit@tab1, QLineEdit@tab2, ...]}
        self.field_widgets: dict[str, list[QLineEdit]] = {}

        self.duplicate_notes: list[Note] = [
            mw.col.get_note(nid) for nid in duplicate_nids if mw.col.get_note(nid)
        ]

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        self.resize(1200, 800)

        # Tab Widget for Duplicates
        self.tab_widget = QTabWidget(self)

        if not self.duplicate_notes:
            main_layout.addWidget(QLabel("Error: Could not load any duplicate notes."))
            return

        # Create a comparison tab for each duplicate
        for i, dupe_note in enumerate(self.duplicate_notes):
            deck_name = None

            dupe_deck_id = dupe_note.cards()[0].did if dupe_note.cards() else None
            if dupe_deck_id:
                deck_name = mw.col.decks.name_if_exists(dupe_deck_id)

            if deck_name:
                dupe_tab_name = f"{i+1}: {deck_name}"
            else:
                dupe_tab_name = f"{i+1} (ID: {dupe_note.id})"
            tab_widget = self._create_comparison_tab(dupe_note)
            self.tab_widget.addTab(tab_widget, dupe_tab_name)

        main_layout.addWidget(self.tab_widget)

        # Dialog Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

    def _create_comparison_tab(self, dupe_note: Note) -> QWidget:
        """Creates a widget for comparing the new card against a single duplicate."""
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)

        comparison_group = QGroupBox("Field Comparison")
        comparison_layout = QGridLayout(comparison_group)

        # Headers
        comparison_layout.addWidget(QLabel("<b>F-Action</b>"), 0, 0)
        comparison_layout.addWidget(QLabel("<b>Field</b>"), 0, 1)
        comparison_layout.addWidget(QLabel("<b>New Card Value (Editable)</b>"), 0, 2)
        comparison_layout.addWidget(QLabel("<b>Duplicate Value</b>"), 0, 3)
        comparison_layout.addWidget(QLabel("<b>D-Action</b>"), 0, 4)

        # Fields
        for row, field_def in enumerate(self.new_note_template.note_type()["flds"]):
            field_name = field_def["name"]

            # --- New Card Field (Editable) ---
            # 1. Create a NEW QLineEdit instance for this specific tab
            new_value = self.current_new_data.get(field_name, "")
            new_edit = QLineEdit(new_value)
            new_edit.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

            # 2. Store the widget instance in the dictionary for synchronization
            self.field_widgets.setdefault(field_name, []).append(new_edit)

            # 3. Connect the signal to the synchronization slot
            new_edit.textEdited.connect(
                # Use default args to pass the field name correctly to the slot
                lambda text, name=field_name: self._update_field_value(name, text)
            )

            # --- Duplicate Field (Read Only) ---
            dupe_value = dupe_note.fields[field_def["ord"]]
            dupe_edit = QLineEdit(dupe_value)
            dupe_edit.setReadOnly(True)
            if theme_manager.night_mode:
                dupe_edit.setStyleSheet("background-color: #0a0a0a;")
            else:
                dupe_edit.setStyleSheet("background-color: #f0f0f0;")

            dupe_edit.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )

            # --- Action Buttons Container ---
            # 1. Create the container widget and its layout
            f_action_widget = QWidget()
            f_action_layout = QHBoxLayout(f_action_widget)
            f_action_layout.setContentsMargins(
                0, 0, 0, 0
            )  # Remove margin/spacing around buttons
            f_action_layout.setSpacing(5)  # Set a small spacing between the buttons

            # "Restore" Button
            restore_btn = QPushButton("Restore")
            restore_btn.clicked.connect(
                lambda _, n=field_name, v=self.original_field_values[
                    field_name
                ]: self._update_field_value(n, v)
            )

            # "Selte" Button
            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(
                lambda _, n=field_name, v="": self._update_field_value(n, v)
            )

            f_action_layout.addWidget(restore_btn)
            f_action_layout.addWidget(clear_btn)

            # --- Action Buttons Container ---
            # 1. Create the container widget and its layout
            d_action_widget = QWidget()
            d_action_layout = QHBoxLayout(d_action_widget)
            d_action_layout.setContentsMargins(
                0, 0, 0, 0
            )  # Remove margin/spacing around buttons
            d_action_layout.setSpacing(5)  # Set a small spacing between the buttons

            # "Copy" Button
            copy_btn = QPushButton("Copy Dupe")
            # Connect the button to update the central data and ALL widgets
            copy_btn.clicked.connect(
                lambda _, n=field_name, v=dupe_value: self._update_field_value(n, v)
            )

            # "Copy" Button
            append_btn = QPushButton("Append Dupe")
            # Connect the button to update the central data and ALL widgets
            append_btn.clicked.connect(
                lambda _, n=field_name, v=dupe_value: self._append_field_value(n, v)
            )

            # Add buttons to the inner layout
            d_action_layout.addWidget(copy_btn)
            d_action_layout.addWidget(append_btn)

            # Add to layout
            comparison_layout.addWidget(f_action_widget, row + 1, 0)
            comparison_layout.addWidget(QLabel(f"<b>{field_name}</b>"), row + 1, 1)
            comparison_layout.addWidget(new_edit, row + 1, 2)
            comparison_layout.addWidget(dupe_edit, row + 1, 3)
            comparison_layout.addWidget(d_action_widget, row + 1, 4)

        tab_layout.addWidget(comparison_group)

        # Bulk Actions
        bulk_layout = QHBoxLayout()

        restore_all_btn = QPushButton("Restore All")
        restore_all_btn.clicked.connect(lambda: self._restore_all())
        bulk_layout.addWidget(restore_all_btn)

        overwrite_all_btn = QPushButton("Overwrite All Fields from Dupe")
        overwrite_all_btn.clicked.connect(lambda: self._copy_all_from_dupe(dupe_note))
        bulk_layout.addWidget(overwrite_all_btn)

        copy_nonempty_btn = QPushButton("Copy Non-Empty Fields from Dupe")
        copy_nonempty_btn.clicked.connect(
            lambda: self._copy_nonempty_from_dupe(dupe_note)
        )
        bulk_layout.addWidget(copy_nonempty_btn)

        open_dupe_btn = QPushButton("Open Dupe in Browser")
        open_dupe_btn.clicked.connect(lambda: self._open_dupe_in_browser(dupe_note.id))
        bulk_layout.addWidget(open_dupe_btn)

        tab_layout.addLayout(bulk_layout)
        tab_layout.addStretch(1)

        return tab_widget

    def _append_field_value(self, field_name: str, new_value: str) -> None:
        """Appends to a field, updates the central data store and synchronizes all widgets for a field."""

        # 1. Update the central data store
        old_value = self.current_new_data[field_name]
        new_value = "".join([old_value, new_value])
        self.current_new_data[field_name] = new_value

        # 2. Synchronize all QLineEdits for this field name
        # We temporarily block signals to prevent an infinite loop (A updates B, B updates A, etc.)
        if field_name in self.field_widgets:
            for widget in self.field_widgets[field_name]:
                if widget.text() != new_value:  # Only update if necessary
                    widget.blockSignals(True)
                    widget.setText(new_value)
                    widget.blockSignals(False)

    def _restore_all(self):
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            original_value = self.original_field_values[field_name]

            # Use the synchronized method to update all fields
            self._update_field_value(field_name, original_value)

    def _update_field_value(self, field_name: str, new_value: str) -> None:
        """Updates the central data store and synchronizes all widgets for a field."""

        # 1. Update the central data store
        self.current_new_data[field_name] = new_value

        # 2. Synchronize all QLineEdits for this field name
        # We temporarily block signals to prevent an infinite loop (A updates B, B updates A, etc.)
        if field_name in self.field_widgets:
            for widget in self.field_widgets[field_name]:
                if widget.text() != new_value:  # Only update if necessary
                    widget.blockSignals(True)
                    widget.setText(new_value)
                    widget.blockSignals(False)

    def _copy_all_from_dupe(self, dupe_note: Note) -> None:
        """Copies all field values from the selected duplicate by updating the central store."""
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            dupe_value = dupe_note.fields[field_def["ord"]]

            # Use the synchronized method to update all fields
            self._update_field_value(field_name, dupe_value)

    def _copy_nonempty_from_dupe(self, dupe_note: Note) -> None:
        """Copies only non-empty field values from the duplicate to the new card."""
        for field_def in self.new_note_template.note_type()["flds"]:
            field_name = field_def["name"]
            dupe_value = dupe_note.fields[field_def["ord"]]
            if dupe_value:
                self._update_field_value(field_name, dupe_value)

    def _open_dupe_in_browser(self, dupe_nid: int) -> None:
        """Opens the Anki Browser filtered to the specific duplicate Note ID."""
        aqt.dialogs.open("Browser", mw, search=f"nid:{dupe_nid}")

    def get_resolved_data(self) -> dict[str, Any]:
        """Collects the final data from the central, authoritative data store."""
        return self.current_new_data


@dataclass
class CardWidgetState:
    checkbox: QCheckBox
    field_widgets_map: dict[str, QLineEdit]
    note_template: Note
    tag_edit_widget: "TagEdit"
    resolve_dupe_btn: QPushButton


@dataclass
class WidgetToCheck:
    temp_note: Note
    card_data_item_dict: dict[str, Any]
    card_frame: QWidget
    global_idx: int


@dataclass
class CardResponseMapEntry:
    response_idx: int
    note_idx: int


class NotesResponsesConfirmationDialog(QDialog):
    def __init__(
        self,
        notes_responses: list["ZikariaResponseData"],
        parent: QWidget | None = None,
        global_tags: list[str] | None = None,
    ):
        super().__init__(parent or mw)
        self.setWindowTitle("Confirm New Cards (Responses)")
        self.resize(1200, 800)
        self.notes_responses: list[ZikariaResponseData] = notes_responses
        self.global_tags: list[str] = global_tags or []

        # Internal state
        self.selected_cards_flags: list[bool] = []
        self.duplicate_nids_map: dict[int, list[int]] = {}
        self.widgets_to_check: list[WidgetToCheck] = []
        self.card_widgets_list_internal: list[CardWidgetState] = []
        self.card_response_map: list[CardResponseMapEntry] = []

        self.setStyleSheet("QWidget { font-size: 16px; }")
        self.main_layout: QVBoxLayout = QVBoxLayout()
        self.setLayout(self.main_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_content_widget.setLayout(scroll_layout)

        card_global_idx = 0
        for response_idx, response in enumerate(notes_responses):
            notetype_dict = response.request_data.notetype_dict
            mid = notetype_dict.get("id")
            for note_idx, card_data_item_dict in enumerate(response.notes_data_list):
                note_template = Note(mw.col, mid)
                # Fill fields from card_data_item_dict
                for field_def in note_template.note_type()["flds"]:
                    fname = field_def["name"]
                    value = card_data_item_dict.get(fname, "")
                    note_template.fields[field_def["ord"]] = value

                card_frame = QFrame()
                card_layout = QVBoxLayout()
                header_layout = QHBoxLayout()
                card_checkbox = QCheckBox(f"Card {card_global_idx + 1}")
                card_checkbox.setChecked(True)
                _ = card_checkbox.stateChanged.connect(
                    lambda state, idx=card_global_idx: self.toggle_card_selection(
                        idx, state
                    )
                )
                header_layout.addWidget(card_checkbox)

                resolve_dupe_btn = QPushButton("Checking Duplicates...")
                resolve_dupe_btn.setToolTip(
                    "Opens a dialog to resolve field-level conflicts."
                )
                resolve_dupe_btn.setEnabled(False)
                _ = resolve_dupe_btn.clicked.connect(
                    lambda _, idx=card_global_idx: self._show_resolve_dialog(idx)
                )
                header_layout.addWidget(resolve_dupe_btn)
                card_layout.addLayout(header_layout)

                field_widgets_map: dict[str, QLineEdit] = {}
                note_type_model = note_template.note_type()
                if not note_type_model:
                    logger.error("ConfirmationDialog: Note template has no model.")
                    continue

                present_keys: set[str] = set()
                temp_note = Note(mw.col, note_template.mid)
                for field_def in note_type_model["flds"]:
                    field_name = field_def["name"]
                    value_str = card_data_item_dict.get(field_name, "")
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
                    temp_note.fields[field_def["ord"]] = field_edit.text()

                tags_list = card_data_item_dict.get("tags", [])
                if "tags" in card_data_item_dict:
                    present_keys.add("tags")

                unused_data_keys = [
                    key
                    for key in card_data_item_dict
                    if key not in present_keys and not key.startswith("__")
                ]
                if unused_data_keys:
                    rest_layout = QHBoxLayout()
                    rest_label = QLabel("Unused data:")
                    rest_edit = QLineEdit(
                        json.dumps(
                            {key: card_data_item_dict[key] for key in unused_data_keys}
                        )
                    )
                    rest_edit.setReadOnly(True)
                    rest_layout.addWidget(rest_label)
                    rest_layout.addWidget(rest_edit)
                    card_layout.addLayout(rest_layout)

                card_tag_edit_widget = self._get_card_tag_edit(tags_list, card_layout)

                self.card_widgets_list_internal.append(
                    CardWidgetState(
                        checkbox=card_checkbox,
                        field_widgets_map=field_widgets_map,
                        note_template=note_template,
                        tag_edit_widget=card_tag_edit_widget,
                        resolve_dupe_btn=resolve_dupe_btn,
                    )
                )
                self.widgets_to_check.append(
                    WidgetToCheck(
                        temp_note=temp_note,
                        card_data_item_dict=card_data_item_dict,
                        card_frame=card_frame,
                        global_idx=card_global_idx,
                    )
                )
                self.selected_cards_flags.append(True)
                self.card_response_map.append(
                    CardResponseMapEntry(
                        response_idx=response_idx,
                        note_idx=note_idx,
                    )
                )

                card_frame.setLayout(card_layout)
                scroll_layout.addWidget(card_frame)
                card_global_idx += 1

        self._first_field_timers: dict[int, QTimer] = {}
        for idx, card_state in enumerate(self.card_widgets_list_internal):
            first_field_name = next(iter(card_state.field_widgets_map))
            first_field_edit = card_state.field_widgets_map[first_field_name]
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(500)
            _ = timer.timeout.connect(lambda idx=idx: self._check_dupe_for_card(idx))
            self._first_field_timers[idx] = timer

            def on_first_field_changed(_text, idx=idx):
                self._first_field_timers[idx].start()

            _ = first_field_edit.textChanged.connect(on_first_field_changed)

        scroll_area.setWidget(scroll_content_widget)
        self.main_layout.addWidget(scroll_area)

        bulk_action_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        _ = select_all_button.clicked.connect(self.select_all_cards)
        bulk_action_layout.addWidget(select_all_button)
        deselect_all_button = QPushButton("Select None")
        _ = deselect_all_button.clicked.connect(self.deselect_all_cards)
        bulk_action_layout.addWidget(deselect_all_button)
        invert_selection_button = QPushButton("Invert Selection")
        _ = invert_selection_button.clicked.connect(self.invert_card_selection)
        bulk_action_layout.addWidget(invert_selection_button)
        remove_all_tags_button = QPushButton("Remove All Tags")
        remove_all_tags_button.setToolTip(
            "Remove all tags from all cards (does not affect global tags)"
        )
        _ = remove_all_tags_button.clicked.connect(self.remove_all_card_tags)
        bulk_action_layout.addWidget(remove_all_tags_button)
        self.main_layout.addLayout(bulk_action_layout)

        self._setup_global_tag_edit(global_tags or [])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        _ = buttons.accepted.connect(self.accept)
        _ = buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(buttons)

        self._check_dupes_on_open()

    def _setup_global_tag_edit(self, global_tags_list: list[str]) -> None:
        tag_edit_frame = QWidget(self)
        tag_edit_frame.setStyleSheet("border: 0")
        tag_edit_layout = QGridLayout()
        tag_edit_layout.setSpacing(12)
        tag_edit_layout.setContentsMargins(2, 6, 2, 6)
        tag_edit_label = QLabel(tr.editing_tags())
        tag_edit_layout.addWidget(tag_edit_label, 1, 0)
        self.global_tag_edit_widget_internal: TagEdit = TagEdit(self)
        self.global_tag_edit_widget_internal.setToolTip(
            shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
        )
        border = theme_manager.var(colors.BORDER)
        self.global_tag_edit_widget_internal.setStyleSheet(
            f"border: 1px solid {border}"
        )
        tag_edit_layout.addWidget(self.global_tag_edit_widget_internal, 1, 1)
        tag_edit_frame.setLayout(tag_edit_layout)
        self.main_layout.addWidget(tag_edit_frame)
        if self.global_tag_edit_widget_internal.col != mw.col:
            self.global_tag_edit_widget_internal.setCol(mw.col)
        self.global_tag_edit_widget_internal.setText(mw.col.tags.join(global_tags_list))

    def _get_card_tag_edit(
        self, tags_list: list[str], card_layout_ref: QVBoxLayout
    ) -> TagEdit:
        tag_layout = QHBoxLayout()
        tag_label = QLabel(f"{tr.editing_tags()}:")
        tag_edit_widget = TagEdit(self)
        tag_edit_widget.setToolTip(
            shortcut(tr.editing_jump_to_tags_with_ctrlandshiftandt())
        )
        if tag_edit_widget.col != mw.col:
            tag_edit_widget.setCol(mw.col)
        tag_edit_widget.setText(mw.col.tags.join(tags_list))
        tag_layout.addWidget(tag_label)
        tag_layout.addWidget(tag_edit_widget)
        card_layout_ref.addLayout(tag_layout)
        return tag_edit_widget

    def remove_all_card_tags(self):
        for card_state in self.card_widgets_list_internal:
            card_state.tag_edit_widget.setText("")

    def _check_dupes_on_open(self) -> None:
        """Start asynchronous duplicate checks for all cards."""
        for widget_to_check in self.widgets_to_check:
            card_state = self.card_widgets_list_internal[widget_to_check.global_idx]
            QueryOp(
                parent=self,
                op=lambda _, temp_note=widget_to_check.temp_note, note_template=card_state.note_template: get_duplicate_note_ids_by_checksum(
                    temp_note, note_template
                ),
                success=lambda nids, idx=widget_to_check.global_idx, btn=card_state.resolve_dupe_btn: self._store_nids_and_update_button(
                    nids, idx, btn
                ),
            ).run_in_background()

    def _check_dupe_for_card(self, global_idx: int) -> None:
        widget_to_check = self.widgets_to_check[global_idx]
        card_state = self.card_widgets_list_internal[global_idx]

        # Update temp_note's first field to current value
        field_widgets_map = card_state.field_widgets_map
        note_template = card_state.note_template
        dupe_btn = card_state.resolve_dupe_btn
        first_field_name = next(iter(field_widgets_map))
        first_field_edit = field_widgets_map[first_field_name]

        # Update temp_note's first field
        note_type_model = note_template.note_type()

        if not note_type_model:
            return

        first_field_ord = note_type_model["flds"][0]["ord"]
        widget_to_check.temp_note.fields[first_field_ord] = first_field_edit.text()
        QueryOp(
            parent=self,
            op=lambda _: get_duplicate_note_ids_by_checksum(
                widget_to_check.temp_note, note_template
            ),
            success=lambda nids, idx=global_idx, btn=dupe_btn: self._store_nids_and_update_button(
                nids, idx, btn
            ),
        ).run_in_background()

    def _store_nids_and_update_button(
        self, nids: list[int], global_idx: int, dupe_btn: QPushButton
    ) -> None:
        """Store the found NIDs and update the button's state and text."""
        if nids:
            self.duplicate_nids_map[global_idx] = nids
            dupe_btn.setStyleSheet("border: 2px solid red;")
            dupe_btn.setText(f"Resolve Duplicate ({len(nids)})")
            dupe_btn.setEnabled(True)
        else:
            dupe_btn.setStyleSheet("border: 2px solid green;")
            dupe_btn.setText("No Duplicates Found")
            dupe_btn.setEnabled(False)

    def _show_resolve_dialog(self, global_idx: int) -> None:
        """Opens the comparison dialog for a specific card."""
        dupe_nids = self.duplicate_nids_map.get(global_idx)
        if not dupe_nids:
            self._open_browser_to_dupes_by_first_field(global_idx)
            return

        card_state = self.card_widgets_list_internal[global_idx]

        # Reconstruct the current card data from the QLineEdits
        current_card_data: dict[str, Any] = {
            field_name: widget.text()
            for field_name, widget in card_state.field_widgets_map.items()
        }
        current_card_data["tags"] = mw.col.tags.split(card_state.tag_edit_widget.text())

        # Open the new resolution dialog
        resolution_dialog = DuplicateResolutionDialog(
            new_card_data=current_card_data,
            new_note_template=card_state.note_template,
            duplicate_nids=dupe_nids,
            parent=self,
        )

        if resolution_dialog.exec() == QDialog.DialogCode.Accepted:
            resolved_data = resolution_dialog.get_resolved_data()

            for field_name, new_value in resolved_data.items():
                if field_name != "tags":
                    card_state.field_widgets_map[field_name].setText(new_value)

            if "tags" in resolved_data:
                new_tags_text = mw.col.tags.join(resolved_data["tags"])
                card_state.tag_edit_widget.setText(new_tags_text)

            card_state.resolve_dupe_btn.setText("Duplicates Resolved (Manual)")
            card_state.resolve_dupe_btn.setStyleSheet("border: 2px solid blue;")

    def _open_browser_to_dupes_by_first_field(self, global_idx: int) -> None:
        """Opens the Anki Browser using the official SearchNode structure."""
        card_state = self.card_widgets_list_internal[global_idx]

        note_type_id = card_state.note_template.note_type()["id"]
        first_field_value = next(iter(card_state.field_widgets_map.values())).text()

        aqt.dialogs.open(
            "Browser",
            mw,
            search=(
                SearchNode(
                    dupe=SearchNode.Dupe(
                        notetype_id=note_type_id,
                        first_field=first_field_value,
                    )
                ),
            ),
        )

    def toggle_card_selection(self, index: int, state: int) -> None:
        self.selected_cards_flags[index] = Qt.CheckState(state) == Qt.CheckState.Checked

    def select_all_cards(self) -> None:
        for card_state in self.card_widgets_list_internal:
            card_state.checkbox.setChecked(True)

    def deselect_all_cards(self) -> None:
        for card_state in self.card_widgets_list_internal:
            card_state.checkbox.setChecked(False)

    def invert_card_selection(self) -> None:
        for card_state in self.card_widgets_list_internal:
            card_state.checkbox.setChecked(not card_state.checkbox.isChecked())

    def get_confirmed_notes_responses(self) -> list["ZikariaResponseData"]:
        global_tags_set = set(
            mw.col.tags.split(self.global_tag_edit_widget_internal.text())
        )
        # Build new notes_responses list
        updated_notes_map: dict[tuple[int, int], dict[str, Any]] = {}
        for idx, selected_flag in enumerate(self.selected_cards_flags):
            if selected_flag:
                card_state = self.card_widgets_list_internal[idx]
                tags_set = set(mw.col.tags.split(card_state.tag_edit_widget.text()))
                tags_set.update(global_tags_set)
                updated_data = {
                    field_name: widget.text()
                    for field_name, widget in card_state.field_widgets_map.items()
                }
                updated_data["tags"] = list(tags_set)
                response_idx = self.card_response_map[idx].response_idx
                note_idx = self.card_response_map[idx].note_idx
                updated_notes_map[(response_idx, note_idx)] = updated_data
        # Build new ZikariaResponseData list
        new_notes_responses = []
        for response_idx, response in enumerate(self.notes_responses):
            new_notes_data_list = []
            for note_idx, _ in enumerate(response.notes_data_list):
                if (response_idx, note_idx) in updated_notes_map:
                    new_notes_data_list.append(
                        updated_notes_map[(response_idx, note_idx)]
                    )
            new_notes_responses.append(
                ZikariaResponseData(
                    request_data=response.request_data,
                    notes_data_list=new_notes_data_list,
                )
            )
        return new_notes_responses


def display_notes_responses_confirmation_dialog(
    notes_responses: list[ZikariaResponseData],
    parent: QWidget | None = None,
    global_tags: list[str] | None = None,
) -> list[ZikariaResponseData]:
    dialog = NotesResponsesConfirmationDialog(
        notes_responses=notes_responses,
        global_tags=global_tags,
        parent=parent,
    )
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_confirmed_notes_responses()
    return []
