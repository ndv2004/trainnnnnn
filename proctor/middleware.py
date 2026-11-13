from starlette.responses import JSONResponse


class BodyLimit:
    """Bound the complete body before multipart parsing, including chunked uploads."""

    def __init__(self, app, limit=1048576 + 65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "IMAGE_TOO_LARGE",
                            "message": "Yêu cầu vượt giới hạn kích thước",
                            "details": {},
                        },
                        "request_id": scope.get("state", {}).get("request_id", "unassigned"),
                    },
                    413,
                )
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
