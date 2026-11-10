"""Local-only operator panel for safe, explicit TenderLand jobs."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


JOBS = {
    "sync-queues": ("Пересортировать вкладки", ["sync-queues"]),
    "collect-documents": ("Собрать ссылки TenderLand", ["collect-documents"]),
    "archive-documents": ("Загрузить 20 документов в Drive", ["archive-documents", "--limit", "20"]),
    "sync-documents": ("Обновить вкладку Документы", ["sync-documents"]),
    "rebuild-documents": ("Пересобрать Документы по Тендерам", ["rebuild-documents"]),
    "clean-ai-analysis": ("Очистить AI-анализ", ["clean-ai-analysis"]),
    "sync-manager-ai": ("Обновить таблицу менеджеров", ["sync-manager-ai"]),
    "refresh-workflow": ("Обновить реестры без поиска", ["refresh-workflow"]),
    "run": ("Полный ежедневный поиск", ["run"]),
}
STATE = {"status": "idle", "job": "", "label": "Ожидание команды", "output": "", "progress": 0}
LOCK = threading.Lock()


def launch(job):
    label, command = JOBS[job]
    with LOCK:
        if STATE["status"] == "running":
            return False, "Уже выполняется: " + STATE["label"]
        STATE.update(status="running", job=job, label=label, output="", progress=5)

    def worker():
        process = subprocess.run(["tender-search", *command], text=True, capture_output=True)
        output = (process.stdout + "\n" + process.stderr)[-6000:]
        with LOCK:
            if "Another tender-search process is running" in output:
                STATE["output"] = "Сейчас уже выполняется утренний поиск или другая операция. Дождитесь её завершения и нажмите кнопку ещё раз."
                STATE["status"] = "busy"
                STATE["progress"] = 0
                STATE["label"] = "Ожидание: другая операция уже выполняется"
            else:
                STATE["output"] = output
                STATE["status"] = "success" if process.returncode == 0 else "failed"
                STATE["progress"] = 100 if process.returncode == 0 else 0
                STATE["label"] = ("Готово: " if process.returncode == 0 else "Ошибка: ") + label

    threading.Thread(target=worker, daemon=True).start()
    return True, "Запущено"


PAGE = """<!doctype html><html lang=ru><meta charset=utf-8><title>Tender Search · Панель</title>
<style>body{margin:0;background:#f7f8fa;color:#18212f;font:15px Inter,Arial,sans-serif}.wrap{max-width:960px;margin:56px auto;padding:0 24px}h1{font-size:30px;margin:0 0 8px}.sub{color:#64748b;margin:0 0 30px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:20px;box-shadow:0 2px 9px #0f172a09}h3{margin:0 0 8px;font-size:16px}p{color:#64748b;min-height:40px;margin:0 0 16px;line-height:1.45}button{border:0;border-radius:8px;background:#1967d2;color:white;padding:10px 14px;font-weight:600;cursor:pointer}button:disabled{background:#94a3b8}.status{margin:24px 0;background:#fff;border-radius:14px;padding:20px;border:1px solid #e5e7eb}.bar{height:8px;background:#e5e7eb;border-radius:9px;overflow:hidden}.fill{height:100%;background:#16a34a;width:0;transition:.3s}pre{white-space:pre-wrap;max-height:220px;overflow:auto;background:#0f172a;color:#dbeafe;padding:14px;border-radius:9px;font-size:12px}</style>
<div class=wrap><h1>Tender Search</h1><p class=sub>Локальная панель управления. Одновременно выполняется только одна операция.</p><div class=grid>
<div class=card><h3>Пересортировать вкладки</h3><p>Применить фильтры к уже найденным тендерам. Поиск и документы не запускаются.</p><button data-job=sync-queues>Запустить</button></div>
<div class=card><h3>Собрать ссылки TenderLand</h3><p>Обновить список файлов в карточках TenderLand. Файлы не скачиваются.</p><button data-job=collect-documents>Запустить</button></div>
<div class=card><h3>Загрузить документы</h3><p>Скачать до 20 реальных документов в Google Drive и обновить столбец F.</p><button data-job=archive-documents>Запустить</button></div>
<div class=card><h3>Обновить «Документы»</h3><p>Опубликовать текущие ссылки и статусы в Google Таблице.</p><button data-job=sync-documents>Запустить</button></div>
<div class=card><h3>Пересобрать «Документы»</h3><p>Оставить только тендеры из вкладки «Тендеры», убрать лишние строки и добавить недостающие.</p><button data-job=rebuild-documents>Запустить</button></div>
<div class=card><h3>Очистить «AI-анализ»</h3><p>Удалить только те строки AI-анализа, для которых тендера больше нет во вкладке «Документы». Вся строка удаляется целиком: флажок и комментарий не останутся у другого тендера.</p><button data-job=clean-ai-analysis>Запустить</button></div>
<div class=card><h3>Обновить таблицу менеджеров</h3><p>Добавить в рабочую копию только новые AI-анализы. Старые строки, флажки, менеджеры, комментарии и цвета не меняются.</p><button data-job=sync-manager-ai>Запустить</button></div>
<div class=card><h3>Обновить всё без нового поиска</h3><p>Применить фильтры, обновить очереди и документы, загрузить доступные файлы в Drive и очистить AI-анализ. Тексты AI не создаются: их пишет отдельный ежедневный чат.</p><button data-job=refresh-workflow>Запустить</button></div>
<div class=card><h3>Полный ежедневный поиск</h3><p>Искать TenderLand по ключевикам, фильтровать и синхронизировать очереди.</p><button data-job=run>Запустить</button></div></div>
<div class=status><b id=label>Загрузка…</b><p id=percent>0%</p><div class=bar><div id=fill class=fill></div></div><pre id=out></pre></div></div>
<script>async function state(){let s=await fetch('/api/state').then(x=>x.json());label.textContent=s.label;percent.textContent=s.progress+'%';fill.style.width=s.progress+'%';out.textContent=s.output;document.querySelectorAll('button').forEach(b=>b.disabled=s.status==='running')}document.querySelectorAll('button').forEach(b=>b.onclick=async()=>{await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job:b.dataset.job})});state()});state();setInterval(state,2000)</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, data, code=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", len(payload)); self.end_headers(); self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/api/state":
            with LOCK: self.send_json(STATE)
            return
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(PAGE.encode())

    def do_POST(self):
        if self.path != "/api/run": self.send_json({"error":"not found"},404); return
        try: job=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))["job"]
        except Exception: self.send_json({"error":"invalid request"},400); return
        if job not in JOBS: self.send_json({"error":"unknown job"},400); return
        ok,message=launch(job); self.send_json({"ok":ok,"message":message},200 if ok else 409)


def main():
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
