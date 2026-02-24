# Refactor: DRY helpers + README Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate code duplication in Direct manage-operations, fix duplicate tool registrations, clean up model exports, and update README for 115 tools.

**Architecture:** Extract shared `parse_action_results` / `format_action_response` helpers into `_helpers.py`. Use a `register_manage_tool` factory to generate identical suspend/resume/archive/unarchive/delete tools from a config table. Fix smartadtargets.py tool name collision with retargeting.py.

**Tech Stack:** Python 3.10+, FastMCP, Pydantic v2, httpx

---

### Task 1: Create `_helpers.py` with shared Direct API helpers

**Files:**
- Create: `yandex_mcp/tools/direct/_helpers.py`

**Step 1: Create the helpers module**

```python
"""Shared helpers for Yandex Direct manage-operations (suspend/resume/archive/unarchive/delete)."""

from typing import Any, Dict, List, Optional, Tuple, Type

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from ...client import api_client
from ...utils import handle_api_error


def parse_action_results(
    result: Dict[str, Any], action: str
) -> Tuple[List[int], List[str]]:
    """Parse Direct API action response into (success_ids, error_messages).

    Direct API returns results in the format:
        {"result": {"<Action>Results": [{"Id": 123}, {"Id": 456, "Errors": [...]}]}}

    Args:
        result: Raw API response dict.
        action: Action name capitalized, e.g. "Suspend", "Resume", "Delete".

    Returns:
        Tuple of (list of successful IDs, list of error strings).
    """
    key = f"{action}Results"
    action_results = result.get("result", {}).get(key, [])

    success_ids = []
    errors = []
    for r in action_results:
        if r.get("Id") and not r.get("Errors"):
            success_ids.append(r["Id"])
        if r.get("Errors"):
            item_id = r.get("Id", "?")
            for e in r["Errors"]:
                errors.append(f"ID {item_id}: {e.get('Message', 'Unknown error')}")

    return success_ids, errors


def format_action_response(
    action: str, entity: str, success_ids: List[int], errors: List[str]
) -> str:
    """Format a human-readable response for a manage action.

    Args:
        action: Past-tense verb, e.g. "suspended", "resumed", "deleted".
        entity: Entity name, e.g. "campaign", "ad", "keyword".
        success_ids: List of successfully processed IDs.
        errors: List of error strings.
    """
    response = f"Successfully {action} {len(success_ids)} {entity}(s)."
    if errors:
        response += "\n\nErrors:\n" + "\n".join(f"- {e}" for e in errors)
    return response


# Mapping of action -> (result_key_prefix, past_tense, is_destructive)
_ACTION_META = {
    "suspend": ("Suspend", "suspended", False),
    "resume": ("Resume", "resumed", False),
    "archive": ("Archive", "archived", False),
    "unarchive": ("Unarchive", "unarchived", False),
    "delete": ("Delete", "deleted", True),
}

# Human-readable descriptions for tool docstrings
_ACTION_DESCRIPTIONS = {
    "suspend": "Suspend (pause) {entities}. {Entities_cap} stop being active but retain all settings.",
    "resume": "Resume suspended {entities}. {Entities_cap} will become active again.",
    "archive": "Archive {entities}. Archived {entities} are hidden but can be restored.",
    "unarchive": "Restore archived {entities}. Makes them visible in the main list again.",
    "delete": "Delete {entities} permanently. WARNING: This action is irreversible.",
}


def register_manage_tool(
    mcp: FastMCP,
    *,
    service: str,
    action: str,
    entity: str,
    input_model: Type[BaseModel],
    ids_field: str,
    tool_name: Optional[str] = None,
    tool_title: Optional[str] = None,
) -> None:
    """Register a single manage-operation MCP tool (suspend/resume/archive/unarchive/delete).

    Args:
        mcp: FastMCP server instance.
        service: Direct API service name, e.g. "campaigns", "ads", "keywords".
        action: API method name, e.g. "suspend", "resume", "delete".
        entity: Singular entity name for messages, e.g. "campaign", "ad".
        input_model: Pydantic model class for the tool input.
        ids_field: Name of the field on input_model that contains the list of IDs.
        tool_name: Override MCP tool name. Default: "direct_{action}_{service}".
        tool_title: Override MCP tool title. Default: auto-generated.
    """
    action_key, past_tense, is_destructive = _ACTION_META[action]

    name = tool_name or f"direct_{action}_{service}"
    title = tool_title or f"{action_key} Yandex Direct {entity.title()}s"

    entities = f"{entity}s"
    entities_cap = entities.capitalize()
    description = _ACTION_DESCRIPTIONS[action].format(
        entities=entities, Entities_cap=entities_cap
    )

    @mcp.tool(
        name=name,
        annotations={
            "title": title,
            "readOnlyHint": False,
            "destructiveHint": is_destructive,
            "idempotentHint": not is_destructive,
            "openWorldHint": False,
        },
    )
    async def _manage_tool(params: input_model) -> str:  # type: ignore[valid-type]
        try:
            ids = getattr(params, ids_field)
            request_params = {"SelectionCriteria": {"Ids": ids}}
            result = await api_client.direct_request(service, action, request_params)
            success_ids, errors = parse_action_results(result, action_key)
            return format_action_response(past_tense, entity, success_ids, errors)
        except Exception as e:
            return handle_api_error(e)

    # Override the docstring so MCP sees the right description
    _manage_tool.__doc__ = description
```

