"""Shared helpers for Yandex Direct manage-operation tools.

Provides factory functions to eliminate code duplication across
suspend/resume/archive/unarchive/delete operations in campaigns,
ads, keywords, audience targets, and other Direct API services.
"""

from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from ...client import api_client
from ...utils import handle_api_error

# Maps action name -> (ResultKeyPrefix, past_tense, is_destructive)
_ACTION_META: Dict[str, Tuple[str, str, bool]] = {
    "suspend": ("Suspend", "suspended", False),
    "resume": ("Resume", "resumed", False),
    "archive": ("Archive", "archived", False),
    "unarchive": ("Unarchive", "unarchived", False),
    "delete": ("Delete", "deleted", True),
}

# Human-readable docstrings per action, parameterised with {entity} / {entities}
_ACTION_DOCSTRINGS: Dict[str, str] = {
    "suspend": (
        "Suspend (pause) {entities}.\n\n"
        "Suspended {entities} stop being displayed but retain all settings.\n"
        "Can be resumed later with direct_resume_{service}."
    ),
    "resume": (
        "Resume suspended {entities}.\n\n"
        "Resumes {entities} that were previously suspended."
    ),
    "archive": (
        "Archive {entities}.\n\n"
        "Archived {entities} are hidden from the main list but can be restored."
    ),
    "unarchive": (
        "Restore archived {entities}.\n\n"
        "Unarchives {entities} and makes them visible in the main list."
    ),
    "delete": (
        "Delete {entities} permanently.\n\n"
        "WARNING: This action is irreversible.\n"
        "Consider archiving instead if you might need them later."
    ),
}


def parse_action_results(
    result: Dict[str, Any],
    action: str,
) -> Tuple[List[int], List[str]]:
    """Parse a Direct API manage-action response into success IDs and errors.

    The Direct API returns results in the shape::

        {"result": {"<Action>Results": [{"Id": 123}, {"Id": 456, "Errors": [...]}]}}

    Parameters
    ----------
    result:
        Raw JSON response dict from ``api_client.direct_request``.
    action:
        The action name (e.g. ``"suspend"``, ``"delete"``).

    Returns
    -------
    tuple of (success_ids, error_messages)
        *success_ids* contains IDs of entities that were processed without
        errors.  *error_messages* contains human-readable error strings.
    """
    prefix, _, _ = _ACTION_META[action]
    result_key = f"{prefix}Results"
    items: List[Dict[str, Any]] = result.get("result", {}).get(result_key, [])

    success_ids: List[int] = []
    errors: List[str] = []

    for item in items:
        has_errors = bool(item.get("Errors"))
        if item.get("Id") and not has_errors:
            success_ids.append(item["Id"])
        if has_errors:
            for err in item["Errors"]:
                entity_id = item.get("Id", "?")
                message = err.get("Message", "Unknown error")
                errors.append(f"ID {entity_id}: {message}")

    return success_ids, errors


def format_action_response(
    action: str,
    entity: str,
    success_ids: List[int],
    errors: List[str],
) -> str:
    """Build a human-readable response string for a manage action.

    Parameters
    ----------
    action:
        Action name (e.g. ``"suspend"``).
    entity:
        Singular entity label used in the message (e.g. ``"campaign"``).
    success_ids:
        IDs that were processed successfully.
    errors:
        Human-readable error strings.

    Returns
    -------
    str
        Formatted markdown-style response.
    """
    _, past_tense, _ = _ACTION_META[action]
    response = f"Successfully {past_tense} {len(success_ids)} {entity}(s)."
    if errors:
        response += "\n\nErrors:\n" + "\n".join(f"- {e}" for e in errors)
    return response


def register_manage_tool(
    mcp: FastMCP,
    *,
    service: str,
    action: str,
    entity: str,
    input_model: type,
    ids_field: str,
    tool_name: Optional[str] = None,
    tool_title: Optional[str] = None,
) -> None:
    """Register a single MCP manage-operation tool (suspend/resume/archive/unarchive/delete).

    This factory replaces ~25 lines of boilerplate per action with a single
    declarative call.

    Parameters
    ----------
    mcp:
        The FastMCP server instance.
    service:
        Direct API service name (e.g. ``"campaigns"``, ``"ads"``,
        ``"keywords"``, ``"audiencetargets"``).
    action:
        One of ``"suspend"``, ``"resume"``, ``"archive"``, ``"unarchive"``,
        ``"delete"``.
    entity:
        Human-readable singular entity name (e.g. ``"campaign"``, ``"ad"``,
        ``"keyword"``).
    input_model:
        Pydantic model class for the tool input.  Must have a field named
        *ids_field* that contains a ``List[int]``.
    ids_field:
        Name of the attribute on *input_model* that holds the list of IDs
        (e.g. ``"campaign_ids"``, ``"ad_ids"``, ``"keyword_ids"``).
    tool_name:
        Override the generated tool name.  Defaults to
        ``"direct_{action}_{service}"``.
    tool_title:
        Override the generated tool title.  Defaults to
        ``"{Action} Yandex Direct {Entity}s"``.
    """
    if action not in _ACTION_META:
        raise ValueError(
            f"Unknown action {action!r}. Must be one of {list(_ACTION_META)}"
        )

    prefix, past_tense, is_destructive = _ACTION_META[action]

    # Derive defaults
    name = tool_name or f"direct_{action}_{service}"
    title = tool_title or f"{prefix} Yandex Direct {entity.title()}s"

    annotations = {
        "title": title,
        "readOnlyHint": False,
        "destructiveHint": is_destructive,
        "idempotentHint": not is_destructive,
        "openWorldHint": False,
    }

    # Build docstring
    entities_plural = f"{entity}s"
    docstring = _ACTION_DOCSTRINGS[action].format(
        entity=entity,
        entities=entities_plural,
        service=service,
    )

    # Use a closure factory so captured variables do not appear as
    # function parameters (FastMCP inspects the signature and rejects
    # parameters whose names start with '_').
    def _make_handler(svc: str, act: str, ent: str, ids_attr: str, model: type):
        async def handler(params) -> str:
            try:
                ids = getattr(params, ids_attr)
                request_params = {"SelectionCriteria": {"Ids": ids}}
                result = await api_client.direct_request(svc, act, request_params)
                success_ids, errors = parse_action_results(result, act)
                return format_action_response(act, ent, success_ids, errors)
            except Exception as e:
                return handle_api_error(e)

        # Set annotations explicitly using the real type object (not a string).
        # This avoids issues with `from __future__ import annotations` or
        # eval-based annotation resolution that FastMCP performs via
        # `inspect.signature(func, eval_str=True)`.
        handler.__annotations__ = {"params": model, "return": str}
        handler.__doc__ = docstring
        handler.__name__ = name
        handler.__qualname__ = name
        return handler

    handler = _make_handler(service, action, entity, ids_field, input_model)

    # Register with FastMCP
    mcp.tool(name=name, annotations=annotations)(handler)
