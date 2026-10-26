# INC-00092: payment: error rate — inbound error rate 100.0% vs baseline 0.0% (1 errors)

_Generated 2026-10-26T07:35:37.309424+00:00 · schema `docock.report/v1`_

## Summary

| Field | Value |
|---|---|
| Status | RESOLVED |
| Severity | sev1 |
| Primary service | payment |
| Opened | 2026-10-26T03:05:55.974181+00:00 |
| Closed | 2026-10-26T03:07:27.393425+00:00 |
| Duration | 91 s |
| Action | reset_fault on payment |
| Verification | PASSED after 70 s |
| Closed by | verifier |

## Diagnosis

**Root cause:** `payment` (application_error), confidence 0.81

payment is the most likely root cause: a recorded payment failure change targets it: a feature flag makes its charges fail. It also ranks first among the deterministic suspects. Symptoms in checkout are consequences of it.

**Recommended action:** `reset_fault` on `payment`

**Affected services:** payment, checkout

- Alternative: {"service": "checkout", "confidence": 0.09}
- Alternative: {"service": "frontend", "confidence": 0.06}
- Alternative: {"service": "frontend-proxy", "confidence": 0.06}

### Cited evidence

| Code | Type | Source | Service | At | Summary |
|---|---|---|---|---|---|
| E0011 | error_origin | jaeger | payment | 2026-10-26T03:05:58.101147+00:00 | origins=1 local=1 propagated=1 silent_peer=0 peers={} |
| E0006 | onset | jaeger | payment | 2026-10-26T03:05:19.136879+00:00 | first anomalous span 2026-10-26T03:05:19.136879+00:00 (+0.0s from earliest) |
| E0002 | metric_anomaly | prometheus | payment | 2026-10-26T03:05:58.101147+00:00 | error rate 100.0% vs 0.0% before onset |
| E0005 | metric_anomaly | prometheus | payment | 2026-10-26T03:05:58.101147+00:00 | throughput 0.03/s vs 0.05/s (0.50x) |
| E0037 | flag_state | faults | payment | 2026-10-26T03:03:52.645860+00:00 | payment_failure on payment ACTIVE {'variant': '100%'} |
| E0032 | container_state | docker | payment | 2026-10-26T03:05:58.101147+00:00 | {"state": "running", "health": "healthy", "paused": false, "service": "payment", "networks": ["opentelemetry-demo"], "r… |
| E0021 | log_template | opensearch | payment | 2026-10-26T03:05:58.101147+00:00 | x1 Payment request failed. Invalid token. demo.user_context.loyalty_level=gold |
| E0012 | trace_sample | jaeger | payment | 2026-10-26T03:05:19.069432+00:00 | trace 3b8ce6f8a1e4d50f1ff2aca4f1e61329 user_checkout_single origin=payment services=['cart', 'checkout', 'currency', 'flagd', 'frontend', 'frontend-proxy', 'lo… |
| E0040 | container_health | docker | payment | 2026-10-26T03:06:13.380719+00:00 | {"state": "running", "health": "healthy", "paused": false, "status": "running (healthy)", "networks": ["opentelemetry-d… |
| E0041 | active_faults | faults | — | 2026-10-26T03:06:13.474980+00:00 | {"active_now": [{"fault": "payment_failure", "status": "ACTIVE", "target": "payment"}]} |
| E0042 | trace_search | jaeger | payment | 2026-10-26T03:06:13.676563+00:00 | {"traces": [{"root": "load-generator user_checkout_single", "spans": 70, "errors": 8, "services": ["cart", "checkout", … |

## Suspects

| Rank | Service | Score |
|---|---|---|
| 1 | payment | 0.4709 |
| 2 | checkout | 0.2176 |
| 3 | frontend | 0.1557 |
| 4 | frontend-proxy | 0.1557 |

## Remediation

### Action #7: `reset_fault` on `payment`

| Field | Value |
|---|---|
| Status | EXECUTED |
| Risk / approval | low / auto |
| Requested by | agent |
| Decision | APPROVED by scenarios at 2026-10-26T03:06:14.281393+00:00 |
| Note | scenario runner |
| Token | issued=True, used=True (never stored in clear) |
| Started | 2026-10-26T03:06:14.331168Z |
| Finished | 2026-10-26T03:06:16.283507Z |
| Error | — |
| Before | container running (health healthy, networks ['opentelemetry-demo']); flags {'paymentFailure': '100%'} |
| After | container running (health healthy, networks ['opentelemetry-demo']); flags {'paymentFailure': 'off'} |

Steps:

- {"revert": {"at": "2026-10-26T03:06:16.062776+00:00", "flag": "paymentFailure", "actor": "remediation:A7", "reason": "remediation", "restored": "off", "verified": true, "mechanism": "flagd", "read_back": {"key": "paymentFailure", "value": …

#### Verification: **PASSED** after 70 s

Focus services: checkout, payment; clean cycles 4/4; timeout 420 s.

| Criterion | Result | Detail |
|---|---|---|
| No detector breach on the focus services | pass | no breach on checkout, payment |
| Reverted faults stay reverted | pass | #195 payment_failure REVERTED |

Before → after (detector window):

| Service | Error rate | p95 | Throughput |
|---|---|---|---|
| checkout | 100.0% → 0.0% | 48 ms → 175 ms | 0.07/s → 0.57/s |
| payment | 100.0% → 0.0% | 8 ms → 40 ms | 0.03/s → 0.10/s |

## Timeline

| # | At | Kind | Detail |
|---|---|---|---|
| 1 | 2026-10-26T03:05:55.963773+00:00 | opened | sev1 payment error_rate: inbound error rate 100.0% vs baseline 0.0% (1 errors) |
| 2 | 2026-10-26T03:05:55.963773+00:00 | signal | checkout error_rate: inbound error rate 100.0% vs baseline 0.0% (1 errors) |
| 3 | 2026-10-26T03:05:58.097637+00:00 | analysis | analysis |
| 4 | 2026-10-26T03:06:04.277800+00:00 | agent | OPEN → INVESTIGATING by scenarios |
| 5 | 2026-10-26T03:06:13.895630+00:00 | agent | INVESTIGATING → DIAGNOSED by agent |
| 6 | 2026-10-26T03:06:13.942841+00:00 | approval | DIAGNOSED → AWAITING_APPROVAL by agent; action_id=reset_fault |
| 7 | 2026-10-26T03:06:14.261201+00:00 | approval | AWAITING_APPROVAL → REMEDIATING by scenarios; action_id=reset_fault |
| 8 | 2026-10-26T03:06:16.291977+00:00 | action | REMEDIATING → VERIFYING by executor; action_id=reset_fault |
| 9 | 2026-10-26T03:06:16.334074+00:00 | verification_started | action_id=reset_fault; target=payment; focus=['checkout', 'payment'] |
| 10 | 2026-10-26T03:07:27.372351+00:00 | verification | VERIFYING → RESOLVED by verifier; verdict=PASSED; action_id=reset_fault |

## Faults overlapping the incident

| # | Fault | Target | Status | Injected | Reset |
|---|---|---|---|---|---|
| 19 | cpu_pressure | ad | FAILED | 2026-10-24T07:21:29.692385Z | — |
| 41 | cpu_pressure | ad | FAILED | 2026-10-24T07:33:01.354606Z | — |
| 195 | payment_failure | payment | REVERTED | 2026-10-26T03:03:52.645860Z | 2026-10-26T03:06:16.062776Z |

## Agent runs

| Run | Status | Model | Tokens in/out | Cost | Latency |
|---|---|---|---|---|---|
| 18 | DIAGNOSED | stub/stub-deterministic-v1 | 11656/214 | $0.0000 | 9546 ms |

## Audit trail

| At | Actor | Event | Outcome | Detail |
|---|---|---|---|---|
| 2026-10-26T03:06:14.053009Z | agent | plan | PENDING | revert fault #195 payment_failure on payment with its recorded pre-image |
| 2026-10-26T03:06:14.328671Z | scenarios | approve | APPROVED | scenario runner |
| 2026-10-26T03:06:14.567052Z | executor | execute_started | started | revert fault #195 payment_failure on payment with its recorded pre-image |
| 2026-10-26T03:06:16.324276Z | executor | executed | EXECUTED | ok |
| 2026-10-26T03:07:27.412319Z | verifier | verified | PASSED | all criteria held for 4 detector cycles |

## Evidence index

| Code | Type | Service | Summary |
|---|---|---|---|
| E0001 | metric_anomaly | checkout | error rate 100.0% vs 0.0% before onset |
| E0002 | metric_anomaly | payment | error rate 100.0% vs 0.0% before onset |
| E0003 | metric_anomaly | checkout | throughput 0.07/s vs 0.35/s (0.21x) |
| E0004 | metric_anomaly | email | throughput 0.00/s vs 0.11/s (0.00x) |
| E0005 | metric_anomaly | payment | throughput 0.03/s vs 0.05/s (0.50x) |
| E0006 | onset | payment | first anomalous span 2026-10-26T03:05:19.136879+00:00 (+0.0s from earliest) |
| E0007 | onset | checkout | first anomalous span 2026-10-26T03:05:19.138436+00:00 (+0.002s from earliest) |
| E0008 | onset | frontend | first anomalous span 2026-10-26T03:05:19.139191+00:00 (+0.002s from earliest) |
| E0009 | onset | frontend-proxy | first anomalous span 2026-10-26T03:05:19.141300+00:00 (+0.004s from earliest) |
| E0010 | onset | load-generator | first anomalous span 2026-10-26T03:05:19.143197+00:00 (+0.006s from earliest) |
| E0011 | error_origin | payment | origins=1 local=1 propagated=1 silent_peer=0 peers={} |
| E0012 | trace_sample | payment | trace 3b8ce6f8a1e4d50f1ff2aca4f1e61329 user_checkout_single origin=payment services=['cart', 'checkout', 'currency', 'flagd', 'frontend', 'frontend-proxy', 'lo… |
| E0013 | trace_sample | load-generator | trace cb45a73da2ac17a3866d38bc3f0ac899 user_ask_agent origin=None services=['load-generator'] |
| E0014 | trace_sample | load-generator | trace 4238335c604622aa50f1c24c846a1d8d user_ask_agent origin=None services=['load-generator'] |
| E0015 | healthy_control | frontend-proxy | trace 7a26f974a3a2950f72a90fe0a3f1a55a GET origin=None services=['frontend', 'frontend-proxy'] |
| E0016 | log_template | frontend | x20 API request completed |
| E0017 | log_template | frontend | x3 [<n>-<n>-26T03:<n>:<n>.956Z] <quoted> <n> - via_upstream - "-<quoted>-" <quoted> "<uuid>" <quoted> "<ip>" frontend <ip> <ip> <ip> - - |
| E0018 | log_template | frontend | x2 [<n>-<n>-26T03:<n>:<n>.294Z] <quoted> <n> - via_upstream - "-<quoted>-" <quoted> "<uuid>" <quoted> "<ip>" frontend <ip> <ip> <ip> - - |
| E0019 | log_template | frontend | x2 [<n>-<n>-26T03:<n>:<n>.606Z] <quoted> <n> - via_upstream - "-<quoted>-" <quoted> "<uuid>" <quoted> "<ip>" image-provider <ip> <ip> <ip> - - |
| E0020 | log_template | frontend | x2 [<n>-<n>-26T03:<n>:<n>.523Z] <quoted> <n> - via_upstream - "-<quoted>-" <quoted> "<uuid>" <quoted> "<ip>" image-provider <ip> <ip> <ip> - - |
| E0021 | log_template | payment | x1 Payment request failed. Invalid token. demo.user_context.loyalty_level=gold |
| E0022 | log_cluster | payment | log totals {'email': 0, 'payment': 2, 'checkout': 1, 'frontend': 100} new_templates=6 |
| E0023 | container_state | ad | {"state": "running", "health": "healthy", "paused": false, "service": "ad", "networks": ["opentelemetry-demo"], "restar… |
| E0024 | container_state | cart | {"state": "running", "health": "none", "paused": false, "service": "cart", "networks": ["opentelemetry-demo"], "restart… |
| E0025 | container_state | checkout | {"state": "running", "health": "healthy", "paused": false, "service": "checkout", "networks": ["opentelemetry-demo"], "… |
| E0026 | container_state | currency | {"state": "running", "health": "healthy", "paused": false, "service": "currency", "networks": ["opentelemetry-demo"], "… |
| E0027 | container_state | email | {"state": "running", "health": "healthy", "paused": false, "service": "email", "networks": ["opentelemetry-demo"], "res… |
| E0028 | container_state | frontend | {"state": "running", "health": "healthy", "paused": false, "service": "frontend", "networks": ["opentelemetry-demo"], "… |
| E0029 | container_state | frontend-proxy | {"state": "running", "health": "healthy", "paused": false, "service": "frontend-proxy", "networks": ["opentelemetry-dem… |
| E0030 | container_state | image-provider | {"state": "running", "health": "healthy", "paused": false, "service": "image-provider", "networks": ["opentelemetry-dem… |
| E0031 | container_state | load-generator | {"state": "running", "health": "healthy", "paused": false, "service": "load-generator", "networks": ["opentelemetry-dem… |
| E0032 | container_state | payment | {"state": "running", "health": "healthy", "paused": false, "service": "payment", "networks": ["opentelemetry-demo"], "r… |
| E0033 | container_state | product-catalog | {"state": "running", "health": "healthy", "paused": false, "service": "product-catalog", "networks": ["opentelemetry-de… |
| E0034 | container_state | quote | {"state": "running", "health": "healthy", "paused": false, "service": "quote", "networks": ["opentelemetry-demo"], "res… |
| E0035 | container_state | recommendation | {"state": "running", "health": "healthy", "paused": false, "service": "recommendation", "networks": ["opentelemetry-dem… |
| E0036 | container_state | shipping | {"state": "running", "health": "healthy", "paused": false, "service": "shipping", "networks": ["opentelemetry-demo"], "… |
| E0037 | flag_state | payment | payment_failure on payment ACTIVE {'variant': '100%'} |
| E0038 | topology | payment | 13 edges over 3 traces |
| E0039 | suspect_ranking | payment | payment 0.4709, checkout 0.2176, frontend 0.1557, frontend-proxy 0.1557 |
| E0040 | container_health | payment | {"state": "running", "health": "healthy", "paused": false, "status": "running (healthy)", "networks": ["opentelemetry-d… |
| E0041 | active_faults | — | {"active_now": [{"fault": "payment_failure", "status": "ACTIVE", "target": "payment"}]} |
| E0042 | trace_search | payment | {"traces": [{"root": "load-generator user_checkout_single", "spans": 70, "errors": 8, "services": ["cart", "checkout", … |
| E0043 | metric_query | payment | {"to": "2026-10-26T03:06:04.434917+00:00", "from": "2026-10-26T03:00:55.974181+00:00", "unit": "ratio", "metric": "erro… |
