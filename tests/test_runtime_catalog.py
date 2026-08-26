from datetime import datetime, timezone

import pytest

from worktrail.runtime.catalog import (
    CatalogError,
    DiscoveredModel,
    catalog_from_dict,
    catalog_to_dict,
    default_catalog,
    discover_catalog,
    load_catalog,
    reconcile_discovery,
)


def _catalog():
    return catalog_from_dict({
        "version": 1,
        "providers": [
            {"name": "alpha", "source": "cli", "models": [
                {"name": "preferred", "cost": "free", "capabilities": ["tools"]},
                {"name": "fallback", "cost": "paid", "capabilities": ["coding"]},
            ]},
            {"name": "beta", "models": [{"name": "last"}]},
        ],
    })


def test_configuration_order_and_metadata_are_preserved():
    catalog = _catalog()

    assert [(item.provider, item.model) for item in catalog.candidates()] == [
        ("alpha", "preferred"), ("alpha", "fallback"), ("beta", "last")
    ]
    assert catalog.find("alpha", "preferred").cost == "free"
    assert catalog.find("alpha", "preferred").capabilities == ("tools",)


def test_discovery_records_evidence_without_rewriting_intent():
    catalog = _catalog()
    when = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)

    updated = reconcile_discovery(
        catalog,
        [DiscoveredModel("alpha", "preferred", "alpha models", True)],
        checked_at=when,
        provider="alpha",
    )

    assert [(item.provider, item.model) for item in updated.candidates()] == [
        ("alpha", "preferred"), ("alpha", "fallback"), ("beta", "last")
    ]
    preferred = updated.find("alpha", "preferred").observed
    assert preferred.available is True
    assert preferred.last_seen == "2026-08-26T16:00:00+00:00"
    assert preferred.checked_at == preferred.last_seen
    assert preferred.unavailable_since is None
    assert preferred.source == "alpha models"
    missing = updated.find("alpha", "fallback").observed
    assert missing.available is False
    assert missing.unavailable_since == "2026-08-26T16:00:00+00:00"
    # beta wasn't part of this complete scan.
    assert updated.find("beta", "last").observed.available is None


def test_reappearing_model_clears_unavailable_since_and_keeps_audit_fields():
    first = reconcile_discovery(_catalog(), [], checked_at="2026-08-01T00:00:00Z", provider="alpha")
    document = catalog_to_dict(first)
    document["providers"][0]["models"][0]["observed"].update({
        "review_at": "2026-09-01", "removal_at": "2026-10-01"
    })
    persisted = catalog_from_dict(document)

    second = reconcile_discovery(
        persisted,
        [DiscoveredModel("alpha", "preferred", "api")],
        checked_at="2026-08-02T00:00:00Z",
        provider="alpha",
    )
    state = second.find("alpha", "preferred").observed
    assert state.available is True
    assert state.unavailable_since is None
    assert state.last_seen == "2026-08-02T00:00:00Z"
    assert state.review_at == "2026-09-01"
    assert state.removal_at == "2026-10-01"


def test_partial_discovery_does_not_mark_omitted_models_unavailable():
    updated = reconcile_discovery(
        _catalog(), [DiscoveredModel("alpha", "preferred", "partial-probe")],
        checked_at="now",
    )
    assert updated.find("alpha", "preferred").observed.available is True
    assert updated.find("alpha", "fallback").observed.available is None


def test_unknown_discovered_model_does_not_change_configured_intent():
    updated = reconcile_discovery(
        _catalog(), [DiscoveredModel("alpha", "new-vendor-model", "api")], checked_at="now"
    )
    assert updated.find("alpha", "new-vendor-model") is None
    assert len(updated.candidates()) == 3


def test_safe_yaml_load_rejects_non_mapping_and_preserves_extras(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("providers:\n  - name: alpha\n    region: us\n    models:\n      - name: m\n        context_tokens: 1000\n")
    catalog = load_catalog(path)
    assert catalog.providers[0].metadata == {"region": "us"}
    assert catalog.candidates()[0].metadata == {"context_tokens": 1000}

    path.write_text("- unsafe-shape\n")
    with pytest.raises(CatalogError, match="catalog must be a mapping"):
        load_catalog(path)


def test_missing_file_requires_explicit_default_opt_in(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(CatalogError, match="does not exist"):
        load_catalog(missing)
    assert load_catalog(missing, missing_ok=True) == default_catalog()


def test_duplicate_candidates_and_mapping_models_fail_closed():
    with pytest.raises(CatalogError, match="duplicate configured candidate"):
        catalog_from_dict({"providers": [{"name": "p", "models": [{"name": "m"}, {"name": "m"}]}]})
    with pytest.raises(CatalogError, match="ordered list"):
        catalog_from_dict({"providers": [{"name": "p", "models": {"m": {}}}]})


def test_discovery_adapters_scan_each_provider_without_mixing_transport():
    updated, errors = discover_catalog(
        _catalog(),
        {"alpha": lambda: ["fallback"], "beta": lambda: ["last"]},
        checked_at="2026-08-26T17:00:00Z",
    )
    assert errors == {}
    assert updated.find("alpha", "preferred").observed.available is False
    assert updated.find("alpha", "fallback").observed.available is True
    assert updated.find("beta", "last").observed.available is True


def test_failed_discovery_adapter_preserves_prior_observations():
    def broken():
        raise RuntimeError("CLI offline")

    original = _catalog()
    updated, errors = discover_catalog(original, {"alpha": broken}, checked_at="now")
    assert updated == original
    assert errors == {"alpha": "RuntimeError: CLI offline"}
