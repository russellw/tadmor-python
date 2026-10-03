"""HTTP concerns of the server-rendered UI."""


class ContentSecurityPolicyMiddleware:
    """Same-origin only: every script, style, and image is served by us."""

    POLICY = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.POLICY)
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
