"""모의고사 도우미 — 혼자 켜서 쓰는 프로그램.

    python -m mogosa.app

하면 서버가 뜨고 브라우저가 저절로 열린다. 인터넷 연결은 필요 없다.
바깥으로 나가는 요청이 하나도 없으므로 시험 문항이 밖으로 새지 않는다.

화면에서 하는 일은 셋이다.
    1. 평가원 PDF나 문항 JSON을 넣는다
    2. 성취기준 파일을 넣으면 문항별로 붙여 준다 (없어도 된다)
    3. 한글 파일을 받는다

성취기준 판정이 미덥지 않은 문항은 표에서 직접 고쳐 다시 만들 수 있다.
"""

from __future__ import annotations

import io
import json
import socket
import tempfile
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from .hwpx_writer import Style, render_exam
from .model import Exam

APP_NAME = "모의고사 도우미"
DEFAULT_PORT = 8765


# ----------------------------------------------------------------------
# 작업 보관 — 브라우저를 닫으면 사라지는 임시 저장소
# ----------------------------------------------------------------------


@dataclass
class Job:
    """변환 한 건. 성취기준을 고쳐서 다시 만들 수 있도록 상태를 들고 있는다."""

    token: str
    exam: Exam
    standards: object | None = None
    warnings: list[str] = field(default_factory=list)
    source_name: str = "시험지"
    options: dict = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)


class JobStore:
    def __init__(self, limit: int = 20):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._limit = limit
        self._lock = threading.Lock()

    def put(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.token] = job
            self._order.append(job.token)
            while len(self._order) > self._limit:
                self._jobs.pop(self._order.pop(0), None)

    def get(self, token: str) -> Job | None:
        with self._lock:
            return self._jobs.get(token)


JOBS = JobStore()
WORK_DIR = Path(tempfile.gettempdir()) / "mogosa_app"
WORK_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# 변환 실행
# ----------------------------------------------------------------------


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "시험지"
    for bad in '\\/:*?"<>|':
        stem = stem.replace(bad, "_")
    return stem


def _load_standards_from_upload(storage) -> object | None:
    """업로드된 성취기준 파일을 읽는다. .json도 되고 붙여넣은 .txt도 된다."""
    if storage is None or not storage.filename:
        return None
    from .standards import StandardSet

    raw = storage.read().decode("utf-8-sig", errors="replace")
    if Path(storage.filename).suffix.lower() == ".json":
        data = json.loads(raw)
        rows = data.get("standards", []) if isinstance(data, dict) else data
        subject = data.get("subject", "") if isinstance(data, dict) else ""
        return StandardSet.from_dicts(rows, subject=subject)
    return StandardSet.from_text(raw)


def _convert_as_is(storage, options: dict) -> tuple[Path, list[str]]:
    """'원본 그대로' 모드: 쪽을 사진처럼 떠서 옮긴다."""
    from .hwpx_writer import render_pdf_as_images

    suffix = Path(storage.filename).suffix.lower()
    if suffix != ".pdf":
        raise ValueError("'원본 그대로' 모드는 PDF에만 쓸 수 있습니다.")

    temp_pdf = WORK_DIR / f"{uuid.uuid4().hex}.pdf"
    storage.save(temp_pdf)
    try:
        stem = _safe_stem(storage.filename)
        out = WORK_DIR / f"{uuid.uuid4().hex[:12]}_{stem}_원본그대로.hwpx"
        render_pdf_as_images(temp_pdf, out, dpi=int(options.get("dpi", 200)))
        return out, [
            "원본 그대로 모드입니다. 모양은 원본과 같지만 글자를 고칠 수 없습니다.",
        ]
    finally:
        temp_pdf.unlink(missing_ok=True)


