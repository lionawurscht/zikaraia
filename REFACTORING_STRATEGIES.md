# Refactoring Strategies for the Anki Gemini Addon

This document outlines a refactoring strategy for the Anki Gemini addon. The goal is to improve the modularity, extensibility, and maintainability of the codebase by introducing a pipeline-based architecture.

## Introduction

The Anki Gemini addon was initially developed to create and update Anki notes using the Gemini API. It started with two main mechanisms: creating notes from a list of words and completing/updating existing notes, both triggered by special tags. Over time, new features were added, such as direct JSON input and a dialog for entering a wordlist.

While the addon is feature-rich, its structure reflects this organic growth. The different workflows are not clearly separated, leading to code that is difficult to maintain and extend. The current data flow, which relies on passing a response object, is not flexible enough to accommodate the different contexts of each workflow.

The primary goal of this refactoring is to introduce a more modular and extensible architecture without sacrificing any existing functionality. Specifically, the refactoring will:

*   **Improve Modularity:** Decouple the different workflows into independent components.
*   **Enhance Extensibility:** Make it easier to add new features and workflows in the future.
*   **Increase Maintainability:** Improve the readability and organization of the code.
*   **Preserve Existing Features:** Ensure that all current features, especially the note confirmation dialog, continue to work as expected.

To achieve these goals, we will adopt a pipeline-based architecture. This approach will allow us to define each workflow as a series of steps, making the process more transparent and easier to manage.

## Proposed Architecture: The Pipeline Pattern

The proposed architecture is based on the pipeline pattern, which is a common design pattern for processing a sequence of data. In our case, the "data" is the information needed to create or update Anki notes. The pipeline will consist of three main components:

### 1. `PipelineContext`

The `PipelineContext` is a dataclass that will serve as the single source of truth for a given workflow. It will hold all the necessary information that needs to be passed between the different steps of the pipeline. This will replace the current `ZikariaRequestData` and `ZikariaResponseData` classes with a more flexible and comprehensive data structure.

The `PipelineContext` will contain, but is not limited to, the following information:

*   **`source_data`**: The initial data that triggers the workflow (e.g., a list of words, a JSON string, or a list of Anki notes).
*   **`prompt_template`**: The prompt template used to generate the AI prompt.
*   **`note_type`**: The Anki note type for the new or updated notes.
*   **`deck_id`**: The ID of the deck where the new notes will be added.
*   **`origin`**: Information about the source of the notes (e.g., the note ID of the original note, the line number in a wordlist, or `None` for direct input).
*   **`ai_responses`**: A list of responses from the Gemini API.
*   **`processed_notes`**: A list of Anki notes that have been created or updated.
*   **`ui_hooks`**: Callbacks to update the UI with the progress of the pipeline.

### 2. `PipelineStep`

A `PipelineStep` is an independent and reusable component that performs a specific task in the workflow. Each step will be a callable (a function or a class with a `__call__` method) that takes a `PipelineContext` object as input and returns a modified `PipelineContext` object.

Examples of pipeline steps include:

*   **`PreparePrompts`**: Generates the AI prompts from the source data.
*   **`SendToAI`**: Sends the prompts to the Gemini API and stores the responses in the context.
*   **`ParseAIResponses`**: Parses the AI responses into a structured format.
*   **`CreateNotes`**: Creates new Anki notes from the parsed responses.
*   **`UpdateNotes`**: Updates existing Anki notes with the parsed responses.
*   **`ShowConfirmationDialog`**: Displays the note confirmation dialog to the user.
*   **`MarkAsProcessed`**: Tags the original notes as processed.

Each step will be responsible for a single, well-defined task. This will make the code easier to understand, test, and reuse.

### 3. `PipelineExecutor`

The `PipelineExecutor` is responsible for managing the execution of the pipeline. It will take a list of `PipelineStep` objects and an initial `PipelineContext` as input. The executor will then run the steps in the specified order, passing the context from one step to the next.

The `PipelineExecutor` will also be responsible for:

*   **Asynchronous Execution**: Running I/O-bound tasks, such as sending requests to the Gemini API, in the background to avoid blocking the Anki UI.
*   **Error Handling**: Catching and logging errors that occur during the execution of the pipeline.
*   **UI Updates**: Using the `ui_hooks` in the `PipelineContext` to update the UI with the progress of the pipeline.

By using a `PipelineExecutor`, we can create different pipelines for different workflows by simply combining different `PipelineStep` objects. For example, the "create notes from tags" workflow could be defined as:

```python
create_notes_from_tags_pipeline = [
    PreparePrompts,
    SendToAI,
    ParseAIResponses,
    CreateNotes,
    ShowConfirmationDialog,
    MarkAsProcessed,
]
```

## Migration Plan

The migration to the new pipeline architecture will be done in a phased approach to minimize disruption and allow for incremental testing.

### Phase 1: Implement the Core Pipeline Components

The first phase will focus on creating the core components of the pipeline architecture:

1.  **Create `PipelineContext`**: Define the `PipelineContext` dataclass with all the necessary fields.
2.  **Create `PipelineStep`**: Define a base class or a type hint for the `PipelineStep`.
3.  **Create `PipelineExecutor`**: Implement the `PipelineExecutor` class with basic functionality for running a sequence of steps.

### Phase 2: Refactor the "Create Notes from Tags" Workflow

The "create notes from tags" workflow will be the first to be migrated to the new architecture. This will involve:

