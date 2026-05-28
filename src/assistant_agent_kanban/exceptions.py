class FsKanbanError(Exception):
    pass


class TransitionError(FsKanbanError):
    pass


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
