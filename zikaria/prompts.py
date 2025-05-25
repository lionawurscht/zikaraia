import json
import random
from anki.models import NoteType
from aqt import mw

# Use accessors for config and logger
from .config_utils import get_config_value, get_logger, get_config

def generate_schema_class(note_type: NoteType) -> dict:
    """
    Create a JSON schema with string fields based on a list of field names.
    """
    logger = get_logger() # Use accessor
    schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    if not mw or not mw.col:
        logger.error("mw.col not available in generate_schema_class.")
        return schema

    col = mw.col
    # Get the prompt_tag from config to exclude notes with this tag from enum generation
    # Using get_config_value for single value access is fine.
    prompt_tag_val = get_config_value('prompt_tag', 'zikaria_prompt') 

    for field in note_type["flds"]:
        name = field["name"]
        property_schema = {"type": "string"} 
        schema["properties"][name] = property_schema

        if desc := field.get("description"):
            property_schema["description"] = desc
            if "(category)" in desc: # Special handling for enum generation
                # Find unique values for this field from notes of the same type,
                # excluding notes that are themselves prompts.
                query = f'note:"{note_type["name"]}" -tag:{prompt_tag_val}'
                note_ids = col.find_notes(query)
                unique_values = set()
                for nid in note_ids:
                    note = col.get_note(nid)
                    if note and (value := note.get(name)): # Ensure note exists and field has value
                        unique_values.add(value)
                
                if unique_values:
                    property_schema["enum"] = sorted(list(unique_values))

        schema["required"].append(name)

    schema["required"].append("tags") # Always require tags field in schema
    schema["properties"]["tags"] = {"type": "array", "items": {"type": "string"}}

    logger.debug("Generated schema for note type '%s': %s", note_type.get('name', 'Unknown'), schema)
    return schema


def example_notes(note_type: NoteType, n: int = 10) -> str:
    """
    Generates example notes string for a given note type.
    """
    logger = get_logger() # Use accessor
    examples = []
    if not mw or not mw.col:
        logger.error("mw.col not available in example_notes.")
        return json.dumps([])

    all_nids_of_type = mw.col.models.nids(note_type) # Get all note IDs for the model
    if not all_nids_of_type:
        logger.info(f"No notes found for notetype '{note_type.get('name', 'Unknown')}' to generate examples.")
        return json.dumps([])
        
    sample_size = min(n, len(all_nids_of_type))
    if sample_size <= 0: # Handles n=0 or empty list
        return json.dumps([])

    try:
        # random.sample requires list, not set. all_nids_of_type is already a list from Anki.
        sampled_nids = random.sample(all_nids_of_type, sample_size)
        for nid in sampled_nids:
            note = mw.col.get_note(nid)
            if not note:
                logger.warning(f"Could not retrieve note with nid {nid} for examples.")
                continue
            # Strip HTML/AV tags from field values for cleaner examples
            example = {k: mw.col.media.strip_av_tags(v) for k, v in note.items()}
            example["tags"] = list(note.tags) # Include tags in the example
            examples.append(example)
    except ValueError as e: # Handles issues like sample_size > population_size (should be caught by min)
        logger.error(f"Error sampling notes for examples (notetype: {note_type.get('name', 'Unknown')}): {e}", exc_info=True)
        return json.dumps([]) # Return empty list on error

    return json.dumps(examples, indent=2, ensure_ascii=False)


def note_to_json(note) -> str:
    """
    Converts an Anki note to a JSON string.
    Fields with "_" are converted to empty strings.
    """
    # No logger needed here unless for debugging specific note content.
    return json.dumps(
        {k: v if v != "_" else "" for k, v in note.items()}, 
        indent=4,
        ensure_ascii=False,
    )

def get_prompt(note, base_prompt: str, current_config: dict) -> str:
    """
    Formats a base prompt with note data, schema, and examples.
    Schema and examples are generated if their placeholders are in the base_prompt.
    """
    logger = get_logger() # Use accessor
    note_type = note.note_type()
    if not note_type:
        logger.error(f"Note {note.id} has no note type. Cannot generate prompt.")
        return "" 

    format_args = {
        "prompt": note.fields[0] if note.fields else "", 
        "json_note": note_to_json(note), # Uses the local note_to_json
    }

    # Conditionally generate schema and examples if placeholders exist
    if "{schema}" in base_prompt:
        format_args["schema"] = generate_schema_class(note_type) # Uses local generate_schema_class
    if "{examples}" in base_prompt:
        # Use a config value for the number of examples, default to 10
        example_count = current_config.get("example_notes_count", 10)
        format_args["examples"] = example_notes(note_type, n=example_count) # Uses local example_notes
        
    try:
        # Use **format_args for safe formatting (only keys present in args are used)
        # This is more robust than relying on all keys being present in base_prompt.
        # However, if a placeholder IS in base_prompt but NOT in format_args, it will error.
        # The current construction of format_args ensures this won't happen for schema/examples.
        return base_prompt.format(**format_args)
    except KeyError as e:
        logger.error(f"Missing key in prompt format: {e}. Base prompt: '{base_prompt}', Available Args: {list(format_args.keys())}", exc_info=True)
        return "" # Return empty or raise, depending on desired error handling


def get_create_prompt(note, current_config: dict) -> str:
    """
    Generates the 'create' prompt for a note using the effective configuration.
    """
    logger = get_logger() # Use accessor
    field_content = note.fields[0].strip() if note.fields else ""

    # Get relevant tags and prompt template from the passed current_config (effective config)
    custom_prompt_tag_val = current_config.get("custom_prompt_tag")
    default_create_prompt_template = current_config.get("create_prompt", "") # Default if not in config

    base_prompt_template_to_use = default_create_prompt_template
    if custom_prompt_tag_val and custom_prompt_tag_val in note.tags:
        base_prompt_template_to_use = field_content # Note content itself is the template
        logger.debug(f"Using custom prompt from note field for note {note.id} due to tag '{custom_prompt_tag_val}'.")
    elif field_content.startswith("@prompt:"): # Check for inline prompt override
        base_prompt_template_to_use = field_content[len("@prompt:") :].strip()
        logger.debug(f"Using inline @prompt override from note field for note {note.id}.")
    
    if not base_prompt_template_to_use:
        logger.warning(f"Create prompt template is empty for note {note.id}. Effective config: {current_config}")

    return get_prompt( # Calls the local get_prompt
        note=note,
        base_prompt=base_prompt_template_to_use,
        current_config=current_config, # Pass effective config for example_notes_count etc.
    )


def get_complete_prompt(note, current_config: dict) -> str:
    """
    Generates the 'complete' prompt for a note using the effective configuration.
    """
    logger = get_logger() # Use accessor
    # Get prompt template from the passed current_config (effective config)
    base_prompt_template_to_use = current_config.get("complete_prompt", "")
    
    if not base_prompt_template_to_use:
        logger.warning(f"Complete prompt template is empty for note {note.id}. Effective config: {current_config}")

    return get_prompt( # Calls the local get_prompt
        note=note,
        base_prompt=base_prompt_template_to_use,
        current_config=current_config, # Pass effective config
    )
