"""AstrBot commands for the Blue Fish Studio image rendering service."""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .client import BlueFishClient, BlueFishError
from .command import parse_catalog_arguments, parse_render_arguments

# Keep chat responses short while retaining copyable, stable material IDs.
PAGE_SIZE = 8
HELP = """肥鱼工坊
/肥鱼 文案 [--id 底图ID] [--font 字体ID] [--color #RRGGBB]
  [--background transparent|white] [--layout caption|overlay]
/肥鱼素材 [关键词] [--page 页码]
/肥鱼字体
/肥鱼帮助

例：/肥鱼 今天也要开心呀 --id studio-design-cheer
例：/肥鱼 "好耶！\\n今天也开心" --font zcool-kuaile
caption 为留白配字，overlay 为叠加文字。
省略样式参数时使用插件配置的默认值。"""


def command_arguments(event: AstrMessageEvent) -> str:
    """Read the original command tail, retaining quoted whitespace and newlines."""
    parts = event.message_str.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


class BlueFishPlugin(Star):
    """Render captioned stickers and browse the service's material catalog.

    Attributes:
        client: HTTP client for the configured rendering service.
        defaults: API parameter values used when command options are omitted.
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        """Read the framework-managed connection and default style settings."""
        super().__init__(context)
        self.client = BlueFishClient(
            config.get("api_url", "http://127.0.0.1:8787"),
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

    @filter.command("肥鱼", alias={"bluefish"})
    async def render(self, event: AstrMessageEvent):
        """Generate a PNG from a caption and optional material/style arguments."""
        event.should_call_llm(False)
        arguments = command_arguments(event)
        if not arguments:
            yield event.plain_result(HELP)
            return
        try:
            payload = parse_render_arguments(arguments, self.defaults)
            png = await self.client.render(payload)
        except (ValueError, BlueFishError) as error:
            yield event.plain_result(f"制图失败：{error}\n用法见 /肥鱼帮助。")
            return
        # Await delivery here so platform errors can be reported to the requester.
        # Yielding the image delegates delivery to a later framework stage.
        try:
            await event.send(event.chain_result([Image.fromBytes(png)]))
        except Exception as error:
            logger.warning(f"Blue Fish image delivery failed: {type(error).__name__}")
            if "rich media transfer failed" in str(error):
                message = "图片已生成，但 QQ 上传失败。请稍后重试或联系管理员检查 NapCat。"
            else:
                message = "图片已生成，但发送失败。请稍后重试或联系管理员检查消息平台连接。"
            yield event.plain_result(message)

    @filter.command("肥鱼素材", alias={"bluefish_stickers"})
    async def stickers(self, event: AstrMessageEvent):
        """Search materials by name, ID, or tag, returning eight items per page."""
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
            lines = [f"肥鱼素材 · 第 {page}/{pages} 页 · 共 {len(matching)} 张"]
            for item in matching[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
                origin = "工坊" if item.get("origin") == "studio" else "档案馆"
                lines.append(f"{item['name']}（{origin}）\n  {item['id']}")
            lines.append("使用：/肥鱼 文案 --id 底图ID")
            yield event.plain_result("\n".join(lines))
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
