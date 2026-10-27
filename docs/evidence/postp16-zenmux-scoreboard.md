# DocOck scenario scoreboard

Run started 2026-10-26T18:21:46+00:00; generated 2026-10-26T19:51:35+00:00.

| Scenario | Expected | Detect s | Top-1 | Agent root (conf) | Tools | Agent ms | Cost $ | Action | Verdict | Verify s | Recover s | Final | Report | Pass |
|---|---|---:|---|---|---:|---:|---:|---|---|---:|---:|---|---|---|
| S1 payment failure | payment | 90 | payment (hit) | payment (0.98) | 4 | 13969 | 0.00152 | clear_feature_flag on payment | PASSED (want PASSED) | 116 | 260 | RESOLVED | ok | PASS |
| S2 catalog lock contention | product-catalog | 158 | product-catalog (hit) | product-catalog (0.95) | 3 | 12312 | 0.002445 | clear_feature_flag on product-catalog | PASSED (want PASSED) | 188 | 404 | RESOLVED | ok | PASS |
| S3 payment unreachable | payment | 112 | payment (hit) | payment (0.92) | 5 | 12967 | 0.003211 | clear_feature_flag on payment | PASSED (want PASSED) | 221 | 399 | RESOLVED | ok | PASS |
| S4 slow image loading | frontend | 146 | frontend (hit) | frontend (0.92) | 4 | 13608 | 0.002945 | clear_feature_flag on frontend | PASSED (want PASSED) | 114 | 318 | RESOLVED | ok | PASS |
| S5 email service down | email | 40 | email (hit) | email (0.95) | 3 | 10405 | 0.001319 | reset_fault on email | PASSED (want PASSED) | 42 | 130 | RESOLVED | ok | PASS |
| S6 ad CPU pressure | ad | 34 | ad (hit) | ad (0.95) | 6 | 12391 | 0.001739 | reset_fault on ad | PASSED (want PASSED) | 40 | 130 | RESOLVED | ok | PASS |
| S7 payment network partition | payment | 34 | payment (hit) | payment (0.98) | 4 | 10592 | 0.001086 | reset_fault on payment | PASSED (want PASSED) | 42 | 128 | RESOLVED | ok | PASS |
| S8 traffic flood | frontend | 98 | frontend (hit) | frontend (0.96) | 5 | 6655 | 0.001446 | clear_feature_flag on frontend | PASSED (want PASSED) | 101 | 248 | RESOLVED | ok | PASS |
| W1 payment failure, wrong action | payment | 98 | payment (hit) | payment (0.95) | 2 | 15937 | 0.002083 | restart_service on payment | FAILED (want FAILED) | 420 | 610 | ESCALATED | ok | PASS |

**Score: 9/9 scenarios pass every check** (top-1 9/9, agent 9/9, remediation 9/9).

| Metric | Value |
|---|---|
| Detection time p50 / max | 98 s / 158 s |
| Recovery time (inject → closed) p50 / max | 260 s / 610 s |
| Agent latency p50 / max | 12391 ms / 15937 ms |
| Agent cost (this run) | $0.0178 over 9 run(s) |
| LLM spend today / daily cap | $0.0178 / $2.00 |
| Models | stub/stub-deterministic-v1 ×2, zenmux/meta/muse-spark-1.3-contributor ×9 |
| Verification verdicts (window) | FAILED 1, PASSED 9 |
| Rate limits | on, 0 rejected |
| Open incidents after the run | 0 |
