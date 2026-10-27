"""Validate meme captions and prepare compact reply-based creation requests."""

import json
import random
import unicodedata


def validate_caption(text: str) -> str:
    """Normalize a caption and enforce the image service's compact text layout.

    Args:
        text: Caption with real or escaped newline characters.

    Returns:
        Trimmed caption containing one or two lines of at most 16 display columns.

    Raises:
        ValueError: The caption has an invalid line count, excessive line width,
            or graphical symbols unsupported by the caption fonts.
    """
    caption = text.replace(r"\n", "\n").strip()
    lines = caption.splitlines()
    # The fixed caption font fits about eight full-width characters per line.
    too_wide = any(
        sum(
            2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
            for char in line
        ) > 16
        for line in lines
    )
    has_symbols = any(
        unicodedata.category(char) in ("So", "Sk")
        or char in ("\ufe0f", "\u20e3", "\u200d")
        for char in caption
    )
    problems = []
    if len(lines) not in (1, 2):
        problems.append(f"当前文案有 {len(lines)} 行，最多 2 行，text 里最多一个换行符")
    if too_wide:
        problems.append("单行过长，每行控制在八个汉字以内，英文按半宽计")
    if has_symbols:
        problems.append("文案包含 emoji 或特殊图形符号，请改为中文、英文或标点")
    if problems:
        raise ValueError("；".join(problems))
    return caption


def select_reply_candidates(
    materials: list[dict], user_text: str, reply_text: str, limit: int = 8
) -> list[dict]:
    """Choose relevant artwork and a random remainder for a reply creation prompt.

    Args:
        materials: Catalog entries containing IDs, names and optional string tags.
        user_text: Current user message used to identify relevant catalog terms.
        reply_text: Complete bot reply used alongside the user message.
        limit: Maximum number of distinct artwork IDs to return.

    Returns:
        Original catalog entries, with at most half the slots reserved for the
        highest relevance scores and remaining slots sampled from unselected art.
        Each matching name and each matching tag of at least two characters
        scores once in the combined conversation text, ignoring case.
    """
    if limit <= 0:
        return []
    unique_materials = {}
    for material in materials:
        unique_materials.setdefault(material["id"], material)
    context = f"{user_text}\n{reply_text}".casefold()
    ranked = []
    for material in unique_materials.values():
        terms = [
            material["name"],
            *(tag for tag in material.get("tags", []) if len(tag) >= 2),
        ]
        score = sum(bool(term) and term.casefold() in context for term in terms)
        if score:
            ranked.append((score, material))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    preferred = [material for _, material in ranked[: limit // 2]]
    selected_ids = {material["id"] for material in preferred}
    remaining = [
        material
        for material in unique_materials.values()
        if material["id"] not in selected_ids
    ]
    # Random slots can select any remaining artwork, including further matches.
    return preferred + random.sample(
        remaining, min(limit - len(preferred), len(remaining))
    )


def parse_reply_creation(content: str, candidates: list[dict]) -> tuple[str, str]:
    """Parse one creation JSON object and validate its selected artwork and caption.

    Args:
        content: JSON text, optionally wrapped in one plain or JSON Markdown fence.
        candidates: Catalog entries whose IDs are available to this request.

    Returns:
        Selected artwork ID and the normalized, validated caption.

    Raises:
        ValueError: The fence, JSON, required string fields, selected ID or caption
            does not satisfy the creation request.
    """
    source = content.strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if (
            len(lines) < 3
            or lines[0].lower() not in ("```", "```json")
            or lines[-1] != "```"
        ):
            raise ValueError("表情创作结果的 Markdown 代码围栏格式不正确。")
        source = "\n".join(lines[1:-1])
    try:
        creation = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError(
            "表情创作结果不是有效 JSON，请返回包含 sticker_id 和 text 的 JSON 对象。"
        ) from error
    if not isinstance(creation, dict) or not all(
        isinstance(creation.get(key), str) for key in ("sticker_id", "text")
    ):
        raise ValueError("表情创作结果必须包含字符串 sticker_id 和 text。")
    sticker_id = creation["sticker_id"]
    if sticker_id not in {item["id"] for item in candidates}:
        raise ValueError("表情创作结果的 sticker_id 不在候选素材中。")
    return sticker_id, validate_caption(creation["text"])
