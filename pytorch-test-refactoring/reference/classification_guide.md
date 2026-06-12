# API Classification Guide

**Primary reference:** `reference/device_api_catalog.yaml` (relative to skill directory).
For human-readable summary, see `reference/device_specific_features_report.md`.

## Classification Hierarchy

```
Category C (device-specific) > Category B (no wrapper) > Category A (replaceable) > none (CPU-only)
```

First match wins. A test using ANY Category C API is Strategy 3.

## Lookup Rules

### To classify an API call:

1. Check `category_a.same_name` — if the API name matches, it's Category A (replace with `torch.accelerator.<api>`)
2. Check `category_a.name_differs` — if the API name matches a device_api entry, it's Category A (use the accelerator_api name)
3. Check `category_c.<backend>.*.apis` — if the full path matches, it's Category C (must stay device-specific)
4. Check `category_b.*` — if the API name matches, it's Category B (no wrapper exists, treat as device-specific)
5. Otherwise — no device dependency (Strategy 1)

### Decision rules (from YAML):

- **Blacklist skips** (`@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd`): NEVER remove
- **Whitelist** (`@onlyCUDA`, `@onlyOn`): Enlarge to `@onlyAccelerator` IF only Cat A/B APIs used
- **Cat A same name**: Drop-in replace `torch.{device}` → `torch.accelerator`
- **Cat A name differs**: Use correct accelerator name (see `name_differs` section)
- **Cat B/C**: DO NOT REPLACE, keep original device module call

## YAML Quick Paths

```
category_a.same_name[].accelerator_api     → accelerator equivalents (same name)
category_a.name_differs[].accelerator_api  → accelerator equivalents (different name)
category_a.name_differs[].device_apis      → original device-specific names
category_b.<group>[].api                   → Category B APIs by functional group
category_c.<backend>.<group>.apis          → Category C APIs by backend and group
decision_rules                             → refactoring rules
architecture                               → dispatch chain, backend integration
```
