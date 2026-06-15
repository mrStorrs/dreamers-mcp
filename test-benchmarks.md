| Command | Last Run | Recommended Timeout | Notes |
| --- | ---: | ---: | --- |
| `python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py bundles/codex/scripts/*.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_codex_bundle.py tests/test_shared_stats.py` | 0.04s | 30s | Local run on 2026-06-15 after adding the static HTML dashboard command. |
| `python3 -m unittest discover -s tests` | 0.68s | 30s | Local run on 2026-06-15 with shared-runtime, optional bundle, hook, and static HTML dashboard coverage. |
