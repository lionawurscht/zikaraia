# Configuration Documentation

- `api_key` (str, default: ): The API key for accessing Generative AI services. If not provided, the user will be prompted to input it on first run.
- `debug` (bool, default: False): Enable debug logging
- `create_prompt_template` (str | None, default: None): The base prompt sent to the Generative AI when completing fields in an existing note. This is formatted dynamically.
- `complete_prompt_template` (str | None, default: None): Template for note completion prompt
- `confirm_before_adding_notes` (bool, default: False): Require confirmation before adding notes
- `model_temperature` (float, default: 0.5): Temperature for model generation. Lower means more predictive, higher means more creative.
- `model_name` (str, default: gemini-2.5-flash): Model name for completions
- `max_output_tokens` (int, default: 512): Maximum output tokens for completions
- `request_timeout` (int, default: 1000): The request timeout in milliseconds for getting the gemini client.
- `note_tag` (str, default: zikria_created): Tag to add to notes created by Gemini
- `processed_tag` (str, default: zikaria_processed): Tag to add to notes processed by Gemini
- `prompt_tag` (str, default: zikaria_prompt): Tag to add to notes with a prompt
- `custom_prompt_tag` (str, default: zikaria_custom_prompt): Tag to add to notes with a custom prompt
- `complete_tag` (str, default: zikaria_complete): Tag to add to notes that are completed
- `json_tag` (str, default: zikaria_from_json): Tag to add to notes created from JSON
- `run_on_sync` (bool, default: False): Whether to run processing on sync
- `custom_config` (list): Per-deck and/or per-note-type configuration overrides. Allows setting prompt-related options for specific decks, note types, or combinations. Each entry is a tuple: (NotetypeId | None, DeckId | None, CustomConfig).