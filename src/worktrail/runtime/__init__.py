"""Provider-neutral runtime configuration and selection primitives."""

from .catalog import ModelCatalog, ModelCandidate, ProviderCatalog, catalog_path
from .selection import ExecutionTarget, select_execution_target

__all__ = [
    "ExecutionTarget",
    "ModelCandidate",
    "ModelCatalog",
    "ProviderCatalog",
    "catalog_path",
    "select_execution_target",
]
