#!/usr/bin/env python3
"""EM Fintech Radar: a weekly market-scanning agent for FX, payments and stablecoins
in Latin America, Africa, the Gulf and Southeast Asia.

Pipeline
  1. Scan   - pull RSS feeds (regulators, central banks, deal news) listed in sources.yaml
  2. Filter - drop items seen in earlier runs and items older than the lookback window
  3. Triage - Claude scores each item 0-3 for relevance to an EM fintech advisory firm
  4. Brief  - Claude writes a one-page brief: the one thing that matters, the "so what"
              for a fund / a payments company / a licence decision, and possible mandates
  5. Save   - brief as Markdown and HTML in out/, seen items in state/seen.json

Claude runs through Claude Code in headless mode (`claude -p`), signed in with a
Claude plan. No API key is used: any ANTHROPIC_API_KEY in the environment is removed
before calling Claude Code, so a run never bills an API account.

Usage
  python3 radar.py                 # full run
  python3 radar.py --no-llm        # scan + filter only, prints the raw list
  python3 radar.py --days 14       # longer lookback
"""
import argparse, datetime as dt, hashlib, html, json, os, pathlib, re, shutil, subprocess, sys, tempfile
import email.utils, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

import yaml  # pip install pyyaml

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "state" / "seen.json"
OUT = ROOT / "out"
UA = {"User-Agent": "Mozilla/5.0 (EM-Fintech-Radar; research use)"}


# ---------- 1. Scan ----------
LOCALES = {"en": ("en-US", "US"), "es": ("es-419", "MX"), "pt": ("pt-BR", "BR")}


def google_news_url(query, lang="en"):
    hl, gl = LOCALES.get(lang, LOCALES["en"])
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"


def load_sources():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    feeds = []
    for s in cfg.get("feeds", []):
        feeds.append({"name": s["name"], "region": s["region"], "url": s["url"]})
    for s in cfg.get("searches", []):
        feeds.append({"name": s["name"], "region": s["region"], "url": google_news_url(s["query"], s.get("lang", "en"))})
    return feeds