def _build_exam(storage, options: dict) -> tuple[Exam, list[str]]:
    """업로드한 파일에서 Exam을 만든다. 확장자로 PDF인지 JSON인지 가린다."""
    suffix = Path(storage.filename).suffix.lower()
    warnings: list[str] = []

    if suffix == ".json":
        exam = Exam.from_dict(json.loads(storage.read().decode("utf-8-sig")))
        warnings.extend(exam.validate())
        return exam, warnings

    if suffix != ".pdf":
        raise ValueError(f"PDF 또는 JSON 파일만 넣을 수 있습니다 (받은 것: {suffix or '확장자 없음'})")

    from .pdf_reader import parse_pdf

    temp_pdf = WORK_DIR / f"{uuid.uuid4().hex}.pdf"
    storage.save(temp_pdf)
    try:
        exam, report = parse_pdf(
            temp_pdf,
            columns=options.get("columns", 2),
            subject=options.get("subject", ""),
            title=options.get("title", ""),
        )
        warnings.extend(report.warnings)
        warnings.append(f"읽은 결과: {report.summary()}")
        return exam, warnings
    finally:
        temp_pdf.unlink(missing_ok=True)


def _analysis_rows(job: Job) -> list[dict]:
    """성취기준 검토용 표. 후보와 근거를 함께 준다."""
    rows = []
    results = {}
    if job.standards is not None:
        results = {r.number: r for r in (job.standards.match(q) for q in job.exam.questions)}

    for question in job.exam.questions:
        result = results.get(question.number)
        candidates = []
        if result:
            for candidate in result.candidates[:3]:
                standard = job.standards.get(candidate.code) if job.standards else None
                candidates.append({
                    "code": candidate.code,
                    "score": round(candidate.score, 2),
                    "summary": standard.short(34) if standard else "",
                    "matched": candidate.matched[:5],
                })
        confidence = question.standard_confidence or 0.0
        rows.append({
            "number": question.number,
            "stem": (question.stem[:60] + "…") if len(question.stem) > 60 else question.stem,
            "standard": question.standard or "",
            "confidence": round(confidence, 2),
            "needs_review": (not question.standard) or confidence < 0.35,
            "points": question.points or 2,
            "answer": question.answer,
            "choices": len(question.choices),
            "candidates": candidates,
        })
    return rows


def _render_outputs(job: Job) -> dict[str, Path]:
    """학생용·교사용 한글 파일을 만든다."""
    options = job.options
    style = Style(columns=options.get("columns", 2))
    if options.get("font"):
        style.body_font = options["font"]

    stem = _safe_stem(job.source_name)
    outputs: dict[str, Path] = {}

    student = WORK_DIR / f"{job.token}_{stem}.hwpx"
    render_exam(job.exam, student, style=style)
    outputs["student"] = student

    if options.get("answers") or options.get("spec_table"):
        teacher = WORK_DIR / f"{job.token}_{stem}_교사용.hwpx"
        render_exam(
            job.exam, teacher, style=style,
            include_answers=bool(options.get("answers")),
            include_spec_table=bool(options.get("spec_table")),
            standards=job.standards,
        )
        outputs["teacher"] = teacher

    data = WORK_DIR / f"{job.token}_{stem}.json"
    job.exam.to_json(data)
    outputs["json"] = data
    return outputs


def _job_payload(job: Job) -> dict:
    return {
        "token": job.token,
        "questions": len(job.exam.questions),
        "warnings": job.warnings,
        "rows": _analysis_rows(job),
        "has_standards": job.standards is not None,
        "standard_count": len(job.standards) if job.standards else 0,
        "unmapped": job.exam.unmapped_questions(),
        "downloads": {key: f"/download/{job.token}/{key}" for key in job.files},
    }


