"""Ordered provider/model intent and its separately recorded discovery state.

The catalog deliberately never deletes or reorders configured candidates during
discovery.  An operator's desired order is configuration; a provider's current
answer is evidence.  Keeping those concepts separate makes transient provider
failures safe and leaves a useful audit trail when a model disappears.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml


class CatalogError(ValueError):
    """The catalog is unsafe or structurally invalid."""


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CatalogError(f"{field_name} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True)
class DiscoveryMetadata:
    """Observed availability. None means discovery has not answered yet."""

    available: bool | None = None
    last_seen: str | None = None
    checked_at: str | None = None
    unavailable_since: str | None = None
    source: str | None = None
    review_at: str | None = None
    removal_at: str | None = None


@dataclass(frozen=True)
class ModelCandidate:
    """One configured model candidate, in operator-authored preference order."""

    provider: str
    model: str
    cost: str = "unknown"
    capabilities: tuple[str, ...] = ()
    purposes: tuple[str, ...] = ()
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    observed: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.model

    @property
    def observed_available(self) -> bool | None:
        return self.observed.available


@dataclass(frozen=True)
class ProviderCatalog:
    """A provider and its ordered configured models."""

    name: str
    models: tuple[ModelCandidate, ...]
    enabled: bool = True
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCatalog:
    """Immutable catalog whose tuple order is selection order."""

    providers: tuple[ProviderCatalog, ...]
    version: int = 1

    def candidates(self, *, enabled_only: bool = True) -> tuple[ModelCandidate, ...]:
        return tuple(
            model
            for provider in self.providers
            if provider.enabled or not enabled_only
            for model in provider.models
            if model.enabled or not enabled_only
        )

    def find(self, provider: str, model: str) -> ModelCandidate | None:
        return next((item for item in self.candidates(enabled_only=False) if item.key == (provider, model)), None)


@dataclass(frozen=True)
class DiscoveredModel:
    provider: str
    model: str
    source: str
    available: bool = True


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{name} must be a mapping")
    return value


def catalog_from_dict(document: Mapping[str, Any]) -> ModelCatalog:
    """Parse and validate a catalog document without mutating its order."""

    version = document.get("version", 1)
    if version != 1:
        raise CatalogError(f"unsupported catalog version: {version!r}")
    raw_providers = document.get("providers", [])
    if not isinstance(raw_providers, list):
        raise CatalogError("providers must be an ordered list")
    providers: list[ProviderCatalog] = []
    seen: set[tuple[str, str]] = set()
    for provider_index, raw_provider in enumerate(raw_providers):
        provider_doc = _mapping(raw_provider, f"providers[{provider_index}]")
        name = provider_doc.get("name")
        if not isinstance(name, str) or not name:
            raise CatalogError(f"providers[{provider_index}].name must be a non-empty string")
        raw_models = provider_doc.get("models", [])
        if not isinstance(raw_models, list):
            raise CatalogError(f"provider {name!r} models must be an ordered list")
        models: list[ModelCandidate] = []
        for model_index, raw_model in enumerate(raw_models):
            model_doc = _mapping(raw_model, f"provider {name!r} models[{model_index}]")
            model_name = model_doc.get("name")
            if not isinstance(model_name, str) or not model_name:
                raise CatalogError(f"provider {name!r} model name must be a non-empty string")
            key = name, model_name
            if key in seen:
                raise CatalogError(f"duplicate configured candidate: {name}:{model_name}")
            seen.add(key)
            observed_doc = _mapping(model_doc.get("observed", {}), f"observed state for {name}:{model_name}")
            available = observed_doc.get("available")
            if available not in (True, False, None):
                raise CatalogError(f"observed.available for {name}:{model_name} must be boolean or null")
            known = {"name", "cost", "capabilities", "purposes", "enabled", "observed"}
            models.append(ModelCandidate(
                provider=name,
                model=model_name,
                cost=str(model_doc.get("cost", "unknown")),
                capabilities=_strings(model_doc.get("capabilities"), "capabilities"),
                purposes=_strings(model_doc.get("purposes"), "purposes"),
                enabled=bool(model_doc.get("enabled", True)),
                metadata={key: value for key, value in model_doc.items() if key not in known},
                observed=DiscoveryMetadata(
                    available=available,
                    last_seen=observed_doc.get("last_seen"),
                    checked_at=observed_doc.get("checked_at"),
                    unavailable_since=observed_doc.get("unavailable_since"),
                    source=observed_doc.get("source"),
                    review_at=observed_doc.get("review_at"),
                    removal_at=observed_doc.get("removal_at"),
                ),
            ))
        known_provider = {"name", "models", "enabled", "source"}
        providers.append(ProviderCatalog(
            name=name,
            models=tuple(models),
            enabled=bool(provider_doc.get("enabled", True)),
            source=provider_doc.get("source"),
            metadata={key: value for key, value in provider_doc.items() if key not in known_provider},
        ))
    return ModelCatalog(tuple(providers), version=version)


def load_catalog(path: str | Path, *, missing_ok: bool = False) -> ModelCatalog:
    """Safely load YAML. Missing config can explicitly opt into documented defaults."""

    catalog_path = Path(path)
    if not catalog_path.exists():
        if missing_ok:
            return default_catalog()
        raise CatalogError(f"catalog does not exist: {catalog_path}")
    try:
        loaded = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"could not load catalog {catalog_path}: {exc}") from exc
    if loaded is None:
        loaded = {}
    return catalog_from_dict(_mapping(loaded, "catalog"))


def reconcile_discovery(
    catalog: ModelCatalog,
    observations: Iterable[DiscoveredModel],
    *,
    checked_at: datetime | str | None = None,
    provider: str | None = None,
) -> ModelCatalog:
    """Return a catalog with new evidence, preserving configured intent exactly.

    When ``provider`` is supplied, configured models omitted by that provider's
    complete scan become unavailable. With no provider, only explicit evidence
    is applied (safe for partial/multi-provider probes). Unknown discovered
    models are ignored: discovery must not silently opt the operator into them.
    """

    when = _timestamp(checked_at)
    observed = {(item.provider, item.model): item for item in observations}
    updated_providers: list[ProviderCatalog] = []
    for configured_provider in catalog.providers:
        updated_models: list[ModelCandidate] = []
        for candidate in configured_provider.models:
            result = observed.get(candidate.key)
            missing_from_complete_scan = provider == candidate.provider and result is None
            if result is None and not missing_from_complete_scan:
                updated_models.append(candidate)
                continue
            is_available = bool(result.available) if result is not None else False
            old = candidate.observed
            source = result.source if result is not None else (configured_provider.source or old.source)
            state = DiscoveryMetadata(
                available=is_available,
                last_seen=when if is_available else old.last_seen,
                checked_at=when,
                unavailable_since=None if is_available else (old.unavailable_since or when),
                source=source,
                review_at=old.review_at,
                removal_at=old.removal_at,
            )
            updated_models.append(replace(candidate, observed=state))
        updated_providers.append(replace(configured_provider, models=tuple(updated_models)))
    return replace(catalog, providers=tuple(updated_providers))


def discover_catalog(
    catalog: ModelCatalog,
    discoverers: Mapping[str, Callable[[], Iterable[str | DiscoveredModel]]],
    *,
    checked_at: datetime | str | None = None,
) -> tuple[ModelCatalog, dict[str, str]]:
    """Run provider CLI/API adapters and reconcile their complete model scans.

    Discovery adapters own provider-specific transport.  This catalog layer
    only normalizes their results, which keeps subprocess/API concerns out of
    selection.  A failed adapter leaves its prior observations untouched and
    returns an audit error instead of marking every configured model missing.
    """

    updated = catalog
    errors: dict[str, str] = {}
    for provider in catalog.providers:
        discover = discoverers.get(provider.name)
        if discover is None:
            continue
        try:
            raw = tuple(discover())
            observations = tuple(
                item if isinstance(item, DiscoveredModel) else DiscoveredModel(
                    provider.name, str(item), provider.source or "adapter"
                )
                for item in raw
            )
        except Exception as exc:
            errors[provider.name] = f"{type(exc).__name__}: {exc}"
            continue
        updated = reconcile_discovery(
            updated, observations, checked_at=checked_at, provider=provider.name
        )
    return updated, errors


def default_catalog() -> ModelCatalog:
    """Conservative, readable sample defaults; list order is preference order."""

    return catalog_from_dict(yaml.safe_load(DEFAULT_CATALOG_YAML))


DEFAULT_CATALOG_YAML = """\
version: 1
# Providers and models are tried top-to-bottom. Reorder list items; do not use maps.
providers:
  - name: claude
    source: cli
    models:
      - name: sonnet
        cost: paid
        capabilities: [tools, coding, long-context]
        purposes: [implement, review]
      - name: haiku
        cost: paid-low
        capabilities: [tools, coding]
        purposes: [classify, cleanup]
  - name: codex
    source: cli
    models:
      - name: gpt-5.4-mini
        cost: paid-low
        capabilities: [tools, coding]
        purposes: [implement, classify]
  - name: opencode
    source: cli
    models:
      - name: opencode/deepseek-v4-flash-free
        cost: free
        capabilities: [tools, low-context]
        purposes: [classify, trivia]
"""


def catalog_to_dict(catalog: ModelCatalog) -> dict[str, Any]:
    """Produce a YAML/JSON-safe representation suitable for audit persistence."""

    providers: list[dict[str, Any]] = []
    for provider in catalog.providers:
        provider_doc: dict[str, Any] = {"name": provider.name, "enabled": provider.enabled}
        if provider.source:
            provider_doc["source"] = provider.source
        provider_doc.update(provider.metadata)
        provider_doc["models"] = []
        for model in provider.models:
            model_doc: dict[str, Any] = {
                "name": model.model, "cost": model.cost,
                "capabilities": list(model.capabilities), "purposes": list(model.purposes),
                "enabled": model.enabled,
            }
            model_doc.update(model.metadata)
            model_doc["observed"] = {
                key: value for key, value in vars(model.observed).items() if value is not None
            }
            provider_doc["models"].append(model_doc)
        providers.append(provider_doc)
    return {"version": catalog.version, "providers": providers}
