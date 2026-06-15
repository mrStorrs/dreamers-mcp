| Command | Last Run | Recommended Timeout | Notes |
| --- | ---: | ---: | --- |
| `python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py bundles/codex/scripts/*.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_codex_bundle.py tests/test_shared_stats.py` | 0.04s | 30s | Local run on 2026-06-15 after tightening dashboard file/stdout parity. |
| `python3 -m unittest discover -s tests` | 0.78s | 30s | Local run on 2026-06-15 with shared-runtime, optional bundle, hook, and static HTML dashboard coverage. |
