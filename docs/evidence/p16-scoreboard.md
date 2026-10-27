# DocOck scenario scoreboard

Run started 2026-10-26T11:14:41+00:00; generated 2026-10-26T12:34:32+00:00.

| Scenario | Expected | Detect s | Top-1 | Agent root (conf) | Tools | Agent ms | Cost $ | Action | Verdict | Verify s | Recover s | Final | Report | Pass |
|---|---|---:|---|---|---:|---:|---:|---|---|---:|---:|---|---|---|
| S1 payment failure | payment | 107 | payment (hit) | payment (0.85) | 4 | 15562 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 118 | 256 | RESOLVED | ok | PASS |
| S2 catalog lock contention | product-catalog | 159 | product-catalog (hit) | product-catalog (0.8) | 4 | 3796 | 0.0 | reset_fault on product-catalog | PASSED (want PASSED) | 176 | 350 | RESOLVED | ok | PASS |
| S3 payment unreachable | payment | 50 | payment (hit) | payment (0.78) | 4 | 3750 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 153 | 221 | RESOLVED | ok | PASS |
| S4 slow image loading | frontend | 150 | frontend (hit) | frontend (0.85) | 4 | 3891 | 0.0 | reset_fault on frontend | PASSED (want PASSED) | 118 | 286 | RESOLVED | ok | PASS |
| S5 email service down | email | 27 | email (hit) | email (0.91) | 4 | 3858 | 0.0 | reset_fault on email | PASSED (want PASSED) | 42 | 86 | RESOLVED | ok | PASS |
| S6 ad CPU pressure | ad | 34 | ad (hit) | ad (0.95) | 4 | 3796 | 0.0 | reset_fault on ad | PASSED (want PASSED) | 41 | 92 | RESOLVED | ok | PASS |
| S7 payment network partition | payment | 34 | payment (hit) | payment (0.95) | 4 | 3968 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 43 | 92 | RESOLVED | ok | PASS |
| S8 traffic flood | frontend | 94 | frontend (hit) | frontend (0.89) | 4 | 3765 | 0.0 | reset_fault on frontend | PASSED (want PASSED) | 121 | 233 | RESOLVED | ok | PASS |
| W1 payment failure, wrong action | payment | 70 | payment (hit) | payment (0.85) | 4 | 4141 | 0.0 | restart_service on payment | FAILED (want FAILED) | 420 | 512 | ESCALATED | ok | PASS |

**Score: 9/9 scenarios pass every check** (top-1 9/9, agent 9/9, remediation 9/9).

| Metric | Value |
|---|---|
| Detection time p50 / max | 70 s / 159 s |
| Recovery time (inject → closed) p50 / max | 233 s / 512 s |
| Agent latency p50 / max | 3858 ms / 15562 ms |
| Agent cost (this run) | $0.0000 over 9 run(s) |
| LLM spend today / daily cap | $0.0000 / $2.00 |
| Models | stub/stub-deterministic-v1 ×9 |
| Verification verdicts (window) | FAILED 1, PASSED 8 |
| Rate limits | on, 0 rejected |
| Open incidents after the run | 0 |