1.  **Create `PipelineStep`s**: Implement the necessary `PipelineStep`s for this workflow, such as `PreparePrompts`, `SendToAI`, `ParseAIResponses`, `CreateNotes`, `ShowConfirmationDialog`, and `MarkAsProcessed`.
2.  **Create the Pipeline**: Define the pipeline for this workflow by creating a list of the implemented `PipelineStep`s.
3.  **Update the UI**: Update the UI code to use the `PipelineExecutor` to run the pipeline when the "create notes from tags" action is triggered.

### Phase 3: Refactor the "Complete Notes from Tags" Workflow

The "complete notes from tags" workflow will be migrated next. This will involve:

1.  **Reuse and Create `PipelineStep`s**: Reuse the `PipelineStep`s from the previous phase where possible (e.g., `SendToAI`, `ParseAIResponses`, `ShowConfirmationDialog`, `MarkAsProcessed`). Implement new steps as needed, such as `UpdateNotes`.
2.  **Create the Pipeline**: Define the pipeline for this workflow.
3.  **Update the UI**: Update the UI code to use the `PipelineExecutor` for this workflow.

### Phase 4: Refactor the Remaining Workflows

The remaining workflows, such as direct JSON input and the wordlist dialog, will be migrated in the final phase. This will follow the same pattern as the previous phases:

1.  **Implement or Reuse `PipelineStep`s**: Create or reuse `PipelineStep`s for each workflow.
2.  **Define Pipelines**: Define the pipelines for each workflow.
3.  **Update the UI**: Update the UI code to use the `PipelineExecutor`.

By the end of this phase, the entire addon will be using the new pipeline architecture.

## Handling Asynchronous Operations and UI Updates

The pipeline architecture is well-suited for handling asynchronous operations and UI updates in a clean and organized manner.

### Asynchronous Operations

The `PipelineExecutor` will be responsible for running I/O-bound tasks, such as sending requests to the Gemini API, in the background. This will be achieved by using Qt's threading capabilities, specifically `QThreadPool` and `QRunnable`, which are already in use in the current codebase.

Each `PipelineStep` can be designed to be either synchronous or asynchronous. When the `PipelineExecutor` encounters an asynchronous step, it will wrap it in a `QRunnable` and submit it to the `QThreadPool`. The executor will then use signals and slots to wait for the step to complete before proceeding to the next one.

This approach will ensure that the Anki UI remains responsive while the addon is communicating with the Gemini API.

### UI Updates

The `PipelineExecutor` will use signals to communicate with the UI. It will emit signals at various points in the pipeline's execution, such as:

*   `pipeline_started`: When the pipeline starts.
*   `step_started`: When a new step starts.
*   `step_finished`: When a step completes.
*   `pipeline_finished`: When the entire pipeline completes.

These signals will carry the `PipelineContext` as a payload, allowing the UI to update its state based on the latest information. For example, the UI can display a progress bar, show intermediate results, or report errors.

The `PipelineContext` can also contain `ui_hooks`, which are callback functions that can be used to trigger specific UI actions. For example, the `ShowConfirmationDialog` step can use a `ui_hook` to display the confirmation dialog and wait for the user's input before allowing the pipeline to continue.

This mechanism will allow for a clean separation between the business logic of the pipeline and the UI code, making both easier to maintain and test.

## Comparison with Other Strategies

While the pipeline pattern is the recommended approach for this refactoring, it is worth considering other strategies and why they are less suitable.

### 1. Monolithic Refactoring

One approach would be to refactor the existing `ZikariaPrompts` class into a more organized, monolithic class. This would involve creating separate methods for each workflow and improving the overall structure of the class.

While this would improve the readability of the code to some extent, it would not address the core problem of modularity. The different workflows would still be tightly coupled, making it difficult to add new features or modify existing ones without affecting the rest of the codebase.

### 2. Service-Oriented Architecture (SOA)

Another approach would be to adopt a service-oriented architecture, where different parts of the addon are broken down into independent services. For example, we could have a `PromptService`, an `AIService`, and a `NoteService`.

While SOA is a powerful pattern for large-scale applications, it is likely overkill for this addon. The overhead of defining and managing the different services would outweigh the benefits. The pipeline pattern provides a good balance between modularity and simplicity for this project.

### Why the Pipeline Pattern is the Best Choice

The pipeline pattern is the best choice for this refactoring because it directly addresses the main challenges of the current codebase:

*   **Modularity**: The pipeline pattern forces a clean separation of concerns, with each step responsible for a single task.
*   **Extensibility**: Adding a new workflow is as simple as creating a new pipeline by combining existing and new steps.
*   **Readability**: The linear flow of a pipeline makes it easy to understand the logic of a workflow.
*   **Reusability**: `PipelineStep`s can be reused across different pipelines.
*   **Testability**: Each `PipelineStep` can be tested in isolation.

Given the specific needs of the Anki Gemini addon, the pipeline pattern offers the most elegant and effective solution for improving its architecture.

## Conclusion

The proposed refactoring to a pipeline-based architecture will significantly improve the quality of the Anki Gemini addon's codebase. By decoupling the different workflows into a series of independent and reusable steps, we will make the addon more modular, extensible, and maintainable.

This new architecture will not only make it easier to manage the existing features but will also provide a solid foundation for adding new functionality in the future. The phased migration plan will ensure a smooth transition to the new architecture, with minimal disruption to the existing functionality.

While the initial investment in refactoring the codebase will require some effort, the long-term benefits of a cleaner and more organized architecture will be well worth it. The addon will be easier to understand, test, and evolve, ensuring its continued success and value to the Anki community.
