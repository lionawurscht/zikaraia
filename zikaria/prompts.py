import enum
import json
import random
import string
import typing
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from anki.models import NotetypeDict
from anki.notes import Note
from aqt import mw
from pydantic import BaseModel, ConfigDict, Field, create_model

# Use accessors for config and logger
from .config_utils import ConfigProxy, ConfigType, config_data, get_config_value, logger
from .prompt_store import load_saved_prompts

type PromptMode = Literal["create", "complete"]


# class NoteDict(TypedDict, extra_items=str):
#     tags: list[str]
# apparently extra_items is not supported until python 3.13
type NoteDict = dict[str, str | list[str]]


@dataclass
class DummyKey:
    key: str

    @property
    def placeholder(self):
        return f"[... {self.key} ...]"


@dataclass
class KnownKeyValue:
    key: str
    value: Any

    def __str__(self):
        return str(self.value)


class CustomFormatter(string.Formatter):
    _known_keys: set[str] = {"prompt", "json_note"}

    def __init__(
        self,
        note_type: NotetypeDict,
        example_count: int | None = None,
        dummy_keys: Iterable[str] | None = None,
    ):
        """
        :param examples_data: List of example strings
        :param schema_data: A JSON-serializable structure
        """
        self._note_type: NotetypeDict = note_type
        self._example_count: int = example_count or config_data["example_count"]
        self._dummy_keys: set[str] = set() if dummy_keys is None else set(dummy_keys)

        self._examples: object = object()
        self._schema: object = object()

    def get_value(self, key, args, kwargs):
        """Support known keywords and fall back to normal behavior."""
        if key in self._dummy_keys:
            return DummyKey(key)
        elif key == "examples":
            return self._examples
        elif key == "schema":
            return self._schema
        elif key in self._known_keys:
            return KnownKeyValue(key=key, value=super().get_value(key, args, kwargs))
        else:
            return key

    def format_field(self, value, format_spec):
        """Handle custom formatting for examples and schema."""
        if value is self._examples:
            num = self._example_count  # default number of examples
            if format_spec.isdigit():
                num = int(format_spec)
            return example_notes(self._note_type, num)

        if value is self._schema:
            style = format_spec.strip().lower() if format_spec else "readable"
            schema = generate_schema_class(self._note_type)
            if style == "inline":
                return json.dumps(schema, separators=(",", ":"))
            else:
                return json.dumps(schema, indent=2, ensure_ascii=False)

        # if value is self.json_note:
        #     pass
        # if value is self.prompt:
        #     pass

        if isinstance(value, KnownKeyValue):
            if value.key == "json_note":
                style = format_spec.strip().lower() if format_spec else "readable"
                if style == "inline":
                    return json.dumps(value.value, separators=(",", ":"))
                else:
                    return json.dumps(value.value, indent=2, ensure_ascii=False)

            return str(value)

        if isinstance(value, DummyKey):
            return value.placeholder

        if not format_spec:
            return f"{{{value}}}"

        return f"{{{value}:{format_spec}}}"
        # Default behavior for other fields
        # return super().format_field(value, format_spec)


def generate_schema_class(note_type: NotetypeDict) -> dict:
    """
    Create a JSON schema with string fields based on a list of field names.
    """
    pydantic_class = generate_pydantic_class(note_type)

    return pydantic_class.model_json_schema()


