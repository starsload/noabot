# QQ Personal 接入指南

状态: draft
日期: 2026-03-28

## 目标

让 `nanobot` 通过 `qq_personal` channel 接入一个真实的个人 QQ 账号。

本文默认环境为:

- Windows
- 本地运行一个兼容 OneBot 11 的桥接程序
- 优先使用 NapCat

## 5 分钟速查版

如果你只想先跑通最短路径，按下面做:

1. 从官方 Releases 下载 `NapCat.Shell.Windows.OneKey.zip`
2. 运行 `NapCatInstaller.exe`
3. 打开生成的 `NapCat.*.Shell` 目录并运行 `napcat.bat`
4. 用控制台输出的 WebUI 地址打开 NapCat 管理页面
5. 用目标个人 QQ 扫码登录
6. 在 NapCat WebUI 中开启:
   - HTTP API：`127.0.0.1:3000`
   - Forward WebSocket：`127.0.0.1:3001`
   - `messageFormat = array`
   - 设置一个统一的 `token`
7. 在 `nanobot` 配置中启用 `qqPersonal`，填写:
   - `wsUrl`
   - `httpUrl`
   - `accessToken`
   - `allowFrom`
8. 运行 `nanobot gateway`
9. 先测试私聊
10. 再测试群聊和文件收发

做完这 10 步，基本就已经接通。

## 你需要准备什么

如果你现在就要接入个人 QQ，需要准备以下内容:

### 机器环境

- 一台 Windows 电脑
- 这台机器上能运行 `nanobot`
- 能运行 NapCat

### QQ 相关

- 一个要登录的个人 QQ 账号
- 一个好友账号用于私聊测试
- 一个 QQ 群用于群聊测试

### 测试材料

- 一个小图片或小文件，用于测试文件收发

## 当前上游信息

截至 2026-03-28：

- NapCat 官网：`napneko.github.io`
- NapCat 官方仓库：`NapNeko/NapCatQQ`
- GitHub Releases 当前显示最新版本：`v4.17.53`
- Release 说明里提到推荐 QQ 版本为 `40768+`

需要注意：

- NapCat 文档更推荐 `Shell` 版本，而不是旧的 LiteLoader 方案
- Windows 下同时有手动 Shell 包和 OneKey 包
- WebUI 的 token 是随机生成的，会在控制台显示，或者保存在 `webui.json` 中

## Windows 推荐下载方式

对于大多数 Windows 用户，最简单的方式是：

1. 下载 `NapCat.Shell.Windows.OneKey.zip`
2. 运行 `NapCatInstaller.exe`
3. 进入生成的 `NapCat.XXXX.Shell` 目录
4. 运行 `napcat.bat`

推荐这条路的原因：

- 步骤最少
- 自带所需运行环境
- 不必优先折腾旧版 LiteLoader 方式

可选方案：

1. 下载 `NapCat.Shell.zip`
2. 本地自行安装兼容版本的 QQ
3. 运行 `launcher.bat` 或 `launcher-win10.bat`

只有当你明确要把 NapCat 挂到自己已经装好的 QQ 上时，才建议用这条。

## 去哪里获取

官方入口：

- 官网：<https://napneko.github.io/>
- 安装指南：<https://napneko.github.io/guide/install>
- Shell 启动指南：<https://napneko.github.io/guide/boot/Shell>
- Framework 指南：<https://napneko.github.io/guide/boot/Framework>
- Releases：<https://github.com/NapNeko/NapCatQQ/releases>
- GitHub 仓库：<https://github.com/NapNeko/NapCatQQ>

如果你还需要匹配版本的 QQ 安装包，NapCat 的 release 说明里通常也会给相关链接。

## 第一次启动

### 第 1 步：启动 NapCat

如果你用的是 OneKey 包：

- 运行 `NapCatInstaller.exe`
- 打开生成的 shell 目录
- 运行 `napcat.bat`

如果你用的是手动 Shell 包：

