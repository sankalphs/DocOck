"""Fault catalog — one definition, one validator, one UI schema.

Application faults are flagd *variant selections*, not free-form numbers: the
API rejects any variant that is not declared in the runtime flag file. Each
definition also declares the observable that proves the fault reached the
service (`symptom`), because a flag write is visible in OFREP in ~0.5 s while the
demo's SDK clients take 30-45 s to observe it (see ARCHITECTURE.md). A fault is
therefore created as `PROPAGATING` and only becomes `ACTIVE` once the symptom is
observed.

Infrastructure faults change container state directly, so the change *is* the
observation and they start `ACTIVE` after a read-back.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.errors import ApiError


class FaultCategory(StrEnum):
    APPLICATION = "application"
    INFRASTRUCTURE = "infrastructure"


class Mechanism(StrEnum):
    FLAGD = "flagd"
    CONTAINER_STOP = "container_stop"
    CONTAINER_PAUSE = "container_pause"
    NETWORK_PARTITION = "network_partition"
    RESOURCE_LIMIT = "resource_limit"
    LOAD_SPIKE = "load_spike"


class FaultStatus(StrEnum):
    PROPAGATING = "PROPAGATING"
    ACTIVE = "ACTIVE"
    REVERTING = "REVERTING"
    REVERTED = "REVERTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


OPEN_STATUSES: frozenset[FaultStatus] = frozenset(
    {FaultStatus.PROPAGATING, FaultStatus.ACTIVE, FaultStatus.REVERTING}
)
CLOSED_STATUSES: frozenset[FaultStatus] = frozenset(
    {FaultStatus.REVERTED, FaultStatus.EXPIRED, FaultStatus.FAILED}
)


class ParamKind(StrEnum):
    VARIANT = "variant"
    NUMBER = "number"
    ENUM = "enum"


class ParamSpec(BaseModel):
    name: str
    label: str
    kind: ParamKind
    required: bool = True
    default: Any = None
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    description: str = ""


class SymptomSpec(BaseModel):
    """What proves a fault has reached the service.

    `threshold` is absolute; `baseline_multiplier` is relative to the reading
    captured immediately before injection.
    """

    probe: Literal[
        "error_rate",
        "p95_latency",
        "throughput",
        "container_memory",
        "container_running",
        "container_paused",
        "container_health",
        "network_attached",
        "cpu_limit",
        "memory_limit",
        "load_users",
    ]
    service: str
    comparator: Literal[">", ">=", "<", "<="] = ">"
    threshold: float | None = None
    baseline_multiplier: float | None = None
    detail: str = ""


class FaultDefinition(BaseModel):
    id: str
    label: str
    description: str
    category: FaultCategory
    mechanism: Mechanism
    target: str
    target_mode: Literal["fixed", "choice"] = "fixed"
    targets: list[str] = Field(default_factory=list)
    flag: str | None = None
    variants: list[str] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)
    default_ttl_s: int = 900
    risk: Literal["low", "medium"] = "low"
    symptom: SymptomSpec
    reversible: bool = True
    notes: str = ""

    @property
    def selectable_targets(self) -> list[str]:
        return self.targets if self.target_mode == "choice" else [self.target]


class FaultRequest(BaseModel):
    fault_id: str
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    ttl_s: int | None = None
    dry_run: bool = False
    actor: str = "operator"
    reason: str | None = None


class NormalizedFault(BaseModel):
    definition: FaultDefinition
    target: str
    params: dict[str, Any]
    ttl_s: int


def _variant_param(choices: list[str], default: str | None = None) -> ParamSpec:
    return ParamSpec(
        name="variant",
        label="Severity variant",
        kind=ParamKind.VARIANT,
        choices=choices,
        default=default or choices[0],
        description="Variant declared in demo.flagd.json; the API rejects anything else.",
    )


def _number_param(
    name: str,
    label: str,
    *,
    minimum: float,
    maximum: float,
    default: float,
    unit: str | None = None,
    description: str = "",
) -> ParamSpec:
    return ParamSpec(
        name=name,
        label=label,
        kind=ParamKind.NUMBER,
        minimum=minimum,
        maximum=maximum,
        default=default,
        unit=unit,
        description=description,
    )


# Targets an operator may touch. Everything else — our own Postgres, the
# observability stack, flagd itself — is rejected with 403 before a write.
INFRA_TARGETS: tuple[str, ...] = (
    "ad",
    "cart",
    "checkout",
    "currency",
    "email",
    "frontend",
    "frontend-proxy",
    "image-provider",
    "payment",
    "product-catalog",
    "quote",
    "recommendation",
    "shipping",
)
LOAD_TARGETS: tuple[str, ...] = ("load-generator",)
ALLOWED_TARGETS: frozenset[str] = frozenset((*INFRA_TARGETS, *LOAD_TARGETS))

_PAYMENT_VARIANTS = ["10%", "25%", "50%", "75%", "90%", "100%"]
_CART_VARIANTS = ["10%", "25%", "50%", "75%", "90%", "100%"]
_SWITCH = ["on"]
_EMAIL_VARIANTS = ["1x", "10x", "100x", "1000x", "10000x"]
_DELAY_VARIANTS = ["5sec", "10sec"]


def _definitions() -> list[FaultDefinition]:
    """The v1 catalog (PLAN §8).

    Deviations from PLAN §8, both verified in the demo source:
    - `image_slow_load` targets **frontend**: the flag is evaluated in
      `src/frontend/components/ProductCard/ProductCard.tsx`, which asks the
      proxy to delay the image request; image-provider itself stays fast.
    - `payment_unreachable` targets **payment** but is observed at **checkout**:
      `checkout/main.go` dials `badAddress:50051` when the flag is on, so
      payment never receives the call.
    """

    def flag(
        fault_id: str,
        label: str,
        description: str,
        flag_key: str,
        target: str,
        variants: list[str],
        symptom: SymptomSpec,
        *,
        variant_default: str | None = None,
        risk: Literal["low", "medium"] = "low",
        notes: str = "",
    ) -> FaultDefinition:
        return FaultDefinition(
            id=fault_id,
            label=label,
            description=description,
            category=FaultCategory.APPLICATION,
            mechanism=Mechanism.FLAGD,
            target=target,
            flag=flag_key,
            variants=variants,
            params=[_variant_param(variants, variant_default)],
            risk=risk,
            symptom=symptom,
            notes=notes,
        )

    def infra(
        fault_id: str,
        label: str,
        description: str,
        mechanism: Mechanism,
        symptom: SymptomSpec,
        *,
        targets: tuple[str, ...] = INFRA_TARGETS,
        params: list[ParamSpec] | None = None,
        default_ttl_s: int = 900,
        risk: Literal["low", "medium"] = "medium",
        notes: str = "",
    ) -> FaultDefinition:
        return FaultDefinition(
            id=fault_id,
            label=label,
            description=description,
            category=FaultCategory.INFRASTRUCTURE,
            mechanism=mechanism,
            target=targets[0],
            target_mode="choice",
            targets=list(targets),
            params=params or [],
            default_ttl_s=default_ttl_s,
            risk=risk,
            symptom=symptom,
            notes=notes,
        )

    return [
        flag(
            "payment_failure",
            "Payment failure",
            "Payment charges fail for the selected share of requests.",
            "paymentFailure",
            "payment",
            _PAYMENT_VARIANTS,
            SymptomSpec(
                probe="error_rate",
                service="payment",
                threshold=0.02,
                detail="payment inbound error rate above 2%",
            ),
        ),
        flag(
            "payment_unreachable",
            "Payment unreachable",
            "Checkout dials an unreachable payment address; payment receives no calls.",
            "paymentUnreachable",
            "payment",
            _SWITCH,
            SymptomSpec(
                probe="error_rate",
                service="checkout",
                threshold=0.02,
                detail="checkout inbound error rate above 2%",
            ),
            notes="Observed at checkout: the flag makes checkout dial badAddress:50051.",
        ),
        flag(
            "cart_failure",
            "Cart failure",
            "Cart RPCs fail for the selected share of requests.",
            "cartFailure",
            "cart",
            _CART_VARIANTS,
            SymptomSpec(
                probe="error_rate",
                service="cart",
                threshold=0.02,
                detail="cart inbound error rate above 2%",
            ),
            notes=(
                "Upstream cartFailure only fails EmptyCart (CartService.cs:82), "
                "which the load generator rarely calls; it can stay PROPAGATING."
            ),
        ),
        flag(
            "recommendation_cache_failure",
            "Recommendation cache failure",
            "Recommendation cache is broken; every lookup recomputes and serves errors.",
            "recommendationCacheFailure",
            "recommendation",
            _SWITCH,
            SymptomSpec(
                probe="error_rate",
                service="recommendation",
                threshold=0.02,
                detail="recommendation inbound error rate above 2%",
            ),
        ),
        flag(
            "ad_failure",
            "Ad failure",
            "Ad service returns errors for every request.",
            "adFailure",
            "ad",
            _SWITCH,
            SymptomSpec(
                probe="error_rate",
                service="ad",
                threshold=0.02,
                detail="ad inbound error rate above 2%",
            ),
        ),
        flag(
            "ad_high_cpu",
            "Ad high CPU",
            "Ad service burns CPU in a tight loop.",
            "adHighCpu",
            "ad",
            _SWITCH,
            SymptomSpec(
                probe="p95_latency",
                service="ad",
                baseline_multiplier=1.5,
                detail="ad p95 at least 1.5x its pre-injection value",
            ),
        ),
        flag(
            "ad_manual_gc",
            "Ad manual GC",
            "Ad service runs full garbage collections continuously.",
            "adManualGc",
            "ad",
            _SWITCH,
            SymptomSpec(
                probe="p95_latency",
                service="ad",
                baseline_multiplier=1.5,
                detail="ad p95 at least 1.5x its pre-injection value",
            ),
        ),
        flag(
            "catalog_lock_contention",
            "Catalog lock contention",
            "Product catalog serialises lookups on a contended lock.",
            "productCatalogLockContention",
            "product-catalog",
            _SWITCH,
            SymptomSpec(
                probe="p95_latency",
                service="product-catalog",
                baseline_multiplier=1.5,
                detail="product-catalog p95 at least 1.5x its pre-injection value",
            ),
        ),
        flag(
            "email_memory_leak",
            "Email memory leak",
            "Email service leaks memory proportional to the multiplier.",
            "emailMemoryLeak",
            "email",
            _EMAIL_VARIANTS,
            SymptomSpec(
                probe="container_memory",
                service="email",
                baseline_multiplier=1.15,
                detail="email container memory 15% above its pre-injection value",
            ),
            variant_default="100x",
        ),
        flag(
            "shipping_slowdown",
            "Shipping slowdown",
            "International shipping requests are delayed.",
            "intlShippingSlowdown",
            "shipping",
            _DELAY_VARIANTS,
            SymptomSpec(
                probe="p95_latency",
                service="shipping",
                baseline_multiplier=1.5,
                detail="shipping p95 at least 1.5x its pre-injection value",
            ),
        ),
        flag(
            "image_slow_load",
            "Slow image loading",
            "Frontend asks the proxy to delay product image responses.",
            "imageSlowLoad",
            "frontend",
            _DELAY_VARIANTS,
            SymptomSpec(
                probe="p95_latency",
                service="frontend",
                baseline_multiplier=1.5,
                detail="frontend p95 at least 1.5x its pre-injection value",
            ),
            notes="Targets frontend: ProductCard.tsx sets x-envoy-fault-delay-request.",
        ),
        flag(
            "readiness_failure",
            "Cart readiness failure",
            "Cart readiness endpoint reports failure; the container is marked unhealthy.",
            "failedReadinessProbe",
            "cart",
            _SWITCH,
            SymptomSpec(
                probe="container_health",
                service="cart",
                comparator=">=",
                threshold=1,
                detail="cart container health is not healthy",
            ),
            risk="medium",
        ),
        flag(
            "traffic_flood",
            "Traffic flood",
            "Load generator floods the storefront homepage.",
            "loadGeneratorFloodHomepage",
            "frontend",
            _SWITCH,
            SymptomSpec(
                probe="throughput",
                service="frontend",
                baseline_multiplier=2.0,
                detail="frontend throughput at least 2x its pre-injection value",
            ),
        ),
        infra(
            "service_down",
            "Service down",
            "Stop one container; start it again on revert.",
            Mechanism.CONTAINER_STOP,
            SymptomSpec(
                probe="container_running",
                service="ad",
                comparator="<=",
                threshold=0,
                detail="container is not running",
            ),
            notes="The target service fills in the symptom service at request time.",
        ),
        infra(
            "service_freeze",
            "Service frozen",
            "Pause one container (SIGSTOP-like); unpause on revert.",
            Mechanism.CONTAINER_PAUSE,
            SymptomSpec(
                probe="container_paused",
                service="ad",
                comparator=">=",
                threshold=1,
                detail="container is paused",
            ),
        ),
        infra(
            "network_partition",
            "Network partition",
            "Disconnect one container from the demo network; reconnect on revert.",
            Mechanism.NETWORK_PARTITION,
            SymptomSpec(
                probe="network_attached",
                service="ad",
                comparator="<=",
                threshold=0,
                detail="container is detached from its demo network",
            ),
            params=[
                ParamSpec(
                    name="direction",
                    label="Direction",
                    kind=ParamKind.ENUM,
                    choices=["both"],
                    default="both",
                    description=(
                        "v1 disconnects the only demo network; direction is recorded for the UI."
                    ),
                )
            ],
            notes="Docker cannot disconnect a single direction without a second network.",
        ),
        infra(
            "cpu_pressure",
            "CPU pressure",
            "Cap a container at the given number of cores.",
            Mechanism.RESOURCE_LIMIT,
            SymptomSpec(
                probe="cpu_limit",
                service="ad",
                comparator=">",
                threshold=0,
                detail="container CPU limit is below its pre-injection value",
            ),
            params=[
                _number_param(
                    "cpus",
                    "CPU cores",
                    minimum=0.05,
                    maximum=2.0,
                    default=0.25,
                    unit="cores",
                    description="Docker --cpus value; demo services have no CPU limit by default.",
                )
            ],
        ),
        infra(
            "memory_pressure",
            "Memory pressure",
            "Cap a container at the given memory limit.",
            Mechanism.RESOURCE_LIMIT,
            SymptomSpec(
                probe="memory_limit",
                service="ad",
                comparator=">",
                threshold=0,
                detail="container memory limit is below its pre-injection value",
            ),
            params=[
                _number_param(
                    "memory_mb",
                    "Memory limit",
                    minimum=64,
                    maximum=2048,
                    default=128,
                    unit="MB",
                )
            ],
        ),
        infra(
            "load_spike",
            "Load spike",
            "Raise the locust user count for a bounded window.",
            Mechanism.LOAD_SPIKE,
            SymptomSpec(
                probe="load_users",
                service="load-generator",
                comparator=">=",
                threshold=1,
                detail="locust user count increased",
            ),
            targets=LOAD_TARGETS,
            params=[
                _number_param(
                    "users",
                    "Users",
                    minimum=6,
                    maximum=200,
                    default=40,
                    description="Target locust user count (the demo runs 5 by default).",
                ),
                _number_param(
                    "duration_s",
                    "Duration",
                    minimum=30,
                    maximum=600,
                    default=180,
                    unit="s",
                    description="Default TTL for this fault; the watchdog reverts at expiry.",
                ),
            ],
            default_ttl_s=180,
            risk="low",
        ),
    ]


DEFINITIONS: dict[str, FaultDefinition] = {item.id: item for item in _definitions()}


def get_definition(fault_id: str) -> FaultDefinition:
    definition = DEFINITIONS.get(fault_id)
    if definition is None:
        raise ApiError(
            404,
            "unknown_fault",
            f"Unknown fault '{fault_id}'",
            {"known": sorted(DEFINITIONS)},
        )
    return definition


def _coerce_number(spec: ParamSpec, raw: Any, fault_id: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ApiError(
            422,
            "invalid_param",
            f"{fault_id}.{spec.name} must be a number",
            {"param": spec.name, "value": raw},
        )
    value = float(raw)
    if spec.minimum is not None and value < spec.minimum:
        raise ApiError(
            422,
            "invalid_param",
            f"{fault_id}.{spec.name} must be >= {spec.minimum:g}",
            {"param": spec.name, "minimum": spec.minimum, "value": value},
        )
    if spec.maximum is not None and value > spec.maximum:
        raise ApiError(
            422,
            "invalid_param",
            f"{fault_id}.{spec.name} must be <= {spec.maximum:g}",
            {"param": spec.name, "maximum": spec.maximum, "value": value},
        )
    return value


def resolve_request(request: FaultRequest) -> NormalizedFault:
    """Validate a request against the catalog. No infrastructure is touched.

    Order matters: the target allowlist is checked before anything else so a
    non-lab name can never reach a Docker call.
    """
    definition = get_definition(request.fault_id)
    target = request.target or definition.target

    if target not in ALLOWED_TARGETS:
        raise ApiError(
            403,
            "unsafe_target",
            f"'{target}' is not a lab target; only demo services are writable",
            {"allowed_targets": sorted(ALLOWED_TARGETS)},
        )

    if target not in definition.selectable_targets:
        raise ApiError(
            422,
            "invalid_target",
            f"{definition.id} cannot target '{target}'",
            {"target": target, "allowed_targets": definition.selectable_targets},
        )

    params: dict[str, Any] = {}
    supplied = dict(request.params or {})
    unknown = sorted(set(supplied) - {param.name for param in definition.params})
    if unknown:
        raise ApiError(
            422,
            "invalid_param",
            f"{definition.id} does not accept {', '.join(unknown)}",
            {"unknown_params": unknown},
        )

    for spec in definition.params:
        raw = supplied.get(spec.name, spec.default)
        if raw is None:
            if spec.required:
                raise ApiError(
                    422,
                    "invalid_param",
                    f"{definition.id}.{spec.name} is required",
                    {"param": spec.name},
                )
            continue
        if spec.kind == ParamKind.VARIANT:
            if raw not in spec.choices:
                raise ApiError(
                    422,
                    "invalid_variant",
                    f"variant '{raw}' is not declared for {definition.id}",
                    {"variant": raw, "allowed": spec.choices},
                )
            params[spec.name] = str(raw)
        elif spec.kind == ParamKind.ENUM:
            if raw not in spec.choices:
                raise ApiError(
                    422,
                    "invalid_param",
                    f"{definition.id}.{spec.name} must be one of {spec.choices}",
                    {"param": spec.name, "allowed": spec.choices},
                )
            params[spec.name] = str(raw)
        else:
            value = _coerce_number(spec, raw, definition.id)
            params[spec.name] = int(value) if value.is_integer() else value

    ttl = request.ttl_s or definition.default_ttl_s
    if "duration_s" in params:
        ttl = request.ttl_s or int(params["duration_s"])
    if ttl < 5:
        raise ApiError(422, "invalid_ttl", "ttl_s must be at least 5 seconds")
    if ttl > settings.fault_max_ttl_s:
        raise ApiError(
            422,
            "invalid_ttl",
            f"ttl_s must be <= {settings.fault_max_ttl_s}",
            {"max_ttl_s": settings.fault_max_ttl_s},
        )

    return NormalizedFault(definition=definition, target=target, params=params, ttl_s=ttl)


class FaultCatalog(BaseModel):
    version: int = 1
    defaults: dict[str, Any]
    limits: dict[str, Any]
    targets: list[str]
    faults: list[FaultDefinition]
    # JSON Schema for POST /api/faults; the dashboard uses it for introspection
    # and the API still validates against the catalog above.
    json_schema: dict[str, Any]


def catalog() -> FaultCatalog:
    return FaultCatalog(
        defaults={
            "ttl_s": settings.fault_default_ttl_s,
            "max_ttl_s": settings.fault_max_ttl_s,
            "actor": "operator",
        },
        limits={
            "cpus_min": 0.05,
            "cpus_max": 2.0,
            "memory_mb_min": 64,
            "memory_mb_max": 2048,
            "users_min": 6,
            "users_max": 200,
        },
        targets=sorted(ALLOWED_TARGETS),
        faults=[DEFINITIONS[key] for key in DEFINITIONS],
        json_schema=FaultRequest.model_json_schema(),
    )


__all__ = [
    "ALLOWED_TARGETS",
    "CLOSED_STATUSES",
    "DEFINITIONS",
    "INFRA_TARGETS",
    "LOAD_TARGETS",
    "OPEN_STATUSES",
    "FaultCatalog",
    "FaultCategory",
    "FaultDefinition",
    "FaultRequest",
    "FaultStatus",
    "Mechanism",
    "NormalizedFault",
    "ParamKind",
    "ParamSpec",
    "SymptomSpec",
    "catalog",
    "get_definition",
    "resolve_request",
]