# ----------------------------------------------------------------------
# 웹 서버
# ----------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64MB

    @app.get("/")
    def index():
        return PAGE

    @app.post("/convert")
    def convert():
        try:
            upload = request.files.get("file")
            if upload is None or not upload.filename:
                return jsonify(error="파일을 먼저 선택해 주세요."), 400

            options = {
                "columns": int(request.form.get("columns", 2)),
                "subject": request.form.get("subject", "").strip(),
                "title": request.form.get("title", "").strip(),
                "answers": request.form.get("answers") == "on",
                "spec_table": request.form.get("spec_table") == "on",
                "mode": request.form.get("mode", "edit"),
            }

            if options["mode"] == "as_is":
                path, notes = _convert_as_is(upload, options)
                job = Job(
                    token=uuid.uuid4().hex[:12],
                    exam=Exam(),
                    warnings=notes,
                    source_name=upload.filename,
                    options=options,
                    files={"as_is": path},
                )
                JOBS.put(job)
                return jsonify(_job_payload(job))

            exam, warnings = _build_exam(upload, options)
            if options["subject"]:
                exam.subject = options["subject"]
            if options["title"]:
                exam.title = options["title"]

            standards = _load_standards_from_upload(request.files.get("standards"))
            if standards is not None:
                if not len(standards):
                    warnings.append("성취기준 파일에서 항목을 하나도 읽지 못했습니다. 형식을 확인해 주세요.")
                    standards = None
                else:
                    standards.tag_exam(exam)

            job = Job(
                token=uuid.uuid4().hex[:12],
                exam=exam,
                standards=standards,
                warnings=warnings,
                source_name=upload.filename,
                options=options,
            )
            job.files = _render_outputs(job)
            JOBS.put(job)
            return jsonify(_job_payload(job))

        except Exception as error:  # 사용자에게 붉은 글씨로 보여 준다
            traceback.print_exc()
            return jsonify(error=f"{type(error).__name__}: {error}"), 500

    @app.post("/update")
    def update():
        """표에서 고친 성취기준을 반영해 다시 만든다."""
        try:
            payload = request.get_json(force=True)
            job = JOBS.get(payload.get("token", ""))
            if job is None:
                return jsonify(error="작업을 찾을 수 없습니다. 다시 변환해 주세요."), 404

            edits = payload.get("standards", {})
            for number_text, code in edits.items():
                number = int(number_text)
                for question in job.exam.questions:
                    if question.number == number:
                        cleaned = (code or "").strip()
                        question.standard = cleaned or None
                        if cleaned:
                            question.standard_confidence = 1.0  # 사람이 정한 값
                        break

            job.options["answers"] = bool(payload.get("answers", job.options.get("answers")))
            job.options["spec_table"] = bool(payload.get("spec_table", job.options.get("spec_table")))
            job.files = _render_outputs(job)
            return jsonify(_job_payload(job))

        except Exception as error:
            traceback.print_exc()
            return jsonify(error=f"{type(error).__name__}: {error}"), 500

    @app.get("/download/<token>/<kind>")
    def download(token: str, kind: str):
        job = JOBS.get(token)
        if job is None or kind not in job.files:
            return "파일을 찾을 수 없습니다. 다시 변환해 주세요.", 404
        path = job.files[kind]
        return send_file(path, as_attachment=True, download_name=path.name.split("_", 1)[-1])

    @app.get("/health")
    def health():
        return jsonify(ok=True, name=APP_NAME)

    return app


def _find_free_port(preferred: int = DEFAULT_PORT) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return 0


def main(open_browser: bool = True, port: int | None = None) -> None:
    port = port or _find_free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"\n  {APP_NAME}")
    print(f"  주소: {url}")
    print("  창을 닫으려면 이 검은 창에서 Ctrl+C\n")

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    create_app().run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


