# 每日新闻摘要脚本

新增了一个可直接给计划任务调用的命令：

```bash
nanobot news-digest --config path/to/news-digest.json
```

它会做这些事：

- 抓取多个 RSS / Atom 源
- 按最近 `windowHours` 小时过滤
- 用状态文件去重，避免重复推送
- 生成一份 Markdown 友好的新闻摘要
- 可选推送到 `stdout / Slack / Discord / 钉钉 / 飞书`

## 配置示例

```json
{
  "title": "AI 每日新闻简报",
  "windowHours": 24,
  "maxItems": 8,
  "summaryLength": 120,
  "requestTimeoutSeconds": 15,
  "statePath": "./runtime/news-digest.state.json",
  "outputPath": "./runtime/latest-news.md",
  "includeKeywords": ["ai", "openai", "anthropic", "google"],
  "excludeKeywords": ["rumor"],
  "push": {
    "type": "dingtalk",
    "webhookUrl": "${NEWS_DIGEST_WEBHOOK}",
    "maxMessageLength": 3500
  },
  "sources": [
    {
      "name": "OpenAI News",
      "url": "https://openai.com/news/rss.xml"
    },
    {
      "name": "Anthropic News",
      "url": "https://www.anthropic.com/news/rss.xml"
    },
    {
      "name": "Hacker News",
      "url": "https://hnrss.org/frontpage"
    }
  ]
}
```

说明：

- `statePath` 不填时，默认使用配置文件同目录下的 `*.state.json`
- `outputPath` 可选，用来落盘最近一次摘要
- `push.type=stdout` 时不会发 webhook，只在终端输出
- `webhookUrl` 支持环境变量展开，适合计划任务注入密钥
- 现成模板文件：`docs/news-digest.example.json`

## 手动调试

只抓取并打印，不发 webhook：

```bash
nanobot news-digest --config .\news-digest.json --dry-run
```

## 计划任务示例

Windows 计划任务可直接执行：

```bash
uv run nanobot news-digest --config D:\Projects\AI\yuukaChan\news-digest.json
```

Linux cron 示例：

```cron
0 9 * * * cd /srv/nanobot && uv run nanobot news-digest --config /srv/nanobot/news-digest.json
```

## 与 nanobot 内置 cron 的关系

当前内置 `cron` 触发的是 agent 指令，不是本地命令任务。

如果你要最稳定地跑这个新闻摘要脚本，优先用系统级计划任务。

如果一定要走 nanobot 内置定时器，可以让定时任务触发 agent，再由 agent 调用 `exec` 工具执行：

```bash
uv run nanobot news-digest --config ...
```
