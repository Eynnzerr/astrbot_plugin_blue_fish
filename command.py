"""Parse the small command-line interface exposed to chat users."""

import re
import shlex


def _split_arguments(
    arguments: str, supported: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Separate positional words and named values without exiting the bot process."""
    try:
        tokens = shlex.split(arguments)
    except ValueError as error:
        raise ValueError("引号未闭合，请检查文案两侧的引号。") from error

    words: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            words.extend(tokens[index + 1 :])
            break
        if not token.startswith("--"):
            words.append(token)
            index += 1
            continue

        name, separator, value = token.partition("=")
        if name not in supported:
            raise ValueError(f"不支持参数 {name}，发送 /肥鱼帮助 查看用法。")
        if not separator:
            index += 1
            if index >= len(tokens) or tokens[index].startswith("--"):
                raise ValueError(f"参数 {name} 后需要填写一个值。")
            value = tokens[index]
        if not value:
            raise ValueError(f"参数 {name} 后需要填写一个值。")
        options[supported[name]] = value
        index += 1
    return words, options


def parse_render_arguments(
    arguments: str, defaults: dict, selection: list[str] | None = None
) -> dict:
    """Build an API payload, resolving an optional leading catalog number.

    ``selection`` contains sticker IDs in the latest catalog's display order.
    An unquoted leading one- or two-digit number selects an entry unless
    ``--id`` is supplied. Quoted captions and text after ``--`` remain literal.
    Invalid numbers, missing catalogs, and empty captions raise ``ValueError``.
    """
    supported = {
        "--id": "stickerId",
        "--font": "font",
        "--color": "color",
        "--background": "background",
        "--layout": "layout",
    }
    words, options = _split_arguments(arguments, supported)
    # Inspect the raw prefix because shlex removes quoting around literal numbers.
    number = re.match(r"[ \t\r\n]*([0-9]{1,2})(?=[ \t\r\n]|$)", arguments)
    if number and "stickerId" not in options:
        index = int(number.group(1))
        if index == 0:
            raise ValueError("素材编号从 1 开始，请发送 /肥鱼素材 查看可选底图。")
        if not selection:
            raise ValueError("请先发送 /肥鱼素材 查看底图，再用编号和文案制作表情包。")
        if index > len(selection):
            raise ValueError(f"素材编号超出范围，请选择当前目录中的 1–{len(selection)}。")
        options["stickerId"] = selection[index - 1]
        words = words[1:]
        if not " ".join(words).strip():
            raise ValueError(f"请在素材编号后填写文案，例如：/肥鱼 {index} 今天也要开心！")
    # Shell quoting preserves a literal backslash-n inside quoted captions.
    text = " ".join(words).replace(r"\n", "\n")
    if not text.strip():
        raise ValueError("请填写文案，例如：/肥鱼 今天也要开心！")
    payload = {key: defaults[key] for key in supported.values() if key in defaults}
    payload.update(options)
    payload["text"] = text
    return payload


def parse_catalog_arguments(arguments: str) -> tuple[str, int]:
    """Read an optional search phrase and a positive catalog page number."""
    words, options = _split_arguments(arguments, {"--page": "page"})
    value = options.get("page", "1")
    try:
        page = int(value)
    except ValueError as error:
        raise ValueError("--page 必须是正整数，例如 --page 2。") from error
    if page < 1:
        raise ValueError("--page 必须是正整数，例如 --page 2。")
    return " ".join(words).strip(), page
