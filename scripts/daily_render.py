#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日新闻页面渲染脚本（GitHub Actions 云端版，纯标准库）。
流程: 抓 AIHOT 三路(模型/投资/趋势) + BBC/NPR RSS -> 渲染仓库根 index.html。
用法: python3 scripts/daily_render.py [--page PATH] [--date YYYY-MM-DD]
日期与时间按北京时间(UTC+8)计算，不依赖运行环境时区。
"""
import re, os, sys, json, ssl, html as html_mod, time as _time
import urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "index.html")
UA = {"User-Agent": "Mozilla/5.0 (news-push-daily)"}
BJ = timezone(timedelta(hours=8))


def now_bj():
    return datetime.now(BJ)


def fetch_json(url, headers=None, timeout=25):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return json.loads(r.read())


def fetch_rss(url, timeout=18):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return ET.fromstring(r.read())


def cjk_len(s):
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def esc(s):
    return html_mod.escape(s or "", quote=True)


def fmt_time(iso):
    """ISO 时间 -> 'MM-DD HH:MM' 北京时间"""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(BJ).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def item_html(url, title, meta, sum_text=None):
    parts = [
        '    <div class="item">',
        f'      <a href="{esc(url)}" target="_blank" rel="noopener">{esc(title)}</a>',
        f'      <div class="meta">{esc(meta)}</div>',
    ]
    if sum_text:
        parts.append(f'      <div class="sum">{esc(sum_text)}</div>')
    parts.append("    </div>")
    return "\n".join(parts)


def get_ai_items():
    urls = {
        "models": "https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&category=ai-models&limit=10",
        "invest": "https://aihot.virxact.com/api/v1/items?mode=selected&window=7d&q=%E6%8A%95%E8%B5%84&limit=10",
        "industry": "https://aihot.virxact.com/api/v1/items?mode=selected&window=7d&category=industry&limit=10",
    }
    KEYWORDS = ("模型", "发布", "GPT", "Claude", "Gemini", "收购", "投资", "估值", "IPO",
                "融资", "Anthropic", "OpenAI", "英伟达", "NVIDIA", "股价", "AI")
    seen, rows = set(), []
    for k in ("models", "invest", "industry"):
        d = None
        for attempt in (1, 2, 3):
            try:
                d = fetch_json(urls[k], headers={"User-Agent": "aihot-skill/1.2.1"})
                break
            except Exception as e:
                if attempt == 3:
                    print(f"[aihot] {k} ERR after 3 tries: {e}", flush=True)
                else:
                    _time.sleep(2 * attempt)
        if not d:
            continue
        for it in d.get("items", []):
            t = (it.get("title") or "").strip()
            if not t or t in seen:
                continue
            score = sum(1 for kw in KEYWORDS if kw.lower() in t.lower())
            rows.append({"t": t, "score": score, "src": (it.get("source") or {}).get("name", ""),
                         "url": (it.get("links") or {}).get("aihot", ""),
                         "time": fmt_time(it.get("publishedAt") or it.get("discoveredAt") or ""),
                         "desc": (it.get("summary") or it.get("description") or "")[:140]})
            seen.add(t)
    rows.sort(key=lambda r: -r["score"])
    out = []
    for r in rows[:8]:
        meta = f'{r["src"]} · {r["time"]}' if r["time"] else r["src"]
        summ = None
        if r["desc"] and cjk_len(r["desc"]) > 8:
            summ = (r["desc"][:60] + "…") if len(r["desc"]) > 60 else r["desc"]
        out.append(item_html(r["url"] or "#", r["t"], meta, summ))
    return out


def get_rss_items(cat_key, per_source):
    feeds = {
        "pol": [("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
                ("NPR", "https://feeds.npr.org/1001/rss.xml")],
        "eco": [("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml")],
        "ent": [("BBC Ent", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml")],
    }
    labels = {"BBC World": "BBC", "NPR": "NPR", "BBC Business": "BBC", "BBC Ent": "BBC"}
    out = []
    for (name, url), want in zip(feeds[cat_key], per_source):
        if want <= 0:
            continue
        try:
            root = fetch_rss(url)
            items = []
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if t and link:
                    items.append((t, link))
        except Exception as e:
            print(f"[rss] {name} ERR: {e}", flush=True)
            continue
        for t, link in items[:want]:
            out.append(item_html(link, t, labels[name]))
    return out


def render(target_date=None):
    target_date = target_date or now_bj().strftime("%Y-%m-%d")
    print(f"== render start date={target_date} ==", flush=True)
    ai = get_ai_items()
    pol = get_rss_items("pol", (2, 1))
    if not pol:
        pol = get_rss_items("pol", (3, 0))
    eco = get_rss_items("eco", (3,))
    ent = get_rss_items("ent", (3,))
    if not (ai or pol or eco or ent):
        print("ALL_SOURCES_FAILED", flush=True)
        return 1

    with open(PAGE, "r", encoding="utf-8") as f:
        html = f.read()

    html = re.sub(r'(<div class="date" id="push-date">)[^<]*(</div>)',
                  r"\g<1>" + target_date + r"\g<2>", html, count=1)
    for tag, items in (("AI_LIST", ai), ("POL_LIST", pol), ("ECO_LIST", eco), ("ENT_LIST", ent)):
        html = re.sub(r"<!--" + tag + r"_START-->(.*?)<!--" + tag + r"_END-->",
                      "<!--" + tag + "_START-->\n" + "\n\n".join(items) + "\n    <!--" + tag + "_END-->",
                      html, count=1, flags=re.S)
    html = re.sub(r'(AI 资讯<span class="count">)[^<]*(</span>)',
                  r"\g<1>" + f'{len(ai)} 条 · 模型/投资/趋势' + r"\g<2>", html, count=1)

    # 移除旧的 updated 提示行（纯文本单行），再插到第一个卡片前
    html = re.sub(r"\n?\s*<div class=\"updated\">[^<]*</div>", "", html)
    ts = now_bj().strftime("%Y-%m-%d %H:%M")
    note = f'<div class="updated">🕗 更新于 {ts}（GitHub Actions 自动）。点击标题在新窗口打开原文。</div>'
    missing = []
    if not ai:
        missing.append("AI 资讯")
    if not pol:
        missing.append("政治")
    if not eco:
        missing.append("商业经济")
    if not ent:
        missing.append("文化娱乐")
    if missing:
        note += '\n<div class="updated">⚠️ ' + "、".join(missing) + " 数据暂不可用（源站故障）。</div>"
    idx = html.find('<div class="card">')
    html = html[:idx] + note + "\n\n  " + html[idx:]

    with open(PAGE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"rendered: ai={len(ai)} pol={len(pol)} eco={len(eco)} ent={len(ent)} date={target_date}", flush=True)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    page = PAGE
    d = None
    for i, a in enumerate(args):
        if a.startswith("--page="):
            page = a.split("=", 1)[1]
        elif a == "--page" and i + 1 < len(args):
            page = args[i + 1]
        elif a.startswith("--date="):
            d = a.split("=", 1)[1]
        elif a == "--date" and i + 1 < len(args):
            d = args[i + 1]
    PAGE = page
    sys.exit(render(d))
