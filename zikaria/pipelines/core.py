# zikaria/pipelines/core.py
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Protocol

from aqt.qt import QObject, QTimer, pyqtSignal, pyqtSlot


@dataclass
class PipelineContext:
    """A dataclass to hold the shared state and data across pipeline steps."""

    source_data: Any
    ai_responses: List[Any] = field(default_factory=list)
    processed_notes: List[Any] = field(default_factory=list)
    ui_hooks: Dict[str, Callable[..., Any]] = field(default_factory=dict)
    misc: Dict[str, Any] = field(default_factory=dict)


class PipelineStep(Protocol):
    """A protocol defining the interface for a pipeline step."""

    is_async: bool = False

    def __call__(self, context: PipelineContext) -> PipelineContext:
        ...


class PipelineExecutor(QObject):
    """Executes a sequence of pipeline steps, handling both sync and async operations."""



    pipeline_finished = pyqtSignal(object)
    pipeline_error = pyqtSignal(Exception)

    def __init__(self, steps: List[PipelineStep]):
        super().__init__()
        self.steps = steps
        self._current_step_index = 0
        self.context: PipelineContext = None

    def execute(self, initial_context: PipelineContext):
        """Starts the execution of the pipeline with an initial context."""
        self.context = initial_context
        self._current_step_index = 0
        self._execute_next_step()

    def _execute_next_step(self):
        if self._current_step_index >= len(self.steps):
            self.pipeline_finished.emit(self.context)
            return

        step = self.steps[self._current_step_index]
        self._current_step_index += 1

        if getattr(step, "is_async", False):
            # Asynchronous step (must be a QObject with a 'finished' signal)
            step.finished.connect(self._execute_next_step)
            step(self.context)
        else:  # Synchronous step
            try:
                self.context = step(self.context)
                QTimer.singleShot(0, self._execute_next_step)
            except Exception as e:
                self.pipeline_error.emit(e)
