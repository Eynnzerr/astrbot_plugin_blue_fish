"""AstrBot commands for the Blue Fish Studio image rendering service."""

import shlex
from dataclasses import dataclass
from time import monotonic

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

from .client import BlueFishClient, BlueFishError
from .command import parse_catalog_arguments, parse_render_arguments

# Match the preview API's maximum number of numbered thumbnails.
PAGE_SIZE = 8
# Keep displayed number mappings available for thirty minutes in this chat.
SELECTION_TTL_SECONDS = 30 * 60
HELP = """肥鱼工坊
/肥鱼素材 [关键词] [--page 页码] → 看图选编号
/肥鱼 编号 文案 → 用刚才目录中的图片制图
/肥鱼 文案 [--id 底图ID] [--font 字体ID] [--color #RRGGBB]
  [--background transparent|white] [--layout caption|overlay]
/肥鱼字体
/肥鱼帮助

例：/肥鱼素材 开心
例：/肥鱼 3 今天也要开心呀
例：/肥鱼 今天也要开心呀 --id studio-design-cheer
例：/肥鱼 "好耶！\\n今天也开心" --font zcool-kuaile
编号对应你在当前聊天中最近收到的一页，30 分钟内有效。
纯数字开头的文案可加引号，例如 /肥鱼 "3 只鱼"。
caption 为留白配字，overlay 为叠加文字。
省略样式参数时使用插件配置的默认值。"""


@dataclass(frozen=True)
class CatalogSelection:
    """An ordered material page successfully delivered to one chat participant.

    Attributes:
        sticker_ids: Material IDs in the same order as the numbered preview.
        expires_at: Monotonic deadline after which users must browse again.
    """

    sticker_ids: list[str]
    expires_at: float


def command_arguments(event: AstrMessageEvent) -> str:
    """Read the original command tail, retaining quoted whitespace and newlines."""
    parts = event.message_str.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


class BlueFishPlugin(Star):
    """Render captioned stickers and browse the service's material catalog.

    Attributes:
        client: HTTP client for the configured rendering service.
        defaults: API parameter values used when command options are omitted.
        selections: Last delivered page for each platform/chat and sender pair.
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        """Read the framework-managed connection and default style settings."""
        super().__init__(context)
        self.client = BlueFishClient(
            config.get("api_url", "https://eynnzerr.cloud/blue-fish"),
            config.get("api_key", ""),
            float(config.get("request_timeout", 20)),
        )
        self.defaults = {
            "stickerId": config.get("default_sticker_id", "studio-design-cheer"),
            "font": config.get("default_font", "maoken"),
            "color": config.get("default_color", "#3565ae"),
            "background": config.get("default_background", "transparent"),
            "layout": config.get("default_layout", "caption"),
        }
        self.selections: dict[tuple[str, str], CatalogSelection] = {}

    def _selection(self, event: AstrMessageEvent) -> list[str] | None:
        """Read this participant's current page, discarding an expired mapping."""
        key = (event.unified_msg_origin, event.get_sender_id())
        selection = self.selections.get(key)
        if selection is None:
            return None
        if selection.expires_at <= monotonic():
            del self.selections[key]
            return None
        return selection.sticker_ids

    async def _send_image(
        self, event: AstrMessageEvent, png: bytes, caption: str = ""
    ) -> bool:
        """Send a PNG and optional instructions, returning whether delivery succeeded."""
        chain = [Image.fromBytes(png)]
        if caption:
            chain.append(Plain(caption))
        # Await the adapter so failed delivery cannot activate invisible numbers.
        try:
            await event.send(event.chain_result(chain))
            return True
        except Exception as error:
            logger.warning(f"Blue Fish image delivery failed: {type(error).__name__}")
            if "rich media transfer failed" in str(error):
                message = "图片已生成，但 QQ 上传失败。请稍后重试或联系管理员检查 NapCat。"
            else:
                message = "图片已生成，但发送失败。请稍后重试或联系管理员检查消息平台连接。"
            await event.send(event.plain_result(message))
            return False

    @filter.command("肥鱼", alias={"bluefish"})
    async def render(self, event: AstrMessageEvent):
        """Generate a PNG from a caption and optional material/style arguments."""
        event.should_call_llm(False)
        arguments = command_arguments(event)
        if not arguments:
            yield event.plain_result(HELP)
            return
        try:
            payload = parse_render_arguments(arguments, self.defaults, self._selection(event))
            png = await self.client.render(payload)
        except (ValueError, BlueFishError) as error:
            yield event.plain_result(f"制图失败：{error}\n用法见 /肥鱼帮助。")
            return
        await self._send_image(event, png)

    @filter.command("肥鱼素材", alias={"bluefish_stickers"})
    async def stickers(self, event: AstrMessageEvent):
        """Show numbered thumbnails and remember this participant's delivered page."""
        event.should_call_llm(False)
        try:
            keyword, page = parse_catalog_arguments(command_arguments(event))
            materials = await self.client.stickers()
            matching = [
                item
                for item in materials
                if keyword.casefold()
                in " ".join(
                    [item["id"], item["name"], *item.get("tags", [])]
                ).casefold()
            ]
            if not matching:
                yield event.plain_result("没有找到匹配的素材，试试其他关键词。")
                return
            pages = (len(matching) + PAGE_SIZE - 1) // PAGE_SIZE
            if page > pages:
                raise ValueError(f"只有 {pages} 页，请调整 --page。")
            items = matching[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            sticker_ids = [item["id"] for item in items]
            png = await self.client.preview(sticker_ids)
            lines = [f"肥鱼素材 · 第 {page}/{pages} 页 · 共 {len(matching)} 张"]
            for number, item in enumerate(items, start=1):
                origin = "工坊" if item.get("origin") == "studio" else "档案馆"
                lines.append(f"{number}. {item['name']}（{origin}）")
            lines.append("使用：/肥鱼 1 想说的话")
            lines.append("编号仅对应你在当前聊天中最近收到的一页，30 分钟内有效。")
            if page < pages:
                query = f" {shlex.quote(keyword)}" if keyword else ""
                lines.append(f"下一页：/肥鱼素材{query} --page {page + 1}")
            if await self._send_image(event, png, "\n".join(lines)):
                now = monotonic()
                self.selections = {
                    key: value for key, value in self.selections.items()
                    if value.expires_at > now
                }
                key = (event.unified_msg_origin, event.get_sender_id())
                self.selections[key] = CatalogSelection(
                    sticker_ids=sticker_ids, expires_at=now + SELECTION_TTL_SECONDS
                )
        except (ValueError, BlueFishError) as error:
            yield event.plain_result(f"素材查询失败：{error}")

    @filter.command("肥鱼字体", alias={"bluefish_fonts"})
    async def fonts(self, event: AstrMessageEvent):
        """List the service's font names and IDs for the --font option."""
        event.should_call_llm(False)
        try:
            fonts = await self.client.fonts()
            lines = ["肥鱼字体"]
            lines.extend(f"{font['name']}：{font['id']}" for font in fonts)
            lines.append("使用：/肥鱼 文案 --font 字体ID")
            yield event.plain_result("\n".join(lines))
        except BlueFishError as error:
            yield event.plain_result(f"字体查询失败：{error}")

    @filter.command("肥鱼帮助", alias={"bluefish_help"})
    async def help(self, event: AstrMessageEvent):
        """Show command syntax and examples without contacting the service."""
        event.should_call_llm(False)
        yield event.plain_result(HELP)
