# QQ 图片上传排障

## Docker Desktop 代理导致图片发送失败

2026-10-09 的 macOS Apple Silicon 联调中，为 QQ 上传地址配置 Docker Desktop 代理绕过规则后，群聊成功收到肥鱼插件生成的 PNG。环境为 AstrBot 4.28.2、NapCat 4.18.33、容器内 QQ 3.2.32-52194（Linux arm64）。

### 现象与定位

图片服务已生成有效 PNG，QQ 文字消息正常，但发送图片时 NapCat 返回：

```text
NodeIKernelMsgService/sendMsg
result: -1
errMsg: "rich media transfer failed"
```

同一个 QQ 账号在官方客户端能向同一群发送同一张图片；直接调用 NapCat 发送容器内的 PNG 仍失败。图片路径、读取权限、缓存写入权限和磁盘空间均正常，重新登录也未恢复。

Docker Desktop 配置了 `http://127.0.0.1:7890` 上游代理。失败的同一时间窗口内，Docker 日志记录 QQ 的 `/cgi-bin/httpconn` 上传请求经过该代理，其中两次返回 `502 Bad Gateway`。

容器未设置 `HTTP_PROXY`、`HTTPS_PROXY` 时，Docker Desktop 的代理仍可能接管出站连接。应同时检查 Desktop 的代理配置与实际请求日志。

### 处理方法

1. 打开 Docker Desktop 的 **Settings → Resources → Proxies**，找到绕过代理的主机与域名列表。在原有列表后追加实际日志中出现的 QQ 上传地址。

   本次使用的地址如下：

   ```text
   htdata3.qq.com,109.244.169.81,111.30.187.192,220.194.81.125,42.81.193.228,42.81.193.229
   ```

   QQ 也会直接向 IP 地址上传，所以仅填写 `*.qq.com` 无法覆盖这些请求。上传节点会变化，其他环境应以自己的日志为准。

2. 保存并应用配置。如果请求仍经过原代理，重启 Docker Desktop，再确认图片服务恢复、NapCat 已登录、AstrBot 的 OneBot 连接正常。

3. 检查上传目标的新请求日志，确认出现以下直连标记：

   ```text
   container via direct connection because matches app settings exclude
   ```

4. 在原群重新发送制图命令，以实际收到 PNG 验证恢复：

   ```text
   /肥鱼 吃白米饭咯 --id studio-design-cheer --font maoken --color #e2547b --background white --layout overlay
   ```

Docker Desktop 的代理设置文件和 HTTP 代理日志在本次 macOS 环境中的位置：

```text
~/Library/Group Containers/group.com.docker/settings-store.json
~/Library/Containers/com.docker.docker/Data/log/host/httpproxy.log
```

设置文件中的对应字段为 `OverrideProxyExclude`。手工修改时先备份，完全退出 Docker Desktop 后修改这一字段，再启动 Docker Desktop。代理请求日志可能包含 QQ 标识和带凭据的查询参数，对外分享时只保留时间、目标主机、路径、代理方式和状态码。

### 参考

- [Docker Desktop 代理设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies)
- [NapCat #684：base64 与文件路径发送图片均失败](https://github.com/NapNeko/NapCatQQ/issues/684)：同名报错的其他案例，可结合官方客户端发送结果进一步排查。
