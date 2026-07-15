# Phase 7d — Workflow Composer

Status: implementation active.

Workflow Operations now includes an accessible composer for creating cross-project DAGs from existing `planned` or `running` tasks. Each step selects a task, agent adapter, title, execution prompt, and zero or more earlier steps as dependencies.

Dependencies can reference only preceding rows. This makes cycles impossible in the client representation while the backend still performs authoritative duplicate-step-key, unique-task-assignment, reference, project/task, size, and cycle validation. Removing a row also removes its references from later steps.

One task may be assigned to only one step in a workflow. The composer removes already-selected tasks from other rows, and the backend independently rejects duplicate task IDs to prevent concurrent runs from sharing one task lifecycle.

The composer derives project IDs from the selected task rather than accepting free-form project input. A task's recognized primary agent is used as the initial adapter; otherwise Codex is the conservative default. Task time budgets remain authoritative during Phase 7b dispatch.

Operators may create a draft or activate immediately. Activation only promotes root steps to `ready`; the UI explicitly states that execution starts only after `Dispatch / reconcile`.
