# Configuration Documentation

- **api_key**: The API key for accessing Generative AI services. If not provided, the user will be prompted to input it on first run.
- **base_prompt**: The base prompt sent to the Generative AI, formatted dynamically for each note.
- **confirm_before_adding_notes**: Boolean indicating if the user should confirm before adding new notes to the collection.
- **debug**: Boolean indicating if debug-level logging should be enabled.
- **model_temperature**: Float for controlling the temperature of the Generative AI responses (e.g., 0.1 for more deterministic output).
- **model_name**: String representing the model name used for Generative AI processing (default: gemini-1.5-flash-latest).
- **max_output_tokens**: Integer for specifying the maximum output tokens in the Generative AI response.
- **note_tag**: Tag applied to new notes created by this add-on.
- **processed_tag**: Tag applied to prompt notes after they are processed.
- **prompt_tag**: Tag used to identify notes to be processed by this add-on.
- **run_on_sync**: Boolean to determine if note processing should automatically run after synchronization.
