# Configuration Documentation

- **api_key**: The API key for accessing Generative AI services. If not provided, the user will be prompted to input it on first run.
- **create_prompt**: The base prompt sent to the Generative AI when creating new notes from a prompt. This is formatted dynamically.
- **complete_prompt**: The base prompt sent to the Generative AI when completing fields in an existing note. This is formatted dynamically.
- **confirm_before_adding_notes**: Boolean indicating if the user should confirm before adding new notes to the collection.
- **debug**: Boolean indicating if debug-level logging should be enabled for the Zikaria add-on.
- **model_temperature**: Float for controlling the temperature of the Generative AI responses (e.g., 0.1 for more deterministic output).
- **model_name**: String representing the model name used for Generative AI processing (e.g., gemini-1.5-flash-latest).
- **max_output_tokens**: Integer for specifying the maximum output tokens in the Generative AI response.
- **note_tag**: Tag applied to new notes created by the Zikaria add-on.
- **processed_tag**: Tag applied to prompt notes after they are processed by the Zikaria add-on.
- **prompt_tag**: Tag used to identify notes whose first field content will be used as a prompt for the Zikaria add-on to create new notes.
- **custom_prompt_tag**: Tag used to indicate that a note's first field contains a custom prompt format to be used by Zikaria.
- **complete_tag**: Tag used to identify notes that Zikaria should attempt to complete by filling in missing fields.
- **json_tag**: Tag applied to notes that are imported from JSON data using the Zikaria add-on's "Process JSON Input" feature.
- **run_on_sync**: Boolean to determine if Zikaria's note processing should automatically run after synchronization.
- **custom_config**: A list of custom configurations that can override the global settings for specific note types and/or decks. Each entry defines conditions (note type, deck) and the settings to apply.
