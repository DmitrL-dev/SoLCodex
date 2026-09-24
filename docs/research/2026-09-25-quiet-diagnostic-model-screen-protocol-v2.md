# Quiet diagnostic: exposed model screen protocol, revision 2

This revision retains every assignment, treatment, quality check, threshold,
and limitation in the [initial protocol](2026-09-25-quiet-diagnostic-model-screen-protocol.md).
The [revision 2 machine-readable freeze](../measurements/data/2026-09-25-quiet-diagnostic-model-screen-development-v2.json)
differs from the initial JSON only in the pinned `host_bridge.py` hash. It was
committed and pushed before any four-run screen launch. No outcome is reported
here.

The initial protocol was frozen in commit `52dafd2275b0a64a2df169f76515daa9a32c85ec`.
Two separate one-request provider compatibility attempts then reached the
provider and received HTTP 200. Both were rejected by the host bridge before
it read the response body: the provider's chunked response omitted
`Content-Type`, while the initial bridge required that header. CLI exit was 1
in both attempts. The host ledgers recorded one attempt each with unknown
usage. Neither attempt is part of the four-run screen or counted as zero cost.
The private host receipt SHA-256 values are
`aea0332240413666a66d2443890b7d9414087619aee1ce0ffabc4e8d8dea499d`
and `fa119298b89d302ddd65a9157a6f4fbe8514d1161a0f7c678933237f23837c64`.
The second attempt collected only response header categories to identify the
mismatch; it did not save provider content or credentials.

Revision 2 permits a missing `Content-Type` only at the fixed HTTPS provider
endpoint. A present type must still be `text/event-stream`. The body must
still pass bounded SSE parsing and include a complete `response.completed`
usage record; malformed, truncated or mismatched responses remain unknown.
A synthetic chunked response without `Content-Type` passed the complete
transport and ledger tests before this revision was frozen. A separate
compatibility smoke under revision 2 may be run before the four assigned
development runs; it is excluded from their outcome and reported separately.

That revision 2 compatibility smoke subsequently passed: one real provider
attempt, CLI exit 0, completed upstream and delivery ledgers, 9,511 observed
input tokens, 5 output tokens and 0 cached input tokens. CLI completion took
2.893 seconds; completion plus accounting took 2.914 seconds. Provider billing
was not independently reconciled. The private host receipt SHA-256 is
`e0548664389ee752317f906e99cfaf69575da89db22f0978098c5acf673b6237`.
It remains excluded from the four-run screen.
