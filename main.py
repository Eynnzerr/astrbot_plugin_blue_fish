"""AstrBot commands and agent tools for the Blue Fish image rendering service."""

import asyncio
import json
import random
import shlex
from dataclasses import dataclass
from math import ceil
from time import monotonic

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, ResultContentType, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star

from .client import BlueFishClient, BlueFishError
from .command import parse_catalog_arguments, parse_render_arguments
from .creation import parse_reply_creation, select_reply_candidates, validate_caption

# Match the preview API's maximum number of numbered thumbnails.
PAGE_SIZE = 8
# Keep displayed number mappings available for thirty minutes in this chat.
SELECTION_TTL_SECONDS = 30 * 60
# Bound each isolated caption request without changing the shared chat provider.
CREATION_TIMEOUT_SECONDS = 30
# Keep automatic captions independent of the Agent's tool and persona instructions.
CREATION_SYSTEM_PROMPT = """你为肥鱼表情包选底图并原创短文案。
根据这次用户消息和机器人回复，从候选中选择一张贴切的底图，只表达一个笑点。
只返回 JSON 对象：{"sticker_id":"候选中的完整ID","text":"短文案"}。
text 最多两行，最多一个换行符；每行最多16个显示列，汉字占2列、英文占1列。
使用中文、英文和标点，不加emoji或其他图形符号，不附加解释或代码围栏。"""
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
    """Render captioned stickers through commands and enabled chat agent tools.

    Attributes:
        client: HTTP client for the configured rendering service.
        defaults: API parameter values used when command options are omitted.
        selections: Last delivered page for each platform/chat and sender pair.
        llm_enabled: Master switch for automatic creation and agent meme tools.
        llm_group_whitelist: Allowed group IDs; an empty set permits all groups.
        llm_private_whitelist: Accounts allowed to use the tools in private chats.
        llm_cooldown_seconds: Tool path's minimum interval since the last attempt.
        llm_last_attempts: Send-attempt start times keyed by adapter ID and chat.
        llm_in_flight: Adapter/chat pairs with an unfinished agent send attempt.
        reply_meme_probability: Chance from zero to one of illustrating a full reply.
        reply_meme_cooldown_seconds: This path's minimum interval between attempts.
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
        self.llm_enabled = config.get("llm_enabled", True)
        self.llm_group_whitelist = set(config.get("llm_group_whitelist", []))
        self.llm_private_whitelist = set(config.get("llm_private_whitelist", []))
        self.llm_cooldown_seconds = max(0, int(config.get("llm_cooldown_seconds", 300)))
        self.llm_last_attempts: dict[tuple[str, str], float] = {}
        self.llm_in_flight: set[tuple[str, str]] = set()
        self.reply_meme_probability = min(
            1.0, max(0.0, float(config.get("reply_meme_probability", 10.0)) / 100)
        )
        self.reply_meme_cooldown_seconds = max(
            0, int(config.get("reply_meme_cooldown_seconds", 0))
        )

    def _llm_chat_key(self, event: AstrMessageEvent) -> tuple[str, str]:
        """Identify a group or private chat across member session isolation."""
        group_id = event.get_group_id()
        chat = f"group:{group_id}" if group_id else f"private:{event.get_sender_id()}"
        return event.get_platform_id(), chat

    def _llm_unavailable_reason(
        self, event: AstrMessageEvent, cooldown_seconds: int | None = None
    ) -> str | None:
        """Check shared chat limits using the calling path's cooldown interval."""
        if not self.llm_enabled:
            return "肥鱼自主创作已关闭。"
        group_id = event.get_group_id()
        if group_id:
            if self.llm_group_whitelist and group_id not in self.llm_group_whitelist:
                return "当前群未开启肥鱼自主创作。"
        elif event.get_sender_id() not in self.llm_private_whitelist:
            return "当前私聊未开启肥鱼创作。"
        if event.get_extra("blue_fish_meme_attempted", False):
            return "本轮已尝试发送一张表情包，制图流程结束。"
        chat_key = self._llm_chat_key(event)
        if chat_key in self.llm_in_flight:
            return "当前聊天已有表情包正在制作，跳过本次。"
        last_attempt = self.llm_last_attempts.get(chat_key)
        if last_attempt is not None:
            cooldown = self.llm_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
            remaining = ceil(cooldown - (monotonic() - last_attempt))
            if remaining > 0:
                return f"当前聊天处于制图冷却中，{remaining} 秒后可再次尝试。"
        return None

    def _reply_aborted(self, event: AstrMessageEvent) -> bool:
        """Identify interrupted replies before starting or delivering an illustration."""
        return bool(
            event.is_stopped()
            or event.get_extra("agent_stop_requested", False)
            or event.get_extra("agent_user_aborted", False)
        )

    @filter.on_llm_response(priority=-100)
    async def remember_final_reply(self, event: AstrMessageEvent, response: LLMResponse):
        """Remember the completed assistant reply, excluding tool rounds and chunks."""
        if (
            response.is_chunk
            or response.role != "assistant"
            or response.tools_call_name
            or not response.completion_text.strip()
            or self._reply_aborted(event)
        ):
            return
        event.set_extra("blue_fish_final_reply", response.completion_text.strip())

    @filter.after_message_sent(priority=-100)
    async def illustrate_sent_reply(self, event: AstrMessageEvent):
        """Consider a meme after the normal reply's complete delivery stage."""
        result = event.get_result()
        if result is not None and result.is_llm_result():
            await self._maybe_send_reply_meme(event)

    @filter.on_decorating_result(priority=-100)
    async def illustrate_streaming_reply(self, event: AstrMessageEvent):
        """Handle the streaming-finish path, which skips the ordinary sent hook."""
        result = event.get_result()
        if result is not None and result.result_content_type == ResultContentType.STREAMING_FINISH:
            await self._maybe_send_reply_meme(event)

    async def _maybe_send_reply_meme(self, event: AstrMessageEvent):
        """Draw once per completed reply and send at most one contextual meme."""
        reply = event.get_extra("blue_fish_final_reply", "")
        if (
            not reply
            or self._reply_aborted(event)
            or event.get_extra("blue_fish_probability_checked", False)
        ):
            return
        event.set_extra("blue_fish_probability_checked", True)
        if (
            self.reply_meme_probability <= 0
            or self._llm_unavailable_reason(event, self.reply_meme_cooldown_seconds)
            or random.random() >= self.reply_meme_probability
        ):
            return
        chat_key = self._llm_chat_key(event)
        # Reserve before catalog/model I/O so another reply cannot create a second meme.
        event.set_extra("blue_fish_meme_attempted", True)
        self.llm_in_flight.add(chat_key)
        self.llm_last_attempts[chat_key] = monotonic()
        try:
            materials = await self.client.stickers()
            candidates = select_reply_candidates(materials, event.message_str, reply)
            if not candidates:
                return
            sticker_id, caption = await self._generate_reply_creation(event, reply, candidates)
            if self._reply_aborted(event):
                return
            png = await self.client.render(
                {**self.defaults, "stickerId": sticker_id, "text": caption}
            )
            if not self._reply_aborted(event) and await self._send_image(
                event, png, report_failure=False
            ):
                logger.info(f"Blue Fish reply meme sent: {sticker_id}")
        except Exception as error:
            logger.warning(f"Blue Fish reply meme failed: {type(error).__name__}")
        finally:
            self.llm_in_flight.discard(chat_key)

    async def _generate_reply_creation(
        self, event: AstrMessageEvent, reply: str, candidates: list[dict]
    ) -> tuple[str, str]:
        """Ask the current chat model for a validated ID/caption, with one rewrite."""
        provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)
        prompt = json.dumps(
            {
                "用户消息": event.message_str,
                "机器人回复": reply,
                "候选素材": [
                    {"id": item["id"], "name": item["name"], "tags": item.get("tags", [])}
                    for item in candidates
                ],
            },
            ensure_ascii=False,
        )
        for attempt in range(2):
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=CREATION_SYSTEM_PROMPT,
                    tools=None,
                    contexts=[],
                    request_max_retries=1,
                ),
                timeout=CREATION_TIMEOUT_SECONDS,
            )
            try:
                return parse_reply_creation(response.completion_text, candidates)
            except ValueError as error:
                if attempt:
                    raise
                prompt += (
                    f"\n上次输出：{response.completion_text}\n校验问题：{error}。"
                    "请改写为符合要求的JSON，保留一个笑点，最多两行纯文字。"
                )
        raise ValueError("未得到可用的表情包文案。")

    @filter.llm_tool(name="search_blue_fish_stickers")
    async def search_blue_fish_stickers(
        self, event: AstrMessageEvent, query: str = ""
    ) -> str:
        """Search existing artwork for a meme in an enabled group or private chat.

        Use when a meme would suit the conversation, including spontaneous
        celebrations or jokes. Returns up to eight IDs, names and tags for
        selection; the catalog stays inside the tool result. An empty query
        browses featured artwork. Stop meme creation on skipped or failed.

        Args:
            query(string): Emotion, scene or action keywords separated by spaces.
        """
        reason = self._llm_unavailable_reason(event)
        if reason:
            return json.dumps({"status": "skipped", "reason": reason}, ensure_ascii=False)
        try:
            materials = await self.client.stickers()
        except BlueFishError as error:
            return json.dumps(
                {"status": "failed", "reason": str(error)}, ensure_ascii=False
            )
        keywords = query.casefold().split()
        ranked = []
        for item in materials:
            searchable = " ".join(
                [item["id"], item["name"], *item.get("tags", [])]
            ).casefold()
            score = sum(keyword in searchable for keyword in keywords)
            if not keywords or score:
                ranked.append((score, item))
        ranked.sort(
            key=lambda match: (match[0], match[1].get("featured", False)), reverse=True
        )
        return json.dumps(
            {
                "status": "ok",
                "total": len(ranked),
                "stickers": [
                    {"id": item["id"], "name": item["name"], "tags": item.get("tags", [])}
                    for _, item in ranked[:PAGE_SIZE]
                ],
            },
            ensure_ascii=False,
        )

    @filter.llm_tool(name="send_blue_fish_meme")
    async def send_blue_fish_meme(
        self,
        event: AstrMessageEvent,
        sticker_id: str,
        text: str,
        font: str = "",
        color: str = "",
        background: str = "",
        layout: str = "",
    ) -> str:
        """Create and send one original captioned meme to the current chat.

        Choose a real ID from search_blue_fish_stickers and write a brief caption
        matching the conversation; an explicit image request is optional. Each
        turn permits one send attempt. Invalid captions can be rewritten once
        before rendering. After sent, the image is already in this chat. After
        skipped or failed, finish meme creation for this turn.
        Use one or two short lines of plain text, leaving the character visible.
        Empty style options use plugin defaults.

        Args:
            sticker_id(string): Exact artwork ID returned by the material search.
            text(string): Original plain-text caption with at most one line break, about 6-8 Chinese characters per line; no emoji; at most 200 characters.
            font(string): Optional font ID: maoken, zcool-kuaile, zcool-qingke-huangyou or ma-shan-zheng.
            color(string): Optional text color in #RRGGBB format.
            background(string): Optional transparent or white canvas background.
            layout(string): Optional caption layout or overlay layout.
        """
        reason = self._llm_unavailable_reason(event)
        if reason:
            return json.dumps({"status": "skipped", "reason": reason}, ensure_ascii=False)
        try:
            caption = validate_caption(text)
        except ValueError as error:
            retry_allowed = not event.get_extra("blue_fish_meme_caption_retry", False)
            event.set_extra("blue_fish_meme_caption_retry", True)
            if not retry_allowed:
                event.set_extra("blue_fish_meme_attempted", True)
            return json.dumps(
                {
                    "status": "invalid" if retry_allowed else "failed",
                    "reason": str(error) + "。只保留一个笑点的两句短文案。"
                    + ("可修正文案一次后重新调用。" if retry_allowed else "本轮制图结束。"),
                },
                ensure_ascii=False,
            )
        chat_key = self._llm_chat_key(event)
        # Reserve before the first await: parallel tool calls share this chat limit.
        event.set_extra("blue_fish_meme_attempted", True)
        self.llm_in_flight.add(chat_key)
        self.llm_last_attempts[chat_key] = monotonic()
        try:
            payload = {
                **self.defaults,
                "stickerId": sticker_id,
                "text": caption,
            }
            payload.update(
                (key, value)
                for key, value in {
                    "font": font,
                    "color": color,
                    "background": background,
                    "layout": layout,
                }.items()
                if value
            )
            png = await self.client.render(payload)
            if not await self._send_image(event, png, report_failure=False):
                return json.dumps(
                    {"status": "failed", "reason": "表情包发送失败，本轮制图结束。"},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"status": "sent", "sticker_id": sticker_id}, ensure_ascii=False
            )
        except BlueFishError as error:
            return json.dumps(
                {"status": "failed", "reason": str(error)}, ensure_ascii=False
            )
        finally:
            self.llm_in_flight.discard(chat_key)

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
        self,
        event: AstrMessageEvent,
        png: bytes,
        caption: str = "",
        report_failure: bool = True,
    ) -> bool:
        """Send a PNG, optionally reporting adapter failures directly to the chat.

        Automatic creation and agent tools disable direct failure messages.
        Commands retain their chat-facing error messages.
        """
        chain = [Image.fromBytes(png)]
        if caption:
            chain.append(Plain(caption))
        # Await the adapter so failed delivery cannot activate invisible numbers.
        try:
            await event.send(event.chain_result(chain))
            return True
        except Exception as error:
            logger.warning(f"Blue Fish image delivery failed: {type(error).__name__}")
            if not report_failure:
                return False
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
