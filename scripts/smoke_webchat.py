"""Exercise installed plugin commands through a real AstrBot WebChat session."""

import argparse
import asyncio
import json
import re
import struct
import time
from pathlib import Path

import aiohttp


def scenarios() -> list[
    tuple[str, str, tuple[str, ...], tuple[int, int | None] | None]
]:
    """Return ordered commands with expected text and optional PNG dimensions.

    A missing dimension tuple means no image is expected. A ``None`` height
    denotes a catalog preview whose height depends on the number of results.
    """
    return [
        ("help", "/肥鱼帮助", ("肥鱼工坊", "--background", "--layout"), None),
        ("empty-command", "/肥鱼", ("肥鱼工坊", "/肥鱼素材"), None),
        (
            "number-without-catalog",
            "/肥鱼 1 今天也要开心呀",
            ("请先发送 /肥鱼素材",),
            None,
        ),
        ("quoted-numeric-caption", '/肥鱼 "3"', (), (1024, 1024)),
        (
            "numeric-after-separator",
            "/肥鱼 -- 3 今天也要开心呀",
            (),
            (1024, 1024),
        ),
        (
            "search",
            "/肥鱼素材 开心",
            ("肥鱼素材", "第 1/", "使用：/肥鱼 1 ", "30 分钟"),
            (960, None),
        ),
        ("select-search-one", "/肥鱼 1 今天也要开心呀", (), (1024, 1024)),
        (
            "number-without-caption",
            "/肥鱼 1",
            ("请在素材编号后填写文案",),
            None,
        ),
        (
            "number-out-of-range",
            "/肥鱼 99 今天也要开心呀",
            ("素材编号超出范围",),
            None,
        ),
        (
            "page-two",
            "/肥鱼素材 --page 2",
            ("第 2/", "使用：/肥鱼 1 "),
            (960, None),
        ),
        (
            "select-page-two-three",
            "/肥鱼 3 轮到第三条鱼出场啦 --background white --layout overlay",
            (),
            (1024, 1024),
        ),
        ("fonts", "/肥鱼字体", ("maoken", "zcool-kuaile", "ma-shan-zheng"), None),
        ("default", "/肥鱼 好耶！", (), (1024, 1024)),
        (
            "full-style",
            '/肥鱼 "今天也要开心呀" --id studio-design-cheer '
            "--font zcool-kuaile --color #e2547b --background white --layout overlay",
            (),
            (1024, 1024),
        ),
        (
            "alias-multiline",
            '/bluefish "第一行\\n第二行" --id studio-design-cheer --font ma-shan-zheng',
            (),
            (1024, 1024),
        ),
        ("option-separator", "/肥鱼 -- --今天开心", (), (1024, 1024)),
        (
            "unknown-material",
            "/肥鱼 开心 --id smoke-missing",
            ("未找到指定素材",),
            None,
        ),
        ("invalid-color", "/肥鱼 开心 --color pink", ("#RRGGBB",), None),
        ("unknown-option", "/肥鱼 开心 --rotate 10", ("不支持参数 --rotate",), None),
        ("missing-option-value", "/肥鱼 开心 --font", ("需要填写一个值",), None),
        ("unclosed-quote", '/肥鱼 "开心', ("引号未闭合",), None),
        ("empty-caption", '/肥鱼 ""', ("请填写文案",), None),
        ("invalid-page", "/肥鱼素材 --page 0", ("必须是正整数",), None),
        ("long-caption", "/肥鱼 " + "鱼" * 201, ("200",), None),
    ]


def require(condition: bool, message: str) -> None:
    """Raise a readable verification failure without exposing HTTP credentials."""
    if not condition:
        raise AssertionError(message)


async def json_request(
    client: aiohttp.ClientSession, method: str, path: str, **kwargs
) -> dict:
    """Read one successful AstrBot envelope, omitting response bodies on failure."""
    async with client.request(method, path, **kwargs) as response:
        require(response.status == 200, f"{path}: HTTP {response.status}")
        envelope = await response.json()
    require(envelope.get("status") == "ok", f"{path}: AstrBot returned an error")
    return envelope["data"]


async def send_command(
    client: aiohttp.ClientSession, session_id: str, command: str
) -> list[dict]:
    """Collect the genuine WebChat SSE events until terminal output or clean EOF."""
    events = []
    async with client.post(
        "/api/chat/send", json={"session_id": session_id, "message": command}
    ) as response:
        require(response.status == 200, f"WebChat HTTP {response.status}")
        require(response.content_type == "text/event-stream", "Expected WebChat SSE")
        # AstrBot emits one JSON data line per event and comment-only heartbeats.
        async for raw_line in response.content:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line.removeprefix("data:").strip())
            events.append(event)
            if event.get("type") == "end":
                break
    require(bool(events), "WebChat returned no SSE events")
    require(not any(e.get("type") == "error" for e in events), "WebChat run failed")
    return events


