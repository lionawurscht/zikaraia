import json
from dataclasses import dataclass
from typing import Any, Callable

from anki.decks import DeckId
from anki.models import NoteType, NotetypeDict
from anki.notes import Note, NoteId
from aqt import QObject, QRunnable, QThreadPool, mw, pyqtSignal, pyqtSlot
from aqt.utils import showCritical, showInfo
from google.genai import types
from google.genai.errors import ServerError  # Retaining the google API imports
from requests.exceptions import ConnectionError  # Exceptions for network errors
from tenacity import retry  # The main retry decorator/constructor
from tenacity import retry_if_exception_type  # Conditional retrying
from tenacity import stop_after_attempt  # Stop condition for retries
from tenacity import wait_exponential  # Wait strategy for retries

# Import your existing modules
from .anki_utils import gemini_client_proxy, normalize_ai_response
from .config_utils import (
    config,
    get_effective_config,
    get_effective_config_for_note,
    logger,
)
from .dialogs.notes_confirmation import display_notes_responses_confirmation_dialog
from .prompts import generate_pydantic_class, get_prompt_text_from_string_by_mode
from .types import LogContainer, PromptMode, ZikariaRequestData, ZikariaResponseData

# --- New/Adapted Data Structures for Concurrency ---


class ZikariaWorkerSignals(QObject):
    """Signals for a single concurrent task."""

    finished = pyqtSignal(ZikariaResponseData)
    error = pyqtSignal(ZikariaRequestData, Exception)


# --- Concurrent Worker (Background Thread) ---


class ZikariaNetworkTask(QRunnable):
    """QRunnable to handle a single network request to the Gemini API."""

    def __init__(self, req_data: ZikariaRequestData):
        super().__init__()
        self.req_data = req_data
        self.signals = ZikariaWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        """Runs on a background thread."""
        try:
            # # 1. Send Prompt to AI
            # response = ZikariaPrompts._send_prompt_to_ai(
            #     self.req_data.prompt_str,
            #     self.req_data.effective_conf,
            #     self.req_data.notetype_dict,  # Pass dict instead of NoteType object
            # )
            #
            # # 2. Parse Response
            # notes_data_list = ZikariaPrompts._parse_response_into_notes_data_static(
            #     response, self.req_data.notetype_dict
            # )
            #
            notes_data_list = ZikariaPrompts._send_prompt_to_ai_and_parse_response_into_notes_data_static(
                self.req_data
            )

            if notes_data_list is None:
                raise ValueError(
                    "Failed to parse or normalize AI response into notes data."
                )

            result = ZikariaResponseData(self.req_data, notes_data_list)
            self.signals.finished.emit(result)

        except Exception as e:
            self.signals.error.emit(self.req_data, e)


# --- Task Manager (Manages and monitors concurrent workers on Main Thread) ---


class ZikariaTaskManager(QObject):
    """Handles initiating concurrent tasks and managing their collective completion."""

    def __init__(
        self,
        request_list: list[ZikariaRequestData],
        # finalize_notes_fn: Callable,
        finalize_notes_responses_fn: Callable,
    ):
        super().__init__(mw)
        self.request_list = request_list
        self.total_tasks = len(request_list)
        self.finished_tasks = 0
        self.final_notes_responses: list[ZikariaRequestData] = []
        self.errors: list[dict] = []
        self.finalize_notes_responses_fn = finalize_notes_responses_fn
        self.threadpool = QThreadPool.globalInstance()

    @pyqtSlot(ZikariaResponseData)
    def _task_finished_callback(self, data: ZikariaResponseData):
        """Called when ANY single task finishes successfully (runs on main thread)."""
        self.finished_tasks += 1

        # Validation and mapping on main thread
        note_id = data.request_data.original_note_id

        # 1. Validate collection state
        self.final_notes_responses.append(data)

        self._check_all_finished()

    @pyqtSlot(ZikariaRequestData, Exception)
    def _task_error_callback(self, req_data: ZikariaRequestData, error: Exception):
        """Called when ANY single task fails (runs on main thread)."""
        self.finished_tasks += 1
        self.errors.append(
            {
                "note_id": req_data.original_note_id,
                "mode": req_data.mode,
                "error": str(error),
            }
        )
        logger.exception(
            f"Error for note ID {req_data.original_note_id} in mode '{req_data.mode}': {error}"
        )
        self._check_all_finished()

    def _check_all_finished(self):
        """Non-blocking check to see if all concurrent tasks are done."""
        progress_val = self.finished_tasks
        progress_max = self.total_tasks

        mw.progress.update(
            label=f"Completed {progress_val} of {progress_max} AI requests...",
            value=progress_val,
            max=progress_max,
        )

        if self.finished_tasks == self.total_tasks:
            mw.progress.finish()
            self._handle_finalization()
            self.deleteLater()  # Clean up the QObject

    def _handle_finalization(self):
        """Triggers the finalization function if there are results."""
        if self.errors:
            # You can decide to halt here or continue with valid results
            showCritical(
                f"🚨 {len(self.errors)} AI request(s) failed. Check logs for details."
            )

        if self.final_notes_responses:
            self.finalize_notes_responses_fn(notes_responses=self.final_notes_responses)

        elif not self.errors:
            showInfo(
                "No results returned from AI or all processed notes were deleted/modified."
            )

    def start_tasks(self):
        """Initiates all concurrent network tasks."""
        if not self.request_list:
            return

        mw.progress.start(
            label=f"Starting {self.total_tasks} AI requests...", max=self.total_tasks
        )

        for req_data in self.request_list:
            worker = ZikariaNetworkTask(req_data)

            # Connect signals to the main thread methods
            worker.signals.finished.connect(self._task_finished_callback)
            worker.signals.error.connect(self._task_error_callback)

            # Start the task in the QThreadPool (concurrently)
            self.threadpool.start(worker)


