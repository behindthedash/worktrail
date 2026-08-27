"""Provider-neutral runtime configuration and selection primitives."""

from .routing_source import routing_candidates
from .selection import ExecutionTarget, select_execution_target

__all__ = [
    "ExecutionTarget",
    "routing_candidates",
    "select_execution_target",
]