**Step 2: Verify the module imports correctly**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp.tools.direct._helpers import register_manage_tool, parse_action_results; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add yandex_mcp/tools/direct/_helpers.py
git commit -m "feat: add shared helpers for Direct manage-operations"
```

---

### Task 2: Simplify `campaigns.py` — replace 5 manage-operations

**Files:**
- Modify: `yandex_mcp/tools/direct/campaigns.py` (lines 75-271 — suspend/resume/archive/unarchive/delete)

**Step 1: Replace the 5 copy-pasted manage functions with `register_manage_tool` calls**

Keep `direct_get_campaigns`, `direct_update_campaign`, `direct_create_campaign` untouched.

Replace the body of `register()` so that after the `direct_get_campaigns` tool definition, the manage tools are registered via helper:

```python
from ._helpers import register_manage_tool

# Inside register():
    # ... direct_get_campaigns stays as-is ...

    # Manage operations (suspend/resume/archive/unarchive/delete)
    for action in ("suspend", "resume", "archive", "unarchive", "delete"):
        register_manage_tool(
            mcp,
            service="campaigns",
            action=action,
            entity="campaign",
            input_model=ManageCampaignInput,
            ids_field="campaign_ids",
        )

    # ... direct_update_campaign stays as-is ...
    # ... direct_create_campaign stays as-is ...
```

**Step 2: Verify tool registration**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('Tools registered:', len(mcp._tool_manager._tools))"`
Expected: Same tool count as before (or fewer if duplicates are removed).

**Step 3: Commit**

```bash
git add yandex_mcp/tools/direct/campaigns.py
git commit -m "refactor: use shared helpers for campaign manage-operations"
```

---

### Task 3: Simplify `ads.py` — replace 5 manage-operations

**Files:**
- Modify: `yandex_mcp/tools/direct/ads.py` (lines 379-628 — suspend/resume/archive/unarchive/delete)

**Step 1: Replace manage functions with helper calls**

Keep `direct_get_ads`, all `direct_create_*` and `direct_update_ad`, `direct_moderate_ads` untouched.

```python
from ._helpers import register_manage_tool

# Inside register(), after direct_moderate_ads:
    for action in ("suspend", "resume", "archive", "unarchive", "delete"):
        register_manage_tool(
            mcp,
            service="ads",
            action=action,
            entity="ad",
            input_model=ManageAdInput,
            ids_field="ad_ids",
        )
```

Note: `ManageAdInput.ad_ids` is Optional. The helper uses `getattr(params, ids_field)` which will get `None` if not provided. That's fine — Direct API will return an error which gets caught. The `moderate` operation is NOT included because it has unique logic (campaign_id lookup for draft ads).

**Step 2: Verify**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('OK')"`

**Step 3: Commit**

```bash
git add yandex_mcp/tools/direct/ads.py
git commit -m "refactor: use shared helpers for ad manage-operations"
```

---

### Task 4: Simplify `keywords.py` — replace 3 manage-operations

**Files:**
- Modify: `yandex_mcp/tools/direct/keywords.py` (lines 155-264 — suspend/resume/delete)

**Step 1: Replace manage functions**

Keep `direct_get_keywords`, `direct_add_keywords`, `direct_set_keyword_bids` untouched.

```python
from ._helpers import register_manage_tool

# Inside register(), after direct_set_keyword_bids:
    for action in ("suspend", "resume", "delete"):
        register_manage_tool(
            mcp,
            service="keywords",
            action=action,
            entity="keyword",
            input_model=ManageKeywordInput,
            ids_field="keyword_ids",
        )
```

**Step 2: Verify**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('OK')"`

**Step 3: Commit**

```bash
git add yandex_mcp/tools/direct/keywords.py
git commit -m "refactor: use shared helpers for keyword manage-operations"
```

---

### Task 5: Fix duplicate tool names — rename smartadtargets.py tools

**Files:**
- Modify: `yandex_mcp/tools/direct/smartadtargets.py` (lines 53, 111, 169, 207, 245 — tool names)

**Problem:** Both `retargeting.py` and `smartadtargets.py` register tools with name `direct_*_audience_targets`. These are different APIs (`audiencetargets` vs `smartadtargets`). The last to register silently overwrites the first.

**Step 1: Rename smartadtargets.py tool names**