# --- Main Class Adaptation ---


@dataclass
class ZikariaPrompts:
    manual_execution: bool = False

    def __post_init__(self):
        pass

    @staticmethod
    def _send_prompt_to_ai_and_parse_response_into_notes_data_static_with_retry(
        request_data: ZikariaRequestData,
    ):
        retry_fn = retry(
            ZikariaPrompts._send_prompt_to_ai_and_parse_response_into_notes_data_static,
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            retry=retry_if_exception_type(
                (TimeoutError, ConnectionError, json.JSONDecodeError, ServerError)
            ),
        )

        return retry_fn(request_data)

    @staticmethod
    def _send_prompt_to_ai_and_parse_response_into_notes_data_static(
        request_data: ZikariaRequestData,
    ):
        # 1. Send Prompt to AI
        response = ZikariaPrompts._send_prompt_to_ai(request_data)

        # 2. Parse Response
        notes_data_list = ZikariaPrompts._parse_response_into_notes_data(
            response, request_data
        )

        return notes_data_list

    @staticmethod
    def _parse_response_into_notes_data(
        response: object,
        request_data: ZikariaRequestData,
    ) -> list[dict[str, str | list[str]]] | None:
        """Extracts text from response and parses it into notes data."""
        response_text = getattr(response, "text", None)
        logger.debug("Raw response: %s", response_text)
        return ZikariaPrompts._parse_text_into_notes_data(response_text, request_data)

    @staticmethod
    def _parse_text_into_notes_data(
        response_text: object,
        request_data: ZikariaRequestData,
    ) -> list[dict[str, str | list[str]]] | None:
        """Parses JSON text and normalizes it into notes data."""
        if not response_text:
            logger.error("No response or empty response from AI")
            return None

        try:
            notes_data = json.loads(response_text)
        except json.JSONDecodeError:
            logger.exception("Failed to decode JSON from raw data:\n%s", response_text)
            return None

        pydantic_class = request_data.pydantic_class or generate_pydantic_class(
            request_data.notetype_dict
        )

        if not pydantic_class:
            return None

        normalized = normalize_ai_response(notes_data, pydantic_class)

        if not normalized:
            logger.error(
                "JSON is not a valid list of notes and can't be transformed into one."
            )
            return None

        logger.debug(f"Parsed JSON: {normalized}")
        return normalized

    @staticmethod
    def _send_prompt_to_ai(request_data: ZikariaRequestData):
        """Static method to send prompt, runs in background thread. No mw.col access."""
        # ... (Your existing _send_prompt_to_ai logic remains largely the same) ...
        # NOTE: The retry logic is best handled *outside* this function, perhaps in the QRunnable itself,
        # but for clean API calling, we keep the core logic here.

        model_name = request_data.effective_conf.model_name
        max_tokens = request_data.effective_conf.max_output_tokens
        temperature = request_data.effective_conf.model_temperature

        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_mime_type": "application/json",
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
            "response_schema": list[
                request_data.pydantic_class
                or generate_pydantic_class(request_data.notetype_dict)
            ],
        }

        logger.debug(
            "Calling ai (model_name=%s) for prompt:\n%s\n\n---\n\nconfig:\n%s",
            model_name,
            request_data.prompt_str,
            config,
        )

        try:
            response = gemini_client_proxy.client.models.generate_content(
                model=model_name,
                contents=request_data.prompt_str,
                config=types.GenerateContentConfig(**config),
            )
            return response
        except Exception:
            # We catch and re-raise the exception for the QRunnable to emit
            logger.exception("Error communicating with Generative AI.")
            raise

    # --- Main Workflow (Main Thread) ---

    def process_notes(self):
        logger.info("ZikariaPrompts.process_notes called.")

        if gemini_client_proxy is None:
            if self.manual_execution:
                showInfo(
                    "AI is not configured. Please check your API key in the Addon Configuration.",
                    parent=mw,
                )
            return

        col = mw.col
        prompt_tag_val = config.get("prompt_tag", "zikaria_prompt")
        processed_tag_val = config.get("processed_tag", "zikaria_processed")
        complete_tag_val = config.get("complete_tag", "zikaria_complete")

        create_notes_ids = col.find_notes(
            f"tag:{prompt_tag_val} -tag:{processed_tag_val}"
        )
        complete_notes_ids = col.find_notes(
            f"tag:{complete_tag_val} -tag:{processed_tag_val}"
        )

        if not create_notes_ids and not complete_notes_ids:
            if self.manual_execution:
                showInfo(
                    "No notes with the prompt/complete tag found for processing.",
                    parent=mw,
                )
            return

        # 1. Gather all requests on the main thread
        all_requests: list[ZikariaRequestData] = []
        for note_id in create_notes_ids:
            all_requests.extend(self._create_requests_for_note(note_id, "create"))
        for note_id in complete_notes_ids:
            all_requests.extend(self._create_requests_for_note(note_id, "complete"))

        # 2. Start the concurrent manager
        if all_requests:
            # NOTE: We use _finalize_create_notes for the entire batch as it covers
            # both note creation and updates to the original note (processing tags, suspending).
            manager = ZikariaTaskManager(
                request_list=all_requests,
                finalize_notes_responses_fn=self._finalize_create_notes_from_responses,
            )
            manager.start_tasks()

    def _create_requests_for_note(
        self,
        note_id: NoteId,
        mode: str,
    ) -> list[ZikariaRequestData]:
        """Fetches data and creates the RequestData object(s) on the main thread."""
        note = mw.col.get_note(note_id)
        if not note:
            return []

        effective_conf = get_effective_config_for_note(note)

        # Prepare notetype dictionary for safe passing to the background thread
        notetype_dict = note.note_type()

        # Create requests per note (adjust if one note generates multiple requests)
        return self._create_requests(
            mode=mode,
            prompt_str=note.fields[0],
            notetype_dict=notetype_dict,
            effective_config=effective_conf,
            original_note_id=note.id,
            original_note_mod=note.mod,
            metadata={"mode": mode},
        )

    def _create_requests(
        self,
        mode: PromptMode,
        prompt_str: str,
        effective_config: dict[Any, Any],
        notetype_dict: NotetypeDict,
        original_note_id: int | None = None,
        original_note_mod: int | None = None,
        metadata: dict[str, int | float | str | bool] | None = None,
        # notetype_id: NotetypeId | None = None,
        # deck_id: DeckId | None = None,
        # tags: list[str] | None = None
    ) -> list[ZikariaRequestData]:
        """Creates the RequestData object(s) on the main thread."""

        # Split note.fields[0] into lines and chunk every 5 lines
        lines = str(prompt_str).splitlines()
        chunk_size = config.get("chunk_size", 3)
        chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]

        requests = []
        for chunk in chunks:
            chunked_prompt = "\n".join(chunk)
            # Use chunked_prompt as the prompt_str for this request
            prompt_str = get_prompt_text_from_string_by_mode(
                mode=mode,
                prompt=chunked_prompt,
                config_=effective_config,
                note_type=notetype_dict,
            )
            req_data = ZikariaRequestData(
                mode=mode,
                prompt_str=prompt_str,
                effective_conf=effective_config,
                original_note_id=original_note_id,
                original_note_mod=original_note_mod,
                notetype_dict=notetype_dict,
                metadata=metadata,
                # notetype_id=notetype_id,
                # deck_id=deck_id,
                # tags=tags,
            )
            requests.append(req_data)
        return requests

    # --- Finalization (Main Thread) ---

    def _finalize_notes_responses(
        self,
        notes_responses: list[
            ZikariaResponseData
        ],  # Note key can be None for standalone tasks
        per_notes_response_fn: Callable,
        finished_msg: str,
        global_tags: str | None = None,
    ):
        """Runs on the main thread after all concurrent tasks are finished."""
        # ... (Your existing _finalize_notes logic) ...
        # NOTE: The loop needs to handle 'None' keys from standalone tasks gracefully.

        # logger.info(
        #     "_finalize_notes called with: %s", LogContainer(responses=notes_responses)
        # )

        # final_notes_data_map = {}
        # for original_note, notes_data_list in notes_map.items():
        #     if original_note is not None:
        #         final_notes_data_map[original_note] = notes_data_list
        #     else:
        #         # Handle standalone data which needs a mock Note for notetype/deck
        #         # You must ensure the notes_data_list contains the necessary context
        #         # to determine NoteType and Deck for creation.
        #         pass  # Skipping complex standalone logic for this mock
        #
        # logger.info("_finalize_notes calling confirmation for with: %s", notes_map)

        if config.get("confirm_before_adding_notes", False):
            # Requires adapting display_notes_confirmation_dialog to use the dictionary format
            # {original_note: notes_data_list}
            confirmed_notes_responses = display_notes_responses_confirmation_dialog(
                notes_responses=notes_responses, parent=mw, global_tags=global_tags
            )
            if not confirmed_notes_responses:
                logger.info(
                    "User cancelled notes confirmation. No notes will be added/updated."
                )
                return
            notes_reponses = confirmed_notes_responses

        for notes_response in notes_reponses:
            per_notes_response_fn(notes_response)

        logger.info(f"Finished processing {finished_msg}.")

    def _finalize_create_notes_from_responses(
        self, notes_responses: list[ZikariaResponseData]
    ):
        """Combines creation/update and final processing tags."""

        completed_notes = set()

        def per_notes_response_fn(notes_response: ZikariaResponseData):
            original_note = mw.col.get_note(
                notes_response.request_data.original_note_id
            )

            note_type = original_note.note_type()
            deck_id = (
                original_note.cards()[0].did
                if original_note.cards()
                else mw.col.decks.selected()
            )

            # If in 'complete' mode, update the original note with the first item
            if notes_response.request_data.metadata["mode"] == PromptModes.COMPLETE:
                if (
                    original_note not in completed_notes
                    and notes_response.notes_data_list
                ):
                    note_data = notes_response.notes_data_list.pop()
                    completed_notes.add(original_note)
                    self._update_note(original_note, note_data)

            if notes_response.notes_data_list:
                self._add_notes(
                    notes_response.notes_data_list, note_type=note_type, deck_id=deck_id
                )

            # Finalize the original note's status (tagging, suspending)
            self._finalize_processed_note(original_note)

        self._finalize_notes_responses(
            notes_responses, per_notes_response_fn, "AI processed notes"
        )

    def _finalize_processed_note(self, original_note: Note):
        """Applies processed tags and suspension to the source note."""
        original_note.add_tag(config.get("processed_tag", "zikaria_processed"))
        mw.col.update_note(note=original_note)

        if config.get("suspend_processed_prompt_notes", True):
            cards_to_suspend = original_note.card_ids()
            if cards_to_suspend:
                mw.col.sched.suspend_cards(ids=cards_to_suspend)
                logger.info(f"Suspended cards for processed note {original_note.id}.")

    # --- Remaining helper methods from your original code (omitted for brevity) ---

    # NOTE: The retry logic for AI communication (_send_prompt_to_ai_with_retry)
    # should be adapted to be used *inside* the QRunnable's run method if you want
    # to retry on network failures *before* sending the error signal to the main thread.

    # You would need to make your retry-decorated function a static method
    # or move the tenacity logic into the QRunnable itself.

    def _add_notes(
        self,
        notes_data_list: list[dict[str, str | list[str]]],
        note_type: NoteType,
        deck_id: DeckId,
        default_tags: list[str] | None = None,
    ):
        col = mw.col

        default_tags = set(default_tags or [])
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
            logger.info(
                "Added new note with data: %s to deck ID %s", note_data, deck_id
            )

    def _update_note(self, original_note: Note, note_data: dict):
        col = mw.col
        for field_name, field_value in note_data.items():
            if field_name == "tags":
                if isinstance(field_value, list):
                    for tag in field_value:
                        original_note.add_tag(tag)
                elif isinstance(field_value, str):
                    for tag in field_value.split(" "):
                        original_note.add_tag(tag)
                continue

            if field_name in original_note:
                original_note[field_name] = (
                    str(field_value) if field_value is not None else ""
                )

        original_note.add_tag(config.get("processed_tag", "zikaria_processed"))
        col.update_note(note=original_note)
        logger.info("Updated note %s with data: %s", original_note.id, note_data)
