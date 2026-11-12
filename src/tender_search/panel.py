"""Local-only operator panel with live, human-readable job progress."""

import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


JOBS = {
    "sync-queues": ("Пересортировать вкладки", ["sync-queues"], "Правила и вкладки"),
    "collect-documents": ("Собрать ссылки TenderLand", ["collect-documents"], "Карточки TenderLand"),
    "archive-documents": ("Загрузить 20 документов в Drive", ["archive-documents", "--limit", "20"], "Google Drive"),
    "sync-documents": ("Обновить вкладку Документы", ["sync-documents"], "Google Таблица"),
    "rebuild-documents": ("Пересобрать Документы по Тендерам", ["rebuild-documents"], "Реестр документов"),
    "clean-ai-analysis": ("Обновить очередь AI-анализа", ["clean-ai-analysis"], "Очередь AI"),
    "sync-manager-ai": ("Обновить таблицу менеджеров", ["sync-manager-ai"], "Копия менеджеров"),
    "refresh-workflow": ("Обновить всё без нового поиска", ["refresh-workflow"], "Все реестры"),
    "retry-failed": ("Повторить сорванные запросы", ["run", "--retry-latest"], "Подготовка повтора"),
    "run": ("Полный ежедневный поиск", ["run"], "Подготовка"),
}
SEARCH_JOBS = {"run", "retry-failed"}
WEIGHTS = (2, 60, 12, 8, 13, 5)
STATE = {"status": "idle", "job": "", "label": "Ожидание команды", "output": "", "progress": 0,
         "stage": "", "stage_index": 0, "stage_total": 0, "stage_progress": 0,
         "current": "", "started_at": "", "pid": None, "progress_known": False}
LOCK = threading.Lock()
JOB_LINE = re.compile(r"Job\s+(\d+)/(\d+):\s*(.*)")
STAGE_LINE = re.compile(r"PANEL_STAGE\|(\d+)\|(\d+)\|(.*)")


def timestamp():
    return datetime.now(UTC).astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")


def append_output(line):
    """Stream genuine CLI output and derive the progress only from log markers."""
    line = line.rstrip()
    if not line:
        return
    with LOCK:
        STATE["output"] = (STATE["output"] + line + "\n")[-24000:]
        stage = STAGE_LINE.search(line)
        if stage:
            index, total, title = int(stage.group(1)), int(stage.group(2)), stage.group(3).strip()
            STATE.update(stage=title, stage_index=index, stage_total=total, stage_progress=0, current="")
            if STATE["job"] in SEARCH_JOBS:
                STATE["progress"] = sum(WEIGHTS[:index - 1])
                STATE["progress_known"] = True
            return
        job = JOB_LINE.search(line)
        if job and STATE["job"] in SEARCH_JOBS:
            current, total, description = int(job.group(1)), int(job.group(2)), job.group(3).strip()
            percent = round(current / total * 100) if total else 0
            STATE.update(stage="Поиск TenderLand по ключевикам", stage_index=2, stage_total=6,
                         stage_progress=percent, current=f"Запрос {current} из {total}: {description}")
            STATE["progress"] = min(62, WEIGHTS[0] + round(WEIGHTS[1] * current / total))
            STATE["progress_known"] = True


def launch(job):
    label, command, stage = JOBS[job]
    with LOCK:
        if STATE["status"] == "running":
            return False, "Уже выполняется: " + STATE["label"]
        STATE.update(status="running", job=job, label=label, output="", progress=0, stage=stage,
                     stage_index=0, stage_total=0, stage_progress=0, progress_known=False,
                     current="Подготавливаю команду…",
                     started_at=timestamp(), pid=None)

    def worker():
        process = subprocess.Popen(["tender-search", *command], text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=1,
                                   env={**os.environ, "PYTHONUNBUFFERED": "1"})
        with LOCK:
            STATE["pid"] = process.pid
            STATE["current"] = "Процесс запущен; журнал поступает в реальном времени."
        assert process.stdout is not None
        for line in process.stdout:
            append_output(line)
        process.wait()
        with LOCK:
            if process.returncode == 0:
                STATE.update(status="success", label="Готово: " + label, progress=100, stage="Завершено",
                             stage_progress=100, progress_known=True, current="")
            else:
                STATE.update(status="failed", label="Ошибка: " + label,
                             current="См. последнюю строку журнала ниже.")

    threading.Thread(target=worker, daemon=True).start()
    return True, "Запущено"


