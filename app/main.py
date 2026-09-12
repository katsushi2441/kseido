"""Kurage 制度ナビ（kseido）— 困りごと → 使える地域制度（市・区・県・国）＋申請先・期限・必要書類＋出典リンク＋相談記録。

  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18385

- 制度データは data/programs.json（他自治体はこのファイルと data/wards.json の差し替え）。
- 検索は Python の絞り込みだけ。LLM は使わない。
- 相談記録は outputs/kseido.sqlite（KSEIDO_ADMIN_TOKEN を知る人だけが読み書き）。
- 公開は kurage.exbridge.jp/kseido.php/ の透過プロキシ経由。相対パスで動く。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import secrets
import sqlite3
from collections import OrderedDict

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DB = os.environ.get("KSEIDO_DB", os.path.join(ROOT, "outputs", "kseido.sqlite"))
TOKEN = os.environ.get("KSEIDO_ADMIN_TOKEN", "")
PUBLIC_BASE = os.environ.get("KSEIDO_PUBLIC_BASE", "https://kurage.exbridge.jp/kseido.php/").rstrip("/") + "/"
SITE = "Kurage 制度ナビ"
VERSION = "0.1.0"

app = FastAPI(title=SITE, version=VERSION, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def load_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


PROG = load_json("programs.json")
WARDS = load_json("wards.json")["wards"]
PROGRAMS: list[dict] = PROG["programs"]
BY_ID = {p["id"]: p for p in PROGRAMS}
SITUATIONS: list[dict] = PROG["situations"]
SIT_BY_ID = {s["id"]: s for s in SITUATIONS}
WHO_TAGS: list[dict] = PROG.get("who_tags", [])
WARD_BY_SLUG = {w["slug"]: w for w in WARDS}
STATUS_LABEL = {"常時": "随時受付", "期限": "期限あり", "終了": "受付終了", "年度": "年度内"}


def root_prefix(request: Request) -> str:
    segs = [s for s in request.url.path.split("/") if s]
    return "../" * max(0, len(segs) - 1)


templates.env.globals.update(site_name=SITE, public_base=PUBLIC_BASE, version=VERSION, status_label=STATUS_LABEL, wards=WARDS, situations=SITUATIONS, who_tags=WHO_TAGS, asof=PROG.get("asof"), region=PROG.get("region", ""))


def deadline_state(p: dict, today: dt.date | None = None) -> dict:
    d = p.get("deadline") or {}
    today = today or dt.date.today()
    typ = d.get("type", "常時")
    date = d.get("date")
    days = None
    if date:
        try:
            days = (dt.date.fromisoformat(date) - today).days
        except ValueError:
            days = None
    if typ == "期限" and days is not None and days < 0:
        typ = "終了"
    return {"type": typ, "date": date, "days": days, "note": d.get("note", ""), "label": STATUS_LABEL.get(typ, typ)}


def match(situation: str = "", ward: str = "", tags: list[str] | None = None, q: str = "", include_ended: bool = False) -> list[dict]:
    tags = [t for t in (tags or []) if t]
    out = []
    for p in PROGRAMS:
        if situation and situation not in p.get("situations", []):
            continue
        if tags and not all(any(t == wt or wt.startswith(t + ":") for wt in p.get("who_tags", [])) for t in tags):
            # 指定された属性タグを全部満たす制度だけ（タグを持たない制度は「誰でも」扱いにしない＝厳しめ）
            continue
        if q:
            hay = " ".join([p.get("name", ""), p.get("who", ""), p.get("benefit", ""), " ".join(p.get("keywords", []))])
            if q not in hay:
                continue
        st = deadline_state(p)
        if st["type"] == "終了" and not include_ended:
            continue
        item = dict(p)
        item["state"] = st
        item["apply_resolved"] = resolve_apply(p, ward)
        out.append(item)
    order = {"期限": 0, "年度": 1, "常時": 2, "終了": 3}
    out.sort(key=lambda x: (order.get(x["state"]["type"], 9), x["state"]["days"] if x["state"]["days"] is not None else 9999, x.get("level_order", 9), x["name"]))
    return out


def resolve_apply(p: dict, ward_slug: str) -> dict:
    a = dict(p.get("apply") or {})
    if a.get("ward_window") and ward_slug and ward_slug in WARD_BY_SLUG:
        w = WARD_BY_SLUG[ward_slug]
        k = w.get("kuyakusho") or {}
        a["window"] = f"{w['name']}役所 {a.get('ward_section', '')}".strip()
        a["addr"] = k.get("addr")
        a["tel"] = a.get("ward_tel", {}).get(ward_slug) or k.get("tel") or a.get("tel")
        a["ward"] = w["name"]
    return a


# ---------------- pages ----------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    counts = {s["id"]: sum(1 for p in PROGRAMS if s["id"] in p.get("situations", []) and deadline_state(p)["type"] != "終了") for s in SITUATIONS}
    soon = [dict(p, state=deadline_state(p)) for p in PROGRAMS]
    soon = [p for p in soon if p["state"]["type"] == "期限" and p["state"]["days"] is not None and 0 <= p["state"]["days"] <= 90]
    soon.sort(key=lambda p: p["state"]["days"])
    return templates.TemplateResponse(request, "index.html", {"root": root_prefix(request), "counts": counts, "soon": soon[:6], "n": len([p for p in PROGRAMS if deadline_state(p)["type"] != "終了"]), "path": ""})


@app.get("/s/{sid}", response_class=HTMLResponse)
def situation(request: Request, sid: str, ward: str = "", tag: list[str] | None = None, q: str = "", ended: str = ""):
    s = SIT_BY_ID.get(sid)
    if not s:
        return HTMLResponse("<h1>見つかりません</h1>", status_code=404)
    tags = request.query_params.getlist("tag")
    items = match(sid, ward, tags, q, include_ended=(ended == "1"))
    tag_defs = [t for t in WHO_TAGS if t["id"] in s.get("tags", []) or not s.get("tags")]
    return templates.TemplateResponse(request, "situation.html", {"root": root_prefix(request), "s": s, "items": items, "ward": ward, "tags": tags, "tag_defs": tag_defs, "q": q, "ended": ended, "path": f"s/{sid}"})


@app.get("/p/{pid}", response_class=HTMLResponse)
def program(request: Request, pid: str, ward: str = ""):
    p = BY_ID.get(pid)
    if not p:
        return HTMLResponse("<h1>見つかりません</h1>", status_code=404)
    item = dict(p, state=deadline_state(p), apply_resolved=resolve_apply(p, ward))
    related = [dict(x, state=deadline_state(x)) for x in PROGRAMS if x["id"] != pid and set(x.get("situations", [])) & set(p.get("situations", []))][:6]
    return templates.TemplateResponse(request, "program.html", {"root": root_prefix(request), "p": item, "ward": ward, "related": related, "path": f"p/{pid}"})


@app.get("/ku/{slug}", response_class=HTMLResponse)
def ward_page(request: Request, slug: str):
    w = WARD_BY_SLUG.get(slug)
    if not w:
        return HTMLResponse("<h1>見つかりません</h1>", status_code=404)
    items = [dict(p, state=deadline_state(p), apply_resolved=resolve_apply(p, slug)) for p in PROGRAMS if (p.get("apply") or {}).get("ward_window") and deadline_state(p)["type"] != "終了"]
    return templates.TemplateResponse(request, "ward.html", {"root": root_prefix(request), "w": w, "items": items, "path": f"ku/{slug}"})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    levels = OrderedDict()
    for p in PROGRAMS:
        levels[p.get("level", "")] = levels.get(p.get("level", ""), 0) + 1
    return templates.TemplateResponse(request, "about.html", {"root": root_prefix(request), "levels": levels, "n": len(PROGRAMS), "path": "about"})


# ---------------- API ----------------
@app.get("/api/match")
def api_match(request: Request, situation: str = "", ward: str = "", q: str = "", ended: str = ""):
    tags = request.query_params.getlist("tag")
    return JSONResponse({"count": len(match(situation, ward, tags, q, ended == "1")), "items": match(situation, ward, tags, q, ended == "1"), "asof": PROG.get("asof")})


@app.get("/api/programs")
def api_programs():
    return JSONResponse({"asof": PROG.get("asof"), "count": len(PROGRAMS), "items": [dict(p, state=deadline_state(p)) for p in PROGRAMS]})


@app.get("/healthz")
def healthz():
    return {"ok": True, "programs": len(PROGRAMS), "asof": PROG.get("asof"), "version": VERSION}


# ---------------- 相談記録（事務所用・トークン制） ----------------
def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS record (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, updated_at TEXT, staff TEXT, resident TEXT, ward TEXT, situation TEXT, who TEXT, memo TEXT, programs TEXT, status TEXT, next_action TEXT, next_date TEXT)""")
    return con


