# AI & LLM 每日简报

每天自动抓取全球 AI 新闻，用 LLM 生成中文 Top5 简报，推送到 Notion 和微信公众号。

## 功能

- 10 个 RSS 源抓取最近 48 小时全球 AI/LLM 新闻
- 去重并按可信度、时效性、主题重要性排序
- LLM 生成中文 Top5 简报（含摘要、重要性分析、可执行建议）
- 自动生成 AI Intelligence Hub 风格封面图（Pillow）
- 推送到 Notion（每天独立子页面 + 封面图 cover + image block）
- 输出公众号纯文本版（无 Markdown 符号，直接粘贴）
- 支持 macOS、Linux、Windows 和 Docker

## 快速启动

### 服务器部署（Linux）

```bash
# 1. 克隆代码
git clone <repo-url> ~/ai-daily-briefing
cd ~/ai-daily-briefing

# 2. 创建虚拟环境
python3 -m venv .venv

# 3. 安装依赖
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --only-binary :all: Pillow

# 4. 安装中文字体（封面图需要）
sudo yum install -y wqy-zenhei-fonts

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，填写以下配置：
#   LLM_API_KEY     - LLM API Key
#   LLM_BASE_URL    - LLM API地址
#   LLM_MODEL       - 模型名称
#   NOTION_TOKEN    - Notion Integration Secret
#   NOTION_PAGE_ID  - Notion 主页面 ID
#   GITHUB_TOKEN    - GitHub Token（封面图上传需要）

# 6. 运行
bash run.sh
```

### macOS 本地运行

```bash
cp .env.example .env
# 编辑 .env
chmod +x run.sh
./run.sh
```

### 定时任务（crontab）

```bash
# 每天 8:00 自动运行
crontab -e
# 添加：
0 8 * * * cd ~/ai-daily-briefing && bash run.sh >> ~/ai-daily-briefing/output/cron.log 2>&1
```

## 输出文件

```
output/
  ai-llm-briefing-YYYY-MM-DD.md           # Markdown 简报
  ai-llm-briefing-YYYY-MM-DD-wechat.txt   # 公众号纯文本版
  cover-YYYY-MM-DD.png                    # 封面图
```

## 配置说明

### .env 配置项

| 配置项 | 说明 | 必填 |
|--------|------|------|
| LLM_API_KEY | LLM API Key | 是 |
| LLM_BASE_URL | LLM API 地址 | 是 |
| LLM_MODEL | 模型名称 | 是 |
| NOTION_TOKEN | Notion Integration Secret | 否 |
| NOTION_PAGE_ID | Notion 主页面 ID | 否 |
| GITHUB_TOKEN | GitHub Token（封面图上传） | 否 |
| GITHUB_REPO | GitHub 仓库（默认 vitoi/ai-daily-briefing） | 否 |
| HOURS_BACK | 抓取时间范围（默认 48） | 否 |
| TOP_N | 简报条数（默认 5） | 否 |

### Notion 配置步骤

1. 访问 https://www.notion.so/profile/integrations 创建 Integration，复制 Secret
2. 在 Notion 中创建一个主页面（如"AI简报"）
3. 点击页面右上角"..." → Connect to → 选择刚创建的 Integration
4. 从页面 URL 中获取页面 ID（32位字符串）
5. 填入 .env

每天运行时会自动在主页面下创建子页面（标题为"AI简报 YYYY-MM-DD"），简报内容放入子页面，封面图设为子页面 cover 和内容顶部 image block。

### GitHub Token 配置（封面图上传）

1. 打开 https://github.com/settings/tokens
2. Generate new token (classic) → 勾选 repo 权限
3. 填入 .env 的 GITHUB_TOKEN

封面图上传到仓库的 gh-pages 分支，通过 raw URL 设为 Notion 子页面 cover。仓库需设为公开。

## 公众号发布流程

未认证订阅号无 API 推送权限，采用 Notion 中转方式：

1. 服务器 crontab 每天 8:00 自动生成简报并推送 Notion
2. 手机 Notion 打开当天子页面，全选复制
3. 粘贴到微信公众号后台编辑器，群发

## 封面图设计

AI Intelligence Hub 风格（900x383px，2.35:1）：

- 深蓝渐变背景（#06152F -> #1E1B4B）
- 右侧 AI Core 发光球体（同心圆环 + 辐射线 + 轨道环 + 高斯模糊光晕）
- 青色（#00D9FF）+ 紫色（#7B61FF）配色
- 30px 间距网格线 + 80 个随机粒子
- 左侧标题区（品牌名 + 青色竖线 + 大标题 + 副标题 + 渐变横线）
- 底部玻璃拟态信息条（半透明渐变 + 期号 + 标签）
- 顶部和底部渐变光带（青 -> 紫）

封面图用 Pillow 生成，无外部 API 依赖，稳定可靠。

## 内容安全

LLM prompt 内置内容安全红线：

- 不提及任何国家领导人姓名
- 不提及任何国家政府机构名称
- 不涉及政治决策、外交关系、制裁政策
- AI 监管政策只描述技术产业影响
- 核心是政府政治行为的新闻不选

## v1.0 问题修复记录

### Notion 推送

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 推送内容带 # 号 | heading block 复制到公众号带 # | 改用 paragraph block |
| 推送内容带星号 | bold annotation 复制转 ** | 去掉所有 bold，纯文本 |
| 来源链接丢失 | Notion link 对象复制丢链接 | 改为纯文本，最终去掉链接只保留来源名称 |
| 重复标题堆叠 | 旧内容未清空 + LLM 输出带标题 | 清空旧内容 + 跳过 LLM 标题行 |
| 子页面创建 400 | append child_page block 不支持 | 改用 create page API |
| 封面图 Notion file upload 失败 | "Too many fields" | 改用 GitHub raw URL external 模式 |

### 封面图

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Pillow 未安装 | pip install 编译失败 | 用 --only-binary :all: 安装预编译包 |
| 中文显示方框 | 服务器无中文字体 | 安装 wqy-zenhei-fonts |
| GitHub 上传 422 | 文件已存在缺 sha | 文件名加时间戳 |
| GitHub raw URL 不可访问 | 仓库私有 | 仓库设为公开 + 上传 gh-pages 分支 |
| 底部信息条报错 | alpha_composite 尺寸不匹配 | bar overlay 改为全画布尺寸 |
| 标题显示不全 | 画布太矮 + 无换行 | 重新设计布局 + 自动换行 |
| Notion cover 不显示 | 私有仓库 raw URL | 仓库设公开 + gh-pages 分支 |

### 内容安全

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 公众号文章被删 | 内容涉国家机关/领导人 | prompt 增加内容安全红线 |

### RSS 源

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Hugging Face RSS 不通 | 服务器网络限制 | 替换为 AI Business + VentureBeat GenAI |
| VentureBeat GenAI 404 | URL 失效 | 移除，保持 10 源 |
| AI Business 解析异常 | XML 格式问题 | feedparser 容错处理 |

## 技术栈

- Python 3.12+
- feedparser（RSS 解析）
- requests（HTTP 请求）
- Pillow（封面图生成）
- python-dotenv（环境变量管理）
- Notion API（子页面推送）
- GitHub API（封面图上传）
- OpenAI 兼容 LLM API（简报生成）

## 项目结构

```
ai-daily-briefing/
  briefing.py          # 主脚本
  run.sh               # 启动脚本
  requirements.txt     # Python 依赖
  .env.example         # 环境变量模板
  README.md            # 项目文档
  assets/              # 静态资源（头像等）
  output/              # 输出目录（gitignore）
```
