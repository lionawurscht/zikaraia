# I have this code, however, I sometimes get Errors from the ai (quota limit - the current parsing mechanism doesn't seem to work or response related stuff), or the returned value is malformed / can't be parsed. Also, if the user tries to create too many words in one go, the returned string gets truncated and can't be parsed. So I wanna be able to do a few things: if I got data, but it can't be parsed, I wanna retry (maybe through the python tenacity library) for a parameterized amount of times. For bad requests or timeout I wanna retry with exponentially longer intervals with parameterized starting delay and times, and I wanna split the input string by newlines and then run in chunks of parameterized length, and since we're at it, in cases where the ai get called several times, these calls should all run in separate threads and the returned data should be merged again at the end. For the threading I qould use the builtin qt threading capabilities. Please implement all the necessary changes.
import json
import time
from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Any, Callable

import aqt
from anki.decks import DeckId
from anki.models import NoteType, NotetypeDict
from anki.notes import Note
from aqt import QMainWindow, QRunnable, QThreadPool, mw, pyqtSlot
from aqt.operations import QueryOp
from aqt.utils import showInfo
from google.genai import types
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .anki_utils import gemini_client_proxy, normalize_ai_response
from .config_utils import ConfigType, config_proxy, get_effective_config, logger
from .prompts import (
    PromptMode,
    generate_pydantic_class,
    get_base_prompt_by_mode,
    get_prompt_text,
    get_prompt_text_by_mode,
)
from .ui import create_confirmation_dialog, display_notes_confirmation_dialog


class RequestData:
    """Holds all necessary data to execute a single network request."""

    def __init__(
        self,
        request_payload: dict[str, Any],
        original_note_id: int | None = None,  # For later validation
        original_note_mod: float | None = None,  # For later validation
    ):
        self.payload = request_payload
        self.original_note_id = original_note_id
        self.original_note_mod = original_note_mod


class ResponseData:
    """Holds the result from the server and the original request data."""

    def __init__(
        self,
        request_data: RequestData,
        server_response: Any,  # The data the server returns for the user to review
    ):
        self.request_data = request_data
        self.server_response = server_response


class DialogAction(IntEnum):
    RETRY_NOTE = auto()
    SKIP_NOTE = auto()
    SKIP_REST = auto()
    CANCEL_ALL = auto()
    WAIT = auto()


