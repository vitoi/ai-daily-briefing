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

DEFAULT_FEEDS = [
    ("OpenAI News", "https://openai.com/news/rss.xml", 10),
    ("Anthropic News", "https://www.anthropic.com/news/rss.xml", 10),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", 10),
    ("Microsoft AI", "https://blogs.microsoft.com/ai/feed/", 8),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", 8),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 7),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 6),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", 5),
]

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
        feed = feedparser.parse(url)

        if getattr(feed, "bozo", False):
            logger.warning("RSS 解析异常: %s | %s", source, getattr(feed, "bozo_exception", ""))

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
    return path


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


def main() -> int:
    try:
        articles = fetch_articles()
        if not articles:
            raise RuntimeError("没有抓取到符合时间范围的新闻，请检查网络或 RSS 配置。")

        logger.info("有效候选新闻: %d", len(articles))
        selected = articles[:MAX_ARTICLES]
        briefing = call_llm(build_prompt(selected))
        path = save_markdown(briefing)
        post_webhook(briefing)

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
