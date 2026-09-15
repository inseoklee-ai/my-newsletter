"""나만의 뉴스레터 에이전트 그래프.

임포트만 해도 안전해야 하므로, 실제 실행은 run.py의
`if __name__ == "__main__":` 아래에서만 일어난다.
"""

import operator
import os
import pathlib
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, TypedDict

import feedparser
import requests
import trafilatura
import yaml
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from openai import OpenAI
from pydantic import BaseModel, Field

UA = {"User-Agent": "Mozilla/5.0 (my-newsletter-course)"}
client = OpenAI()  # OPENAI_API_KEY는 환경변수(Actions Secrets)에서 읽는다

SOURCES = [(s["name"], s["url"]) for s in
           yaml.safe_load(pathlib.Path("sources.yaml").read_text(encoding="utf-8"))["sources"]]


def build_criteria(cfg):
    out = [f"독자는 {cfg['독자']['누구']}입니다.",
           f"이미 아는 것: {cfg['독자']['이미_아는_것']}",
           "", "중요도 기준 (위에 있을수록 우선):"]
    out += [f"- {x}" for x in cfg["중요도_기준"]]
    out += ["", "버릴 것:"]
    out += [f"- {x}" for x in cfg["버릴_것"]]
    return "\n".join(out)


CFG = yaml.safe_load(pathlib.Path("audience.yaml").read_text(encoding="utf-8"))


class Brief(TypedDict):
    hours: int
    collected: list
    picked: list
    drafted: Annotated[list, operator.add]
    verified: list
    channels: dict  # ← 채널별 발행 결과 (지표 기록용)
    log: Annotated[list, operator.add]


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def published_at(entry):
    t = entry.get("published_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def collect(s: dict) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=s["hours"])
    items, dead, seen = [], [], set()
    for name, url in SOURCES:
        try:
            feed = feedparser.parse(requests.get(url, headers=UA, timeout=20).content)
        except Exception:
            dead.append(name)
            continue
        for e in feed.entries:
            at = published_at(e)
            if not at or at < cutoff:
                continue
            key = e.link.split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": e.title, "url": e.link, "source": name, "at": at,
                          "summary": strip_tags(e.get("summary", ""))[:300]})
    return {"collected": items,
            "log": [f"① 수집   {s['hours']}시간 창 · {len(items)}건"
                    + (f" · 응답 없음 {dead}" if dead else "")]}


class Pick(BaseModel):
    index: int = Field(description="후보 목록에서의 번호")
    reason: str = Field(description="왜 골랐는지 한 문장")
    event: str = Field(description="이 기사가 다루는 사건을 짧은 라벨로. 같은 사건이면 같은 라벨")


class Shortlist(BaseModel):
    picks: list[Pick]


BATCH, TARGET = 40, 5
CRITERIA = build_criteria(CFG)


def ask_picks(items, n):
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']}" for i, it in enumerate(items))
    sys = (f"{CRITERIA}\n\n아래 목록에서 중요한 순서대로 {n}건을 고르세요.\n"
           "같은 사건을 다룬 기사에는 같은 event 라벨을 붙이세요.")
    out = client.chat.completions.parse(
        model="gpt-4.1-mini", temperature=0,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": listing}],
        response_format=Shortlist).choices[0].message.parsed
    return [p for p in out.picks if 0 <= p.index < len(items)]


def select(s: dict) -> dict:
    items = s["collected"]
    survivors = []
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        survivors += [chunk[p.index] for p in ask_picks(chunk, 8)]
    finals = ask_picks(survivors, TARGET)
    return {"picked": [survivors[p.index] for p in finals],
            "log": [f"② 선별   {len(items)} → 예선 {len(survivors)} → {len(finals)}건"]}


class Draft(BaseModel):
    headline: str = Field(description="20자 내외의 한국어 헤드라인")
    summary: str = Field(description="세 문장 요약. ~합니다체, 과장 없이 건조하게")
    why: str = Field(description="스터디원들에게 왜 중요한지 한 문장")
    실무적용법: str = Field(description="지금 당장 실무나 스터디에서 해볼 수 있는 구체적 행동 한 가지. "
                                   "원문에 근거가 없으면 일반론 대신 '적용 방안 불명확'이라고 쓸 것")


SYS = (f"당신은 뉴스레터 기자입니다. 독자는 {CFG['독자']['누구']}입니다.\n"
       "아래 기사 본문을 읽고 헤드라인·요약·왜 중요한지·실무 적용법을 쓰세요.\n"
       "반드시 한국어로 쓰세요. '주목된다·기대를 모은다' 같은 기자체 표현은 쓰지 마세요.")