Replace tool names:
- `direct_add_audience_target` → `direct_add_smart_ad_target`
- `direct_get_audience_targets` → `direct_get_smart_ad_targets`
- `direct_suspend_audience_targets` → `direct_suspend_smart_ad_targets`
- `direct_resume_audience_targets` → `direct_resume_smart_ad_targets`
- `direct_delete_audience_targets` → `direct_delete_smart_ad_targets`

Also update the titles to say "Smart Ad Target" instead of generic.

**Step 2: Simplify the 3 manage operations in smartadtargets.py using helpers**

```python
from ._helpers import register_manage_tool

# Inside register(), after get and add:
    for action in ("suspend", "resume", "delete"):
        register_manage_tool(
            mcp,
            service="smartadtargets",
            action=action,
            entity="smart ad target",
            input_model=ManageSmartAdTargetsInput,
            ids_field="target_ids",
            tool_name=f"direct_{action}_smart_ad_targets",
        )
```

**Step 3: Verify**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('OK')"`

**Step 4: Commit**

```bash
git add yandex_mcp/tools/direct/smartadtargets.py
git commit -m "fix: rename smart ad target tools to avoid collision with audience targets"
```

---

### Task 6: Simplify `retargeting.py` — replace 3 audience target manage-operations

**Files:**
- Modify: `yandex_mcp/tools/direct/retargeting.py` (lines 318-397 — audience target suspend/resume/delete)

**Step 1: Replace manage functions**

Keep retargeting list CRUD and `direct_get_audience_targets`, `direct_add_audience_target` untouched.

```python
from ._helpers import register_manage_tool

# Inside register(), after direct_add_audience_target:
    for action in ("suspend", "resume", "delete"):
        register_manage_tool(
            mcp,
            service="audiencetargets",
            action=action,
            entity="audience target",
            input_model=ManageAudienceTargetsInput,
            ids_field="audience_target_ids",
        )
```

**Step 2: Verify**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('OK')"`

**Step 3: Commit**

```bash
git add yandex_mcp/tools/direct/retargeting.py
git commit -m "refactor: use shared helpers for audience target manage-operations"
```

---

### Task 7: Clean up `models/__init__.py`

**Files:**
- Modify: `yandex_mcp/models/__init__.py`

**Step 1: Reduce to minimal re-exports**

All tool files import directly from submodules (`from ...models.direct import ...`), so the giant re-export list in `__init__.py` is unused. Replace with:

```python
"""Pydantic models for Yandex MCP Server."""

from .common import ResponseFormat

__all__ = ["ResponseFormat"]
```

**Step 2: Verify nothing breaks**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; print('OK')"`

**Step 3: Commit**

```bash
git add yandex_mcp/models/__init__.py
git commit -m "refactor: simplify models __init__ - remove unused re-exports"
```

---

### Task 8: Write README.md (English)

**Files:**
- Modify: `README.md`

**Step 1: Rewrite README.md in English**

Full content with:
- Badges (Python, License, MCP)
- Project description
- Features overview (Yandex Direct 72 tools + Yandex Metrika 43 tools)
- Quick start (install, token, Claude Code config)
- Environment variables
- Tool tables grouped by category:
  - Direct: Campaigns, Ad Groups, Ads, Keywords, Statistics, Sitelinks, VCards, Bid Modifiers, Retargeting, Smart Ad Targets, Dictionaries, Negative Keywords, Clients, Ad Extensions, Videos, Creatives, Feeds
  - Metrika: Counters, Goals, Reports, Segments, Filters, Grants, Offline Data, Labels, Annotations, Delegates
- Usage examples
- Alternative run methods
- Security notes
- Development section
- Links

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README in English with full 115 tool coverage"
```

---

### Task 9: Write README.ru.md (Russian)

**Files:**
- Create: `README.ru.md`

**Step 1: Create Russian README**

Same structure as README.md but in Russian. Add link to English README at the top. Add link from README.md to README.ru.md.

**Step 2: Add cross-links between READMEs**

In `README.md` add: `[🇷🇺 Русская версия](README.ru.md)`
In `README.ru.md` add: `[🇬🇧 English version](README.md)`

**Step 3: Commit**

```bash
git add README.ru.md README.md
git commit -m "docs: add Russian README with full tool coverage"
```

---

### Task 10: Delete old `yandex_mcp.py` and verify everything works

**Files:**
- Delete: `yandex_mcp.py` (already deleted per git status, just needs staging)
- Verify: `main.py` still works

**Step 1: Stage the deletion and verify**

Run: `cd /c/Users/13k13/projects/yandexDirectAsia/yandex-mcp && .venv/Scripts/python -c "from yandex_mcp import mcp; tools = list(mcp._tool_manager._tools.keys()); print(f'Total tools: {len(tools)}'); print('Sample:', tools[:5])"`

**Step 2: Commit cleanup**

```bash
git add yandex_mcp.py pyproject.toml main.py
git commit -m "chore: remove old monolithic yandex_mcp.py, finalize package structure"
```