async def check_scenario(
    client: aiohttp.ClientSession,
    session_id: str,
    case: tuple[str, str, tuple[str, ...], tuple[int, int | None] | None],
    output: Path,
) -> dict:
    """Verify bot output and download real image attachments to the evidence folder."""
    name, command, expected_text, expected_dimensions = case
    expected_images = int(expected_dimensions is not None)
    started = time.monotonic()
    result = {
        "name": name,
        "command": command,
        "session_id": session_id,
        "passed": False,
    }
    try:
        events = await send_command(client, session_id, command)
        result["events"] = [event.get("type") for event in events]
        text = "\n".join(
            str(event.get("data", ""))
            for event in events
            if event.get("type") == "plain"
        )
        result["text"] = text
        for expected in expected_text:
            require(expected in text, f"Missing reply text: {expected}")
        images = [event for event in events if event.get("type") == "image"]
        attachments = [
            event["data"]["id"]
            for event in events
            if event.get("type") == "attachment_saved"
            and event.get("data", {}).get("type") == "image"
        ]
        require(
            len(images) == expected_images,
            f"Expected {expected_images} image events, got {len(images)}",
        )
        require(
            len(attachments) == expected_images,
            f"Expected {expected_images} saved image attachments",
        )
        if expected_images and not expected_text:
            require(not text.strip(), "Image command unexpectedly replied with text")
        if expected_dimensions and expected_dimensions[1] is None:
            entries = re.findall(r"^([0-9]+)\.\s+(\S[^\n]*)", text, re.MULTILINE)
            require(1 <= len(entries) <= 8, "Catalog must contain 1–8 numbered names")
            require(
                [int(number) for number, _ in entries] == list(range(1, len(entries) + 1)),
                "Catalog numbers must start at 1 and follow display order",
            )
            result["catalog_entries"] = [name for _, name in entries]
        result["images"] = []
        for index, attachment_id in enumerate(attachments, start=1):
            async with client.get(
                "/api/chat/get_attachment", params={"attachment_id": attachment_id}
            ) as response:
                require(response.status == 200, f"Attachment HTTP {response.status}")
                require(
                    response.content_type == "image/png",
                    "Attachment MIME is not image/png",
                )
                png = await response.read()
            require(
                len(png) >= 24 and png.startswith(b"\x89PNG\r\n\x1a\n"),
                "Invalid PNG signature",
            )
            require(png[12:16] == b"IHDR", "PNG has no leading IHDR")
            dimensions = struct.unpack(">II", png[16:24])
            expected_width, expected_height = expected_dimensions
            if expected_height is None:
                # The preview uses two columns with fixed card rows and page margins.
                rows = (len(result["catalog_entries"]) + 1) // 2
                expected_height = 200 + rows * 484
            require(dimensions[0] == expected_width, f"Unexpected PNG width: {dimensions}")
            require(
                dimensions[1] == expected_height,
                f"Unexpected PNG height: {dimensions}, expected {expected_height}",
            )
            filename = f"{name}-{index}.png"
            (output / filename).write_bytes(png)
            result["images"].append(
                {
                    "file": filename,
                    "bytes": len(png),
                    "width": dimensions[0],
                    "height": dimensions[1],
                }
            )
        result["passed"] = True
    except (AssertionError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        # Assertion text is ours; HTTP/library exception details may contain URLs.
        result["error"] = (
            str(error) if isinstance(error, AssertionError) else type(error).__name__
        )
    result["seconds"] = round(time.monotonic() - started, 2)
    detail = f": {result['error']}" if "error" in result else ""
    print(f"{'PASS' if result['passed'] else 'FAIL'} {name}{detail}", flush=True)
    return result


async def run(args: argparse.Namespace) -> bool:
    """Verify commands and catalog session isolation, then write a public-safe report."""
    credentials = json.loads(args.credentials.read_text(encoding="utf-8-sig"))
    args.output.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession(
        base_url=args.base_url.rstrip("/"), timeout=aiohttp.ClientTimeout(total=45)
    ) as client:
        login = await json_request(
            client,
            "POST",
            "/api/auth/login",
            json={
                "username": credentials["username"],
                "password": credentials["password"],
            },
        )
        client.headers["Authorization"] = f"Bearer {login['token']}"
        session = await json_request(client, "GET", "/api/chat/new_session")
        session_id = session["session_id"]
        print(f"WebChat session: {session_id}", flush=True)
        results = []
        for case in scenarios():
            results.append(await check_scenario(client, session_id, case, args.output))
        # A fresh chat must not inherit this user's successful catalog selection.
        other_session = await json_request(client, "GET", "/api/chat/new_session")
        isolated_session_id = other_session["session_id"]
        results.append(
            await check_scenario(
                client,
                isolated_session_id,
                (
                    "other-chat-has-no-catalog",
                    "/肥鱼 1 不能沿用另一个聊天的目录",
                    ("请先发送 /肥鱼素材",),
                    None,
                ),
                args.output,
            )
        )
    passed = sum(result["passed"] for result in results)
    report = {
        "base_url": args.base_url,
        "session_id": session_id,
        "isolated_session_id": isolated_session_id,
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{passed}/{len(results)} passed; report: {args.output / 'report.json'}")
    return passed == len(results)


def main() -> None:
    """Parse the standalone smoke-test CLI and return a failing status when needed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:6185")
    parser.add_argument(
        "--credentials",
        required=True,
        type=Path,
        help="Private JSON file containing username and password",
    )
    parser.add_argument("--output", default=Path(".preview/webchat"), type=Path)
    args = parser.parse_args()
    try:
        passed = asyncio.run(run(args))
    except (
        AssertionError,
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
        KeyError,
        OSError,
    ) as error:
        message = str(error) if isinstance(error, AssertionError) else type(error).__name__
        parser.exit(1, f"WebChat smoke setup failed: {message}\n")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
