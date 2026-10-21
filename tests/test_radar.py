import sys, json, datetime as dt, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import radar
now = dt.datetime.now(dt.timezone.utc)
rss = f"""<?xml version="1.0"?><rss><channel>
<item><title>Banxico: Directo a M&#233;xico ends 20 November</title><link>https://x/1</link><pubDate>{now:%a, %d %b %Y %H:%M:%S +0000}</pubDate><description>&lt;b&gt;Fed&lt;/b&gt; exits rail</description></item>
<item><title>Banxico: Directo a Mexico ends 20 November!</title><link>https://x/dup</link><pubDate>{now:%a, %d %b %Y %H:%M:%S +0000}</pubDate></item>
<item><title>Old news</title><link>https://x/2</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate></item>
</channel></rss>""".encode()
atom = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>MAS licence granted</title><link href="https://x/3"/><updated>{now.isoformat()}</updated><summary>MPI licence</summary></entry></feed>""".encode()
items = radar.parse_feed(rss, {"name":"Banxico","region":"LatAm"}) + radar.parse_feed(atom, {"name":"MAS","region":"SEA"})
fresh = radar.filter_new(items, 7, set())
print([i["title"] for i in fresh])
assert len(fresh)==2, "dedupe/date filter failed"
# mock Claude
calls=[]
def fake(system, user, max_tokens=4000):
    calls.append(system[:30])
    if "Return only JSON" in system:
        return "Sure: " + json.dumps({fresh[0]["id"]:3, fresh[1]["id"]:1})
    return "# EM Fintech Radar | test\n**The one thing:** x\n## Why it matters\n- [a](https://x/1)"
radar.claude = fake
ranked = radar.triage(fresh); print("kept", [i["title"] for i in ranked]); assert len(ranked)==1
md = radar.brief(ranked, "2026-W43"); h = radar.to_html(md, "t"); assert '<a href="https://x/1">' in h
print("sources:", len(radar.load_sources())); print("google url ok:", radar.google_news_url("a b")[:60])
a={"title":"Banxico closes rail - Reuters"}; b={"title":"Banxico closes rail - El Economista"}
assert radar.key(a)==radar.key(b), "publisher suffix not stripped"
assert "hl=es-419" in radar.google_news_url("x","es")
big=[dict(fresh[0], id=f"i{n}") for n in range(120)]
radar.claude=lambda s,u,max_tokens=0: json.dumps({l.split(" | ")[0]:2 for l in u.splitlines()})
assert len(radar.triage(big))==120, "batching lost items"
print("ALL OK")
