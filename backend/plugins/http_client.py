"""
HTTP Client Plugin

General-purpose HTTP request tool for testing APIs and fetching data from endpoints.
Uses urllib.request from stdlib (no external dependencies).
"""
import json
import urllib.request
import urllib.parse
import ssl

PLUGIN_NAME = "http-client"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Send HTTP requests to test APIs or fetch data from endpoints"

# SSL context that does not verify certificates (for local/dev APIs)
_no_verify_ctx = ssl.create_default_context()
_no_verify_ctx.check_hostname = False
_no_verify_ctx.verify_mode = ssl.CERT_NONE


def tool_http_request(
    method: str,
    url: str,
    headers: str = "",
    body: str = "",
    content_type: str = "application/json",
    timeout: int = 30,
) -> str:
    """Send an HTTP request and return the response. Use this to test APIs, call webhooks, or fetch data from any HTTP endpoint.

    Parameters:
    - method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
    - url: Target URL (e.g. http://localhost:8000/api/health)
    - headers: Request headers as JSON string, e.g. {"Authorization": "Bearer xxx"} (optional)
    - body: Request body content (optional). For JSON, pass a JSON string. For form data, pass key=value&key2=value2
    - content_type: Content-Type header (default: application/json). Use application/x-www-form-urlencoded for form data
    - timeout: Request timeout in seconds (default: 30)
    """
    method = method.upper().strip()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        return f"Error: Invalid HTTP method '{method}'. Must be one of: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"

    # Parse headers
    req_headers = {"User-Agent": "KB-Agent-HTTP-Client/1.0"}
    if headers:
        try:
            req_headers.update(json.loads(headers))
        except json.JSONDecodeError:
            return f"Error: Invalid headers JSON — {headers}"

    # Encode body
    data = None
    if body and method not in ("GET", "HEAD", "OPTIONS"):
        if "json" in content_type:
            # Validate JSON
            try:
                json.loads(body)
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON body — {e}"
            data = body.encode("utf-8")
            req_headers.setdefault("Content-Type", content_type)
        else:
            data = body.encode("utf-8")
            req_headers.setdefault("Content-Type", content_type)

    # Build request
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_no_verify_ctx) as resp:
            status = resp.status
            resp_headers = dict(resp.headers)
            resp_body = resp.read().decode("utf-8", errors="replace")

            # Try to pretty-print JSON
            try:
                parsed = json.loads(resp_body)
                resp_body = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass

            # Truncate very long responses
            if len(resp_body) > 5000:
                resp_body = resp_body[:5000] + f"\n... (truncated, total {len(resp_body)} chars)"

            lines = [
                f"Status: {status} {resp.reason}",
                f"Headers: {json.dumps(resp_headers, indent=2, ensure_ascii=False)}",
                f"Body:\n{resp_body}",
            ]
            return "\n".join(lines)

    except urllib.error.HTTPError as e:
        # Server returned an error status code
        resp_body = ""
        try:
            resp_body = e.read().decode("utf-8", errors="replace")
            if len(resp_body) > 3000:
                resp_body = resp_body[:3000] + f"\n... (truncated, total {len(resp_body)} chars)"
        except Exception:
            pass
        return f"HTTP Error {e.code} {e.reason}\nBody:\n{resp_body}"

    except urllib.error.URLError as e:
        return f"Connection Error: {e.reason}"

    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def tool_http_get(url: str, headers: str = "", timeout: int = 30) -> str:
    """Send an HTTP GET request and return the response. A convenience wrapper for tool_http_request with method=GET.

    Parameters:
    - url: Target URL
    - headers: Request headers as JSON string (optional)
    - timeout: Timeout in seconds (default: 30)
    """
    return tool_http_request("GET", url, headers=headers, timeout=timeout)


def tool_http_post(
    url: str, body: str = "", headers: str = "", content_type: str = "application/json", timeout: int = 30
) -> str:
    """Send an HTTP POST request and return the response. A convenience wrapper for tool_http_request with method=POST.

    Parameters:
    - url: Target URL
    - body: Request body (JSON string or form-encoded data)
    - headers: Additional request headers as JSON string (optional)
    - content_type: Content-Type (default: application/json)
    - timeout: Timeout in seconds (default: 30)
    """
    return tool_http_request("POST", url, headers=headers, body=body, content_type=content_type, timeout=timeout)
