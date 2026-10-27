# DocOck scenario scoreboard

Run started 2026-10-26T19:59:16+00:00; generated 2026-10-26T21:41:02+00:00.

| Scenario | Expected | Detect s | Top-1 | Agent root (conf) | Tools | Agent ms | Cost $ | Action | Verdict | Verify s | Recover s | Final | Report | Pass |
|---|---|---:|---|---|---:|---:|---:|---|---|---:|---:|---|---|---|
| S1 payment failure | payment | 119 | payment (hit) | payment (0.86) | 4 | 6171 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 146 | 312 | RESOLVED | ok | PASS |
| S2 catalog lock contention | product-catalog | 138 | product-catalog (hit) | product-catalog (0.8) | 4 | 4093 | 0.0 | reset_fault on product-catalog | PASSED (want PASSED) | 168 | 353 | RESOLVED | ok | PASS |
| S3 payment unreachable | payment | 104 | payment (hit) | payment (0.79) | 4 | 3812 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 144 | 295 | RESOLVED | ok | PASS |
| S4 slow image loading | frontend | 147 | frontend (hit) | frontend (0.85) | 4 | 4296 | 0.0 | reset_fault on frontend | PASSED (want PASSED) | 115 | 307 | RESOLVED | ok | PASS |
| S5 email service down | email | 31 | email (hit) | email (0.81) | 4 | 4000 | 0.0 | reset_fault on email | PASSED (want PASSED) | 37 | 112 | RESOLVED | ok | PASS |
| S6 ad CPU pressure | ad | 26 | ad (hit) | ad (0.92) | 4 | 3750 | 0.0 | reset_fault on ad | PASSED (want PASSED) | 37 | 109 | RESOLVED | ok | PASS |
| S7 payment network partition | payment | 31 | payment (hit) | payment (0.95) | 4 | 3844 | 0.0 | reset_fault on payment | PASSED (want PASSED) | 37 | 112 | RESOLVED | ok | PASS |
| S8 traffic flood | frontend | 103 | frontend (hit) | frontend (0.95) | 4 | 3735 | 0.0 | reset_fault on frontend | PASSED (want PASSED) | 106 | 252 | RESOLVED | ok | PASS |
| W1 payment failure, wrong action | payment | 38 | payment (hit) | payment (0.85) | 4 | 4046 | 0.0 | restart_service on payment | FAILED (want FAILED) | 420 | 512 | ESCALATED | ok | PASS |

**Score: 9/9 scenarios pass every check** (top-1 9/9, agent 9/9, remediation 9/9).

| Metric | Value |
|---|---|
| Detection time p50 / max | 103 s / 147 s |
| Recovery time (inject → closed) p50 / max | 295 s / 512 s |
| Agent latency p50 / max | 4000 ms / 6171 ms |
| Agent cost (this run) | $0.0000 over 9 run(s) |
| LLM spend today / daily cap | $0.0178 / $2.00 |
| Models | stub/stub-deterministic-v1 ×9, zenmux/meta/muse-spark-1.3-contributor ×1 |
| Verification verdicts (window) | FAILED 2, PASSED 8 |
| Rate limits | on, 0 rejected |
| Open incidents after the run | 0 |
