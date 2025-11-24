from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

# Type aliases using built-in types
type NotesDataList = list[dict[str, str | list[str]]]
type NoteDict = dict[str, str | list[str]]


class PromptMode(StrEnum):
    CREATE = "create"
    COMPLETE = "complete"


class ReprMixin:
    """
    Mixin that provides a standard __repr__ by consuming the __rich_repr__ iterator.

    This ensures that the custom, concise formatting defined in __rich_repr__
    is used even when standard Python logging calls repr() directly on the object.
    """

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        """Custom standard representation using the __rich_repr__ method."""
        cls_name = type(self).__name__

        # Consume the __rich_repr__ generator to get the field data
        try:
            field_data = list(self.__rich_repr__())
        except AttributeError:
            # Fallback if __rich_repr__ is missing, though it shouldn't be for our dataclasses
            return super().__repr__()

        # Format the data into a concise string: ClassName(field=value, ...)
        repr_parts = []
        for item in field_data:
            if isinstance(item, tuple) and len(item) == 2:
                # Handle (name, value) tuples
                name, value = item
                repr_parts.append(f"{name}={repr(value)}")
            elif isinstance(item, str):
                # Handle bare positional fields (less common for dataclasses, but rich supports it)
                repr_parts.append(repr(item))
            else:
                # Handle unexpected items gracefully
                repr_parts.append(repr(item))

        return f"{cls_name}({', '.join(repr_parts)})"


# --- Helper Function for Truncation ---
def get_short_notetype_dict_repr(value: dict[str, Any]) -> dict[str, Any]:
    """
    Returns a dictionary for rich repr that keeps 'name' and replaces the 'flds'
    list of dicts with a list of just field names, while summarizing 'tmpls' and 'css'.
    """
    if not isinstance(value, dict) or not value:
        return {"summary": "<Empty or Invalid Notetype Dict>"}

    # --- Process flds to extract only names ---
    flds_list = value.get("flds", [])
    field_names = [f.get("name", f"<Field {i}>") for i, f in enumerate(flds_list)]
    # ------------------------------------------

    # Calculate lengths for the summary placeholders
    num_tmpls = len(value.get("tmpls", []))
    css_len = len(value.get("css", ""))
    name = value.get("name", "Unknown Name")

    # Construct the new, condensed dictionary
    new_dict = {
        "name": name,
        "id": value.get("id"),  # Keep the ID for context
        # --- NEW CONCISE REPRESENTATION FOR FIELDS ---
        "flds_names": field_names,
        # ---------------------------------------------
        "tmpls": f"<templates: {num_tmpls}>",
        "css": f"<CSS: {css_len} chars>",
        # Use descriptive summaries for the omitted/long fields
    }

    # Optional: Add back the fields that were kept out of the main construction loop
    for k, v in value.items():
        # Only copy non-verbose top-level fields (excluding flds, tmpls, css, name, id)
        if k not in ["flds", "tmpls", "css", "name", "id"] and not isinstance(
            v, (dict, list, str)
        ):
            new_dict[k] = v

    return new_dict


# -------------------------------------
# -------------------------------------


@dataclass
class ZikariaRequestData(ReprMixin):
    """Data required for a single network request."""

    # Use built-in dict/list and the union operator | None
    notetype_dict: dict  # The actual large field
    mode: PromptMode | None = None
    # Marked for exclusion from rich/default repr due to potential large size
    prompt_str: str | None = field(default=None, repr=False)

    effective_conf: dict | None = None
    original_note_id: int | None = None
    original_note_mod: int | None = None
    metadata: dict[str, int | float | str | bool] | None = None
    pydantic_class: type[BaseModel] | None = None

    def __rich_repr__(self) -> Iterator[Any]:
        """Custom rich representation for log message truncation."""

        # 1. Truncate the verbose notetype_dict using the helper function
        yield "notetype_dict", get_short_notetype_dict_repr(self.notetype_dict)

        # 2. Yield other fields normally
        yield "mode", self.mode

        # # Shorten effective_conf for logs
        # if self.effective_conf:
        #     yield "effective_conf", f"<Config Dict | {len(self.effective_conf)} keys>"
        # else:
        #     yield "effective_conf", self.effective_conf
        yield "effective_conf", self.effective_conf

        yield "original_note_id", self.original_note_id
        yield "original_note_mod", self.original_note_mod
        yield "metadata", self.metadata
        yield "pydantic_class", self.pydantic_class


@dataclass
class ZikariaResponseData(ReprMixin):
    """Result from a single network request."""

    # Use built-in dict and class reference
    request_data: ZikariaRequestData
    notes_data_list: list[dict]  # The parsed, normalized data

    def __rich_repr__(self) -> Iterator[Any]:
        """Custom rich representation for log message truncation."""
        # This will use the custom __rich_repr__ from ZikariaRequestData
        yield "request_data", self.request_data

        # # Truncate the list of notes to show the count
        # if self.notes_data_list is not None:
        #     yield "notes_data_list", f"<list of {len(self.notes_data_list)} notes>"
        # else:
        #     yield "notes_data_list", None
        yield "notes_data_list", self.notes_data_list


@dataclass
class LogContainer(ReprMixin):
    """A wrapper to force custom rich repr on a list of objects."""

    responses: list[ZikariaResponseData]

    def __rich_repr__(self) -> Iterator[Any]:
        # Yield the list of responses.
        # Rich will see this as the item to format and should recursively
        # use the __rich_repr__ of each ZikariaResponseData item.
        # Note: We name it 'responses' so rich knows what to label it.
        yield "Responses", self.responses
