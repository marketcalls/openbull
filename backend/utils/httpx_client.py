import httpx

_client: httpx.Client | None = None


def get_httpx_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=30.0, follow_redirects=True)
    return _client


def close_httpx_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None
