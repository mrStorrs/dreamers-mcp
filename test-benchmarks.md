| Command | Last Run | Recommended Timeout | Notes |
| --- | ---: | ---: | --- |
| `python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py bundles/codex/scripts/*.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_codex_bundle.py tests/test_shared_stats.py` | 0.04s | 30s | Local run on 2026-06-16 after reliable-run report filtering. |
| `python3 -m unittest discover -s tests` | 0.93s | 30s | Local run on 2026-06-17 after the TypeScript runtime module split. |
| `npm run typecheck` | 0.95s | 30s | Local run on 2026-06-17 after closing public TypeScript event metric contracts. |
| `npm test` | 1.73s | 30s | Local run on 2026-06-17 with clean build/load, hook, token, report, tilde-home token resolution, dashboard parity, and compile-time package-root type assertions. |
| `npm run build` | 1.26s | 30s | Local run on 2026-06-17 after correcting the `dist/index.js` package entrypoint. |
