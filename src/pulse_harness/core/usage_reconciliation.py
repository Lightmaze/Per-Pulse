"""Capability-routed authority for provider usage reconciliation."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .contracts import ModelRoundRecord, ModelRoundStatus
from .errors import (
    ProviderCapabilityMissing,
    ProviderProtocolError,
    ProviderUnavailable,
)
from .provider import ProviderEvidence, ProviderUsage


class ProviderUsageReconciler(Protocol):
    """Provider-specific lookup port selected by durable capability evidence."""

    capability: str

    def lookup(self, provider_response_id: str) -> ProviderUsage: ...


class _ProviderUsageReconciliationPermit:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class UsageReconciliationBlocker:
    round_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class UsageReconciliationReport:
    reconciled_round_ids: tuple[str, ...]
    blockers: tuple[UsageReconciliationBlocker, ...]
    remaining_unaccounted_rounds: int


class ProviderUsageReconciliationManager:
    """Route durable usage gaps through exactly one authorized lookup port."""

    def __init__(
        self,
        store,
        reconciler: ProviderUsageReconciler
        | Iterable[ProviderUsageReconciler],
    ) -> None:
        if callable(getattr(reconciler, "lookup", None)):
            registered = (reconciler,)
        else:
            try:
                registered = tuple(reconciler)
            except TypeError as exc:
                raise ValueError(
                    "usage reconciler must implement lookup or be iterable"
                ) from exc
        if not registered:
            raise ValueError("at least one usage reconciler is required")

        reconcilers_by_capability: dict[str, ProviderUsageReconciler] = {}
        for candidate in registered:
            capability = getattr(candidate, "capability", None)
            if not isinstance(capability, str) or not capability.strip():
                raise ValueError("usage reconciler capability must be non-empty")
            if not callable(getattr(candidate, "lookup", None)):
                raise ValueError("usage reconciler must implement lookup")
            if capability in reconcilers_by_capability:
                raise ValueError("usage reconciler capabilities must be unique")
            reconcilers_by_capability[capability] = candidate

        self.store = store
        self.reconcilers = tuple(registered)
        self._reconcilers_by_capability = reconcilers_by_capability
        self.__permit = _ProviderUsageReconciliationPermit()
        store._bind_provider_usage_reconciler(
            self,
            self.__permit,
            frozenset(reconcilers_by_capability),
        )

    def reconcile_round(self, round_record: ModelRoundRecord) -> ModelRoundRecord:
        if round_record.status is not ModelRoundStatus.COMPLETED:
            raise ProviderCapabilityMissing(
                "only completed model rounds can be usage-reconciled",
                code="provider_usage_reconciliation_ineligible",
            )
        if round_record.provider_usage is not None:
            return round_record
        if round_record.provider_response_id is None:
            raise ProviderCapabilityMissing(
                "model round has no provider response identity",
                code="provider_response_identity_missing",
            )

        capability, reconciler = self._select_reconciler(round_record)
        usage = reconciler.lookup(round_record.provider_response_id)
        if not isinstance(usage, ProviderUsage):
            raise ProviderProtocolError(
                "provider usage lookup returned an invalid value",
                code="provider_usage_lookup_invalid",
            )
        return self.store.reconcile_provider_usage(
            round_record.scope,
            round_record.round_id,
            expected_provider_response_id=round_record.provider_response_id,
            expected_provider_evidence_digest=round_record.provider_evidence_digest,
            provider_usage=usage.as_dict(),
            usage_evidence_class=usage.evidence_class,
            _reconciler=self,
            _reconciler_capability=self.__permit,
            _lookup_capability=capability,
        )

    def reconcile_all(self, *, max_rounds: int | None = None) -> UsageReconciliationReport:
        if max_rounds is not None and (
            type(max_rounds) is not int or max_rounds <= 0
        ):
            raise ValueError("max_rounds must be a positive integer")
        reconciled: list[str] = []
        blockers: list[UsageReconciliationBlocker] = []
        rounds = self.store.unaccounted_provider_usage_rounds()
        if max_rounds is not None:
            rounds = rounds[:max_rounds]
        for round_record in rounds:
            reason_code = self._static_blocker(round_record)
            if reason_code is not None:
                blockers.append(
                    UsageReconciliationBlocker(
                        round_id=round_record.round_id,
                        reason_code=reason_code,
                    )
                )
                continue
            try:
                self.reconcile_round(round_record)
            except (ProviderProtocolError, ProviderUnavailable) as exc:
                blockers.append(
                    UsageReconciliationBlocker(
                        round_id=round_record.round_id,
                        reason_code=exc.code,
                    )
                )
            else:
                reconciled.append(round_record.round_id)
        return UsageReconciliationReport(
            reconciled_round_ids=tuple(reconciled),
            blockers=tuple(blockers),
            remaining_unaccounted_rounds=(
                self.store.count_unaccounted_provider_usage_rounds()
            ),
        )

    def _static_blocker(self, round_record: ModelRoundRecord) -> str | None:
        if round_record.provider_response_id is None:
            return "provider_response_identity_missing"
        evidence = ProviderEvidence.from_dict(round_record.provider_evidence)
        matches = self._matching_capabilities(evidence)
        if not matches:
            return "provider_usage_reconciliation_namespace_mismatch"
        if len(matches) > 1:
            return "provider_usage_reconciliation_namespace_ambiguous"
        return None

    def _matching_capabilities(self, evidence: ProviderEvidence) -> tuple[str, ...]:
        return tuple(
            sorted(
                evidence.capabilities.intersection(self._reconcilers_by_capability)
            )
        )

    def _select_reconciler(
        self, round_record: ModelRoundRecord
    ) -> tuple[str, ProviderUsageReconciler]:
        evidence = ProviderEvidence.from_dict(round_record.provider_evidence)
        matches = self._matching_capabilities(evidence)
        if not matches:
            raise ProviderCapabilityMissing(
                "model round provider evidence does not authorize a registered usage lookup",
                code="provider_usage_reconciliation_namespace_mismatch",
            )
        if len(matches) > 1:
            raise ProviderCapabilityMissing(
                "model round provider evidence authorizes multiple usage lookups",
                code="provider_usage_reconciliation_namespace_ambiguous",
            )
        capability = matches[0]
        return capability, self._reconcilers_by_capability[capability]