def generate_pydantic_class(
    note_type: NotetypeDict, enforce_enum: bool = True
) -> type[BaseModel]:
    """
    Create a Pydantic class dynamically with fields based on a list of values,
    including Enum-like types for category fields and their descriptions.
    """
    # Fields dictionary mapping field name to the (type, Field | ...) tuple
    fields: dict[str, tuple] = {}

    # 1. Check for Collection
    if not mw or not mw.col:
        logger.error(
            "mw.col not available in generate_pydantic_class. Returning empty model."
        )
        return create_model("EmptyModel", __base__=BaseModel)

    col = mw.col
    model_id = note_type.get("id")
    field_separator = "\x1f"
    prompt_tag_val = get_config_value("prompt_tag", "zikaria_prompt")

    # 2. Pre-load Data for ALL Category Fields (New Fast Approach)

    # Anki stores tags with spaces around them: " tag1 tag2 ". The LIKE pattern must match this.
    tag_filter_pattern = f"% {prompt_tag_val} %"

    try:
        # Fetch all 'flds' strings for notes of this type, excluding prompt notes
        # Result: list of tuples (flds_str,)
        notes_data_flds = col.db.list(
            """
            SELECT flds FROM notes 
            WHERE mid = ? AND tags NOT LIKE ?
            """,
            model_id,
            tag_filter_pattern,
        )
    except Exception as e:
        logger.error(
            f"Error querying notes for unique values (mid: {model_id}): {e}",
            exc_info=True,
        )
        # If the query fails, proceed as if no notes were found for enums
        notes_data_flds = []

    # 3. Process Field Definitions
    # Keep track of field index to correctly extract from the 'flds' string
    for field_index, field in enumerate(note_type["flds"]):
        name = field["name"]
        field_type = str
        desc = field.get("description")

        # Default field definition: Required field with optional description
        field_definition = Field(description=desc) if desc else ...

        # Check for special 'category' handling
        if desc and "(category)" in desc and enforce_enum:

            # --- Optimized Dynamic Enum Generation ---
            unique_values = set()

            # Iterate over the raw field data fetched from the database
            for flds_str in notes_data_flds:
                try:
                    # Split the field data string into a list of values
                    field_values = flds_str.split(field_separator)

                    # Get the value for the current field index
                    # Check list bounds before accessing
                    if field_index < len(field_values):
                        value = field_values[field_index]
                    else:
                        continue  # Skip malformed notes

                    if value:  # Only include non-empty values
                        # Strip HTML/AV tags here for the final clean value
                        clean_value = col.media.strip_av_tags(value)
                        unique_values.add(clean_value)

                except Exception as e:
                    # Catch any other processing errors for a single note
                    logger.warning(
                        f"Error processing field data for field '{name}': {e}"
                    )
                    continue

            if unique_values:
                # Use a proper base for Enum if possible, otherwise it defaults to str/int
                enum_members = {v: v for v in sorted(list(unique_values))}
                FieldEnum = typing.cast(
                    type[str], enum.Enum(f"{name}Enum", enum_members)
                )
                field_type = FieldEnum
            # --- End Optimized Dynamic Enum Generation ---

        # All fields are required. Definition format: (Type, Field | ...)
        fields[name] = (field_type, field_definition)

    # 4. Handle the 'tags' Field
    fields["tags"] = (list[str], Field(description="Tags associated with the note."))

    # 5. Dynamically Create the Pydantic Model Class
    model_name = f'{note_type.get("name", "DynamicNote")}Model'
    DynamicClass = create_model(
        "".join(word.capitalize() for word in model_name.split()),
        __base__=BaseModel,
        __config__=ConfigDict(use_enum_values=True),
        **fields,
    )

    logger.debug(
        "Generated Pydantic class '%s' with fields: %s",
        DynamicClass.__name__,
        list(fields.keys()),
    )
    return DynamicClass


def example_notes(note_type: NotetypeDict, n: int = 10) -> str:
    """
    Generates example notes string for a given note type using an optimized
    SQLite query with ORDER BY random() LIMIT.
    """
    examples = []
    if not mw or not mw.col:
        logger.error("mw.col not available in example_notes.")
        return json.dumps([])

    model_id = note_type.get("id")
    if not model_id:
        logger.error(f"NoteType object missing 'id': {note_type}")
        return json.dumps([])

    if n <= 0:
        return json.dumps([])

    # Anki's field separator character
    field_separator = "\x1f"

    try:
        # Use a single, fast SQL query to randomly sample the notes and retrieve
        # the required data (nid, tags, flds)
        # mw.col.db.all() returns a list of tuples: (nid, tags, flds)
        notes_data = mw.col.db.all(
            """
            SELECT id, tags, flds FROM notes 
            WHERE mid = ? 
            ORDER BY random() 
            LIMIT ?
            """,
            model_id,
            n,
        )
    except Exception as e:
        logger.error(
            f"Error querying database for notes (mid: {model_id}): {e}", exc_info=True
        )
        return json.dumps([])

    if not notes_data:
        logger.info(
            f"No notes found for notetype '{note_type.get('name', 'Unknown')}'."
        )
        return json.dumps([])

    # Get the ordered list of field names once
    field_names = mw.col.models.field_names(note_type)

    # Process the small sampled list of data
    for nid, tags_str, flds_str in notes_data:
        try:
            # Split the field data string into a list of values
            field_values = flds_str.split(field_separator)

            # Map field names to field values
            example = {}
            for name, value in zip(field_names, field_values):
                # Strip HTML/AV tags from field values for cleaner examples
                example[name] = mw.col.media.strip_av_tags(value)

            # Clean up and include tags
            # Tags are stored as " tag1 tag2 tag3 " in the database
            example["tags"] = tags_str.strip().split(" ") if tags_str.strip() else []
            examples.append(example)

        except Exception as e:
            logger.warning(f"Error processing note data (nid: {nid}): {e}")
            continue  # Skip this note

    return json.dumps(examples, indent=2, ensure_ascii=False)