def parse_date(text):
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(xml_bytes, source):
    """Parse RSS 2.0 or Atom into a list of items."""
    items = []
    root = ET.fromstring(xml_bytes)
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall(".//item") or root.findall(f".//{atom}entry")
    for n in nodes:
        def t(tag):
            e = n.find(tag)
            if e is None:
                e = n.find(atom + tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        link = t("link")
        if not link:
            le = n.find(atom + "link")
            link = le.get("href", "") if le is not None else ""
        desc = re.sub(r"<[^>]+>", " ", t("description") or t("summary"))
        items.append({
            "title": html.unescape(t("title")),
            "link": link,
            "date": parse_date(t("pubDate") or t("updated") or t("published")),
            "summary": html.unescape(re.sub(r"\s+", " ", desc))[:400],
            "source": source["name"],
            "region": source["region"],
        })
    return items


def fetch(source, timeout=20):
    req = urllib.request.Request(source["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_feed(r.read(), source)


def scan(feeds):
    items, errors = [], []
    for f in feeds:
        try:
            items += fetch(f)
        except Exception as e:  # one broken feed must not stop the run
            errors.append(f"{f['name']}: {e.__class__.__name__}")
    return items, errors


# ---------- 2. Filter ----------
def key(item):
    # same story from two outlets: compare titles without accents and punctuation
    # Google News appends " - Publisher" to every title
    title = re.sub(r"\s+[-|]\s+[^-|]{2,40}$", "", item["title"])
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    base = re.sub(r"\W+", " ", t.lower()).strip()
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def filter_new(items, days, seen):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    fresh, keys = [], set()
    for it in items:
        k = key(it)
        if k in seen or k in keys:
            continue
        if it["date"] and it["date"].astimezone(dt.timezone.utc) < cutoff:
            continue
        keys.add(k)
        it["id"] = k
        fresh.append(it)
    return fresh


# ---------- 3-4. Triage and brief (Claude) ----------
def claude(system, user, max_tokens=None):
    """Ask Claude through Claude Code (headless), using the signed-in Claude plan."""
    if not shutil.which("claude"):
        sys.exit("Claude Code not found. Install it and sign in with your Claude account first.")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # never bill an API key
    prompt = f"{system}\n\n---\n\n{user}"
    # News text is untrusted. Claude Code gets no tools (no files, no shell, no web)
    # and runs in an empty temporary folder, so it can only read the prompt and answer.
    blocked = ["Bash", "Edit", "Write", "NotebookEdit", "Read", "Glob", "Grep",
               "WebFetch", "WebSearch", "Agent", "Task", "MultiEdit"]
    for _ in range(len(blocked)):
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run(["claude", "-p", "--disallowedTools", ",".join(blocked)], input=prompt,
                               capture_output=True, text=True, env=env, cwd=empty, timeout=600)
        # Tool names change between Claude Code versions: drop any name this version rejects
        bad = re.search(r'rule "([^"]+)" matches no known tool', r.stderr + r.stdout)
        if r.returncode == 0 or not bad or bad.group(1) not in blocked:
            break
        blocked.remove(bad.group(1))
    if r.returncode != 0:
        sys.exit(f"Claude Code failed: {r.stderr.strip()[:300]}")
    return r.stdout


def triage(items, batch=50):
    """Score items in batches so the JSON answer never gets cut off."""
    system = (ROOT / "prompts" / "triage.md").read_text()
    scores = {}
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        lines = [f'{it["id"]} | {it["region"]} | {it["source"]} | {it["title"]} | {it["summary"][:200]}'
                 for it in chunk]
        raw = claude(system, "\n".join(lines))
        try:
            scores.update(json.loads(raw[raw.find("{"): raw.rfind("}") + 1]))
        except json.JSONDecodeError:
            print(f"  triage batch {i // batch} unreadable, skipped", file=sys.stderr)
    for it in items:
        it["score"] = int(scores.get(it["id"], 0))
    return sorted([i for i in items if i["score"] >= 2], key=lambda i: -i["score"])


def brief(items, week):
    system = (ROOT / "prompts" / "brief.md").read_text()
    body = "\n".join(f'- [{it["region"]}] {it["title"]} ({it["source"]}, '
                     f'{it["date"]:%d %b %Y} ) {it["link"]}\n  {it["summary"]}'
                     if it["date"] else f'- [{it["region"]}] {it["title"]} ({it["source"]}) {it["link"]}'
                     for it in items[:40])
    return claude(system, f"Week: {week}\n\nItems scored 2-3 this week:\n{body}")


# ---------- 5. Save ----------
def to_html(md, title):
    out = []
    for line in md.splitlines():
        line = html.escape(line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", line)
        if line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("- "):
            out.append(f"<li>{line[2:]}</li>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    css = ("body{font:15px/1.5 Georgia,serif;max-width:680px;margin:32px auto;padding:0 16px;color:#111}"
           "h1{font-size:22px}h2{font-size:16px;margin-top:22px}li{margin:4px 0}a{color:#0b5}")
    return f"<!doctype html><meta charset=utf-8><title>{title}</title><style>{css}</style>{''.join(out)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    seen = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    items, errors = scan(load_sources())
    fresh = filter_new(items, a.days, seen)
    print(f"scanned {len(items)} items, {len(fresh)} new, {len(errors)} feed errors", file=sys.stderr)
    for e in errors:
        print("  feed error:", e, file=sys.stderr)

    week = dt.date.today().strftime("%G-W%V")
    OUT.mkdir(exist_ok=True)
    if a.no_llm:
        for it in fresh:
            print(f'[{it["region"]}] {it["title"]} - {it["source"]}')
        return

    ranked = triage(fresh)
    md = brief(ranked, week)
    (OUT / f"radar-{week}.md").write_text(md)
    page = to_html(md, f"EM Fintech Radar {week}")
    (OUT / f"radar-{week}.html").write_text(page)

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(sorted(seen | {i["id"] for i in fresh})))
    print(f"wrote out/radar-{week}.md ({len(ranked)} items kept)", file=sys.stderr)


if __name__ == "__main__":
    main()