def authed(request: Request) -> bool:
    if not TOKEN:
        return False
    t = request.query_params.get("token") or request.cookies.get("kseido_token")
    return bool(t) and secrets.compare_digest(t, TOKEN)


@app.get("/records", response_class=HTMLResponse)
def records(request: Request, status: str = ""):
    if not authed(request):
        return templates.TemplateResponse(request, "records_login.html", {"root": root_prefix(request), "path": "records", "enabled": bool(TOKEN)})
    con = db()
    try:
        rows = con.execute("SELECT * FROM record" + (" WHERE status=?" if status else "") + " ORDER BY id DESC", ((status,) if status else ())).fetchall()
        rows = [dict(r, programs_list=[BY_ID.get(i, {"id": i, "name": i}) for i in json.loads(r["programs"] or "[]")]) for r in rows]
    finally:
        con.close()
    resp = templates.TemplateResponse(request, "records.html", {"root": root_prefix(request), "rows": rows, "status": status, "programs": PROGRAMS, "path": "records", "token": request.query_params.get("token", "")})
    if request.query_params.get("token"):
        resp.set_cookie("kseido_token", request.query_params["token"], httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/records")
async def records_add(request: Request):
    if not authed(request):
        return PlainTextResponse("forbidden", status_code=403)
    f = await request.form()
    now = dt.datetime.now().isoformat(timespec="minutes")
    programs = [p for p in f.getlist("programs") if p]
    con = db()
    try:
        rid = f.get("id")
        if rid:
            con.execute("UPDATE record SET updated_at=?,staff=?,resident=?,ward=?,situation=?,who=?,memo=?,programs=?,status=?,next_action=?,next_date=? WHERE id=?",
                        (now, f.get("staff", ""), f.get("resident", ""), f.get("ward", ""), f.get("situation", ""), f.get("who", ""), f.get("memo", ""), json.dumps(programs, ensure_ascii=False), f.get("status", "対応中"), f.get("next_action", ""), f.get("next_date", ""), int(rid)))
        else:
            con.execute("INSERT INTO record(created_at,updated_at,staff,resident,ward,situation,who,memo,programs,status,next_action,next_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now, now, f.get("staff", ""), f.get("resident", ""), f.get("ward", ""), f.get("situation", ""), f.get("who", ""), f.get("memo", ""), json.dumps(programs, ensure_ascii=False), f.get("status", "対応中"), f.get("next_action", ""), f.get("next_date", "")))
        con.commit()
    finally:
        con.close()
    return RedirectResponse(url="records", status_code=303)


@app.get("/records.csv")
def records_csv(request: Request):
    if not authed(request):
        return PlainTextResponse("forbidden", status_code=403)
    con = db()
    try:
        rows = con.execute("SELECT * FROM record ORDER BY id").fetchall()
    finally:
        con.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "updated_at", "staff", "resident", "ward", "situation", "who", "memo", "programs", "status", "next_action", "next_date"])
    for r in rows:
        w.writerow([r[k] for k in ["id", "created_at", "updated_at", "staff", "resident", "ward", "situation", "who", "memo", "programs", "status", "next_action", "next_date"]])
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=kseido_records.csv"})


