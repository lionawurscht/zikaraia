import json
from dataclasses import dataclass

from aqt import mw, QMainWindow
from aqt.utils import showInfo
from anki.notes import Note
import google.generativeai as genai

# Imports from our new modules
from .config_utils import (
    get_config, # Use accessor
    get_logger, # Use accessor
    get_effective_config, # Changed from get_effective_config_for_note
    get_config_value
)
from .anki_utils import (
    is_valid_card_data,
    is_valid_cards_data,
    disable_running_on_sync,
    configure_generative_ai 
)
from .prompts import (
    get_create_prompt,
    get_complete_prompt
)
from .ui import (
    display_cards_confirmation_dialog,
)


@dataclass
class ZikariaPrompts:
    mw: QMainWindow = mw 
    manual_execution: bool = False

    def __post_init__(self):
        self._model = None 
        self.logger = get_logger() # Get logger instance for the class
        self.config = get_config() # Get config instance for the class

    def process_notes(self):
        self.logger.info("ZikariaPrompts.process_notes called.")
        if not configure_generative_ai(): 
            self.logger.info("AI not configured. Aborting ZikariaPrompts.process_notes.")
            if self.manual_execution:
                showInfo("AI is not configured. Please check your API key in the Addon Configuration.", parent=self.mw)
            return

        col = self.mw.col
        if not col:
            self.logger.error("Collection (mw.col) is not available.")
            if self.manual_execution:
                showInfo("Collection is not available.", parent=self.mw)
            return

        prompt_tag_val = self.config.get('prompt_tag', 'zikaria_prompt')
        processed_tag_val = self.config.get('processed_tag', 'zikaria_processed')
        complete_tag_val = self.config.get('complete_tag', 'zikaria_complete')

        prompt_notes_ids = col.find_notes(
            f"tag:{prompt_tag_val} -tag:{processed_tag_val}"
        )
        complete_notes_ids = col.find_notes(
            f"tag:{complete_tag_val} -tag:{processed_tag_val}"
        )

        if not prompt_notes_ids and not complete_notes_ids:
            self.logger.info("No notes found with prompt/complete tags for processing.")
            if self.manual_execution:
                showInfo("No notes with the prompt/complete tag found for processing.", parent=self.mw)
            return
        
        if prompt_notes_ids:
            self.process_prompt_notes(prompt_notes_ids)
        if complete_notes_ids:
            self.process_complete_notes(complete_notes_ids)

    def process_prompt_notes(self, prompt_notes_ids: list):
        col = self.mw.col
        new_cards_map = {} 

        for note_id in prompt_notes_ids:
            note = col.get_note(note_id)
            if not note:
                self.logger.warning(f"Could not retrieve note with id {note_id}. Skipping.")
                continue
            
            try:
                single_note_cards_data = self._process_prompt_note_with_generative_ai(note) # Renamed to use leading underscore
            except Exception as e:
                self.logger.error(f"Error processing note {note_id} with Generative AI: {e}", exc_info=True)
                disable_running_on_sync() 
                self.logger.error("Aborting further processing in this run due to an error.")
                if self.manual_execution:
                    showInfo(f"Error processing note {note_id}: {e}. Further processing aborted.", parent=self.mw)
                break 

            if single_note_cards_data is not None: 
                new_cards_map[note] = single_note_cards_data
        
        if not new_cards_map:
            self.logger.info("No new cards generated from prompt notes.")
            return

        if self.config.get("confirm_before_adding_notes", False):
            confirmed_cards_map = display_cards_confirmation_dialog(
                cards_data_map=new_cards_map, parent=self.mw, current_mw_ref=self.mw
            )
            if not confirmed_cards_map:
                self.logger.info("User cancelled card confirmation. No notes will be added.")
                return
            new_cards_map = confirmed_cards_map 

        for original_note, cards_data_list in new_cards_map.items():
            self.add_new_notes(original_note, cards_data_list) 
            self.finalize_prompt_note(original_note)

        self.logger.info("Finished processing prompt notes.")
        if self.manual_execution:
            showInfo("Finished processing prompt notes.", parent=self.mw)

    def process_complete_notes(self, complete_notes_ids: list):
        col = self.mw.col
        updated_cards_map = {}

        for note_id in complete_notes_ids:
            note = col.get_note(note_id)
            if not note:
                self.logger.warning(f"Could not retrieve note with id {note_id} for completion. Skipping.")
                continue

            try:
                completed_card_data = self._process_complete_note_with_generative_ai(note) # Renamed
            except Exception as e:
                self.logger.error(f"Error completing note {note_id} with Generative AI: {e}", exc_info=True)
                disable_running_on_sync()
                self.logger.error("Aborting further completion processing in this run due to an error.")
                if self.manual_execution:
                    showInfo(f"Error completing note {note_id}: {e}. Further processing aborted.", parent=self.mw)
                break

            if completed_card_data is not None:
                updated_cards_map[note] = [completed_card_data] 

        if not updated_cards_map:
            self.logger.info("No notes were updated by completion process.")
            return

        if self.config.get("confirm_before_adding_notes", False): 
            confirmed_cards_map = display_cards_confirmation_dialog(
                cards_data_map=updated_cards_map, parent=self.mw, current_mw_ref=self.mw
            )
            if not confirmed_cards_map:
                self.logger.info("User cancelled card update confirmation.")
                return
            updated_cards_map = confirmed_cards_map
        
        for original_note, card_data_list in updated_cards_map.items():
            if card_data_list: 
                self.update_completed_note(original_note, card_data_list[0])
        
        self.logger.info("Finished processing complete notes.")
        if self.manual_execution:
            showInfo("Finished processing complete notes.", parent=self.mw)

    def _get_effective_config_for_note(self, note: Note) -> dict: # Helper method
        # get_effective_config is from config_utils
        # It needs note_type_id and deck_id
        note_model = note.note_type()
        if not note_model:
            self.logger.warning(f"Note {note.id} has no model. Using global config as effective config.")
            return self.config.copy() # Return a copy of the global config

        note_type_id = note_model['id']
        deck_id = note.cards()[0].did if note.cards() else None
        
        return get_effective_config(note_type_id, deck_id)


    def _process_prompt_note_with_generative_ai(self, note: Note) -> Optional[List[dict]]: # Renamed
        effective_conf = self._get_effective_config_for_note(note)
        prompt_str = get_create_prompt(note, effective_conf) # from prompts.py
        if not prompt_str:
            self.logger.error(f"Generated prompt for note {note.id} is empty. Skipping.")
            return None

        response = self.send_prompt_to_generative_ai(prompt_str, effective_conf)
        if not response or not response.text:
            self.logger.error(f"No response or empty response from AI for note {note.id}.")
            return None

        self.logger.debug("AI Raw text for prompt note %s: %s", note.id, response.text)
        try:
            cards_data_list = json.loads(response.text)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode AI JSON response for note {note.id}: {e}", exc_info=True)
            if self.manual_execution:
                showInfo(f"Error decoding AI response: {e}", parent=self.mw)
            return None

        if not is_valid_cards_data(cards_data_list): 
            self.logger.error(f"AI JSON response for note {note.id} is not a valid list of cards.")
            if self.manual_execution:
                showInfo("AI response was not a valid list of cards.", parent=self.mw)
            return None
        
        self.logger.debug("AI Parsed JSON for prompt note %s: %s", note.id, cards_data_list)
        return cards_data_list


    def _process_complete_note_with_generative_ai(self, note: Note) -> Optional[dict]: # Renamed
        effective_conf = self._get_effective_config_for_note(note)
        prompt_str = get_complete_prompt(note, effective_conf) # from prompts.py
        if not prompt_str:
            self.logger.error(f"Generated completion prompt for note {note.id} is empty. Skipping.")
            return None
            
        response = self.send_prompt_to_generative_ai(prompt_str, effective_conf)
        if not response or not response.text:
            self.logger.error(f"No response or empty response from AI for completion note {note.id}.")
            return None

        self.logger.debug("AI Raw text for complete note %s: %s", note.id, response.text)
        try:
            card_data = json.loads(response.text)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode AI JSON response for completion note {note.id}: {e}", exc_info=True)
            if self.manual_execution:
                showInfo(f"Error decoding AI response for completion: {e}", parent=self.mw)
            return None

        if not is_valid_card_data(card_data): 
            self.logger.error(f"AI JSON response for completion note {note.id} is not a valid card.")
            if self.manual_execution:
                showInfo("AI response for completion was not a valid card data.", parent=self.mw)
            return None
            
        self.logger.debug("AI Parsed JSON for complete note %s: %s", note.id, card_data)
        return card_data

    def _get_model_instance(self, current_config: dict): # Renamed, helper
        model_name = current_config.get("model_name", "gemini-1.5-flash-latest")
        max_tokens = current_config.get("max_output_tokens", 1000) # Default from original config
        temperature = current_config.get("model_temperature", 0.1) # Default from original config

        # Caching logic: if relevant params match, return cached model
        # This is a simplified cache. A more robust one would check all relevant generation_config params.
        if self._model and hasattr(self._model, '_model_name_used_for_cache') and \
           self._model._model_name_used_for_cache == model_name and \
           hasattr(self._model, '_temperature_used_for_cache') and \
           self._model._temperature_used_for_cache == temperature and \
           hasattr(self._model, '_max_tokens_used_for_cache') and \
           self._model._max_tokens_used_for_cache == max_tokens:
            return self._model

        self.logger.debug(f"Creating new GenerativeModel instance: name={model_name}, temp={temperature}, tokens={max_tokens}")
        try:
            model_instance = genai.GenerativeModel(
                model_name=model_name,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            # Store params used for caching on the instance itself
            model_instance._model_name_used_for_cache = model_name
            model_instance._temperature_used_for_cache = temperature
            model_instance._max_tokens_used_for_cache = max_tokens
            self._model = model_instance # Cache it
            return model_instance
        except Exception as e:
            self.logger.error(f"Error creating GenerativeModel (name: {model_name}): {e}", exc_info=True)
            if self.manual_execution:
                showInfo(f"Error initializing AI model: {e}. Please check model name and configuration.", parent=self.mw)
            raise 


    def send_prompt_to_generative_ai(self, prompt: str, custom_config: dict):
        self.logger.debug("Running Zikaria's AI with prompt (first 200 chars): %s", prompt[:200])
        
        try:
            model_instance = self._get_model_instance(custom_config) 
            if not model_instance: 
                self.logger.error("AI Model instance is None. Cannot send prompt.")
                return None

            timeout_seconds = custom_config.get("ai_request_timeout", 600) # Default from original config

            response = model_instance.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                ),
                request_options={"timeout": timeout_seconds},
            )
            return response
        except Exception as e:
            self.logger.error(f"Error communicating with Generative AI: {e}", exc_info=True)
            if self.manual_execution:
                showInfo(f"Error communicating with AI: {e}", parent=self.mw)
            return None 

    def update_completed_note(self, original_note: Note, card_data: dict):
        col = self.mw.col
        for field_name, field_value in card_data.items():
            if field_name == "tags": 
                if isinstance(field_value, list):
                    for tag in field_value: original_note.add_tag(tag)
                elif isinstance(field_value, str): 
                    for tag in field_value.split(" "): original_note.add_tag(tag)
                continue

            if field_name in original_note:
                original_note[field_name] = str(field_value) if field_value is not None else ""
        
        original_note.add_tag(self.config.get("processed_tag", "zikaria_processed"))
        col.update_note(note=original_note)
        self.logger.info("Updated note %s with data: %s", original_note.id, card_data)

    def add_new_notes(
        self,
        original_note: Note, 
        cards_data_list: List[dict],
        deck_id_override: Optional[int] = None,
        note_type_override: Optional[dict] = None, 
        default_tags: Optional[List[str]] = None,
    ):
        col = self.mw.col
        
        if deck_id_override is not None:
            deck_id = deck_id_override
        elif original_note.cards():
            deck_id = original_note.cards()[0].did
        else: 
            deck_id = col.decks.selected()
            self.logger.info(f"Original note for add_new_notes had no cards, using current deck: {deck_id}")

        note_type = note_type_override if note_type_override is not None else original_note.note_type()
        if not note_type:
            self.logger.error("Cannot determine note type for adding new notes. Aborting.")
            return

        final_default_tags = set(default_tags or [])
        config_note_tag = self.config.get("note_tag")
        if config_note_tag:
            final_default_tags.add(config_note_tag)

        for card_data in cards_data_list:
            new_note = Note(col, note_type) 
            current_tags = set(card_data.pop("tags", [])) 
            current_tags.update(final_default_tags) 

            for field_name, field_value in card_data.items():
                if field_name in new_note:
                    new_note[field_name] = str(field_value) if field_value is not None else ""
            
            for tag in current_tags:
                new_note.add_tag(tag) 

            col.add_note(note=new_note, deck_id=deck_id)
            self.logger.info("Added new note with data: %s to deck ID %s", card_data, deck_id)


    def finalize_prompt_note(self, prompt_note: Note):
        prompt_note.add_tag(self.config.get("processed_tag", "zikaria_processed"))
        self.mw.col.update_note(note=prompt_note) 

        if self.config.get("suspend_processed_prompt_notes", True): # Default true
            cards_to_suspend = prompt_note.card_ids()
            if cards_to_suspend:
                self.mw.col.sched.suspend_cards(ids=cards_to_suspend)
                self.logger.info(f"Suspended cards for processed prompt note {prompt_note.id}.")
        
        self.logger.info(f"Finalized prompt note {prompt_note.id}.")
