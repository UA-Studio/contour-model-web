"""공개 서버: 의견 보내기, 개발자 열쇠(/dev), 받은 의견 보기(/admin/feedback)."""
import json
import os
import re
import time
from . import app_state as S
from .app_state import _sess, _slog, _state




def _feedback(self, n):
    """사이트 '의견 보내기': 서버 자료 폴더의 feedback.jsonl 에 한 줄씩 쌓는다."""
    try:
        body = json.loads(self.rfile.read(n) or b"{}")
    except Exception:
        return self._send(400, {"error": "잘못된 요청"})
    text = str(body.get("text") or "").strip()[:4000]
    if not text:
        return self._send(400, {"error": "내용을 적어 주세요."})
    job = _state().get("job")
    rec = dict(at=time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600)),   # 한국 시간 text=text, kind=str(body.get("kind") or "")[:20],
               rating=body.get("rating") if isinstance(body.get("rating"), int) else None,
               contact=str(body.get("contact") or "").strip()[:200], page=str(body.get("page") or "")[:40],
               sid=_sess()["sid"][:8],
               last_error=(job.error if job else None),
               ua=(self.headers.get("User-Agent") or "")[:200])
    os.makedirs(S.DATA, exist_ok=True)
    with S.LOCK, open(os.path.join(S.DATA, "feedback.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return self._send(200, {"ok": True})


def _dev_mark():
    """개발자 쿠키 값: 관리자 토큰에서 만든 서명 (토큰 자체는 쿠키에 넣지 않음)."""
    import hashlib
    import hmac
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    return hmac.new(tok.encode(), b"contour-dev", hashlib.sha256).hexdigest() if tok else None


def _is_dev(handler):
    mark = _dev_mark()
    m = re.search(r"(?:^|;\s*)cdev=([0-9a-f]{64})", handler.headers.get("Cookie") or "")
    import hmac
    return bool(mark and m and hmac.compare_digest(m.group(1), mark))


def _dev_login(self, q):
    """/dev?key=관리자토큰 → 이 브라우저를 1년 동안 개발자로 기억하고 /app 으로. /dev?off=1 → 해제."""
    import hmac
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    if q.get("off"):
        cookie = "cdev=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    elif tok and hmac.compare_digest((q.get("key") or "").encode(), tok.encode()):
        cookie = f"cdev={_dev_mark()}; Path=/; Max-Age={365 * 86400}; HttpOnly; SameSite=Lax"
        if os.environ.get("CONTOUR_HTTPS") == "1":
            cookie += "; Secure"
        _slog("개발자 모드 켬")
    else:
        return self._send(403, {"error": "열쇠가 맞지 않습니다."})
    self.send_response(302)
    self.send_header("Set-Cookie", cookie)
    self.send_header("Location", "/app")
    self.send_header("Content-Length", "0")
    self.end_headers()


def _admin_feedback(self, q):
    tok = os.environ.get("CONTOUR_ADMIN_TOKEN")
    if not tok or q.get("token") != tok:
        return self._send(403, {"error": "없음"})
    p = os.path.join(S.DATA, "feedback.jsonl")
    rows = [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []
    stats = {"count": len(rows), "queue": len(S.QUEUE), "sessions": len(S.SESSIONS),
             "runs_today": sum(v for k, v in S.RUNS.items() if k[1] == time.strftime("%Y%m%d"))}
    if q.get("format") == "json":
        return self._send(200, dict(stats, items=rows[::-1]))
    from html import escape as h
    kinds = {"bug": "오류", "ux": "불편", "idea": "기능 제안", "etc": "기타"}
    trs = "".join(f"<tr><td>{h(r.get('at', ''))}</td><td>{h(kinds.get(r.get('kind'), r.get('kind') or ''))}</td>"
                  f"<td>{r.get('rating') or ''}</td><td class=t>{h(r.get('text', ''))}"
                  + (f"<small>마지막 오류: {h(r['last_error'])}</small>" if r.get("last_error") else "")
                  + f"</td><td>{h(r.get('contact') or '')}</td><td>{h(r.get('page') or '')}</td></tr>" for r in rows[::-1])
    page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>받은 의견</title><style>
@font-face{{font-family:P;src:url(/vendor/fonts/PretendardVariable.woff2)}}
body{{font-family:P,system-ui;margin:32px;color:#111;background:#fff}}h1{{font-size:24px}}
.s{{color:#555;margin-bottom:20px}}.s b{{color:#2563eb}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border-bottom:1px solid #e6e6e6;padding:10px 8px;text-align:left;vertical-align:top}}th{{color:#555;font-weight:600}}
td.t{{white-space:pre-wrap;max-width:560px}}small{{display:block;color:#8a8a8a;margin-top:6px}}</style>
<h1>받은 의견 {stats['count']}개</h1>
<p class=s>오늘 만든 모델 <b>{stats['runs_today']}</b>번 · 지금 기다리는 작업 <b>{stats['queue']}</b>개 · 지금 남아 있는 방문 칸 <b>{stats['sessions']}</b>개</p>
<table><tr><th>시간(한국)</th><th>종류</th><th>점수</th><th>내용</th><th>연락처</th><th>위치</th></tr>{trs}</table>"""
    return self._send(200, page, "text/html; charset=utf-8")
