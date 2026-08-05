#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI & LLM Daily Briefing
- Fetches recent AI/LLM news from trusted RSS feeds
- Deduplicates and ranks articles
- Calls any OpenAI-compatible LLM endpoint
- Saves a Markdown briefing locally
- Optionally posts the briefing to a webhook
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import feedparser
import requests
from dateutil import parser as date_parser
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./output"))
HOURS_BACK = int(os.getenv("HOURS_BACK", "48"))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "40"))
TOP_N = int(os.getenv("TOP_N", "5"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID", "3b26c7e3-f2b1-8039-a9fc-c882801b5819").strip()

DEFAULT_FEEDS = [
    ("OpenAI News", "https://openai.com/news/rss.xml", 10),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 7),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 6),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", 5),
    ("Ars Technica AI", "https://feeds.arstechnica.com/arstechnica/features", 5),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 5),
    ("AI News (The Decoder)", "https://the-decoder.com/feed/", 5),
    ("ZDNet AI", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml", 4),
    ("AI Business", "https://aibusiness.com/feed", 4),
    ("VentureBeat Generative AI", "https://venturebeat.com/category/generative-ai/feed/", 4),
]

# HTTP 请求超时（秒）
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

# 请求间隔（秒），避免触发反爬风控
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))

# User-Agent，模拟正常浏览器访问
HTTP_HEADERS = {
    "User-Agent": os.getenv(
        "HTTP_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36",
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

KEYWORDS = {
    "llm": 8,
    "large language model": 8,
    "artificial intelligence": 6,
    "generative ai": 6,
    "agent": 5,
    "open source": 4,
    "open-weight": 5,
    "multimodal": 5,
    "reasoning": 5,
    "benchmark": 4,
    "inference": 4,
    "training": 3,
    "model release": 6,
    "safety": 5,
    "regulation": 5,
    "governance": 4,
    "chip": 4,
    "data center": 4,
    "compute": 4,
    "copyright": 4,
    "enterprise": 3,
}

@dataclass
class Article:
    source: str
    title: str
    link: str
    summary: str
    published: datetime
    source_weight: int
    score: float = 0.0

    @property
    def fingerprint(self) -> str:
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", self.title.lower())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def parse_feed_config() -> list[tuple[str, str, int]]:
    raw = os.getenv("RSS_FEEDS_JSON", "").strip()
    if not raw:
        return DEFAULT_FEEDS
    try:
        data = json.loads(raw)
        feeds = []
        for item in data:
            feeds.append(
                (
                    str(item["name"]),
                    str(item["url"]),
                    int(item.get("weight", 5)),
                )
            )
        return feeds
    except Exception as exc:
        raise RuntimeError(f"RSS_FEEDS_JSON 配置错误: {exc}") from exc


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def parse_datetime(entry: dict) -> datetime:
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if value:
            try:
                dt = date_parser.parse(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    return datetime.now(timezone.utc)


def fetch_articles() -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles: list[Article] = []

    for source, url, weight in parse_feed_config():
        logger.info("抓取 RSS: %s", source)

        # 使用带超时和 User-Agent 的请求
        try:
            response = requests.get(
                url,
                headers=HTTP_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except requests.RequestException as exc:
            logger.warning("RSS 请求失败: %s | %s", source, exc)
            time.sleep(REQUEST_DELAY)
            continue
        except Exception as exc:
            logger.warning("RSS 解析失败: %s | %s", source, exc)
            time.sleep(REQUEST_DELAY)
            continue

        if getattr(feed, "bozo", False):
            logger.warning("RSS 解析异常: %s | %s", source, getattr(feed, "bozo_exception", ""))

        # 请求间隔，避免触发反爬风控
        time.sleep(REQUEST_DELAY)

        for entry in feed.entries[:30]:
            published = parse_datetime(entry)
            if published < cutoff:
                continue

            title = clean_html(entry.get("title", ""))
            link = entry.get("link", "").strip()
            summary = clean_html(
                entry.get("summary")
                or entry.get("description")
                or entry.get("content", [{}])[0].get("value", "")
            )
            if not title or not link:
                continue

            article = Article(
                source=source,
                title=title,
                link=link,
                summary=summary,
                published=published,
                source_weight=weight,
            )
            article.score = score_article(article)
            articles.append(article)

    return deduplicate(articles)


def score_article(article: Article) -> float:
    text = f"{article.title} {article.summary}".lower()
    keyword_score = sum(weight for keyword, weight in KEYWORDS.items() if keyword in text)

    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - article.published).total_seconds() / 3600,
    )
    freshness_score = max(0.0, 20.0 - age_hours / 3.0)
    title_bonus = 5 if any(k in article.title.lower() for k in KEYWORDS) else 0

    return article.source_weight + keyword_score + freshness_score + title_bonus


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen_links: set[str] = set()
    seen_titles: list[str] = []
    result: list[Article] = []

    for article in sorted(articles, key=lambda x: x.score, reverse=True):
        if article.link in seen_links:
            continue

        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", article.title.lower())
        words = set(normalized.split())
        duplicate = False

        for old in seen_titles:
            old_words = set(old.split())
            union = words | old_words
            overlap = len(words & old_words) / max(1, len(union))
            if overlap >= 0.72:
                duplicate = True
                break

        if duplicate:
            continue

        seen_links.add(article.link)
        seen_titles.append(normalized)
        result.append(article)

    return result


def build_prompt(articles: list[Article]) -> str:
    payload = []
    for i, article in enumerate(articles[:MAX_ARTICLES], 1):
        payload.append(
            {
                "id": i,
                "source": article.source,
                "title": article.title,
                "published_utc": article.published.isoformat(),
                "summary": article.summary,
                "url": article.link,
                "ranking_score": round(article.score, 2),
            }
        )

    return f"""
你是一名资深 AI 行业分析师和技术顾问。

请根据下面的候选新闻，生成一份“全球 AI 与 LLM 每日简报”。

要求：
1. 只选最重要的 {TOP_N} 条，不要为了凑数选择低价值新闻。
2. 优先级依次为：行业影响、可信度、时效性、实际应用价值。
3. 避免同一事件重复出现。
4. 不得编造候选新闻中没有的信息。
5. 对未经独立验证的厂商数据，要明确写“厂商披露”或“尚待独立验证”。
6. 每条包含：
   - 标题
   - 2～3 句简明摘要
   - 为什么重要
   - 一条可执行建议
   - 来源链接
7. 最后给出“今日行动优先级”，包含 1～3 条具体行动。
8. 使用中文，语气专业、直接、务实。
9. 输出 Markdown，不使用代码块。

候选新闻：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def call_llm(prompt: str) -> str:
    if not LLM_API_KEY:
        raise RuntimeError("缺少 LLM_API_KEY，请先复制 .env.example 为 .env 并填写。")

    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": LLM_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": "你必须严格基于输入材料，不得虚构新闻或数据。",
            },
            {"role": "user", "content": prompt},
        ],
    }

    response = requests.post(url, headers=headers, json=body, timeout=120)
    response.raise_for_status()
    data = response.json()

    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise RuntimeError(f"无法解析模型响应: {data}") from exc


def save_markdown(content: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"ai-llm-briefing-{date_str}.md"
    header = f"# AI & LLM 每日简报 — {date_str}\n\n"
    path.write_text(header + content + "\n", encoding="utf-8")

    # 同时生成公众号纯文本版（去除Markdown符号，适合直接粘贴）
    wx_path = OUTPUT_DIR / f"ai-llm-briefing-{date_str}-wechat.txt"
    wx_content = convert_to_wechat_text(content, date_str)
    wx_path.write_text(wx_content, encoding="utf-8")
    logger.info("公众号纯文本版: %s", wx_path)

    return path


def convert_to_wechat_text(md_content: str, date_str: str) -> str:
    """将Markdown简报转换为公众号友好的纯文本格式"""
    text = md_content

    # 去掉Markdown标题符号
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)

    # 去掉加粗符号
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)

    # 去掉斜体符号
    text = re.sub(r'\*(.+?)\*', r'\1', text)

    # 去掉代码块
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)

    # 链接 [text](url) -> text(url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1（\2）', text)

    # 去掉列表符号但保留缩进
    text = re.sub(r'^[\-\*]\s+', '  ', text, flags=re.MULTILINE)

    # 去掉多余空行（保留单个空行分隔）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 添加标题和分隔线
    header = f"AI & LLM 每日简报\n{date_str}\n{'=' * 30}\n\n"

    return header + text.strip() + "\n"


def post_webhook(content: str) -> None:
    if not WEBHOOK_URL:
        return

    # 默认发送通用 JSON；企业微信/飞书/Slack 可通过网关适配。
    response = requests.post(
        WEBHOOK_URL,
        json={"text": content},
        timeout=30,
    )
    response.raise_for_status()
    logger.info("简报已发送到 Webhook")




def upload_cover_to_github(cover_path, date_str: str) -> str | None:
    """上传封面图到GitHub仓库，返回raw URL"""
    import base64
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        logger.warning("GITHUB_TOKEN 未配置，跳过封面上传")
        return None

    repo = os.getenv("GITHUB_REPO", "vitoi/ai-daily-briefing")
    branch = os.getenv("GITHUB_BRANCH", "gh-pages")
    path_in_repo = f"output/cover-{date_str}-{int(time.time())}.png"

    with open(cover_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode()

    url = f"https://api.github.com/repos/{repo}/contents/{path_in_repo}"
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "message": f"cover {date_str}",
            "content": content_b64,
            "branch": branch,
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path_in_repo}?t={int(time.time())}"
        logger.info("封面图已上传GitHub: %s", raw_url)
        return raw_url
    else:
        logger.warning("GitHub上传失败: %s", resp.text[:200])
        return None


def generate_cover(content: str, date_str: str) -> Path | None:
    """生成封面图（AI Intelligence Hub风格 - 深蓝背景+AI Core发光球体+网格粒子+底部信息条）"""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        logger.warning("Pillow 未安装，跳过封面图生成")
        return None

    import random
    import math

    W, H = 900, 383
    img = Image.new("RGBA", (W, H), (6, 21, 47, 255))
    draw = ImageDraw.Draw(img)

    # ── 深蓝渐变背景 #06152F → #1E1B4B ──
    for y in range(H):
        t = y / H
        r = int(6 + (30 - 6) * t)
        g = int(21 + (27 - 21) * t)
        b = int(47 + (75 - 47) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # ── 微弱网格线 ──
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for x in range(0, W, 30):
        od.line([(x, 0), (x, H)], fill=(255, 255, 255, 6), width=1)
    for y in range(0, H, 30):
        od.line([(0, y), (W, y)], fill=(255, 255, 255, 6), width=1)

    # ── 粒子点（右上+右侧AI Core区域）──
    random.seed(hash(date_str) % 2**32)
    for _ in range(80):
        px = random.randint(W - 350, W - 30)
        py = random.randint(20, H - 60)
        ps = random.randint(1, 2)
        pa = random.randint(10, 50)
        color_choice = random.choice([(0, 217, 255), (123, 97, 247), (255, 255, 255)])
        od.ellipse([(px, py), (px + ps, py + ps)], fill=(*color_choice, pa))

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # ── 右侧 AI Core 发光球体 ──
    cx, cy = W - 140, H // 2
    # 外层光晕（多层模糊圆）
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for radius, alpha in [(90, 8), (75, 15), (60, 25), (48, 40)]:
        gd.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)], fill=(0, 217, 255, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # AI Core 同心圆环
    for ring_r, alpha in [(42, 60), (34, 100), (26, 160), (18, 220)]:
        draw.ellipse([(cx - ring_r, cy - ring_r), (cx + ring_r, cy + ring_r)],
                      outline=(0, 217, 255, alpha), width=1)

    # AI Core 核心实心圆 + 紫色内核
    draw.ellipse([(cx - 14, cy - 14), (cx + 14, cy + 14)], fill=(0, 217, 255, 200))
    draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], fill=(123, 97, 247, 255))

    # AI Core 辐射线（8方向）
    for angle_deg in range(0, 360, 45):
        angle = math.radians(angle_deg)
        x1 = cx + 48 * math.cos(angle)
        y1 = cy + 48 * math.sin(angle)
        x2 = cx + 68 * math.cos(angle)
        y2 = cy + 68 * math.sin(angle)
        draw.line([(x1, y1), (x2, y2)], fill=(0, 217, 255, 80), width=1)
        # 端点小圆
        draw.ellipse([(x2 - 2, y2 - 2), (x2 + 2, y2 + 2)], fill=(0, 217, 255, 120))

    # AI Core 轨道环（倾斜椭圆）
    orbit = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od2 = ImageDraw.Draw(orbit)
    od2.ellipse([(cx - 75, cy - 30), (cx + 75, cy + 30)], outline=(123, 97, 247, 40), width=1)
    od2.ellipse([(cx - 60, cy - 55), (cx + 60, cy + 55)], outline=(0, 217, 255, 25), width=1)
    img = Image.alpha_composite(img, orbit)
    draw = ImageDraw.Draw(img)

    # ── 顶部渐变光带 ──
    for x in range(60, W - 60):
        t = (x - 60) / (W - 120)
        r = int(0 + (123 - 0) * t)
        g = int(217 + (97 - 217) * t)
        b = int(255 + (247 - 255) * t)
        draw.point((x, 15), fill=(r, g, b, 200))

    # ── 字体 ──
    try:
        font_brand = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 18)
        font_date = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 14)
        font_title = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 42)
        font_sub = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 16)
        font_bar = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 13)
    except Exception:
        try:
            font_brand = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18)
            font_date = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 14)
            font_title = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 42)
            font_sub = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
            font_bar = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 13)
        except Exception:
            font_brand = font_date = font_title = font_sub = font_bar = ImageFont.load_default()

    # ── 左侧标题区 ──
    # 品牌名
    draw.text((40, 35), "理看AI", fill=(255, 255, 255, 220), font=font_brand)
    # 日期（右上）
    date_display = date_str.replace("-", ".")
    date_bbox = draw.textbbox((0, 0), date_display, font=font_date)
    date_w = date_bbox[2] - date_bbox[0]
    draw.text((W - date_w - 40, 37), date_display, fill=(150, 180, 220, 200), font=font_date)

    # 主标题
    title_y = 110
    # 标题左侧青色竖线
    draw.rectangle([(40, title_y + 5), (44, title_y + 50)], fill=(0, 217, 255, 255))
    draw.text((56, title_y), "AI 每日简报", fill=(255, 255, 255, 255), font=font_title)

    # 副标题
    draw.text((56, title_y + 60), "全球 AI 与大模型领域每日精选", fill=(130, 160, 200, 220), font=font_sub)

    # 标题下方渐变横线
    for x in range(56, 256):
        t = (x - 56) / 200
        r = int(0 + (123 - 0) * t)
        g = int(217 + (97 - 217) * t)
        b = int(255 + (247 - 255) * t)
        draw.point((x, title_y + 90), fill=(r, g, b, 200))

    # ── 底部半透明信息条（玻璃拟态）──
    bar_h = 38
    bar_overlay = Image.new("RGBA", (W, bar_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar_overlay)
    # 渐变半透明背景
    for x in range(W):
        t = x / W
        alpha = int(80 + 40 * math.sin(t * math.pi))
        bd.line([(x, 0), (x, bar_h)], fill=(6, 15, 47, alpha))
    img = Image.alpha_composite(img, bar_overlay)
    draw = ImageDraw.Draw(img)

    # 信息条上方渐变光带
    for x in range(W):
        t = x / W
        r = int(0 + (123 - 0) * t)
        g = int(217 + (97 - 217) * t)
        b = int(255 + (247 - 255) * t)
        draw.point((x, H - bar_h - 1), fill=(r, g, b, 180))

    # 底部文字
    draw.text((40, H - bar_h + 11), "# " + date_str.replace("-", ""), fill=(180, 200, 230, 200), font=font_bar)
    tags = "Technology · AI · Newsletter"
    tags_bbox = draw.textbbox((0, 0), tags, font=font_bar)
    tags_w = tags_bbox[2] - tags_bbox[0]
    draw.text((W - tags_w - 40, H - bar_h + 11), tags, fill=(180, 200, 230, 200), font=font_bar)

    # ── 保存 ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cover_path = OUTPUT_DIR / f"cover-{date_str}.png"
    img.convert("RGB").save(cover_path, "PNG")
    logger.info("封面图已生成: %s", cover_path)
    return cover_path


def post_notion(content: str, date_str: str, cover_path: Path | None = None) -> None:
    """在Notion主页面顶部创建带日期的子页面，简报内容放入子页面"""
    if not NOTION_TOKEN:
        return

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    # 构建简报内容blocks
    children = []
    lines = content.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # 去掉#和星号
            clean = stripped.lstrip("#").strip().replace("**", "").replace("*", "")
            if not clean:
                continue
            # 今日行动优先级特殊处理
            if "行动" in clean or "优先" in clean:
                prefix = "\U0001F3AF "
            else:
                prefix = ""
            children.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": prefix + clean}}]},
            })
            continue
        if stripped.startswith("===") or stripped.startswith("---"):
            children.append({"type": "divider", "divider": {}})
        elif stripped.startswith("- "):
            # 列表项：去-前缀，去星号
            clean = stripped.lstrip("- ").strip().replace("**", "").replace("*", "")
            children.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": "  " + clean}}]},
            })
        elif stripped and stripped[0].isdigit() and ". " in stripped[:5]:
            # 编号标题或编号项：去星号
            clean = stripped.replace("**", "").replace("*", "")
            children.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": clean}}]},
            })
        else:
            clean = stripped.replace("**", "").replace("*", "")
            # 把 [text](url) 转为 text（url）纯文本，公众号粘贴可见
            clean = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", clean)
            children.append({
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": clean}}]},
            })

    # Notion API限制：每次最多100个blocks
    batch = children[:100]

    # 创建子页面（以主页面为parent）
    page_data = {
        "parent": {"page_id": NOTION_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "📢"},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": "AI简报 " + date_str}}]
            }
        },
        "children": batch,
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=page_data,
        timeout=30,
    )
    resp.raise_for_status()
    page_id = resp.json().get("id")

    # 上传封面图到GitHub，获取raw URL，设为cover+插入image block
    if cover_path and os.path.exists(cover_path) and page_id:
        try:
            cover_url = upload_cover_to_github(cover_path, date_str)
            if cover_url:
                # 设为子页面cover
                requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=headers,
                    json={"cover": {"type": "external", "external": {"url": cover_url}}},
                    timeout=15,
                )
                # 在子页面内容顶部插入image block
                requests.patch(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    headers=headers,
                    json={"children": [{"type": "image", "image": {"type": "external", "external": {"url": cover_url}}}]},
                    timeout=15,
                )
                logger.info("封面图已设为cover+插入image block: %s", cover_url)
        except Exception as exc:
            logger.warning("封面设置异常: %s", exc)

    logger.info("简报已推送到Notion子页面: %s", date_str)


def main() -> int:
    try:
        articles = fetch_articles()
        if not articles:
            raise RuntimeError("没有抓取到符合时间范围的新闻，请检查网络或 RSS 配置。")

        logger.info("有效候选新闻: %d", len(articles))
        selected = articles[:MAX_ARTICLES]
        briefing = call_llm(build_prompt(selected))
        path = save_markdown(briefing)
        date_str = datetime.now().strftime("%Y-%m-%d")
        cover_path = generate_cover(briefing, date_str)
        post_webhook(briefing)
        post_notion(briefing, date_str, cover_path)

        print(f"\n生成成功：{path.resolve()}\n")
        print(briefing)
        return 0

    except requests.HTTPError as exc:
        body = exc.response.text[:1000] if exc.response is not None else ""
        logger.error("HTTP 请求失败: %s\n%s", exc, body)
        return 2
    except Exception as exc:
        logger.exception("执行失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
