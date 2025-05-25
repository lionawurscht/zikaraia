# Zikaria Anki Add-on

Zikaria is an Anki add-on designed to assist users in creating and enhancing notes with the help of generative AI. It allows for:

*   **Automated Note Generation:** Generate new Anki notes based on a simple prompt.
*   **Note Completion:** Fill in or correct information in existing notes.
*   **Bulk Import from JSON:** Create multiple notes at once by providing data in JSON format.
*   **Customizable Prompts:** Tailor the AI's behavior with custom prompts for different note types or decks.

## Features

*   Process notes tagged with a specific "prompt" tag to generate new content.
*   Process notes tagged with a "complete" tag to fill in missing fields.
*   Manual trigger for processing notes via the Tools menu.
*   Optional automatic processing after Anki sync.
*   Flexible configuration for API keys, AI model parameters, and tags.
*   Custom configuration overrides for specific note types and decks.
*   Dialog for reviewing and editing AI-generated notes before adding them.
*   Dialog for importing notes directly from JSON.
*   Dialog for creating notes from a simple text prompt.
*   Debugging tools for inspecting prompts and schemas.

## Installation

1.  Download the add-on package (or clone this repository into your Anki add-ons folder).
2.  Ensure the add-on folder is named `zikaria`.
3.  Restart Anki.

## Configuration

The add-on can be configured via the Anki Tools menu: `Tools > Zikaria Addon Configuration`.

Key settings include:

*   **API Key:** Your API key for the generative AI service.
*   **Model Name & Temperature:** Parameters for the AI model.
*   **Tags:** Customize the tags used by the add-on (e.g., `zikaria_prompt`, `zikaria_processed`).
*   **Prompts:** Define the base prompts used for note creation and completion.
*   **Custom Configurations:** Set up specific prompt behaviors for different note types or decks.

Refer to the in-app configuration dialog for detailed explanations of each setting.

## Basic Usage

1.  **Tag a note:** Add the tag specified in the configuration (default: `zikaria_prompt`) to a note you want the AI to process. The first field of this note usually contains your input/prompt for the AI.
2.  **Process:**
    *   Trigger processing manually via `Tools > Process Zikaria Notes`.
    *   Or, enable `run_on_sync` in the configuration for automatic processing.
3.  **Review (if enabled):** If `confirm_before_adding_notes` is enabled, a dialog will appear allowing you to review, edit, or discard the AI-generated notes.

For more advanced usage, explore custom prompts, JSON import, and creating notes from text via the Tools menu.

---

This add-on utilizes generative AI. Always review generated content for accuracy and appropriateness.
