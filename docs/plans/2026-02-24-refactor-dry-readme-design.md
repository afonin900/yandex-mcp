# Refactor: DRY helpers + README update

**Date**: 2026-02-24
**Status**: Approved

## Problem

1. ~20 manage-operations (suspend/resume/archive/unarchive/delete) across campaigns, ads, keywords, audience targets are near-identical copy-paste blocks (~25 lines each)
2. `models/__init__.py` has 248 lines of re-exports that no consumer uses (tools import directly from submodules)
3. README describes 33 tools but the server actually has 120

## Solution: Approach B - Shared helpers + README

### 1. Create `yandex_mcp/tools/direct/_helpers.py`

Two utility functions:

- `parse_action_results(result, action)` - parse Direct API response, return `(success_ids, errors)`
- `format_action_response(action, entity, success_ids, errors)` - format human-readable response

One factory function:

- `register_manage_tool(mcp, service, action, entity_name, input_model, ids_field, ...)` - registers a complete manage MCP tool in one call

### 2. Simplify tool files

Replace copy-pasted manage blocks with single-line `register_manage_tool()` calls in:
- `campaigns.py` (5 operations)
- `ads.py` (5 operations: suspend/resume/archive/unarchive/delete)
- `keywords.py` (3 operations: suspend/resume/delete)
- `retargeting.py` (3 operations on audience targets: suspend/resume/delete)

Keep unique logic untouched (get, create, update, moderate).

### 3. Clean up `models/__init__.py`

Reduce from 248 lines to minimal re-exports (ResponseFormat + key enums). Tools already import directly from submodules.

### 4. README update

- `README.md` - English version (primary for GitHub)
- `README.ru.md` - Russian version (linked from README.md)
- Document all 120 tools grouped by category
- Update version, capabilities description

## Files changed

| File | Action |
|------|--------|
| `yandex_mcp/tools/direct/_helpers.py` | Create |
| `yandex_mcp/tools/direct/campaigns.py` | Simplify manage ops |
| `yandex_mcp/tools/direct/ads.py` | Simplify manage ops |
| `yandex_mcp/tools/direct/keywords.py` | Simplify manage ops |
| `yandex_mcp/tools/direct/retargeting.py` | Simplify manage ops |
| `yandex_mcp/models/__init__.py` | Trim re-exports |
| `README.md` | Rewrite in English |
| `README.ru.md` | Create Russian version |

## Not changed

- `client.py`, `config.py`, `utils.py` - already clean
- `formatters/` - no duplication
- Unique tool logic (get, create, update)
- Model definitions in `models/direct.py`, `models/metrika.py`, etc.
