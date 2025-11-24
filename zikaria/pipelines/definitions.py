# zikaria/pipelines/definitions.py

from ..types import PromptMode
from . import steps

create_notes_from_tags_pipeline = [
    steps.PreparePrompts(mode=PromptMode.CREATE),
    steps.SendToAI(),
    steps.ShowConfirmationDialog(),
    steps.CreateNotes(),
    steps.MarkAsProcessed(),
]