- 先确保安装了兼容版本的 QQ
- 运行 `launcher.bat`
- Windows 10 下优先使用 `launcher-win10.bat`

### 第 2 步：打开 WebUI

根据 NapCat 文档，默认 WebUI 端口通常是 `6099`。

启动后，控制台一般会显示类似：

```text
http://127.0.0.1:6099/webui?token=xxxxx
```

如果你错过了：

- 重新看控制台输出
- 或者打开 `webui.json` 查看 `token`

### 第 3 步：登录 QQ

在 NapCat WebUI 里：

1. 进入 QQ 登录页
2. 选择 `QRCode`
3. 用目标个人 QQ 扫码登录

NapCat 文档提到，登录后 WebUI token 可能会刷新。如果页面重新要求 token：

- 回到 NapCat 控制台查看
- 或重新读取 `webui.json`

## 为 nanobot 配置网络接口

当前 `nanobot` 的 `qq_personal` channel 需要 NapCat 提供：

- 一个 **Forward WebSocket** 用于接收入站事件
- 一个 **HTTP API** 用于发送动作
- `messageFormat = array`，保证消息段结构明确

### 推荐本机端口

建议本机部署这样设置：

- HTTP：`127.0.0.1:3000`
- WebSocket：`127.0.0.1:3001`
- HTTP / WS 共用同一个 token
- `messageFormat = array`

推荐这样配的原因：

- 安全性更高
- 与当前 `nanobot` 的 `qq_personal` 实现最匹配
- 文件、图片、语音、视频等消息段更容易正确处理

### 为什么要用 `array`

`qq_personal` 依赖 OneBot 的消息段结构来识别：

- `text`
- `image`
- `record`
- `video`
- `file`

如果不是 `array`，很多段结构会变得不稳定，不利于文件收发和群聊处理。

### WebUI 中建议这样配置

在 NapCat WebUI 中：

1. 打开网络设置
2. 新建一个 HTTP 服务
3. 新建一个 Forward WebSocket 服务
4. 两个服务都设为启用
5. Host 设为 `127.0.0.1`
6. HTTP 端口设为 `3000`
7. WS 端口设为 `3001`
8. `messageFormat` 设为 `array`
9. 设置一个非空 token
10. 保存并启用

## nanobot 配置示例

```json
{
  "channels": {
    "qq_personal": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "httpUrl": "http://127.0.0.1:3000",
      "accessToken": "change-me",
      "allowFrom": ["123456789"],
      "groupPolicy": "mention",
      "groupAllowFrom": []
    }
  }
}
```

说明：

- `allowFrom`：允许私聊机器人的 QQ 号列表，填字符串形式的 QQ 号
- 第一次调试时，可以临时设为 `["*"]`
- 调试通过后，再收紧成你自己的白名单
- `groupPolicy`：
  - `mention`：默认，群里必须 `@bot` 才回应
  - `open`：群里所有消息都回应
  - `allowlist`：只有指定群才回应
- `groupAllowFrom`：当 `groupPolicy = "allowlist"` 时填写允许的群号

## 如何拿到自己的 QQ 号 / allowFrom

最简单的方法：

1. 先把 `allowFrom` 临时设成 `["*"]`
2. 用你的好友账号给这个 QQ 发一条私聊消息
3. 查看 `nanobot` 日志中的发送者 ID
4. 把它填回 `allowFrom`

因为 `qq_personal` 走的是个人 QQ 的 OneBot 事件，所以这里使用的是 QQ 数字 ID，不是 QQ 开放平台的 `openid`。

## 启动 nanobot

运行：

```bash
nanobot gateway
```

只要 NapCat 已经启动，并且 HTTP / WS 都启用，`nanobot` 就会尝试连接。

## 验证步骤

### 私聊文本测试

1. 用好友账号给这个 QQ 发一条普通私聊消息
2. 确认 `nanobot` 有回复

预期：

- 入站事件进入 `qq_personal`
- 回复通过 `send_private_msg` 发出

### 私聊收文件测试

