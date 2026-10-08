"""Authenticated asynchronous access to the Blue Fish rendering service."""

import asyncio
import json
from urllib.parse import urlsplit

import aiohttp


class BlueFishError(Exception):
    """Represent a service or configuration failure that is safe to show in chat."""


class BlueFishClient:
    """Call the fixed image-service endpoints without persisting user images.

    Attributes:
        base_url: Administrator-configured HTTP origin and optional path prefix.
        api_key: Bearer credential used only in outgoing authorization headers.
        timeout: Maximum duration in seconds for one complete HTTP request.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 20):
        """Store configuration, deferring missing-value errors until a command runs."""
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = timeout

    async def render(self, payload: dict) -> bytes:
        """Render one sticker and return verified PNG bytes for a bot image reply."""
        content_type, content = await self._request("POST", "render", payload)
        if content_type != "image/png" or not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BlueFishError("图片服务返回的内容不是 PNG 图片，请联系管理员。")
        return content

    async def stickers(self) -> list[dict]:
        """Return the public sticker catalog from the configured service."""
        return await self._catalog("stickers")

    async def fonts(self) -> list[dict]:
        """Return the supported font identifiers and display names."""
        return await self._catalog("fonts")

    async def _catalog(self, name: str) -> list[dict]:
        """Decode one catalog response and verify its documented list shape."""
        _, content = await self._request("GET", name)
        try:
            body = json.loads(content)
        except ValueError as error:
            raise BlueFishError("图片服务返回的列表格式不正确，请联系管理员。") from error
        items = body.get(name) if isinstance(body, dict) else None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise BlueFishError("图片服务返回的列表格式不正确，请联系管理员。")
        return items

    def _validated_base_url(self) -> str:
        """Require a configured HTTP address and a separately supplied API key."""
        if not self.base_url or not self.api_key:
            raise BlueFishError("肥鱼服务尚未配置，请管理员填写服务地址和 API Key。")
        try:
            parsed = urlsplit(self.base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise BlueFishError("肥鱼服务地址格式不正确，请管理员填写 HTTP 或 HTTPS 地址。")
        return self.base_url

    async def _request(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> tuple[str, bytes]:
        """Perform one authenticated call while keeping network details out of chat."""
        url = f"{self._validated_base_url()}/api/v1/{endpoint}"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                # Credentials must never follow a redirect to a different service.
                async with session.request(
                    method,
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    allow_redirects=False,
                ) as response:
                    content = await response.read()
                    if response.status != 200:
                        raise BlueFishError(self._error_message(response.status, content))
                    return response.content_type, content
        except asyncio.TimeoutError as error:
            raise BlueFishError("图片服务响应超时，请稍后重试。") from error
        except aiohttp.ClientError as error:
            raise BlueFishError("暂时无法连接图片服务，请稍后重试或联系管理员。") from error
        except ValueError as error:
            raise BlueFishError("服务地址或 API Key 格式不正确，请联系管理员检查配置。") from error

    @staticmethod
    def _error_message(status: int, content: bytes) -> str:
        """Extract documented service errors without exposing arbitrary response text."""
        if status == 401:
            return "图片服务鉴权失败，请管理员检查 API Key。"
        try:
            body = json.loads(content)
            error = body.get("error") if isinstance(body, dict) else None
            if (
                isinstance(error, dict)
                and isinstance(error.get("code"), str)
                and isinstance(error.get("message"), str)
                and error["message"].strip()
            ):
                return error["message"]
        except ValueError:
            pass
        if status == 429:
            return "图片请求过于频繁，请稍后重试。"
        if status == 503:
            return "图片服务正在忙，请稍后重试。"
        return "图片服务暂时无法完成请求，请稍后重试或联系管理员。"
