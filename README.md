# 肥鱼工坊 · AstrBot 插件

在聊天中输入文案和样式参数，机器人调用 [肥鱼工坊](https://github.com/Eynnzerr/blue-fish-studio) 图片服务，回复一张 1024 × 1024 PNG 表情包。支持查询底图、选择字体、修改文字颜色，以及透明或白色画布、留白或叠加排版。

```text
/肥鱼 今天也要开心呀
/肥鱼 "肥鱼上线\n快乐加倍" --id studio-design-cheer --font zcool-kuaile
```

## 安装与配置

运行环境：Python 3.10+、AstrBot 4.28.2+，以及可访问的肥鱼工坊图片服务。

首次安装推荐使用 AstrBot WebUI 的本地 ZIP 上传功能。将本目录内容打包，确保 ZIP 根目录包含 `main.py`、`metadata.yaml`、`_conf_schema.json` 和 `requirements.txt`。也可以将完整插件目录复制到 `AstrBot/data/plugins/astrbot_plugin_blue_fish`，在 AstrBot 使用的 Python 环境中安装 `requirements.txt`，然后重启或重载插件。

在插件配置页面填写服务地址与 API Key：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_url` | `http://127.0.0.1:8787` | 图片服务根地址，不包含 `/api/v1` |
| `api_key` | 空，需填写 | 与服务端 `API_KEY` 相同；配置页面遮罩显示 |
| `request_timeout` | `20` | HTTP 请求超时，单位为秒 |
| `default_sticker_id` | `studio-design-cheer` | 未指定 `--id` 时使用的底图 |
| `default_font` | `maoken` | 默认字体 ID |
| `default_color` | `#3565ae` | 默认文字颜色 |
| `default_background` | `transparent` | 默认画布背景 |
| `default_layout` | `caption` | 默认排版方式 |

启动本机图片服务时，在 `blue-fish-studio` 仓库目录执行：

```sh
npm ci
API_KEY='replace-with-your-key' npm run api
```

如使用服务仓库提供的 Docker Compose：

```sh
cp .env.api.example .env.api
# 编辑 .env.api，填写 API_KEY。
docker compose --env-file .env.api up -d --build
```

服务的 Compose 默认映射到主机 `127.0.0.1:8787`，供在主机直接运行的 AstrBot 访问。AstrBot 在容器内运行时，`127.0.0.1` 指向 AstrBot 容器自身；将两个容器加入同一个 Docker 网络后，可使用图片服务的网络名称，例如 `http://api:8787`。配置中的地址须能从 AstrBot 所在环境访问。

服务端运行要求与完整部署说明见 [图片服务 API 文档](https://github.com/Eynnzerr/blue-fish-studio/blob/main/docs/api.md)。

## 聊天命令

示例使用默认唤醒前缀 `/`；修改过 AstrBot 唤醒前缀时，按实际配置输入。

| 命令 | 用途 | 英文别名 |
| --- | --- | --- |
| `/肥鱼 <文案> [选项]` | 生成并回复 PNG 图片 | `/bluefish` |
| `/肥鱼素材 [关键词] [--page 页码]` | 查找底图 ID，每页 8 条 | `/bluefish_stickers` |
| `/肥鱼字体` | 查询可用字体 ID 与名称 | `/bluefish_fonts` |
| `/肥鱼帮助` | 查看命令说明 | `/bluefish_help` |

生成选项：

| 选项 | 取值 |
| --- | --- |
| `--id` | `/肥鱼素材` 返回的底图 ID |
| `--font` | `/肥鱼字体` 返回的字体 ID |
| `--color` | `#RRGGBB`，如 `#e86ca4` |
| `--background` | `transparent` 透明画布，或 `white` 白色画布 |
| `--layout` | `caption` 留白配字，或 `overlay` 叠加文字 |

未填写的选项使用插件配置中的默认值。文案最多 200 个字符；包含空格时可以加引号，双引号内的字面 `\n` 表示换行。长文案可手动分行，服务不自动缩小字号。

```text
/肥鱼素材 开心
/肥鱼素材 --page 2
/肥鱼字体
/肥鱼 "今天也要 开心呀" --color #e86ca4 --background white
/肥鱼 "第一行\n第二行" --font ma-shan-zheng --layout overlay
```

文案以 `--` 开头时，在文案前加入独立的 `--`，后面的内容全部作为文案；样式选项放在分隔符前：

```text
/肥鱼 --color #e86ca4 -- --今天也要开心！
```

底图、字体、颜色和排版参数由图片服务校验，输入无效时机器人回复中文错误提示。服务鉴权失败、忙碌或请求超时，也会通过文字说明。

如果图片已经生成，但 QQ 的图片上传失败，插件会回复“图片已生成，但 QQ 上传失败”。管理员可检查 NapCat 日志中的 `rich media transfer failed`；这一步发生在 QQ 媒体发送阶段。

## 本地联调

已在 AstrBot 4.28.2 的真实 WebChat 中通过 17 个场景，覆盖帮助、素材搜索与分页、字体列表、样式参数、英文别名、多行文案和错误输入。4 张机器人回复的 PNG 均为 1024 × 1024，与相同参数直接调用图片服务的结果逐字节一致；OneBot 适配器的图片消息序列化也已验证。

复用仓库中的联调脚本时，先安装并配置插件，然后使用 AstrBot 的 Python 环境运行：

```sh
python scripts/smoke_webchat.py \
  --base-url http://127.0.0.1:6185 \
  --credentials /path/to/private-webui-credentials.json
```

凭据文件包含 `username` 和 `password` 两个字段，使用本地 WebUI 的登录账号。脚本会新建 WebChat 会话、发送测试命令，并将回复图片和报告写入已忽略的 `.preview/webchat/`；终端输出会话 ID，便于在 WebUI 查看结果。测试使用默认样式配置和 `studio-design-cheer` 素材。

## 相关项目

- [肥鱼工坊](https://github.com/Eynnzerr/blue-fish-studio)：网页编辑器、图片渲染服务、素材与字体。
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：插件运行框架与消息平台适配。

素材与字体的来源和授权范围见肥鱼工坊的 [第三方来源与许可](https://github.com/Eynnzerr/blue-fish-studio/blob/main/THIRD_PARTY_NOTICES.md)。
