import re
from typing import List

from aqt import mw
from aqt.browser.browser import Browser
from aqt.qt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, qconnect, showInfo


class FormatStringDialog(QDialog):
    def __init__(self, field_count: int, parent: QWidget | None = None):
        super().__init__(parent or mw)
        self.setWindowTitle("Wordlist Export - Format String")
        self.resize(600, 250)
        self.layout = QVBoxLayout(self)

        self.layout.addWidget(
            QLabel(
                f"Enter a format string using field_1 ... field_{field_count}.\n"
                "Example: {field_1} - {field_2}"
            )
        )

        self.format_input = QTextEdit()
        self.format_input.setPlainText("{field_1}")
        self.layout.addWidget(self.format_input)

        self.layout.addWidget(
            QLabel(r"Join string (use \\n for newline, \\t for tab):")
        )
        self.join_input = QTextEdit()
        self.join_input.setFixedHeight(30)
        self.join_input.setPlainText(r"\n")
        self.layout.addWidget(self.join_input)

        self.button_box = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.button_box.addWidget(self.ok_button)
        self.button_box.addWidget(self.cancel_button)
        self.layout.addLayout(self.button_box)

    def get_format_string(self) -> str:
        return self.format_input.toPlainText().strip()

    def get_join_string(self) -> str:
        return self.join_input.toPlainText().strip()


class WordlistResultDialog(QDialog):
    def __init__(self, result_text: str, parent: QWidget | None = None):
        super().__init__(parent or mw)
        self.setWindowTitle("Wordlist Export - Result")
        self.resize(600, 400)
        self.layout = QVBoxLayout(self)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText(result_text)
        self.layout.addWidget(self.text_area)

        self.copy_button = QPushButton("Copy to Clipboard")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self.layout.addWidget(self.copy_button)

    def copy_to_clipboard(self):
        mw.app.clipboard().setText(self.text_area.toPlainText())


def transform_note_to_dict(note) -> dict:
    # Map fields to field_1, field_2, ...
    return {f"field_{i+1}": val for i, val in enumerate(note.fields)}


def format_notes(notes, format_string: str) -> List[str]:
    formatted = []
    for note in notes:
        d = transform_note_to_dict(note)
        try:
            formatted.append(format_string.format(**d))
        except Exception as e:
            formatted.append(f"[Format error: {e}]")
    return formatted


def wordlist_export_action(browser):
    selected_nids = browser.selected_notes()
    if not selected_nids:
        showInfo("No notes selected.", parent=browser)
        return

    notes = [browser.mw.col.get_note(nid) for nid in selected_nids]
    if not notes:
        showInfo("No notes found.", parent=browser)
        return

    field_count = len(notes[0].fields)
    dlg = FormatStringDialog(field_count, parent=browser)
    if not dlg.exec():
        return

    fmt = dlg.get_format_string()
    join_str = dlg.get_join_string()
    # Interpret escape sequences like \n and \t
    join_str = join_str.encode("utf-8").decode("unicode_escape")
    result_lines = format_notes(notes, fmt)
    result_text = join_str.join(result_lines)

    result_dlg = WordlistResultDialog(result_text, parent=browser)
    result_dlg.exec()


def add_wordlist_export_context_menu(browser_instance: Browser, menu: QWidget):
    action = QPushButton("Export Wordlist from Selected Notes")
    action = menu.addAction("Export Wordlist from Selected Notes")
    qconnect(action.triggered, lambda: wordlist_export_action(browser_instance))
