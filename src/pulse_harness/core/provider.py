"""Provider-neutral model and tool adapter contracts.

The kernel owns the turn loop.  A model adapter only produces typed deltas;
it never owns transcript persistence, action admission, approval, or turn
settlement.  The OpenAI-compatible shape below is an ABI description only in
v0.1: it deliberately has no network transport or credential resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event
from typing import Any, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from .contracts import ToolCall, ToolDescriptor, ToolResult
from .errors import (
    HarnessError,
    ProviderCapabilityMissing,
    ProviderProtocolError,
    ProviderUnavailable,
)
from .scope import HarnessScope


class CancellationRequested(Exception):
    """A tool handler observed a cooperative cancellation request."""


class CancellationToken:
    """Small provider-neutral cancellation latch passed to tool adapters."""

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = Event()
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._reason:
            self._reason = reason
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancellationRequested(self._reason or "cancelled")

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class ModelDeltaKind(StrEnum):
    TEXT = "text_delta"
    REASONING = "reasoning_delta"
    TOOL_CALL = "tool_call"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Normalized provider-reported usage for one terminal response.

    Missing usage stays ``None`` at the model-round boundary.  Zero values are
    therefore evidence supplied by the provider, never a local estimate.
    """

    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    evidence_class: str = "PROVIDER_REPORTED_USAGE_V1"

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_write_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"provider usage {name} must be a non-negative int")
        if self.cached_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("provider cache usage cannot exceed input tokens")
        if not isinstance(self.evidence_class, str) or not self.evidence_class.strip():
            raise ValueError("provider usage evidence_class must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


def validate_provider_response_id(value: str) -> str:
    """Validate one bounded opaque provider-issued response identity."""

    if not isinstance(value, str) or not value:
        raise ValueError("provider response identity must be non-empty text")
    if len(value.encode("utf-8")) > 512:
        raise ValueError("provider response identity exceeds the byte bound")
    if any(character.isspace() or not character.isprintable() for character in value):
        raise ValueError("provider response identity contains unsafe characters")
    return value


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """Capability provenance required before a model adapter can run."""

    adapter_id: str
    adapter_version: str
    source: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    verified: bool = False
    test_only: bool = False

    def __post_init__(self) -> None:
        for name in ("adapter_id", "adapter_version", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"provider evidence {name} must be non-empty")
        if not isinstance(self.capabilities, frozenset):
            object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        if not all(isinstance(capability, str) and capability.strip() for capability in self.capabilities):
            raise ValueError("provider capabilities must be non-empty strings")

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "source": self.source,
            "capabilities": sorted(self.capabilities),
            "verified": self.verified,
            "test_only": self.test_only,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderEvidence":
        """Reconstruct typed evidence from the durable provider envelope."""

        if not isinstance(value, Mapping):
            raise ValueError("provider evidence must be an object")
        required = {
            "adapter_id",
            "adapter_version",
            "source",
            "capabilities",
            "verified",
            "test_only",
        }
        if set(value) != required:
            raise ValueError("provider evidence has an invalid field set")
        capabilities = value["capabilities"]
        if not isinstance(capabilities, (list, tuple, set, frozenset)):
            raise ValueError("provider evidence capabilities must be a sequence")
        if not isinstance(value["verified"], bool) or not isinstance(value["test_only"], bool):
            raise ValueError("provider evidence flags must be boolean")
        return cls(
            adapter_id=value["adapter_id"],
            adapter_version=value["adapter_version"],
            source=value["source"],
            capabilities=frozenset(capabilities),
            verified=value["verified"],
            test_only=value["test_only"],
        )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Canonical input to one model adapter invocation."""

    scope: HarnessScope
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[ToolDescriptor, ...]
    cursor: int
    continuation_of: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()

    def __post_init__(self) -> None:
        if self.cursor < 0:
            raise ValueError("model request cursor must be non-negative")
        if not self.scope.turn_id:
            raise ValueError("model request requires a turn scope")
        if not all(isinstance(message, Mapping) for message in self.messages):
            raise ValueError("model request messages must be mappings")
        if not all(isinstance(tool, ToolDescriptor) for tool in self.tools):
            raise ValueError("model request tools must be ToolDescriptor values")
        if not all(isinstance(call, ToolCall) for call in self.continuation_of):
            raise ValueError("model continuation calls must be ToolCall values")
        if not all(isinstance(result, ToolResult) for result in self.tool_results):
            raise ValueError("model tool results must be ToolResult values")

    def as_dict(self) -> dict[str, Any]:
        """Canonical request shape used for durable attempt provenance."""

        return {
            "scope": self.scope.as_dict(),
            "messages": [dict(message) for message in self.messages],
            "tools": [
                {
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "mutable": descriptor.mutable,
                    "requires_approval": descriptor.requires_approval,
                    "capability": descriptor.capability,
                    "source": descriptor.source,
                }
                for descriptor in self.tools
            ],
            "cursor": self.cursor,
            "continuation_of": [
                {
                    "tool_call_id": call.tool_call_id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                }
                for call in self.continuation_of
            ],
            "tool_results": [
                {
                    "tool_call_id": result.tool_call_id,
                    "status": result.status,
                    "output": dict(result.output),
                    "error_code": result.error_code,
                    "action_request_id": result.action_request_id,
                    "approval_id": result.approval_id,
                    "operation_id": result.operation_id,
                }
                for result in self.tool_results
            ],
        }


@dataclass(frozen=True, slots=True)
class ModelDelta:
    """One typed streaming delta emitted by a ModelAdapter."""

    kind: ModelDeltaKind
    text: str = ""
    tool_call: ToolCall | None = None
    finish_reason: str | None = None
    usage: ProviderUsage | None = None
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ModelDeltaKind):
            raise ValueError("model delta kind must be ModelDeltaKind")
        if not isinstance(self.text, str):
            raise ValueError("model delta text must be text")
        if self.kind in {ModelDeltaKind.TEXT, ModelDeltaKind.REASONING}:
            if not self.text:
                raise ValueError("text/reasoning deltas require text")
            if self.tool_call is not None:
                raise ValueError("text/reasoning delta cannot contain a tool call")
            if self.usage is not None or self.provider_response_id is not None:
                raise ValueError("text/reasoning delta cannot contain terminal evidence")
        elif self.kind is ModelDeltaKind.TOOL_CALL:
            if self.tool_call is None:
                raise ValueError("tool-call delta requires a ToolCall")
            if self.text:
                raise ValueError("tool-call delta cannot contain text")
            if self.usage is not None or self.provider_response_id is not None:
                raise ValueError("tool-call delta cannot contain terminal evidence")
            if (
                not isinstance(self.tool_call.tool_call_id, str)
                or not self.tool_call.tool_call_id.strip()
                or not isinstance(self.tool_call.name, str)
                or not self.tool_call.name.strip()
                or not isinstance(self.tool_call.arguments, Mapping)
            ):
                raise ValueError("tool-call delta has invalid identity or arguments")
        elif self.kind is ModelDeltaKind.DONE:
            if self.text or self.tool_call is not None:
                raise ValueError("done delta cannot contain content")
            if self.usage is not None and not isinstance(self.usage, ProviderUsage):
                raise ValueError("done delta usage must be ProviderUsage")
            if self.provider_response_id is not None:
                validate_provider_response_id(self.provider_response_id)


@runtime_checkable
class ClosableModelStream(Protocol):
    """Bounded model response stream with an explicit cleanup contract.

    ``TurnRunner`` must close the provider-owned stream before it publishes a
    model-round terminal.  A plain iterable therefore is not a valid model
    stream for the production ABI: it has no way to prove that a late reader
    or transport has stopped.  Generators and HTTP/SDK stream wrappers may
    satisfy this protocol through their ordinary ``close`` method.
    """

    def __iter__(self) -> Iterator[ModelDelta]:
        ...

    def close(self) -> None:
        ...


class ModelAdapter(Protocol):
    """The only model-facing port owned by the independent Harness."""

    @property
    def capabilities(self) -> ProviderEvidence:
        ...

    def stream(
        self,
        request: ModelRequest,
        *,
        cancel_event: Event | None = None,
    ) -> ClosableModelStream:
        ...

    def preflight_stream(self) -> None:
        """Prove cleanup-capable streaming before a request is started."""
        ...

    def close(self) -> None:
        ...


class ToolProvider(Protocol):
    """Tool catalog and effect port consumed by TurnRunner."""

    def descriptors(self) -> Sequence[ToolDescriptor]:
        ...

    def execute(self, call: ToolCall, cancellation: CancellationToken) -> Mapping[str, Any]:
        ...


class ScopedToolProvider(Protocol):
    """Optional scope-aware execution seam for durable effect adapters.

    The legacy ``ToolProvider`` shape remains valid for simple providers.  A
    provider that creates operation/change/checkpoint records must receive the
    canonical Harness scope explicitly so one shared provider instance cannot
    accidentally borrow another session's subject or lease.
    """

    def execute_for_scope(
        self,
        scope: HarnessScope,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        action_request_id: str,
    ) -> Mapping[str, Any]:
        ...


class ActionAuthorityProvider(Protocol):
    """Effect-adapter seam for durable authority and live binding.

    Mutable actions must expose a stable, provider-neutral identity before
    the action is admitted.  ``bind_action_authority`` then binds the same
    action to the adapter's live process-local authority before any effect.
    A provider that implements only the historical binder is intentionally not
    sufficient for a resumable mutation.
    """

    def action_authority_identity(
        self,
        scope: HarnessScope,
        call: ToolCall,
        *,
        action_request_id: str,
    ) -> Mapping[str, Any]:
        ...

    def bind_action_authority(
        self,
        scope: HarnessScope,
        call: ToolCall,
        *,
        action_request_id: str,
    ) -> None:
        ...


_REQUIRED_MODEL_CAPABILITIES = frozenset({"streaming", "tool_calls", "cancel"})


def validate_model_adapter(
    adapter: ModelAdapter | None,
    *,
    required: frozenset[str] = _REQUIRED_MODEL_CAPABILITIES,
) -> ProviderEvidence:
    """Fail closed unless typed, verified model capability evidence exists."""

    if adapter is None:
        raise ProviderUnavailable("no ModelAdapter is configured")
    try:
        evidence = adapter.capabilities
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        raise ProviderProtocolError("ModelAdapter did not expose capabilities") from exc
    if not isinstance(evidence, ProviderEvidence):
        raise ProviderProtocolError("ModelAdapter capabilities are not ProviderEvidence")
    missing = required.difference(evidence.capabilities)
    if missing:
        raise ProviderCapabilityMissing(
            f"model adapter is missing capabilities: {', '.join(sorted(missing))}"
        )
    if not evidence.verified:
        raise ProviderCapabilityMissing(
            "model adapter capability evidence is not verified",
            code="provider_capability_unverified",
        )
    preflight = getattr(adapter, "preflight_stream", None)
    if not callable(preflight):
        raise ProviderCapabilityMissing(
            "model adapter has no cleanup-capable stream preflight",
            code="closable_stream_unsupported",
        )
    try:
        preflight_result = preflight()
    except HarnessError:
        raise
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        raise ProviderProtocolError(
            "model adapter stream preflight failed",
            code="closable_stream_unsupported",
        ) from exc
    if preflight_result is False:
        raise ProviderCapabilityMissing(
            "model adapter declined cleanup-capable streaming",
            code="closable_stream_unsupported",
        )
    return evidence


@dataclass(frozen=True, slots=True)
class OpenAICompatibleAdapterConfig:
    """Network-neutral configuration shape for a later adapter transport."""

    endpoint: str
    model: str
    credential_ref: Mapping[str, Any] | None = None
    request_timeout_sec: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint.strip():
            raise ValueError("OpenAI-compatible endpoint must be non-empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("OpenAI-compatible model must be non-empty")
        if self.request_timeout_sec <= 0:
            raise ValueError("request timeout must be positive")
        if self.credential_ref is not None and not isinstance(self.credential_ref, Mapping):
            raise ValueError("credential_ref must be an opaque mapping")


class OpenAICompatibleModelAdapter:
    """ABI-only adapter; no network transport is enabled in provider-free v0.1."""

    def __init__(self, config: OpenAICompatibleAdapterConfig) -> None:
        self.config = config
        self._capabilities = ProviderEvidence(
            adapter_id="openai-compatible",
            adapter_version="pulse-harness-kernel.v0.1",
            source="configured-openai-compatible",
            capabilities=frozenset({"streaming", "tool_calls", "cancel"}),
            verified=False,
            test_only=False,
        )

    @property
    def capabilities(self) -> ProviderEvidence:
        return self._capabilities

    def request_body(self, request: ModelRequest) -> dict[str, Any]:
        """Return a canonical OpenAI-compatible body without sending it."""

        return {
            "model": self.config.model,
            "stream": True,
            "messages": [dict(message) for message in request.messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": descriptor.name,
                        "description": descriptor.description,
                    },
                }
                for descriptor in request.tools
            ],
        }

    def preflight_stream(self) -> None:
        """The future transport must return a closable stream wrapper."""

        return None

    def stream(
        self,
        request: ModelRequest,
        *,
        cancel_event: Event | None = None,
    ) -> ClosableModelStream:
        del request, cancel_event
        raise ProviderUnavailable(
            "OpenAI-compatible network transport is disabled in provider-free v0.1",
            code="provider_unconfigured",
        )

    def close(self) -> None:
        return None