1. 给这个 QQ 发送一个小文件
2. 确认文件被下载到 `media/qq_personal/<user_id>/`
3. 确认 agent 能把它作为入站媒体处理

预期：

- `nanobot` 收到本地媒体路径
- 文本中能看到 `[file: ...]` 或 `[image: ...]`

### 私聊发文件测试

让 agent 通过 `message(..., media=[...])` 发一个文件。

预期：

- `qq_personal` 会把本地文件路径转成 OneBot 文件消息段
- QQ 对方能收到文件

### 群聊测试

1. 把登录的 QQ 账号拉进一个群
2. 保持 `groupPolicy = "mention"`
3. 先在群里发一条不 `@` 它的消息
4. 确认 `nanobot` 不回复
5. 再发一条 `@` 它的消息
6. 确认 `nanobot` 在群里回复

预期：

- `mention` 模式下，未提及消息被忽略
- 提及后的消息会被处理并回复

### 群文件测试

1. 在群里发送一个小文件
2. 确认文件被下载到 `media/qq_personal/<group_id>/`
3. 再触发一次发送文件到群的流程

预期：

- 入站群文件通过 `get_group_file_url` 解析
- 出站群文件通过 `send_group_msg` 发送

## NapCat 相关接口说明

当前 `nanobot` 里实际依赖的主要接口：

- `send_private_msg`
- `send_group_msg`
- `get_private_file_url`
- `get_group_file_url`
- `get_file`

NapCat 文档里还提到：

- 通过 `send_private_msg` 发送图片/文件
- 通过 `send_group_msg` 发送群图片/文件
- `upload_private_file`
- `upload_group_file`

当前 `nanobot` 的实现方式是：

- 文本和媒体出站：`send_private_msg` / `send_group_msg`
- 私聊文件入站：`get_private_file_url`
- 群文件入站：`get_group_file_url`

## 常见问题排查

### WebUI 能打开但登录不上

- 检查 token 是否已经刷新
- 回到控制台或 `webui.json` 重新取 token
- 确认扫码登录已完成

### nanobot 连不上 WebSocket

- 确认 NapCat 的 Forward WS 已启用
- 确认 `wsUrl` 和端口一致
- 确认 token 一致

### nanobot 能收不能发

- 确认 HTTP API 已启用
- 确认 `httpUrl` 和端口一致
- 确认 token 一致

### 收不到私聊文件

- 确认 NapCat 的消息格式是 `array`
- 确认文件消息里有足够的文件元数据
- 确认文件还在 NapCat 可取的缓存窗口内

### 群里不回复

- 确认 `groupPolicy`
- 如果是 `mention`，确认你真的 `@` 到了 bot
- 如果是 `allowlist`，确认该群号在 `groupAllowFrom` 里

### 群文件收发失败

- 确认桥接端支持 `get_group_file_url`
- 确认群文件消息里带有正确的 `file_id`
- 先用小图片或小文档做测试

### 发文件失败

- 确认本地文件路径真实存在
- 确认 QQ / NapCat 支持该文件类型
- 先从小图片、小文本文件开始测试

## 安全建议

- 除非你明确需要远程访问，否则 HTTP 和 WS 都只绑定 `127.0.0.1`
- 如果接口不是纯本地，务必设置 token
- 测试完成后，把 `allowFrom` 收紧，不要长期用 `["*"]`
- 不要把 WebUI 直接暴露到公网

## 参考链接

- NapCat 官网：<https://napneko.github.io/>
- 安装指南：<https://napneko.github.io/guide/install>
- Shell 启动指南：<https://napneko.github.io/guide/boot/Shell>
- Framework 指南：<https://napneko.github.io/guide/boot/Framework>
- WebUI / 基础配置：<https://napneko.github.io/config/basic>
- OneBot 网络配置：<https://napneko.github.io/onebot/network>
- 文件处理文档：<https://napneko.github.io/develop/file>
- API 文档入口：<https://napcat.apifox.cn/>
- Releases：<https://github.com/NapNeko/NapCatQQ/releases>
