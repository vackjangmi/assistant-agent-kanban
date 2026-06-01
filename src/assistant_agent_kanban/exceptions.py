class FsKanbanError(Exception):
    pass


class TransitionError(FsKanbanError):
    pass


class DirtyTargetRepoConfirmationRequired(TransitionError):
    def __init__(self, confirmation) -> None:
        self.confirmation = confirmation
        super().__init__("target repo has local changes; confirmation is required before human verification")


class LockError(FsKanbanError):
    pass


class ServerAlreadyRunningError(FsKanbanError):
    pass


class NoSupportedAssistantError(FsKanbanError):
    pass


class TaskNotFoundError(FsKanbanError):
    pass


class AdapterRunError(FsKanbanError):
    pass


class InspectionError(FsKanbanError):
    pass


class IntegrationError(FsKanbanError):
    pass


class IntegrationConflictError(IntegrationError):
    pass


class CommitError(FsKanbanError):
    pass


class WorkspaceSyncError(FsKanbanError):
    pass