def note_to_dict(note: Note) -> NoteDict:
    """
    Converts an Anki note to a JSON string.
    """
    # return {str(k): str(v) if v != "_" else "" for k, v in note.items()},
    # this one filtered for "_" values and converted them into empty strings -> why?
    return {
        str(k): [str(item) for item in v] if k == "tags" else str(v)
        for k, v in note.items()
    }


def get_prompt_text(
    prompt: str,
    base_prompt: str,
    note_type: NotetypeDict,
    config_: ConfigType | None,
    dummy_keys: Iterable[str] | None = None,
    json_note: NoteDict | None = None,
) -> str:
    # TODO: change this around to accept a string prompt and we need to deal with json_note not always being there ...
    """
    Formats a base prompt with note data, schema, and examples.
    Schema and examples are generated if their placeholders are in the base_prompt.
    """
    config_ = config_ or config_data
    example_count = config_.get("example_notes_count", 10)
    formatter = CustomFormatter(
        note_type=note_type,
        example_count=example_count,
        dummy_keys=dummy_keys,
    )

    return formatter.format(base_prompt, prompt=prompt, json_note=json_note)


def get_prompt_text_from_note(
    note: Note,
    base_prompt: str,
    config_: ConfigType | None,
    dummy_keys: Iterable[str] | None = None,
) -> str:
    """
    Formats a base prompt with note data, schema, and examples.
    Schema and examples are generated if their placeholders are in the base_prompt.
    """
    note_type = note.note_type()

    # if not note_type:
    #     logger.error(f"Note {note.id} has no note type. Cannot generate prompt.")
    #     return ""

    prompt: str = str(note.fields[0])
    json_note = note_to_dict(note)

    return get_prompt_text(
        prompt=prompt,
        base_prompt=base_prompt,
        note_type=note_type,
        config_=config_,
        dummy_keys=dummy_keys,
        json_note=json_note,
    )


def get_prompt_text_by_mode(
    mode: PromptMode,
    note: Note,
    config_: ConfigType | None = None,
    **kwargs,
) -> str:
    """
    Generates the 'create' prompt for a note using the effective configuration.
    """
    base_prompt = get_base_prompt_by_mode(mode, config_)

    if not base_prompt:
        raise RuntimeError(
            f"Prompt template for {mode} is empty for note {note.id}. Effective config: {config_}"
        )

    return get_prompt_text_from_note(  # Calls the local get_prompt
        note=note,
        base_prompt=base_prompt,
        config_=config_,  # Pass effective config for example_notes_count etc.
        **kwargs,
    )


def get_base_prompt_by_mode(
    mode: PromptMode, config_: ConfigType | None = None
) -> str | None:
    """
    Retrieves the base prompt template for a given mode from saved prompts.
    """
    config_ = config_ or config_data
    saved_prompts = load_saved_prompts()

    prompt_template_uuid = config_.get(f"{mode}_prompt_template")
    if prompt_template_uuid is None:
        logger.warning(f"No prompt template chosen for %s", mode)
        return None

    try:
        base_prompt = saved_prompts[prompt_template_uuid]["template"]
    except KeyError:
        logger.warning(f"No saved template found for uuid: %s", prompt_template_uuid)
        return None

    return base_prompt


def get_create_prompt(note: Note, config_: ConfigType | None = None, **kwargs) -> str:
    """
    Generates the 'create' prompt for a note using the effective configuration.
    """
    return get_prompt_text_by_mode("create", note, config_, **kwargs)


def get_complete_prompt(note, current_config: dict, **kwargs) -> str:
    """
    Generates the 'complete' prompt for a note using the effective configuration.
    """
    return get_prompt_text_by_mode("complete", note, config_, **kwargs)