# ---------------- SEO ----------------
@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nDisallow: /records\nSitemap: {PUBLIC_BASE}sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    today = dt.date.today().isoformat()
    urls = [PUBLIC_BASE, PUBLIC_BASE + "about"] + [PUBLIC_BASE + f"s/{s['id']}" for s in SITUATIONS] + [PUBLIC_BASE + f"p/{p['id']}" for p in PROGRAMS] + [PUBLIC_BASE + f"ku/{w['slug']}" for w in WARDS]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n" for u in urls) + "</urlset>\n"
    return Response(content=body, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms():
    lines = [f"# {SITE}（{PROG.get('region','')} デモ版）", "", "> 困りごと（出産・子育て、ひとり親、学費、医療費、住まいの耐震・改修、収入減、高齢の親、物価高など）から、使える地域制度（市・区・県・国）を引き、申請先・電話・期限・必要書類と出典リンクを出すサイト。議員事務所・相談窓口が自分で維持できる形（制度データは JSON 1本）。", "", f"- 制度数: {len(PROGRAMS)}（データ時点 {PROG.get('asof')}）", f"- 困りごと: {', '.join(s['name'] for s in SITUATIONS)}", f"- API: {PUBLIC_BASE}api/match?situation=<id>&ward=<区slug>&tag=<属性>、{PUBLIC_BASE}api/programs", f"- 制度ページ: {PUBLIC_BASE}p/<id>", "", "## 制度一覧"]
    for p in PROGRAMS:
        st = deadline_state(p)
        lines.append(f"- {p['name']}（{p.get('level','')}・{st['label']}{'・'+st['date'] if st['date'] else ''}）: {p.get('benefit','')[:80]} → {PUBLIC_BASE}p/{p['id']}（出典 {p.get('source_url','')}）")
    lines += ["", "## 買い切り版",
              "- 商品ページ: https://kappstore.exbridge.jp/app.php?id=237974724fb41216",
              "- 税込55,000円。ソースコード（MIT）・制度データのJSON・設置手順書を同梱。自社サーバーで動かせる。"]
    lines += ["", "## 免責", "要件・金額・期限は変わります。各制度の出典リンク（公式ページ）で確認し、申請先に電話してから動いてください。名古屋市サイトの本文は転載せず、事実（窓口・電話・期限・書類）とリンクだけを載せています。"]
    return "\n".join(lines) + "\n"
