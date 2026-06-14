| Command | Last Run | Recommended Timeout | Notes |
| --- | ---: | ---: | --- |
| `python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_shared_stats.py` | 0.03s | 30s | Local run on 2026-06-14 after adding the optional Copilot bundle. |
| `python3 -m unittest discover -s tests` | 0.13s | 30s | Local run on 2026-06-14 with shared-runtime coverage plus optional Copilot bundle tests. |
