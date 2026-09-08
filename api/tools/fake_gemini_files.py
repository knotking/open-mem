"""A Gemini Files API that runs on loopback.

The same argument as `fake_sources.py` and `fake_salesforce.py`: the parts of an
integration most likely to be wrong cannot be seen by reading it. Here they are
the ones the small path never had --

**A file is not usable when the upload returns.** It is `PROCESSING`, and a
`generateContent` against it fails. Code that uploads and immediately asks works
perfectly against a simulator that returns `ACTIVE` at once, and fails against
every real video. So this one stays `PROCESSING` for a set number of polls, and
refuses a file that is not yet `ACTIVE`.

**The upload is two requests, and the second URL comes from a header.** A start
request returns `X-Goog-Upload-URL`; the bytes go there, not to the API host.
Reading that URL out of the body instead -- which is empty -- is a mistake that
looks like a network problem.

**A file has to be deleted, and the delete is easy to get the wrong side of.**
Deleting before the model has answered leaves a correct-looking crawl that
returns nothing. So a deleted URI is refused here, and the deletes are counted
so a test can assert the file did not outlive the call.

Run it:

    python -m tools.fake_gemini_files          # 127.0.0.1:8799
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# How many `get` calls a file spends in PROCESSING before going ACTIVE. Two,
# rather than one, so that code which polls exactly once still fails here.
POLLS_BEFORE_ACTIVE = 2

# What the model "says" about whatever it is shown. A test asserts this text
# comes out the far end of the pipeline, which is what proves the large path
# reached retrieval rather than merely returning.
TRANSCRIPT = (
    "Speaker 1: the migration ran for six hours and finished at 04:12 UTC. "
    "Speaker 2: the rollback plan was never needed."
)

_LOCK = threading.Lock()

# name -> {"uri", "mime", "state", "polls", "bytes", "display_name"}
FILES: dict[str, dict] = {}
# What the test asks about afterwards.
COUNTS: dict[str, int] = {}
# Set to a state to force it -- "FAILED" exercises the branch that gives up.
FORCE_STATE: dict[str, str] = {}


def reset() -> None:
    with _LOCK:
        FILES.clear()
        COUNTS.clear()
        FORCE_STATE.clear()


def _count(key: str) -> None:
    COUNTS[key] = COUNTS.get(key, 0) + 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:      # noqa: A003 -- quiet in tests
        pass

    # ------------------------------------------------------------ helpers

    def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    # --------------------------------------------------------------- POST

    def do_POST(self) -> None:                 # noqa: N802 -- the base class
        path = urlparse(self.path).path

        # 1. Start a resumable upload. Metadata only; the answer is a header.
        if path == "/upload/v1beta/files" and \
                self.headers.get("X-Goog-Upload-Command") == "start":
            _count("start")
            self._body()
            name = f"files/fake{len(FILES) + 1}"
            with _LOCK:
                FILES[name] = {
                    "uri": f"https://fake.invalid/{name}",
                    "mime": self.headers.get(
                        "X-Goog-Upload-Header-Content-Type", "application/octet-stream"),
                    "state": "PROCESSING", "polls": 0, "bytes": 0,
                }
            base = f"http://{self.headers.get('host')}"
            self._send(200, {}, {"X-Goog-Upload-URL": f"{base}/upload/bytes/{name}"})
            return

        # 2. The bytes. Only now does the file resource come back.
        match = re.fullmatch(r"/upload/bytes/(files/[A-Za-z0-9_-]+)", path)
        if match:
            _count("upload")
            payload = self._body()
            name = match.group(1)
            with _LOCK:
                record = FILES.get(name)
                if record is None:
                    self._send(404, {"error": {"message": f"no upload at {name}"}})
                    return
                record["bytes"] = len(payload)
                record["state"] = FORCE_STATE.get("state", "PROCESSING")
            self._send(200, {"file": _resource(name)})
            return

        # 3. generateContent -- the same endpoint the inline path calls.
        model = re.fullmatch(r"/v1beta/models/([^:]+):generateContent", path)
        if model:
            _count("generate")
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                self._send(400, {"error": {"message": "unparseable body"}})
                return
            parts = (body.get("contents") or [{}])[0].get("parts") or []
            refs = [p["file_data"] for p in parts if "file_data" in p]
            if not refs:
                # The inline path is not this simulator's job, and answering it
                # anyway would let a test pass that never used the upload.
                self._send(400, {"error": {"message": "no file_data part"}})
                return
            uri = refs[0].get("file_uri")
            with _LOCK:
                found = next((n for n, r in FILES.items() if r["uri"] == uri), None)
                if found is None:
                    # Deleted, or never uploaded. This is what catches a delete
                    # that happens before the answer rather than after.
                    self._send(403, {"error": {
                        "message": f"{uri} is not available to this request"}})
                    return
                if FILES[found]["state"] != "ACTIVE":
                    self._send(400, {"error": {"message": (
                        f"{uri} is still processing and cannot be used yet")}})
                    return
            self._send(200, {
                "candidates": [{"content": {"parts": [{"text": TRANSCRIPT}]}}],
                "usageMetadata": {"promptTokenCount": 900_000,
                                  "candidatesTokenCount": 40,
                                  "totalTokenCount": 900_040},
                "modelVersion": model.group(1),
                "responseId": "fake-response-1",
            })
            return

        self._send(404, {"error": {"message": f"no route for {path}"}})

    # ---------------------------------------------------------------- GET

    def do_GET(self) -> None:                  # noqa: N802 -- the base class
        path = urlparse(self.path).path
        match = re.fullmatch(r"/v1beta/(files/[A-Za-z0-9_-]+)", path)
        if not match:
            self._send(404, {"error": {"message": f"no route for {path}"}})
            return
        _count("get")
        name = match.group(1)
        with _LOCK:
            record = FILES.get(name)
            if record is None:
                self._send(404, {"error": {"message": f"{name} not found"}})
                return
            record["polls"] += 1
            forced = FORCE_STATE.get("state")
            if forced:
                record["state"] = forced
            elif record["polls"] >= POLLS_BEFORE_ACTIVE:
                record["state"] = "ACTIVE"
        self._send(200, _resource(name))

    # ------------------------------------------------------------- DELETE

    def do_DELETE(self) -> None:               # noqa: N802 -- the base class
        path = urlparse(self.path).path
        match = re.fullmatch(r"/v1beta/(files/[A-Za-z0-9_-]+)", path)
        if not match:
            self._send(404, {"error": {"message": f"no route for {path}"}})
            return
        _count("delete")
        with _LOCK:
            FILES.pop(match.group(1), None)
        self._send(200, {})


def _resource(name: str) -> dict:
    record = FILES[name]
    return {"name": name, "uri": record["uri"], "mimeType": record["mime"],
            "state": record["state"], "sizeBytes": str(record["bytes"])}


def serve(port: int = 8799) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    server = serve()
    host, port = server.server_address
    print(f"fake gemini files on http://{host}:{port}")
    server.serve_forever()