class ReportIn(TypedDict):
    item: dict


def extract_body(url):
    d = trafilatura.fetch_url(url)
    return trafilatura.extract(d) if d else None


def draft(body):
    return client.chat.completions.parse(
        model="gpt-4.1-mini", temperature=0,
        messages=[{"role": "system", "content": SYS},
                  {"role": "user", "content": body[:6000]}],
        response_format=Draft).choices[0].message.parsed


def fan_report(s: dict):
    return [Send("report", {"item": it}) for it in s["picked"]]


def report(s: ReportIn) -> dict:
    it = s["item"]
    body = extract_body(it["url"])
    if not body or len(body) < 600:
        return {"drafted": [],
                "log": [f"   취재 제외 {it['source']} · 본문 {len(body or '')}자"]}
    d = draft(body)
    return {"drafted": [{**it, "body": body[:6000], **d.model_dump()}]}


class Verdict(BaseModel):
    ok: bool = Field(description="요약이 원문에 근거하면 true")
    problems: list[str] = Field(description="근거 없는 부분. 없으면 빈 목록")


SYS_CHECK = ("요약이 원문에서 뒷받침되는지 판정하세요.\n"
             "headline과 summary만 보고 판단하세요 — why와 실무적용법은 독자 관점의 "
             "해석이라 원문과 대조할 대상이 아니니 판정에서 제외합니다.\n"
             "번역이나 단위 환산은 문제가 아닙니다.")


def check(d):
    user = (f"[원문]\n{d['body'][:5000]}\n\n"
            f"[헤드라인]\n{d['headline']}\n\n[요약]\n{d['summary']}")
    return client.chat.completions.parse(
        model="gpt-4.1-mini", temperature=0,
        messages=[{"role": "system", "content": SYS_CHECK},
                  {"role": "user", "content": user}],
        response_format=Verdict).choices[0].message.parsed


def verify(s: dict) -> dict:  # 예외 처리 정책: 재생성 없이 스킵 — 실무 적용과 안 맞는 기사는
    kept, dropped = [], []      # 다시 써도 크게 달라지지 않는다고 판단해 재시도 비용을 아낀다
    for d in s["drafted"]:
        (kept if check(d).ok else dropped).append(d)
    return {"verified": kept,
            "log": [f"④ 검수   {len(s['drafted'])} → {len(kept)}건"
                    + (f" · 불합격 {[x['source'] for x in dropped]}" if dropped else "")]}


EMAIL_RECIPIENTS = ["lis2983@gmail.com", "cyphrus101@gmail.com", "yjj890@hanmail.net"]
DISCORD_USERNAME = "나만의뉴스레터봇"


def make_lead(arts):
    if not arts:
        return ""
    srcs = ", ".join(dict.fromkeys(a["source"] for a in arts))
    return f"오늘은 {len(arts)}건을 골랐습니다. ({srcs})"


def build_embeds(run_id, lead, articles):
    if not articles:
        return [{"title": f"🗞️ {run_id}", "description": "오늘은 조용합니다."}]
    embeds = [{"title": f"🗞️ {run_id} · AI 스터디 브리핑", "description": lead}]
    for i, a in enumerate(articles, 1):
        desc = a["summary"]
        if a.get("why"):
            desc += f"\n\n💡 **{a['why']}**"
        if a.get("실무적용법"):
            desc += f"\n\n🛠️ {a['실무적용법']}"
        embeds.append({
            "title": f"{i}. {a['headline']}"[:256],
            "description": desc[:4096],
            "url": a["url"],
            "footer": {"text": f"{a['source']} · {a['when']}"},
        })
    total = lambda es: sum(len(e.get("title", "")) + len(e.get("description", ""))
                           + len(e.get("footer", {}).get("text", "")) for e in es)
    while len(embeds) > 10 or total(embeds) > 5800:
        embeds.pop()
    return embeds


def send_discord(run_id, lead, articles, webhook, dry_run):  # -> "sent"|"dry_run"|"skipped"|"failed"
    payload = {"username": DISCORD_USERNAME, "embeds": build_embeds(run_id, lead, articles)}
    if dry_run:
        print(f"[dry-run:discord] embed {len(payload['embeds'])}개 — 보내지 않음")
        return "dry_run"
    if not webhook:
        print("발행(Discord): 건너뜀 — DISCORD_WEBHOOK_URL 미설정")
        return "skipped"
    r = requests.post(webhook, json=payload, timeout=20)
    ok = r.status_code in (200, 204)
    print("발행(Discord):", "성공" if ok else f"실패 {r.status_code} {r.text[:120]}")
    return "sent" if ok else "failed"


