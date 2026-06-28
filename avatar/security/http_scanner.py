"""Generic, schema-agnostic HTTP content scanner.

``HttpScanner`` talks to *any* external content-scan HTTP API. It is entirely
config-driven: the request body is built from a Jinja2 ``request_template`` (or
a simple field map) and the verdict is extracted from the response via JMESPath
expressions. Nothing here is tied to a specific vendor's request/response shape.

Configuration (all read from the scanner settings dict, env already
interpolated)::

    name: content-scan          # scanner name (defaults to "http")
    type: http
    endpoint: https://...       # required at call time
    method: POST                # default POST
    timeout_seconds: 5          # default 5
    auth:
      kind: header | bearer | query
      header: x-api-token       # for kind == header
      param: token              # for kind == query
      token: secret
    request_template: |         # Jinja2 -> JSON string body
      {"contents": [{"prompt": {{ text | tojson }}}]}
    # ...or a simpler field map (each value is a Jinja2 template):
    request_fields:
      prompt: "{{ text }}"
      direction: "{{ direction }}"
    verdict:
      path: "action"            # JMESPath to a value
      block_values: ["block"]   # value in this list => blocked
      allow_values: ["allow"]   # value in this list => allowed
      block_expression: "result.flagged"   # JMESPath truthy => blocked
      category_path: "category"
    block_on_status: [403, 451] # HTTP status codes that mean "blocked"
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import jmespath
from jinja2 import Environment

from avatar.core.registry import register_scanner
from avatar.core.types import ScanRequest, ScanVerdict

_DEFAULT_TIMEOUT = 5.0


@register_scanner("http")
class HttpScanner:
    """A configurable scanner that maps to/from arbitrary HTTP scan APIs."""

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self._settings = dict(settings)
        self.name: str = self._settings.get("name") or "http"

        self._endpoint: str | None = self._settings.get("endpoint")
        self._method: str = str(self._settings.get("method") or "POST").upper()
        self._timeout: float = float(self._settings.get("timeout_seconds", _DEFAULT_TIMEOUT))

        self._auth: dict[str, Any] = dict(self._settings.get("auth") or {})
        self._verdict_cfg: dict[str, Any] = dict(self._settings.get("verdict") or {})
        self._block_on_status: list[int] = [
            int(code) for code in (self._settings.get("block_on_status") or [])
        ]

        self._request_template: str | None = self._settings.get("request_template")
        self._request_fields: dict[str, Any] = dict(self._settings.get("request_fields") or {})

        # Jinja2 environment with the ``tojson`` filter available.
        self._jinja = Environment(autoescape=False)  # JSON output, not HTML

        # Pre-compile JMESPath expressions where present.
        self._path_expr = (
            jmespath.compile(self._verdict_cfg["path"]) if self._verdict_cfg.get("path") else None
        )
        self._block_expr = (
            jmespath.compile(self._verdict_cfg["block_expression"])
            if self._verdict_cfg.get("block_expression")
            else None
        )
        self._category_expr = (
            jmespath.compile(self._verdict_cfg["category_path"])
            if self._verdict_cfg.get("category_path")
            else None
        )

        self._client: httpx.AsyncClient | None = None

    # -- helpers ------------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _render_context(self, req: ScanRequest) -> dict[str, Any]:
        return {
            "text": req.text,
            "direction": str(req.direction),
            "context": req.context,
            "metadata": req.metadata,
        }

    def _build_body(self, req: ScanRequest) -> Any:
        """Render the request body into a JSON-serialisable object."""
        ctx = self._render_context(req)
        if self._request_template:
            rendered = self._jinja.from_string(self._request_template).render(**ctx)
            return _loads(rendered)
        if self._request_fields:
            out: dict[str, Any] = {}
            for key, tmpl in self._request_fields.items():
                if isinstance(tmpl, str) and ("{{" in tmpl or "{%" in tmpl):
                    out[key] = self._jinja.from_string(tmpl).render(**ctx)
                else:
                    out[key] = tmpl
            return out
        # Sensible default body if nothing configured.
        return {"text": req.text, "direction": str(req.direction)}

    def _apply_auth(self, headers: dict[str, str], params: dict[str, str]) -> None:
        kind = str(self._auth.get("kind") or "").lower()
        token = self._auth.get("token")
        if not kind or token is None:
            return
        if kind == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif kind == "header":
            header_name = self._auth.get("header") or "Authorization"
            headers[str(header_name)] = str(token)
        elif kind == "query":
            param_name = self._auth.get("param") or "token"
            params[str(param_name)] = str(token)

    def _extract_verdict(self, status_code: int, payload: Any) -> ScanVerdict:
        reasons: list[str] = []
        blocked = False

        if status_code in self._block_on_status:
            blocked = True
            reasons.append(f"status {status_code} in block_on_status")

        if self._path_expr is not None:
            value = self._path_expr.search(payload)
            block_values = self._verdict_cfg.get("block_values")
            allow_values = self._verdict_cfg.get("allow_values")
            if block_values is not None and value in block_values:
                blocked = True
                reasons.append(f"verdict value {value!r} in block_values")
            elif allow_values is not None and value in allow_values:
                # Explicit allow short-circuits the path check (but not status
                # or block_expression based blocking).
                reasons.append(f"verdict value {value!r} in allow_values")
            elif block_values is None and allow_values is None:
                # No lists configured: treat any truthy path value as a block.
                if value:
                    blocked = True
                    reasons.append(f"verdict value {value!r} is truthy")

        if self._block_expr is not None:
            if self._block_expr.search(payload):
                blocked = True
                reasons.append("block_expression matched")

        category: str | None = None
        if self._category_expr is not None:
            cat = self._category_expr.search(payload)
            category = str(cat) if cat is not None else None

        raw = payload if isinstance(payload, dict) else {"response": payload}
        return ScanVerdict(
            allowed=not blocked,
            category=category,
            reasons=reasons,
            scanner=self.name,
            raw=raw,
        )

    # -- protocol -----------------------------------------------------------
    async def scan(self, req: ScanRequest) -> ScanVerdict:
        if not self._endpoint:
            raise RuntimeError(f"http scanner {self.name!r} has no 'endpoint' configured")

        body = self._build_body(req)
        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        self._apply_auth(headers, params)

        # On request error/timeout we let it propagate; the pipeline's
        # fail_open/fail_closed policy decides what to do.
        response = await self._http().request(
            self._method,
            self._endpoint,
            json=body,
            headers=headers,
            params=params,
        )

        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"text": response.text}

        return self._extract_verdict(response.status_code, payload)

    async def healthcheck(self) -> bool:
        return bool(self._endpoint)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _loads(rendered: str) -> Any:
    import json

    return json.loads(rendered)
