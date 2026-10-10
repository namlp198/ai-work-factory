"""Task and job state machines. Every state change goes through `transition` so illegal moves fail loudly."""

from __future__ import annotations

from awf.domain.enums import JobState, TaskState

T = TaskState

TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    T.NEW: {T.READY, T.CANCELLED, T.FAILED},
    T.READY: {T.WAITING, T.RUNNING, T.APPROVAL_REQUIRED, T.CANCELLED, T.FAILED},
    # WAITING: job queued, waiting for a worker to claim it.
    T.WAITING: {T.RUNNING, T.READY, T.FAILED, T.CANCELLED},
    T.RUNNING: {T.REVIEW, T.READY, T.WAITING, T.FAILED, T.CANCELLED},
    # REVIEW: output produced, critic evaluating.
    T.REVIEW: {T.DONE, T.CHANGES_REQUIRED, T.APPROVAL_REQUIRED, T.FAILED, T.CANCELLED},
    T.CHANGES_REQUIRED: {T.READY, T.FAILED, T.CANCELLED},
    T.APPROVAL_REQUIRED: {T.DONE, T.CHANGES_REQUIRED, T.FAILED, T.CANCELLED},
    # Manual retry from the API.
    T.FAILED: {T.READY},
    T.CANCELLED: set(),
    T.DONE: set(),
}

J = JobState

JOB_TRANSITIONS: dict[JobState, set[JobState]] = {
    J.QUEUED: {J.CLAIMED, J.CANCELLED},
    J.CLAIMED: {J.PREPARING, J.RUNNING, J.FAILED, J.QUEUED, J.CANCELLED},
    J.PREPARING: {J.RUNNING, J.FAILED, J.QUEUED, J.CANCELLED},
    J.RUNNING: {J.UPLOADING_RESULT, J.SUCCESS, J.FAILED, J.QUEUED, J.CANCELLED},
    J.UPLOADING_RESULT: {J.SUCCESS, J.FAILED, J.QUEUED, J.CANCELLED},
    J.FAILED: {J.RETRYABLE, J.TERMINAL},
    J.RETRYABLE: {J.QUEUED},
    J.SUCCESS: set(),
    J.TERMINAL: set(),
    J.CANCELLED: set(),
}


class InvalidTransition(ValueError):
    pass


def can_transition_task(current: TaskState | str, target: TaskState | str) -> bool:
    return TaskState(target) in TASK_TRANSITIONS[TaskState(current)]


def check_task_transition(current: TaskState | str, target: TaskState | str) -> TaskState:
    current, target = TaskState(current), TaskState(target)
    if target not in TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"task transition {current} -> {target} is not allowed")
    return target


def check_job_transition(current: JobState | str, target: JobState | str) -> JobState:
    current, target = JobState(current), JobState(target)
    if target not in JOB_TRANSITIONS[current]:
        raise InvalidTransition(f"job transition {current} -> {target} is not allowed")
    return target
