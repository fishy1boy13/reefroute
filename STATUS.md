# Verification status

Verified by execution: offline CLI demo, wheel entry point, wheel/source-distribution builds and artifact inventory. Demo: two successful requests, two cap rejections, 200 microcredits total, one alert attempt.

Verified by tests/fixtures: 46 passed, 0 failed. Authentication, routing policy, malformed requests, SQLite cap persistence, concurrent reservations, UTC rollover, retained charges after errors/cancellation, catalogs, alert deduplication, compatible adapter and webhook contracts, configuration and CLI reports. The same 46 tests pass from the extracted source distribution. Socket connections and DNS are blocked by test fixtures.

Untested: a clean dependency installation, listening HTTP transport, external compatible APIs, real webhook delivery and systemd installation. Existing local Python packages were used without network access. Wheel contents were extracted and its declared console entry point executed; no package manager was available for an installation check. Python versions other than the available 3.14 runtime were not exercised.

Open questions: none. Before external deployment, configure credentials, endpoints, local dispatch prices and caps. These are microcredit budgets, not upstream currency billing guarantees. Review and commit locally when ready.
