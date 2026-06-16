| Command | Last Run | Recommended Timeout | Notes |
| --- | ---: | ---: | --- |
| `python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py bundles/codex/scripts/*.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_codex_bundle.py tests/test_shared_stats.py` | 0.05s | 30s | Local run on 2026-06-15 after run-detail standard-name changes. |
| `python3 -m unittest discover -s tests` | 0.66s | 30s | Local run on 2026-06-15 with 51 shared-runtime, optional bundle, hook, token, and dashboard tests. |