PAGE = """<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Tender Search · Панель</title><style>
:root{--ink:#13213a;--muted:#64748b;--line:#dfe7f0;--paper:#f5f8fc;--blue:#155eef;--green:#13a36e}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#f8fbff,#edf3fa);color:var(--ink);font:15px/1.45 Inter,ui-sans-serif,system-ui,Arial,sans-serif}.wrap{max-width:1180px;margin:42px auto;padding:0 22px}.top{display:flex;justify-content:space-between;gap:20px;align-items:start;margin-bottom:24px}.eyebrow{color:var(--blue);font-weight:750;text-transform:uppercase;letter-spacing:.08em;font-size:11px;margin:0 0 5px}h1{font-size:32px;letter-spacing:-.035em;margin:0}.sub{color:var(--muted);margin:7px 0 0;max-width:700px}.badge{white-space:nowrap;border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 11px;color:var(--muted);font-size:12px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.card,.status{background:#fff;border:1px solid var(--line);box-shadow:0 10px 30px #15355b0b;border-radius:14px}.card{padding:17px;min-height:190px;display:flex;flex-direction:column}.card h3{font-size:15px;margin:0 0 8px}.card p{color:var(--muted);font-size:13px;margin:0 0 16px;min-height:56px}.card button{margin-top:auto}button{border:0;border-radius:8px;background:var(--blue);color:#fff;padding:9px 13px;font-weight:700;cursor:pointer}button:disabled{cursor:not-allowed;background:#a9b7cc}.status{margin:25px 0;padding:22px}.state{font-size:18px;font-weight:760}.meta{font-size:12px;color:var(--muted);margin-top:4px}.stage-box{background:var(--paper);border:1px solid #e4ebf4;border-radius:10px;padding:13px;margin:18px 0 12px}.stage-title{font-weight:700}.current{font-size:13px;color:var(--muted);margin:5px 0 0;word-break:break-word}.bar-row{display:flex;align-items:center;gap:11px;margin:12px 0 0}.bar{height:9px;background:#e5ebf3;border-radius:999px;overflow:hidden;flex:1}.fill{height:100%;width:0;background:linear-gradient(90deg,#1767e8,#24a7d8);transition:width .35s ease}.stage-fill{background:linear-gradient(90deg,#14a36d,#4dc28c)}.fill.indeterminate{width:35%!important;animation:slide 1.2s ease-in-out infinite}@keyframes slide{0%{transform:translateX(-140%)}100%{transform:translateX(320%)}}.pct{min-width:68px;text-align:right;font-weight:750;font-variant-numeric:tabular-nums}.log-head{display:flex;align-items:center;justify-content:space-between;margin-top:19px}.log-head b{font-size:13px}#copy{background:#edf3ff;color:#155eef;padding:7px 10px;font-size:12px}pre{white-space:pre-wrap;word-break:break-word;margin:8px 0 0;min-height:145px;max-height:390px;overflow:auto;background:#0d1b2d;color:#d4e4ff;border-radius:10px;padding:14px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.top{display:block}.badge{display:inline-block;margin-top:12px}}@media(max-width:560px){.wrap{margin:25px auto;padding:0 14px}.grid{grid-template-columns:1fr}h1{font-size:26px}.card{min-height:0}.card p{min-height:0}}</style>
<div class=wrap><div class=top><div><p class=eyebrow>Операторская панель</p><h1>Tender Search</h1><p class=sub>Отдельные безопасные операции и живой журнал сервера. Одновременно выполняется одна команда из панели.</p></div><span id=badge class=badge>Ожидание</span></div><div class=grid>
<div class=card><h3>Пересортировать вкладки</h3><p>Применить фильтры к найденным тендерам. Поиск и документы не запускаются.</p><button data-job=sync-queues>Запустить</button></div><div class=card><h3>Собрать ссылки TenderLand</h3><p>Обновить список файлов в карточках TenderLand, без скачивания файлов.</p><button data-job=collect-documents>Запустить</button></div><div class=card><h3>Загрузить документы</h3><p>Скачать до 20 доступных файлов в Google Drive и обновить реестр.</p><button data-job=archive-documents>Запустить</button></div><div class=card><h3>Обновить «Документы»</h3><p>Опубликовать текущие ссылки, статусы и документы Google Drive.</p><button data-job=sync-documents>Запустить</button></div><div class=card><h3>Пересобрать «Документы»</h3><p>Сверить лист с «Тендерами»: удалить лишнее и добавить недостающее.</p><button data-job=rebuild-documents>Запустить</button></div><div class=card><h3>Обновить очередь AI</h3><p>Сверить все ID, автоматические поля и ссылки документов без AI-решений.</p><button data-job=clean-ai-analysis>Запустить</button></div><div class=card><h3>Обновить таблицу менеджеров</h3><p>Добавить новые AI-анализы, не меняя старые решения, менеджеров и цвета.</p><button data-job=sync-manager-ai>Запустить</button></div><div class=card><h3>Обновить всё без поиска</h3><p>Фильтры, очереди, документы, Drive и AI-реестр. AI-тексты не создаются.</p><button data-job=refresh-workflow>Запустить</button></div><div class=card><h3>Полный ежедневный поиск</h3><p>Поиск по ключевикам, карточки, фильтры, документы, Drive и очередь AI.</p><button data-job=run>Запустить</button></div></div>
<section class=status><div id=label class=state>Загрузка…</div><div id=meta class=meta>—</div><div class=stage-box><div id=stage class=stage-title>—</div><div id=current class=current>—</div><div class=bar-row><div class=bar><div id=stageFill class='fill stage-fill'></div></div><span id=stagePct class=pct>0%</span></div></div><div class=bar-row><b>Общий прогресс</b><div class=bar><div id=fill class=fill></div></div><span id=percent class=pct>0%</span></div><div class=log-head><b>Живой журнал процесса</b><button id=copy>Копировать журнал</button></div><pre id=out>Операция ещё не запущена.</pre></section></div>
<script>const $=id=>document.getElementById(id);async function state(){try{let s=await fetch('/api/state',{cache:'no-store'}).then(x=>x.json()),known=!!s.progress_known;$('label').textContent=s.label;$('badge').textContent=s.status==='running'?'Выполняется':s.status==='success'?'Готово':s.status==='failed'?'Ошибка':'Ожидание';$('meta').textContent=[s.started_at&&'Начато: '+s.started_at,s.pid&&'PID: '+s.pid].filter(Boolean).join(' · ')||'Операция не запущена';$('stage').textContent=(s.stage_index&&s.stage_total?'Этап '+s.stage_index+' из '+s.stage_total+' · ':'')+(s.stage||'—');$('current').textContent=s.current||'—';$('percent').textContent=known?s.progress+'%':'выполняется';$('stagePct').textContent=known?s.stage_progress+'%':'выполняется';$('fill').style.width=known?s.progress+'%':'0';$('stageFill').style.width=known?s.stage_progress+'%':'0';$('fill').classList.toggle('indeterminate',s.status==='running'&&!known);$('stageFill').classList.toggle('indeterminate',s.status==='running'&&!known);$('out').textContent=s.output||'Журнал появится после запуска.';document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=s.status==='running')}catch(e){$('label').textContent='Нет связи с панелью';$('current').textContent=e.message}}document.querySelectorAll('button[data-job]').forEach(b=>b.onclick=async()=>{let r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job:b.dataset.job})});let x=await r.json();if(!x.ok)alert(x.message);state()});$('copy').onclick=async()=>{await navigator.clipboard.writeText($('out').textContent);$('copy').textContent='Скопировано';setTimeout(()=>$('copy').textContent='Копировать журнал',1300)};state();setInterval(state,1500)</script>"""

# This narrow retry action is generated separately so it remains visible even
# while the static layout above is kept compact for the local-only panel.
PAGE = PAGE.replace(
    "<section class=status>",
    "<div class=card><h3>Повторить сорванные запросы</h3>"
    "<p>Повторить только запросы, не завершившиеся в последнем частичном запуске. "
    "Полный список ключевиков заново не запускается.</p>"
    "<button data-job=retry-failed>Запустить повтор</button></div><section class=status>",
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(payload)); self.end_headers(); self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/api/state":
            with LOCK: self.send_json(STATE)
            return
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers(); self.wfile.write(PAGE.encode())

    def do_POST(self):
        if self.path != "/api/run": self.send_json({"error": "not found"}, 404); return
        try: job = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))["job"]
        except Exception: self.send_json({"error": "invalid request"}, 400); return
        if job not in JOBS: self.send_json({"error": "unknown job"}, 400); return
        ok, message = launch(job); self.send_json({"ok": ok, "message": message}, 200 if ok else 409)


def main():
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
