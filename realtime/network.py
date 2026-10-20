from requests.adapters import HTTPAdapter


class BoundedHTTPAdapter(HTTPAdapter):
    """Bound Alpaca SDK REST reads without changing its authentication or retries."""

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (5, 20)
        return super().send(request, **kwargs)