# ----------------------------------------------------------------------
# 화면 — 바깥에서 아무것도 받아오지 않는다 (오프라인 동작)
# ----------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>모의고사 도우미</title>
<style>
  :root{
    --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --muted:#6b7280;
    --line:#e3e6ea; --accent:#1b4d8f; --accent-soft:#eaf1fa;
    --warn:#b45309; --warn-soft:#fef6e7; --danger:#b3261e; --ok:#166534;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Malgun Gothic","맑은 고딕",-apple-system,"Apple SD Gothic Neo",sans-serif;
       font-size:15px;line-height:1.6}
  header{background:var(--accent);color:#fff;padding:18px 24px}
  header h1{margin:0;font-size:19px;letter-spacing:-.02em}
  header p{margin:4px 0 0;opacity:.85;font-size:13px}
  main{max-width:960px;margin:0 auto;padding:24px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:20px;margin-bottom:18px}
  .card h2{margin:0 0 14px;font-size:15px;color:var(--accent)}
  .drop{border:2px dashed #c3cbd6;border-radius:10px;padding:30px 18px;text-align:center;
        cursor:pointer;transition:.15s;background:#fbfcfd}
  .drop:hover,.drop.over{border-color:var(--accent);background:var(--accent-soft)}
  .drop strong{display:block;font-size:16px;margin-bottom:4px}
  .drop span{color:var(--muted);font-size:13px}
  .picked{margin-top:10px;font-size:13px;color:var(--accent);font-weight:700;word-break:break-all}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
  label.field{display:block;font-size:13px;color:var(--muted);margin-bottom:5px}
  input[type=text],input[type=number],select{width:100%;padding:8px 10px;border:1px solid var(--line);
        border-radius:6px;font-size:14px;font-family:inherit;background:#fff}
  .checks{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px}
  .checks label{display:flex;align-items:center;gap:7px;font-size:14px;cursor:pointer}
  label.mode{display:flex;gap:9px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);
        border-radius:8px;margin-bottom:8px;cursor:pointer;font-size:13.5px;line-height:1.5}
  label.mode:has(input:checked){border-color:var(--accent);background:var(--accent-soft)}
  label.mode input{margin-top:3px}
  button{font-family:inherit;font-size:15px;font-weight:700;padding:12px 22px;border-radius:8px;
         border:0;background:var(--accent);color:#fff;cursor:pointer}
  button:hover{background:#16407a}
  button:disabled{background:#9aa5b1;cursor:progress}
  button.ghost{background:#fff;color:var(--accent);border:1px solid var(--accent);font-size:14px;padding:9px 16px}
  .small{font-size:13px;color:var(--muted)}
  .hidden{display:none}
  .msg{padding:12px 14px;border-radius:8px;margin-bottom:14px;font-size:14px}
  .msg.err{background:#fdecea;color:var(--danger);border:1px solid #f5c6c2}
  .msg.warn{background:var(--warn-soft);color:var(--warn);border:1px solid #f5dfb0}
  .msg.ok{background:#e8f5ec;color:var(--ok);border:1px solid #c2e2cd}
  .dl{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
  .dl a{display:inline-block;padding:11px 18px;border-radius:8px;background:var(--accent);
        color:#fff;text-decoration:none;font-weight:700;font-size:14px}
  .dl a.alt{background:#fff;color:var(--accent);border:1px solid var(--accent)}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
  th{background:#f2f4f7;font-weight:700;white-space:nowrap}
  td.num{text-align:center;width:42px;font-weight:700}
  tr.review{background:#fffaf0}
  .cand{display:inline-block;margin:2px 4px 2px 0;padding:2px 7px;border-radius:11px;
        background:var(--accent-soft);color:var(--accent);font-size:12px;cursor:pointer;border:1px solid #d3e0f0}
  .cand:hover{background:#d7e6f7}
  input.stdedit{width:130px;padding:5px 7px;border:1px solid var(--line);border-radius:5px;font-size:13px}
  .tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:700}
  .tag.review{background:#fdecc8;color:#8a5a00}
  .tag.ok{background:#dcf0e3;color:var(--ok)}
  footer{text-align:center;color:var(--muted);font-size:12px;padding:10px 0 30px}
</style>
</head>
<body>
<header>
  <h1>모의고사 도우미</h1>
  <p>평가원 PDF를 한글 파일로 바꾸고, 문항별 성취기준과 이원목적표를 만듭니다</p>
</header>

<main>
  <div class="card">
    <h2>1. 파일 넣기</h2>
    <div class="drop" id="drop">
      <strong>여기로 파일을 끌어다 놓으세요</strong>
      <span>평가원 기출 PDF, 또는 출제한 문항 JSON &nbsp;·&nbsp; 눌러서 고를 수도 있습니다</span>
      <div class="picked" id="picked"></div>
    </div>
    <input type="file" id="file" accept=".pdf,.json" class="hidden">

    <div style="margin-top:16px">
      <label class="field">성취기준 파일 (선택) — 넣으면 문항마다 성취기준을 붙입니다</label>
      <input type="file" id="standards" accept=".json,.txt" style="font-size:13px">
      <div class="small" style="margin-top:5px">
        교육과정 문서에서 복사해 <code>[12현윤01-01] 내용…</code> 형태로 한 줄에 하나씩 넣은 txt 파일이면 됩니다.
      </div>
    </div>
  </div>

  <div class="card">
    <h2>2. 설정</h2>
    <div class="grid">
      <div>
        <label class="field" for="subject">과목명 (머리말에 찍힙니다)</label>
        <input type="text" id="subject" placeholder="생활과 윤리">
      </div>
      <div>
        <label class="field" for="title">시험명</label>
        <input type="text" id="title" placeholder="2026학년도 1학기 기말고사 대비">
      </div>
      <div>
        <label class="field" for="columns">단 수</label>
        <select id="columns"><option value="2">2단 (평가원 형식)</option><option value="1">1단</option></select>
      </div>
    </div>
    <div style="margin-top:16px">
      <label class="field">변환 방식</label>
      <label class="mode"><input type="radio" name="mode" value="edit" checked>
        <span><b>편집할 수 있게</b> — 글자를 다시 짜 넣습니다. 문항을 고치거나 재구성할 수 있고,
        그림·도표는 원본에서 오려 붙입니다.</span></label>
      <label class="mode"><input type="radio" name="mode" value="as_is">
        <span><b>원본 그대로</b> — 쪽을 사진처럼 떠서 옮깁니다. 모양이 원본과 100% 같지만
        글자를 고칠 수 없습니다. 그대로 인쇄해 쓰실 때 알맞습니다.</span></label>
    </div>
    <div class="checks">
      <label><input type="checkbox" id="answers" checked> 교사용에 정답·해설 넣기</label>
      <label><input type="checkbox" id="spec_table" checked> 교사용에 이원목적표 넣기</label>
    </div>
    <div style="margin-top:18px">
      <button id="go">한글 파일 만들기</button>
      <span class="small" id="status" style="margin-left:12px"></span>
    </div>
  </div>

  <div id="result"></div>
</main>
<footer>인터넷에 아무것도 보내지 않습니다. 모든 처리는 이 컴퓨터 안에서 끝납니다.</footer>

<script>
let picked=null, token=null;
const $=id=>document.getElementById(id);
const drop=$('drop'), fileInput=$('file');

drop.onclick=()=>fileInput.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};
drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');
  if(e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);};
fileInput.onchange=e=>{if(e.target.files.length) setFile(e.target.files[0])};

function setFile(f){picked=f;$('picked').textContent='선택됨: '+f.name;}

function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}

$('go').onclick=async()=>{
  if(!picked){alert('먼저 파일을 넣어 주세요.');return;}
  const fd=new FormData();
  fd.append('file',picked);
  const st=$('standards').files[0]; if(st) fd.append('standards',st);
  fd.append('subject',$('subject').value);
  fd.append('title',$('title').value);
  fd.append('columns',$('columns').value);
  fd.append('mode',document.querySelector('input[name=mode]:checked').value);
  if($('answers').checked) fd.append('answers','on');
  if($('spec_table').checked) fd.append('spec_table','on');

  $('go').disabled=true; $('status').textContent='만드는 중입니다…';
  try{
    const res=await fetch('/convert',{method:'POST',body:fd});
    const data=await res.json();
    if(!res.ok) throw new Error(data.error||'변환에 실패했습니다');
    token=data.token; render(data);
    $('status').textContent='완료';
  }catch(err){
    $('result').innerHTML='<div class="card"><div class="msg err">'+esc(err.message)+'</div></div>';
    $('status').textContent='';
  }finally{$('go').disabled=false;}
};

async function reRender(){
  const edits={};
  document.querySelectorAll('input.stdedit').forEach(i=>edits[i.dataset.number]=i.value);
  $('status').textContent='다시 만드는 중…';
  const res=await fetch('/update',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token,standards:edits,
      answers:$('answers').checked,spec_table:$('spec_table').checked})});
  const data=await res.json();
  if(!res.ok){alert(data.error);return;}
  render(data); $('status').textContent='다시 만들었습니다';
}

function render(d){
  let html='<div class="card"><h2>결과</h2>';

  const real=d.warnings.filter(w=>!w.startsWith('읽은 결과'));
  const info=d.warnings.filter(w=>w.startsWith('읽은 결과'));
  if(d.downloads.as_is){
    html+='<div class="msg ok">원본 그대로 옮겼습니다.</div>';
  } else {
    html+='<div class="msg ok">문항 '+d.questions+'개를 만들었습니다.'+
          (info.length?' ('+esc(info[0])+')':'')+'</div>';
  }
  if(real.length) html+='<div class="msg warn"><b>확인이 필요합니다</b><br>'+
        real.map(esc).join('<br>')+'</div>';

  html+='<div class="dl">';
  if(d.downloads.as_is) html+='<a href="'+d.downloads.as_is+'">한글 파일 받기 (원본 그대로)</a>';
  if(d.downloads.student) html+='<a href="'+d.downloads.student+'">학생용 시험지 받기</a>';
  if(d.downloads.teacher) html+='<a href="'+d.downloads.teacher+'">교사용 받기 (정답·이원목적표)</a>';
  if(d.downloads.json) html+='<a class="alt" href="'+d.downloads.json+'">문항 JSON 받기</a>';
  html+='</div></div>';

  if(d.rows.length){
    html+='<div class="card"><h2>문항 분석'+
      (d.has_standards?' — 성취기준 '+d.standard_count+'개 기준':' — 성취기준 파일을 넣으면 자동으로 채워집니다')+'</h2>';
    if(d.has_standards && d.unmapped.length)
      html+='<div class="msg warn">성취기준을 정하지 못한 문항이 있습니다: '+
            d.unmapped.join('번, ')+'번. 아래에서 직접 넣어 주세요. '+
            '<b>확신이 없을 때는 비워 두도록 만들었습니다.</b> 아무 코드나 채우면 검토를 건너뛰게 되기 때문입니다.</div>';

    html+='<table><thead><tr><th>번호</th><th>발문</th><th>배점</th><th>정답</th>'+
          '<th>성취기준</th><th>후보 (눌러서 넣기)</th></tr></thead><tbody>';
    d.rows.forEach(r=>{
      html+='<tr class="'+(r.needs_review&&d.has_standards?'review':'')+'">'+
        '<td class="num">'+r.number+'</td>'+
        '<td>'+esc(r.stem)+(r.choices!==5?'<br><span class="tag review">선택지 '+r.choices+'개</span>':'')+'</td>'+
        '<td class="num">'+r.points+'</td>'+
        '<td class="num">'+(r.answer||'')+'</td>'+
        '<td><input class="stdedit" data-number="'+r.number+'" value="'+esc(r.standard)+'" placeholder="비어 있음"><br>'+
          (d.has_standards?(r.standard?'<span class="tag '+(r.needs_review?'review':'ok')+'">확신 '+r.confidence+'</span>':'<span class="tag review">미판정</span>'):'')+
        '</td><td>';
      r.candidates.forEach(c=>{
        html+='<span class="cand" data-code="'+esc(c.code)+'" data-number="'+r.number+'" title="'+
              esc(c.summary+' / 근거: '+c.matched.join(', '))+'">'+esc(c.code)+' ('+c.score+')</span>';
      });
      if(!r.candidates.length) html+='<span class="small">—</span>';
      html+='</td></tr>';
    });
    html+='</tbody></table>';
    html+='<div style="margin-top:14px"><button class="ghost" id="again">고친 내용으로 다시 만들기</button>'+
          '<span class="small" style="margin-left:10px">성취기준을 고친 뒤 누르면 한글 파일을 새로 만듭니다</span></div>';
    html+='</div>';
  }

  $('result').innerHTML=html;
  document.querySelectorAll('.cand').forEach(el=>el.onclick=()=>{
    document.querySelector('input.stdedit[data-number="'+el.dataset.number+'"]').value=el.dataset.code;});
  const again=$('again'); if(again) again.onclick=reRender;
}
</script>
</body>
</html>
"""


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="mogosa.app", description=f"{APP_NAME} 실행")
    parser.add_argument("--port", type=int, help=f"포트 번호 (기본 {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않는다")
    args = parser.parse_args()
    main(open_browser=not args.no_browser, port=args.port)


if __name__ == "__main__":
    _cli()
