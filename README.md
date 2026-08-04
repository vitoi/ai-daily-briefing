# AI & LLM 每日简报（本地部署版）

## 功能

- 从可信 RSS 源抓取最近 48 小时的全球 AI / LLM 新闻
- 去重并按可信度、时效性和主题重要性排序
- 调用 OpenAI 兼容接口生成中文 Top 5 简报
- 输出 Markdown 文件 + 公众号纯文本版
- 可选推送到 Webhook
- 可选推送到 Notion（每天创建独立子页面）
- 支持 macOS、Linux、Windows 和 Docker

## 一、快速启动

### macOS / Linux

```bash
cp .env.example .env
# 编辑 .env，填写 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
chmod +x run.sh
./run.sh
```

### Windows

```powershell
copy .env.example .env
notepad .env
run.bat
```

生成结果位于：

```text
output/ai-llm-briefing-YYYY-MM-DD.md          # Markdown 版
output/ai-llm-briefing-YYYY-MM-DD-wechat.txt   # 公众号纯文本版
```

## 二、使用自定义模型

只要接口兼容 OpenAI `/chat/completions` 即可。

例如本地 Ollama：

```env
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3:14b
```

例如公司内部网关：

```env
LLM_BASE_URL=https://your-company-gateway.example.com/v1
LLM_API_KEY=your_key
LLM_MODEL=your_model_name
```

## 三、每天自动运行

### macOS / Linux：cron

执行：

```bash
crontab -e
```

每天早上 8 点运行：

```cron
0 8 * * * cd /绝对路径/ai_daily_briefing && /bin/bash run.sh >> briefing.log 2>&1
```

### Linux：systemd timer

将项目放到固定路径后，创建：

`/etc/systemd/system/ai-briefing.service`

```ini
[Unit]
Description=AI Daily Briefing

[Service]
Type=oneshot
WorkingDirectory=/opt/ai_daily_briefing
ExecStart=/opt/ai_daily_briefing/.venv/bin/python /opt/ai_daily_briefing/briefing.py
EnvironmentFile=/opt/ai_daily_briefing/.env
```

`/etc/systemd/system/ai-briefing.timer`

```ini
[Unit]
Description=Run AI Daily Briefing every day

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-briefing.timer
systemctl list-timers | grep ai-briefing
```

### Windows：任务计划程序

程序：

```text
C:\Windows\System32\cmd.exe
```

参数：

```text
/c D:\ai_daily_briefing\run.bat
```

触发器设置为每天 08:00。

## 四、Docker

```bash
cp .env.example .env
docker compose run --rm ai-briefing
```

若需定时执行，建议由宿主机 cron 或 Windows 任务计划程序调用：

```bash
docker compose run --rm ai-briefing
```

## 五、Webhook 推送

`.env` 中填写：

```env
WEBHOOK_URL=https://your-webhook.example.com/briefing
```

程序会发送：

```json
{
  "text": "Markdown 简报正文"
}
```

企业微信、飞书、Slack 的消息格式不同，建议通过一个轻量网关适配。

## 六、Notion 推送

`.env` 中填写：

```env
NOTION_TOKEN=ntn_your_integration_secret
NOTION_PAGE_ID=your-main-page-id
```

配置步骤：

1. 访问 https://www.notion.so/profile/integrations 创建 Integration，复制 Secret
2. 在 Notion 中创建一个主页面（如"AI简报"）
3. 点击页面右上角"..." → Connect to → 选择刚创建的 Integration
4. 从页面 URL 中获取页面 ID（32位字符串）
5. 填入 .env

每天运行时会自动在主页面下创建子页面（标题为"AI简报 YYYY-MM-DD"），简报内容放入子页面。

## 七、公众号发布

未认证订阅号无 API 推送权限，采用手动方式：

1. 服务器 crontab 每天 8:00 自动生成简报并推送 Notion
2. 手机 Notion 打开当天子页面，全选复制
3. 粘贴到微信公众号后台编辑器，群发

## 八、建议配置

- `HOURS_BACK=48`：周末或低新闻量时更稳定
- `TOP_N=5`：保持简报紧凑
- `temperature=0.2`：降低幻觉风险
- 使用至少 8 个来源，避免单一媒体偏差
- 对厂商博客内容保留“厂商披露”措辞