@dataclass
class ZikariaPrompts:
    mw: QMainWindow = mw
    manual_execution: bool = False

    def __post_init__(self):
        self.thread_pool = QThreadPool.globalInstance()
        self.chunk_size = config_proxy.get("ai_chunk_size", 10)
        self.max_parse_retries = config_proxy.get("ai_parse_max_retries", 3)
        self.max_api_retries = config_proxy.get("ai_api_max_retries", 3)
        self.api_retry_start_delay = config_proxy.get(
            "ai_api_retry_start_delay", 2
        )  # seconds

    def process_notes(self):
        logger.info("ZikariaPrompts.process_notes called.")

        if gemini_client_proxy is None:
            logger.info("AI not configured. Aborting ZikariaPrompts.process_notes.")
            if self.manual_execution:
                showInfo(
                    "AI is not configured. Please check your API key in the Addon Configuration.",
                    parent=mw,
                )
            return

        col = mw.col
        # if not col:
        #     logger.error("Collection (mw.col) is not available.")
        #     if self.manual_execution:
        #         showInfo("Collection is not available.", parent=mw)
        #     return

        prompt_tag_val = config_proxy.get("prompt_tag", "zikaria_prompt")
        processed_tag_val = config_proxy.get("processed_tag", "zikaria_processed")
        complete_tag_val = config_proxy.get("complete_tag", "zikaria_complete")

        create_notes_ids = col.find_notes(
            f"tag:{prompt_tag_val} -tag:{processed_tag_val}"
        )
        complete_notes_ids = col.find_notes(
            f"tag:{complete_tag_val} -tag:{processed_tag_val}"
        )

        if not create_notes_ids and not complete_notes_ids:
            logger.info("No notes found with prompt/complete tags for processing.")
            if self.manual_execution:
                showInfo(
                    "No notes with the prompt/complete tag found for processing.",
                    parent=mw,
                )
            return

        if create_notes_ids:
            self._run_create_notes_queryop(create_notes_ids, {})
        if complete_notes_ids:
            self._run_complete_notes_queryop(complete_notes_ids, {})

    def _run_create_notes_queryop(
        self, remaining_note_ids: list[int], accumulated_cards_map: dict
    ):
        self._run_notes_queryop(
            remaining_note_ids,
            accumulated_cards_map,
            self._process_create_note_with_generative_ai,
            self._finalize_create_notes,
        )

    def _run_complete_notes_queryop(
        self, remaining_note_ids: list[int], accumulated_cards_map: dict
    ):
        self._run_notes_queryop(
            remaining_note_ids,
            accumulated_cards_map,
            self._process_complete_note_with_generative_ai,
            self._finalize_complete_notes,
        )

    def _run_notes_queryop(
        self,
        remaining_note_ids: list[int],
        accumulated_notes_map: dict,
        process_note_fn,
        finalize_notes_fn,
    ):
        # Fetch notes up front
        notes_map = {
            nid: mw.col.get_note(nid)
            for nid in remaining_note_ids
            if mw.col.get_note(nid)
        }

        def ai_gather_op(co, notes_map):
            new_notes_map = {}
            note_ids = list(notes_map.keys())
            aqt.mw.taskman.run_on_main(
                lambda: aqt.mw.progress.update(
                    label=f"Remaining: {len(note_ids)}",
                    value=0,
                    max=len(note_ids),
                )
            )
            for i, note_id in enumerate(note_ids):
                note = notes_map[note_id]
                try:
                    notes_data = process_note_fn(note)
                    if notes_data is not None:
                        new_notes_map[note_id] = (note, notes_data)
                except Exception as e:
                    error_info = getattr(e, "args", [str(e)])[0]
                    # If error_info is a dict (from google.genai.errors.ClientError), preserve it
                    error_dict = (
                        error_info
                        if isinstance(error_info, dict)
                        else {"message": str(e)}
                    )
                    return {
                        "error": error_dict,
                        "failed_note_id": note_id,
                        "new_notes_map": new_notes_map,
                        "error_time": time.time(),
                    }
                aqt.mw.taskman.run_on_main(
                    lambda: aqt.mw.progress.update(
                        label=f"Remaining: {len(note_ids) - (i + 1)}",
                        value=i + 1,
                        max=len(note_ids),
                    )
                )
            return {"new_notes_map": new_notes_map}

        def on_success(result):
            for nid, (note, notes_data) in result.get("new_notes_map", {}).items():
                accumulated_notes_map[nid] = (note, notes_data)

            if "error" in result:
                failed_note_id = result["failed_note_id"]
                error = result["error"]
                error_time = result.get("error_time", time.time())
                # Check for Gemini quota error
                code = error.get("code") if isinstance(error, dict) else None
                status = error.get("status") if isinstance(error, dict) else None
                retry_delay = None
                if code == 429 and status == "RESOURCE_EXHAUSTED":
                    details = error.get("details", [])
                    for detail in details:
                        if (
                            detail.get("@type")
                            == "type.googleapis.com/google.rpc.RetryInfo"
                        ):
                            retry_delay_str = detail.get("retryDelay", "0s")
                            # Parse retryDelay (e.g., "2s")
                            if retry_delay_str.endswith("s"):
                                try:
                                    retry_delay = float(retry_delay_str[:-1])
                                except Exception:
                                    retry_delay = None
                buttons = {
                    "Retry Note": DialogAction.RETRY_NOTE,
                    "Skip Note": DialogAction.SKIP_NOTE,
                    "Skip Rest": DialogAction.SKIP_REST,
                    "Cancel All": DialogAction.CANCEL_ALL,
                }
                if retry_delay is not None:
                    buttons["Wait"] = DialogAction.WAIT
                action = create_confirmation_dialog(
                    "Generative AI Error",
                    message=f"An error occurred while trying to complete note {failed_note_id}: {error}\n\nHow would you like to proceed?",
                    buttons=buttons,
                )
                logger.debug("User chose %s", action)
                new_remaining = [
                    nid
                    for nid in remaining_note_ids
                    if (nid != failed_note_id and nid not in accumulated_notes_map)
                ]
                if action == DialogAction.RETRY_NOTE:
                    self._run_notes_queryop(
                        [failed_note_id] + new_remaining,
                        accumulated_notes_map,
                        process_note_fn,
                        finalize_notes_fn,
                    )
                elif action == DialogAction.SKIP_NOTE:
                    if new_remaining:
                        self._run_notes_queryop(
                            new_remaining,
                            accumulated_notes_map,
                            process_note_fn,
                            finalize_notes_fn,
                        )
                elif action == DialogAction.SKIP_REST:
                    finalize_notes_fn(accumulated_notes_map)
                elif action == DialogAction.WAIT and retry_delay is not None:
                    # Wait for the remaining retry delay before retrying
                    elapsed = time.time() - error_time
                    wait_time = max(0, retry_delay - elapsed)
                    if wait_time > 0:
                        time.sleep(wait_time)
                    self._run_notes_queryop(
                        [failed_note_id] + new_remaining,
                        accumulated_notes_map,
                        process_note_fn,
                        finalize_notes_fn,
                    )
                # If cancel, do nothing
                return

            notes_map = {
                note: notes_data for note, notes_data in accumulated_notes_map.values()
            }
            finalize_notes_fn(notes_map)

        QueryOp(
            parent=mw,
            op=lambda col: ai_gather_op(col, notes_map),
            success=on_success,
        ).without_collection().with_progress(
            f"Remaining: {len(remaining_note_ids)}"
        ).run_in_background()

    def _process_create_note_with_generative_ai(self, note: Note) -> list[dict] | None:
        return self._process_note_with_generative_ai(note, "create")

    def _process_complete_note_with_generative_ai(
        self, note: Note
    ) -> list[dict] | None:
        return self._process_note_with_generative_ai(note, "complete")

    def _chunk_input(self, input_str: str) -> list[str]:
        lines = [line for line in input_str.splitlines() if line.strip()]
        return [
            "\n".join(lines[i : i + self.chunk_size])
            for i in range(0, len(lines), self.chunk_size)
        ]

    def _run_ai_in_threads(
        self,
        prompt_chunks: list[list[str]],
        base_prompt: str,
        effective_conf: ConfigType,
        note_type: NotetypeDict,
        callback: Callable,
    ):
        results = []
        errors = []
        completed = [0]

        class AIRunnable(QRunnable):
            def __init__(self, prompt, idx):
                super().__init__()
                self.prompt = prompt
                self.idx = idx

            @pyqtSlot()
            def run(self):
                try:
                    response = self._send_prompt_to_ai_with_retry(
                        self.prompt, effective_conf, note_type
                    )
                    notes_data = self._parse_response_with_retry(response, note_type)
                    results.append((self.idx, notes_data))
                except Exception as e:
                    errors.append((self.idx, str(e)))
                finally:
                    completed[0] += 1
                    if completed[0] == len(prompts):
                        callback(sorted(results), errors)

        for idx, prompt_chunk in enumerate(prompt_chunks):
            prompt = get_prompt_text(
                prompt=prompt_chunk,
                base_prompt=base_prompt,
                note_type=note_type,
                config_=effective_conf,
            )
            runnable = AIRunnable(prompt, idx)
            runnable._send_prompt_to_ai_with_retry = self._send_prompt_to_ai_with_retry
            runnable._parse_response_with_retry = self._parse_response_with_retry
            self.thread_pool.start(runnable)

    def _process_note_with_generative_ai(
        self, note: Note, mode: PromptMode
    ) -> list[dict] | None:
        # TODO: Implement how to wait for the ai call to finish and then send back the result
        effective_conf = self._get_effective_config_for_note(note)

        prompt_str: str = str(note.fields[0])

        if not prompt_str:
            logger.error(
                "Generated %s prompt for note %s is empty. Skipping.", mode, note.id
            )
            return None

        prompt_chunks = self._chunk_input(prompt_str)
        results = []
        errors = []

        def merge_callback(res, errs):
            for idx, notes_data in res:
                if notes_data:
                    results.extend(notes_data)
            if errs:
                logger.error(f"Errors in AI threads: {errs}")

        self._run_ai_in_threads(
            prompt_chunks=prompt_chunks,
            base_prompt=get_base_prompt_by_mode(mode, config_=effective_conf),
            effective_conf=effective_conf,
            note_type=note.note_type(),
            callback=merge_callback,
        )
        # Wait for threads to finish (could use QEventLoop or similar if needed)
        return results if results else None

    def _process_note_with_generative_ai(
        self, note: Note, mode: PromptMode
    ) -> list[dict] | None:
        """
        mode: "create" or "complete"
        """
        effective_conf = self._get_effective_config_for_note(note)
        prompt_str = get_prompt_text_by_mode(mode, note=note, config_=effective_conf)

        response = self._send_prompt_to_ai(prompt_str, effective_conf)
        notes_data = self._parse_response_into_notes_data(
            response, note_type=note.note_type()
        )

        return notes_data

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    )
    def _send_prompt_to_ai_with_retry(
        self, prompt: str, custom_config: dict, note_type: NoteType | None = None
    ):
        return self._send_prompt_to_ai(prompt, custom_config, note_type)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(json.JSONDecodeError),
    )
    def _parse_response_with_retry(self, response: Any, note_type: NoteType):
        return self._parse_response_into_notes_data(response, note_type)

    def _send_prompt_to_ai(
        self, prompt: str, custom_config: dict, note_type: NoteType | None = None
    ):
        logger.debug(
            "Running Zikaria's AI with prompt (first 200 chars): %s", prompt[:200]
        )

        model_name = custom_config.get("model_name", "gemini-2.5-flash")
        max_tokens = custom_config.get(
            "max_output_tokens", 1000
        )  # Default from original config
        temperature = custom_config.get(
            "model_temperature", 0.1
        )  # Default from original config

        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_mime_type": "application/json",
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
        }
        if note_type is not None:
            config["response_schema"] = list[generate_pydantic_class(note_type)]

        try:
            response = gemini_client_proxy.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config),
            )
            return response
        except Exception as e:
            logger.exception(f"Error communicating with Generative AI.")
            # if self.manual_execution:
            #     showInfo(f"Error communicating with AI: {e}", parent=mw)
            raise

    def _parse_response_into_notes_data(
        self, response: Any, note_type: NoteType
    ) -> list[dict[str, str | list[str]]] | None:
        if not response or not getattr(response, "text", None):
            logger.error(f"No response or empty response from AI")
            return None

        return self._parse_json_into_notes_data(
            json_string=response.text, note_type=note_type
        )

    def _parse_json_into_notes_data(
        self, json_string: str, note_type: NoteType
    ) -> list[dict[str, str | list[str]]] | None:

        try:
            notes_data = json.loads(json_string)
        except json.JSONDecodeError:
            logger.exception(f"Failed to decode JSON from raw data:\n%s", json_string)
            return None

        if note_type:
            normalized = normalize_ai_response(notes_data, note_type)

            if not normalized:
                logger.error(
                    "JSON is not a valid list of notes and can't be transformed into one.",
                    normalized,
                )
                return None

            logger.debug(f"Parsed JSON: {normalized}")
            return normalized

        return None

    def _finalize_notes(
        self,
        notes_map: dict,
        per_note_fn,
        finished_msg: str,
        global_tags: str | None = None,
    ):

        if config_proxy.get("confirm_before_adding_notes", False):
            confirmed_notes_map = display_notes_confirmation_dialog(
                notes_data_map=notes_map, parent=mw, global_tags=global_tags
            )
            if not confirmed_notes_map:
                logger.info(
                    "User cancelled notes confirmation. No notes will be added."
                )
                return
            notes_map = confirmed_notes_map

        for original_note, notes_data_list in notes_map.items():
            per_note_fn(original_note, notes_data_list)

        logger.info(f"Finished processing {finished_msg}.")
        if self.manual_execution:
            showInfo(f"Finished processing {finished_msg}.", parent=mw)

    def _finalize_create_notes(self, notes_map):
        def per_note_fn(original_note, notes_data_list):
            note_type = original_note.note_type()
            if original_note.cards():
                deck_id = original_note.cards()[0].did
            else:
                deck_id = mw.col.decks.selected()

            self._add_notes(notes_data_list, note_type=note_type, deck_id=deck_id)

            self._finalize_create_note(original_note)

        self._finalize_notes(notes_map, per_note_fn, "create notes")

    def _add_notes(
        self,
        notes_data_list: list[dict[str, str | list[str]]],
        note_type: NoteType,
        deck_id: DeckId,
        default_tags: list[str] | None = None,
    ):
        col = mw.col

        default_tags = set(default_tags or [])
        config_note_tag = config_proxy.get("note_tag")
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

    def _finalize_create_note(self, create_note: Note):
        create_note.add_tag(config_proxy.get("processed_tag", "zikaria_processed"))
        mw.col.update_note(note=create_note)

        if config_proxy.get("suspend_processed_prompt_notes", True):  # Default true
            cards_to_suspend = create_note.card_ids()
            if cards_to_suspend:
                mw.col.sched.suspend_cards(ids=cards_to_suspend)
                logger.info(
                    f"Suspended cards for processed prompt note {create_note.id}."
                )

        logger.info(f"Finalized prompt note {create_note.id}.")

    def _finalize_complete_notes(self, notes_map):
        def per_note_fn(original_note, notes_data_list):
            if notes_data_list:
                self._update_note(original_note, notes_data_list.pop())

            if notes_data_list:
                note_type = original_note.note_type()
                if original_note.cards():
                    deck_id = original_note.cards()[0].did
                else:
                    deck_id = mw.col.decks.selected()

                self._add_notes(
                    notes_data_list[1:], note_type=note_type, deck_id=deck_id
                )

        self._finalize_notes(notes_map, per_note_fn, "complete notes")

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

        original_note.add_tag(config_proxy.get("processed_tag", "zikaria_processed"))
        col.update_note(note=original_note)
        logger.info("Updated note %s with data: %s", original_note.id, note_data)

    def _get_effective_config_for_note(self, note: Note) -> dict:  # Helper method
        # get_effective_config is from config_utils
        # It needs note_type_id and deck_id
        note_type = note.note_type()

        note_type_id = note_type["id"]
        deck_id = note.cards()[0].did if note.cards() else None

        return get_effective_config(note_type_id, deck_id)
