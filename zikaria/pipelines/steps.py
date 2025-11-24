# zikaria/pipelines/steps.py

import json

from anki.notes import Note
from aqt import mw
from aqt.qt import QObject, QRunnable, pyqtSignal
from google.genai import types
from google.genai.errors import ServerError
from requests.exceptions import ConnectionError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..anki_utils import gemini_client_proxy, normalize_ai_response
from ..config_utils import config, get_effective_config_for_note
from ..dialogs.notes_confirmation import display_notes_responses_confirmation_dialog
from ..prompts import generate_pydantic_class, get_prompt_text_from_string_by_mode
from ..types import PromptMode, ZikariaRequestData, ZikariaResponseData
from .core import PipelineContext


class PreparePrompts:
    """Prepares Gemini API requests from a list of Anki notes."""

    def __init__(self, mode: PromptMode):
        self.mode = mode

    def __call__(self, context: PipelineContext) -> PipelineContext:
        notes: list[Note] = context.source_data
        requests = []

        for note in notes:
            effective_conf = get_effective_config_for_note(note)
            notetype_dict = note.note_type()
            prompt_text = note.fields[0]

            lines = str(prompt_text).splitlines()
            chunk_size = config.get("chunk_size", 3)
            chunks = [
                lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)
            ]

            for chunk_index, chunk in enumerate(chunks):
                chunked_prompt = "\n".join(chunk)
                prompt_str = get_prompt_text_from_string_by_mode(
                    mode=self.mode,
                    prompt=chunked_prompt,
                    config_=effective_conf,
                    note_type=notetype_dict,
                )

                req_data = ZikariaRequestData(
                    mode=self.mode,
                    prompt_str=prompt_str,
                    effective_conf=effective_conf,
                    original_note_id=note.id,
                    original_note_mod=note.mod,
                    notetype_dict=notetype_dict,
                    metadata={"mode": self.mode, "chunk_index": chunk_index},
                )
                requests.append(req_data)

        context.misc["requests"] = requests
        return context


class AIWorker(QRunnable):
    """Worker to send a single AI request."""

    def __init__(self, request_data: ZikariaRequestData):
        super().__init__()
        self.request_data = request_data
        self.signals = self.WorkerSignals()

    class WorkerSignals(QObject):
        finished = pyqtSignal(object)  # Emits ZikariaResponseData
        error = pyqtSignal(object, Exception)  # Emits request_data, error

    def run(self):
        try:
            notes_data_list = self.send_request(self.request_data)
            if notes_data_list is not None:
                response = ZikariaResponseData(self.request_data, notes_data_list)
                self.signals.finished.emit(response)
        except Exception as e:
            self.signals.error.emit(self.request_data, e)

    def send_request(self, request_data: ZikariaRequestData):
        """Sends a single request with retry logic and parses the response."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            retry=retry_if_exception_type(
                (TimeoutError, ConnectionError, json.JSONDecodeError, ServerError)
            ),
        )
        def _send_and_parse():
            response = self._send_prompt_to_ai(request_data)
            return self._parse_response_into_notes_data(response, request_data)

        return _send_and_parse()

    def _send_prompt_to_ai(self, request_data: ZikariaRequestData):
        model_name = request_data.effective_conf.model_name
        max_tokens = request_data.effective_conf.max_output_tokens
        temperature = request_data.effective_conf.model_temperature
        pydantic_class = request_data.pydantic_class or generate_pydantic_class(
            request_data.notetype_dict
        )
        api_config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_mime_type": "application/json",
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
            "response_schema": list[pydantic_class],
        }
        return gemini_client_proxy.client.models.generate_content(
            model=model_name,
            contents=request_data.prompt_str,
            config=types.GenerateContentConfig(**api_config),
        )

    def _parse_response_into_notes_data(
        self, response: object, request_data: ZikariaRequestData
    ):
        response_text = getattr(response, "text", None)
        if not response_text:
            return None
        try:
            notes_data = json.loads(response_text)
        except json.JSONDecodeError:
            return None
        pydantic_class = request_data.pydantic_class or generate_pydantic_class(
            request_data.notetype_dict
        )
        return normalize_ai_response(notes_data, pydantic_class)


class SendToAI(QObject):
    """Sends requests to the Gemini API concurrently using QThreadPool."""

    is_async = True
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()

    def __call__(self, context: PipelineContext) -> PipelineContext:
        self.context = context
        self.requests = context.misc.get("requests", [])
        self.total_tasks = len(self.requests)
        self.finished_tasks = 0
        self.responses = []
        self.errors = []

        if not self.requests:
            self.finished.emit()
            return self.context

        for req in self.requests:
            worker = AIWorker(req)
            worker.signals.finished.connect(self._on_task_finished)
            worker.signals.error.connect(self._on_task_error)
            mw.threadPool.start(worker)

        return self.context

    def _on_task_finished(self, response: ZikariaResponseData):
        self.responses.append(response)
        self._check_all_finished()

    def _on_task_error(self, request_data: ZikariaRequestData, error: Exception):
        self.errors.append({"request": request_data, "error": error})
        self._check_all_finished()

    def _check_all_finished(self):
        self.finished_tasks += 1
        if self.finished_tasks == self.total_tasks:
            self.context.ai_responses = self.responses
            self.context.misc["ai_errors"] = self.errors
            self.finished.emit()


class ShowConfirmationDialog:
    """Displays the notes confirmation dialog to the user."""

    def __call__(self, context: PipelineContext) -> PipelineContext:
        if config.get("confirm_before_adding_notes", False):
            confirmed_responses = display_notes_responses_confirmation_dialog(
                notes_responses=context.ai_responses, parent=mw
            )
            context.ai_responses = confirmed_responses
        return context


class CreateNotes:
    """Creates Anki notes from the AI responses."""

    def __call__(self, context: PipelineContext) -> PipelineContext:
        responses: list[ZikariaResponseData] = context.ai_responses
        col = mw.col
        for res in responses:
            request_data = res.request_data
            notes_data_list = res.notes_data_list
            note_type = mw.col.models.get(request_data.notetype_dict["id"])
            original_note = mw.col.get_note(request_data.original_note_id)
            deck_id = (
                original_note.cards()[0].did
                if original_note.cards()
                else mw.col.decks.selected()
            )
            default_tags = set()
            config_note_tag = config.get("note_tag")
            if config_note_tag:
                default_tags.add(config_note_tag)
            for note_data in notes_data_list:
                new_note = Note(col, note_type)
                current_tags = set(note_data.pop("tags", []))
                current_tags.update(default_tags)
                for field_name, field_value in note_data.items():
                    if field_name in new_note:
                        new_note[field_name] = (
                            str(field_value) if field_value is not None else ""
                        )
                for tag in current_tags:
                    new_note.add_tag(tag)
                col.add_note(note=new_note, deck_id=deck_id)
                context.processed_notes.append(new_note)
        return context


class MarkAsProcessed:
    """Tags the original notes as processed and optionally suspends them."""

    def __call__(self, context: PipelineContext) -> PipelineContext:
        processed_note_ids = set()
        if context.source_data and isinstance(context.source_data[0], Note):
            notes: list[Note] = context.source_data
            for note in notes:
                processed_note_ids.add(note.id)
        for nid in processed_note_ids:
            note = mw.col.get_note(nid)
            if note:
                note.add_tag(config.get("processed_tag", "zikaria_processed"))
                mw.col.update_note(note=note)
                if config.get("suspend_processed_prompt_notes", True):
                    cards_to_suspend = note.card_ids()
                    if cards_to_suspend:
                        mw.col.sched.suspend_cards(ids=cards_to_suspend)
        return context
