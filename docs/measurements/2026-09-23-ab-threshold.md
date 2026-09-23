# Local A/B pilot: a lower packing threshold

Five matched pairs used SoL Codex `0.1.6+codex.20260923` with `SOL_CODEX_PACK_THRESHOLD_BYTES=4096` in both arms. Only the ON arm enabled the plugin. This pilot motivated the more conservative 6,144-byte default in `0.1.7`; the released default itself was not measured end to end.

| Fixture / pair | OFF tokens | ON tokens | Difference | ON packed |
| --- | ---: | ---: | ---: | ---: |
| Ledger 1 | 96,323 | 66,140 | −30,183 | 1 |
| Ledger 2 | 95,623 | 66,630 | −28,993 | 1 |
| Ledger 3 | 90,295 | 83,383 | −6,912 | 1 |
| Config parser 1 | 86,090 | 82,893 | −3,197 | 1 |
| Config parser 2 | 103,870 | 66,124 | −37,746 | 1 |
| **Total** | **472,201** | **365,170** | **−107,031 (−22.7%)** | **5** |

Tokens are provider-reported input plus output, including cached input. ON contained 315,904 cached input tokens; OFF contained 377,856. Across five pairs, ON took 148.67 seconds and OFF 143.87 seconds. These are usage counts and wall times, not cost or account-quota measurements.

Both fixtures were isolated three-file Python projects. The ledger task rejected non-string amounts, including integer zero, while preserving blank string handling. The config task preserved extra equals signs inside values while keeping comments and whitespace behavior. Each prompt required a first inspection command that emitted about 25 KB, an edit limited to one source file, and `python3 -m unittest -q`. All ten arms changed only the requested source file and passed 3/3 tests. Pair order was ON/OFF, OFF/ON, ON/OFF for ledger and OFF/ON, ON/OFF for config parser. All runs used `gpt-6-luna` with low reasoning, the same Codex CLI configuration, no subagents or network requests, and other plugins disabled. The benchmark used hook-trust bypass only after reviewing the local hook source.

The five ON sessions each recorded one packed observation, totaling 48,535 source bytes and 10,363 receipt bytes. No agent command reopened an observation artifact. The CLI JSON trace contained the full inspection output, while the hook received host-truncated strings of 8,107 or 12,107 bytes. A separate diagnostic hook confirmed that `PostToolUse` receives this truncated string. Under the prior 12,288-byte default, these observations stayed inline; under the tested 4,096-byte override, they were packed. Every captured source size also exceeds the new 6,144-byte default, so this exact mechanism would activate at the released setting.

The fixtures are synthetic and the sample is small. Agent paths and provider cache reuse varied. The result supports a narrower claim: lowering the threshold activated packing on these truncated non-verifier observations without reducing task success in five local pairs. It does not establish reliable savings, unchanged quality, or faster completion on general coding work.