def build_email_body(run_id, lead, articles):
    if not articles:
        return f"{run_id} — 오늘은 조용합니다."
    lines = [f"{run_id} · AI 스터디 브리핑", "", lead, ""]
    for i, a in enumerate(articles, 1):
        lines += [f"{i}. {a['headline']}", a["summary"]]
        if a.get("why"):
            lines.append(f"💡 {a['why']}")
        if a.get("실무적용법"):
            lines.append(f"🛠️ {a['실무적용법']}")
        lines += [f"({a['source']} · {a['when']}) {a['url']}", ""]
    return "\n".join(lines)


def send_email(run_id, lead, articles, recipients, dry_run):  # -> "sent"|"dry_run"|"skipped"|"failed"
    body = build_email_body(run_id, lead, articles)
    if dry_run:
        print(f"[dry-run:email] 수신자 {len(recipients)}명 · {len(body)}자 — 보내지 않음")
        return "dry_run"
    sender, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print("발행(Email): 건너뜀 — GMAIL_ADDRESS/GMAIL_APP_PASSWORD 미설정")
        return "skipped"
    import smtplib
    from email.message import EmailMessage  # 유니코드 헤더/본문을 자동으로 안전하게 인코딩한다
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[AI 스터디 브리핑] {run_id}", sender, ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print("발행(Email): 성공")
        return "sent"
    except Exception as e:
        print(f"발행(Email): 실패 {e}")
        return "failed"


def send_telegram(run_id, lead, articles, dry_run):  # -> "sent"|"dry_run"|"skipped"|"failed"
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    text = build_email_body(run_id, lead, articles)[:4000]
    if dry_run:
        print(f"[dry-run:telegram] {len(text)}자 — 보내지 않음")
        return "dry_run"
    if not token or not chat_id:
        print("발행(Telegram): 건너뜀 — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정")
        return "skipped"
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat_id, "text": text}, timeout=20)
    ok = r.status_code == 200
    print("발행(Telegram):", "성공" if ok else f"실패 {r.status_code} {r.text[:120]}")
    return "sent" if ok else "failed"


def publish(s: dict) -> dict:
    arts = [{"headline": a["headline"], "summary": a["summary"], "why": a["why"],
             "실무적용법": a["실무적용법"], "url": a["url"], "source": a["source"],
             "when": a["at"].strftime("%m-%d %H:%M")} for a in s["verified"]]
    today = datetime.now().strftime("%Y-%m-%d")
    dry_run = os.environ.get("DRY_RUN", "1") == "1"
    lead = make_lead(arts)

    d = send_discord(today, lead, arts, os.environ.get("DISCORD_WEBHOOK_URL"), dry_run)
    e = send_email(today, lead, arts, EMAIL_RECIPIENTS, dry_run)
    t = send_telegram(today, lead, arts, dry_run)

    label = f"{len(arts)}건" if arts else "조용합니다"
    return {"channels": {"discord": d, "email": e, "telegram": t},
            "log": [f"⑤ 발행   {label} · Discord {d} · Email {e} · Telegram {t}"]}


NODES = {"collect": collect, "select": select, "report": report,
         "verify": verify, "publish": publish}


def build_graph():
    g = StateGraph(Brief)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "collect")
    g.add_edge("collect", "select")
    g.add_conditional_edges("select", fan_report, ["report"])
    g.add_edge("report", "verify")
    g.add_edge("verify", "publish")
    g.add_edge("publish", END)
    return g.compile()


INIT = {"hours": 24, "collected": [], "picked": [], "drafted": [], "verified": [],
        "channels": {}, "log": []}


def run():                                     # 돌리고, 한 줄 남긴다
    import json

    out = build_graph().invoke(INIT)
    row = {"run_id":    datetime.now().strftime("%Y-%m-%d %H:%M"),
           "collected": len(out["collected"]),
           "picked":    len(out["picked"]),
           "drafted":   len(out["drafted"]),
           "published": len(out["verified"]),
           "hours":     out["hours"],
           "channels":  out["channels"],
           "by_source": {},
           "log":       out["log"]}
    for a in out["verified"]:
        row["by_source"][a["source"]] = row["by_source"].get(a["source"], 0) + 1
    path = pathlib.Path("store/metrics.jsonl")
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:  # 로케일 기본 인코딩에 깨지지 않도록 명시
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out
