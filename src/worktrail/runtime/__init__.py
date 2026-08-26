"""Provider-neutral runtime configuration and selection primitives."""

from .catalog import ModelCatalog, ModelCandidate, ProviderCatalog
from .selection import ExecutionTarget, select_execution_target

__all__ = [
    "ExecutionTarget",
    "ModelCandidate",
    "ModelCatalog",
    "ProviderCatalog",
    "select_execution_target",
]
