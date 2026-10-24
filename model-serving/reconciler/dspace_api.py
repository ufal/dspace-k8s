"""Minimal authenticated DSpace REST client.

Ports the CSRF/JWT dance from dspace-e2e/lib/rest.js to a sync requests.Session:
GET /security/csrf sets a DSPACE-XSRF-TOKEN header + DSPACE-XSRF-COOKIE cookie;
mutating requests echo the token in X-XSRF-TOKEN. The token rotates on every
response to a mutation, so it is refreshed after every non-GET call.
"""

import os

import requests


class DspaceApiError(Exception):
    def __init__(self, method, url, response):
        self.status_code = response.status_code
        body = response.text[:500]
        super().__init__(f"{method} {url} -> {response.status_code}: {body}")


class DspaceClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.bearer = None

    def _refresh_csrf(self):
        res = self.session.get(f"{self.base_url}/security/csrf")
        token = res.headers.get("DSPACE-XSRF-TOKEN")
        if token:
            self.session.headers["X-XSRF-TOKEN"] = token

    def login(self, user, password):
        self._refresh_csrf()
        res = self.session.post(
            f"{self.base_url}/authn/login",
            data={"user": user, "password": password},
        )
        if not res.ok:
            raise DspaceApiError("POST", "/authn/login", res)
        bearer = res.headers.get("Authorization")
        if not bearer:
            raise DspaceApiError("POST", "/authn/login (no Authorization header)", res)
        self.bearer = bearer
        self.session.headers["Authorization"] = bearer
        rotated = res.headers.get("DSPACE-XSRF-TOKEN")
        if rotated:
            self.session.headers["X-XSRF-TOKEN"] = rotated

    def request(self, method, path_or_url, **kwargs):
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        if method != "GET":
            self._refresh_csrf()
        res = self.session.request(method, url, **kwargs)
        rotated = res.headers.get("DSPACE-XSRF-TOKEN")
        if rotated:
            self.session.headers["X-XSRF-TOKEN"] = rotated
        if not res.ok:
            raise DspaceApiError(method, url, res)
        return res

    def get_json(self, path, **kwargs):
        return self.request("GET", path, headers={"Accept": "application/json"}, **kwargs).json()

    def post_json(self, path, body, **kwargs):
        return self.request(
            "POST", path, json=body, headers={"Accept": "application/json"}, **kwargs
        ).json()

    def patch_json(self, path, ops, **kwargs):
        return self.request(
            "PATCH", path, json=ops, headers={"Accept": "application/json"}, **kwargs
        ).json()


def client_from_env(base_url):
    user = os.environ["DSPACE_USER"]
    password = os.environ["DSPACE_PASS"]
    client = DspaceClient(base_url)
    client.login(user, password)
    return client
