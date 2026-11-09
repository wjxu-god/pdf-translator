# -*- coding: utf-8 -*-
"""PDF 保排版翻译 · 本地一键工具（Web UI）
引擎：BabelDOC（Bing / 腾讯免费 / Google / 免费大模型）+ 本地自研引擎
"""
import base64, json, os, subprocess, sys, threading, time, traceback, urllib.parse, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)          # 打包后：exe 所在目录
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "译文输出")  # 默认值，实际用 outdir()
UPLOADS = os.path.join(HERE, "_uploads")
LOG = os.path.join(HERE, "pdf工具.log")
PORT = int(os.environ.get("PDFTRANS_PORT", "8910"))
for _i, _a in enumerate(sys.argv):
    if _a == "--port" and _i + 1 < len(sys.argv):
        PORT = int(sys.argv[_i + 1])
JOBS = {}
HISTORY = os.path.join(HERE, "历史记录.json")
SETTINGS = os.path.join(HERE, "设置.json")
APP_NAME = "PDF翻译专家"
APP_VERSION = "1.0.1"

def load_settings():
    try:
        return json.load(open(SETTINGS, "r", encoding="utf-8"))
    except Exception:
        return {}

def save_settings(d):
    try:
        json.dump(d, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".w")
        open(probe, "w").close()
        os.remove(probe)
        return True
    except Exception:
        return False

def _fallback_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "PDF翻译专家", "译文输出")

def outdir():
    p = (load_settings().get("outdir") or "").strip()
    if p and _writable(p):
        return p
    default = os.path.join(HERE, "译文输出")
    if _writable(default):
        return default
    fb = _fallback_dir()
    os.makedirs(fb, exist_ok=True)
    return fb

ENGINES = [
    ("auto",            "自动（推荐）",        "Bing → 腾讯 → Google → 本地"),
    ("babeldoc_tencent","BabelDOC + 腾讯",     "免费无 Key，国内网络可用"),
    ("babeldoc_bing",   "BabelDOC + Bing",     "速度快"),
    ("babeldoc_google", "BabelDOC + Google",   "备用通道"),
    ("babeldoc_llm",    "BabelDOC + 大模型",   "质量高，较慢"),
    ("local",           "本地引擎",            "零依赖兜底"),
]

def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass

def babeldoc_ready():
    try:
        import babeldoc_engine
        return babeldoc_engine.available()
    except Exception:
        return False

def clear_outputs():
    """一键清空：删除输出目录里的成果文件与上传缓存（保留目录本身）。"""
    removed, freed = 0, 0
    targets = [outdir(), UPLOADS]
    for d in targets:
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            fp = os.path.join(d, n)
            try:
                if os.path.isfile(fp):
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                    removed += 1
                elif os.path.isdir(fp) and os.path.basename(fp) in ("译文输出",):
                    continue
            except Exception:
                pass
    log("clear: %d files, %.1f MB" % (removed, freed / 1048576))
    return {"removed": removed, "freed_mb": round(freed / 1048576, 1), "outdir": outdir()}

def load_history():
    try:
        return json.load(open(HISTORY, "r", encoding="utf-8"))[:50]
    except Exception:
        return []

def add_history(item):
    h = load_history()
    h.insert(0, item)
    try:
        json.dump(h[:50], open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

NULLDEV = subprocess.DEVNULL

def run_job(job_id, name, raw, src, tgt, engine):
    job = JOBS[job_id]
    try:
        out_dir = outdir()
        os.makedirs(UPLOADS, exist_ok=True)
        base = os.path.splitext(name)[0] or "document"
        tmp = os.path.join(out_dir, "_src_" + job_id + ".pdf")
        open(tmp, "wb").write(raw)
        protected = os.path.join(UPLOADS, name)
        try:
            open(protected, "wb").write(raw)
        except Exception:
            pass
        job.update({"stage": "准备引擎", "percent": 2})

        def call_babeldoc(service):
            import babeldoc_engine
            return babeldoc_engine.translate(
                tmp, out_dir, src, tgt, base, log=log,
                progress=lambda ev: job.update(ev), service=service)

        def finish(res):
            mono = os.path.basename(res["mono"])
            dual = os.path.basename(res["dual"]) if res.get("dual") else None
            job.update({"stage": "done", "percent": 100, "out": mono, "dual": dual,
                        "elapsed": int(time.time() - job.get("t0", time.time()))})
            add_history({"name": name, "out": mono, "dual": dual,
                         "engine": job.get("engine_label", ""),
                         "time": time.strftime("%Y-%m-%d %H:%M")})
            log("done %s -> %s" % (name, mono))

        chain = []
        if engine == "auto":
            chain = ["bing", "tencentfree", "google"]
        elif engine.startswith("babeldoc"):
            chain = [{"babeldoc_bing": "bing", "babeldoc_tencent": "tencentfree",
                      "babeldoc_google": "google", "babeldoc_llm": "siliconflowfree"}.get(engine, "bing")]
        if chain and babeldoc_ready():
            for svc in chain:
                try:
                    job.update({"stage": "引擎启动", "service": svc, "percent": 3})
                    res = call_babeldoc(svc)
                    finish(res)
                    return
                except Exception as e:
                    log("  !! %s failed: %r" % (svc, e))
                    job.update({"stage": "切换备用通道", "percent": 4})
        # local engine fallback
        job.update({"stage": "本地引擎处理", "percent": 5, "service": "local"})
        import pdf_translate
        out_name = "%s_%s.pdf" % (base, tgt)
        out_path = os.path.join(out_dir, out_name)
        st = pdf_translate.translate_pdf(
            tmp, out_path, src, tgt, log=log,
            progress=lambda a, b: job.update({"page": a, "pages": b,
                                              "percent": int(100.0 * a / max(b, 1)),
                                              "stage": "翻译与回填"}))
        dual = st.get("dual")
        job.update({"stage": "done", "percent": 100,
                    "out": os.path.basename(out_path),
                    "dual": os.path.basename(dual) if dual else None,
                    "elapsed": int(time.time() - job.get("t0", time.time()))})
        add_history({"name": name, "out": os.path.basename(out_path),
                     "dual": os.path.basename(dual) if dual else None,
                     "engine": "本地引擎", "time": time.strftime("%Y-%m-%d %H:%M")})
    except Exception:
        job.update({"stage": "error", "error": traceback.format_exc()[-600:]})
        log("error %s\n%s" % (name, traceback.format_exc()))

HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDF翻译专家</title>
<style>
:root{--bg:#0b1020;--card:#141b2e;--card2:#1a2338;--line:#26314d;--txt:#e9eefb;--dim:#8fa0c2;--blue:#4d8dff;--cyan:#5fd0ff}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#16224a 0%,var(--bg) 55%);color:var(--txt);
 font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:860px;margin:0 auto;padding:34px 22px 70px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:.5px}
h1 span{background:linear-gradient(90deg,var(--blue),var(--cyan));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:16px}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.row .grow{flex:1;min-width:220px}
label.lbl{font-size:12px;color:var(--dim);display:block;margin-bottom:6px}
select{width:100%;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;outline:none}
select:focus{border-color:var(--blue)}
input[type=text],input:not([type]){width:100%;background:var(--card2);color:var(--txt);border:1px solid var(--line);
 border-radius:10px;padding:10px 12px;font-size:13px;outline:none}
.ver{font-size:12px;color:var(--dim);border:1px solid var(--line);border-radius:20px;padding:2px 9px;vertical-align:middle;margin-left:8px}
#drop{border:2px dashed #33456b;border-radius:16px;padding:40px 18px;text-align:center;cursor:pointer;transition:.18s;background:#121a2c}
#drop.hover{border-color:var(--blue);background:#16223a;transform:translateY(-1px)}
#drop .big{font-size:16px;margin-bottom:6px}
#drop .tip{font-size:12px;color:var(--dim)}
#drop b{color:#8ab4ff}
.files{margin-top:6px}
.file{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin-bottom:10px}
.file .top{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:9px}
.file .nm{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file .st{font-size:12px;color:var(--dim);white-space:nowrap}
.bar{height:9px;border-radius:5px;background:#0f1729;overflow:hidden}
.bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--cyan));transition:width .35s}
.file .meta{display:flex;justify-content:space-between;font-size:12px;color:var(--dim);margin-top:7px}
.dl{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
a.btn,button.btn{display:inline-flex;align-items:center;gap:6px;background:#1d2b47;color:#cfe2ff;border:1px solid #2f4a76;
 border-radius:9px;padding:7px 12px;font-size:13px;text-decoration:none;cursor:pointer;transition:.15s}
a.btn:hover,button.btn:hover{background:#24406b;border-color:var(--blue)}
a.primary{background:linear-gradient(90deg,#2f6dff,#3fa9ff);border:none;color:#04122b;font-weight:600}
.foot{display:flex;justify-content:space-between;align-items:center;color:var(--dim);font-size:12px;margin-top:18px}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;border:1px solid var(--line);color:var(--dim)}
.badge.ok{color:#7ee0a8;border-color:#2b5a44;background:#10241b}
.badge.no{color:#ff9b9b;border-color:#5b2a2a;background:#241212}
.err{color:#ff9b9b;font-size:12px;margin-top:8px;white-space:pre-wrap;max-height:120px;overflow:auto}
button.danger{background:#2a1620;border-color:#5b2a3a;color:#ffb3c1}
button.danger:hover{background:#3d1e2b;border-color:#ff6b8b}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);opacity:0;
 background:#1d2b47;border:1px solid #2f4a76;color:#dceaff;padding:10px 16px;border-radius:10px;
 font-size:13px;transition:.25s;pointer-events:none}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style></head><body>
<div class="wrap">
  <h1>PDF <span>翻译专家</span> <span class="ver">v__VERSION__</span></h1>
  <div class="sub">保排版翻译 · 段落重排 · 公式保留 · 双语对照 ｜ 全部在本机运行</div>

  <div class="panel">
    <div class="row">
      <div class="grow"><label class="lbl">翻译引擎</label>
        <select id="engine">__ENGINE_OPTIONS__</select>
      </div>
      <div class="grow"><label class="lbl">原文语言</label>
        <select id="src"><option value="en">英语</option><option value="zh">中文</option><option value="ja">日语</option><option value="ko">韩语</option><option value="de">德语</option><option value="fr">法语</option><option value="ru">俄语</option></select>
      </div>
      <div class="grow"><label class="lbl">译文语言</label>
        <select id="tgt"><option value="zh">中文</option><option value="en">英语</option><option value="ja">日语</option><option value="ko">韩语</option></select>
      </div>
    </div>
  </div>

  <div class="panel">
    <label class="lbl">输出目录</label>
    <div class="row">
      <div class="grow"><input id="outdir" readonly></div>
      <button class="btn" onclick="pickDir()">更改</button>
      <button class="btn" onclick="openDir()">打开</button>
    </div>
  </div>

  <div class="panel">
    <div id="drop">
      <div class="big">把 <b>PDF</b> 拖到这里，或 <b>点击选择</b></div>
      <div class="tip">支持批量；扫描件需先 OCR（暂不支持）</div>
    </div>
    <div class="files" id="files"></div>
  </div>

  <div class="foot">
    <div><span class="badge __BABEL_CLS__">BabelDOC 引擎：__BABEL_TXT__</span></div>
    <div>
      <button class="btn" onclick="openDir()">打开输出文件夹</button>
      <button class="btn danger" onclick="clearAll()">一键清空</button>
    </div>
  </div>
</div>

<div id="toast"></div>
<script>
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');
 clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),2200);}
function clearAll(){
 if(!confirm('清空输出目录里的所有译文和缓存？此操作不可撤销。')) return;
 fetch('/clear',{method:'POST'}).then(r=>r.json()).then(j=>{
   toast('已清理 '+j.removed+' 个文件，释放 '+j.freed_mb+' MB');
   document.querySelectorAll('.file').forEach(el=>{
     if(el.querySelector('.dl')) el.remove();
   });
 }).catch(()=>toast('清理失败'));
}
const drop=document.getElementById('drop'), filesBox=document.getElementById('files');
const input=document.createElement('input'); input.type='file'; input.accept='.pdf'; input.multiple=true;
drop.onclick=()=>input.click();
input.onchange=()=>{ if(input.files.length) queue([...input.files]); };
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover')}));
drop.addEventListener('drop',ev=>{ const fs=[...ev.dataTransfer.files].filter(f=>f.name.toLowerCase().endsWith('.pdf')); if(fs.length) queue(fs); });

const queueList=[];
let running=false;

function refreshSettings(){
  fetch('/settings').then(r=>r.json()).then(s=>{
    const el=document.getElementById('outdir'); if(el) el.value=s.outdir||'';
  }).catch(()=>{});
}
function openDir(){ fetch('/open',{method:'POST'}).catch(()=>{}); }
function pickDir(){
  if(window.pywebview && window.pywebview.api && window.pywebview.api.choose_folder){
    window.pywebview.api.choose_folder().then(d=>{ if(d) setDir(d); });
  } else {
    const d=prompt('输入输出目录的完整路径：', document.getElementById('outdir').value);
    if(d) setDir(d);
  }
}
function setDir(p){
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({outdir:p})}).then(r=>r.json()).then(s=>{
      document.getElementById('outdir').value=s.outdir||p;
    }).catch(()=>{});
}
refreshSettings();

function queue(files){
  files.forEach(f=>{
    const card=document.createElement('div');
    card.className='file';
    card.innerHTML='<div class="top"><div class="nm">'+escapeHtml(f.name)+'</div><div class="st">排队中</div></div>'+
      '<div class="bar"><i></i></div><div class="meta"><span class="ph">0%</span><span class="sv"></span></div>';
    filesBox.prepend(card);
    queueList.push({file:f, card});
  });
  pump();
}

function escapeHtml(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function pump(){
  if(running) return;
  const job=queueList.shift();
  if(!job){ running=false; return; }
  running=true;
  const st=job.card.querySelector('.st'), fill=job.card.querySelector('.bar>i'),
        ph=job.card.querySelector('.ph'), sv=job.card.querySelector('.sv');
  st.textContent='读取中';
  const r=new FileReader();
  r.onload=()=>{
    const b64=r.result.split(',')[1];
    fetch('/translate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:job.file.name,data:b64,
        src:document.getElementById('src').value,
        tgt:document.getElementById('tgt').value,
        engine:document.getElementById('engine').value})})
      .then(x=>x.json()).then(j=>{
        if(j.error){ st.textContent='失败'; job.card.querySelector('.bar').insertAdjacentHTML('afterend','<div class="err">'+escapeHtml(j.error)+'</div>'); running=false; pump(); return; }
        poll(j.id, job);
      }).catch(e=>{ st.textContent='失败'; running=false; pump(); });
  };
  r.readAsDataURL(job.file);
}

function poll(id, job){
  const st=job.card.querySelector('.st'), fill=job.card.querySelector('.bar>i'),
        ph=job.card.querySelector('.ph'), sv=job.card.querySelector('.sv');
  fetch('/status?id='+id).then(x=>x.json()).then(j=>{
    let p=j.percent||0;
    if(!p && j.pages && j.page) p=Math.min(99,Math.round(100*j.page/j.pages));
    fill.style.width=p+'%'; ph.textContent=p+'%';
    if(j.stage==='done'){
      st.textContent='完成 · '+Math.round(j.elapsed||0)+' 秒';
      sv.textContent='';
      let html='<div class="dl">';
      if(j.out)  html+='<a class="btn primary" href="/file?name='+encodeURIComponent(j.out)+'">下载纯译文</a>';
      if(j.dual) html+='<a class="btn" href="/file?name='+encodeURIComponent(j.dual)+'">下载对照版</a>';
      html+='</div>';
      job.card.insertAdjacentHTML('beforeend', html);
      running=false; pump(); return;
    }
    if(j.stage==='error'){
      st.textContent='失败';
      job.card.insertAdjacentHTML('beforeend','<div class="err">'+escapeHtml(j.error||'')+'</div>');
      running=false; pump(); return;
    }
    st.textContent=j.stage||'处理中';
    let extra='';
    if(j.service) extra=j.service;
    if(j.pages) extra+=' · '+j.page+'/'+j.pages;
    sv.textContent=extra;
    setTimeout(()=>poll(id,job),700);
  });
}
</script></body></html>"""

def build_html():
    opts = "\n".join('<option value="%s">%s — %s</option>' % (k, n, d) for k, n, d in ENGINES)
    ok = babeldoc_ready()
    return (HTML.replace("__VERSION__", APP_VERSION)
                .replace("__ENGINE_OPTIONS__", opts)
                .replace("__BABEL_CLS__", "ok" if ok else "no")
                .replace("__BABEL_TXT__", "已就绪" if ok else "未安装（可用本地引擎）"))

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._send(200, build_html(), "text/html; charset=utf-8")
        if u.path == "/status":
            q = urllib.parse.parse_qs(u.query)
            jid = (q.get("id") or [""])[0]
            return self._send(200, json.dumps(JOBS.get(jid, {"stage": "unknown"}), ensure_ascii=False))
        if u.path == "/settings":
            s = load_settings()
            return self._send(200, json.dumps({
                "outdir": outdir(), "default_outdir": OUTDIR, "version": APP_VERSION,
            }, ensure_ascii=False))
        if u.path == "/history":
            return self._send(200, json.dumps(load_history(), ensure_ascii=False))
        if u.path == "/file":
            q = urllib.parse.parse_qs(u.query)
            name = os.path.basename((q.get("name") or [""])[0])
            path = os.path.join(outdir(), name)
            if os.path.exists(path):
                data = open(path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, "not found", "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/clear":
            return self._send(200, json.dumps(clear_outputs(), ensure_ascii=False))
        if self.path == "/open":
            try:
                os.startfile(outdir())
            except Exception:
                pass
            return self._send(200, json.dumps({"ok": True}))
        if self.path == "/settings":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                s = load_settings()
                if d.get("outdir"):
                    v = str(d["outdir"]).strip()
                    if v:
                        os.makedirs(v, exist_ok=True)
                        s["outdir"] = v
                save_settings(s)
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps({"outdir": outdir()}, ensure_ascii=False))
        if self.path != "/translate":
            return self._send(404, "no", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            j = json.loads(raw.decode("utf-8"))
            data = base64.b64decode(j.get("data") or "")
            name = j.get("name") or "document.pdf"
            job_id = uuid.uuid4().hex[:12]
            JOBS[job_id] = {"stage": "排队中", "percent": 0, "name": name,
                            "t0": time.time(), "page": 0, "pages": 0}
            th = threading.Thread(target=run_job, args=(job_id, name, data,
                                                        j.get("src") or "en", j.get("tgt") or "zh",
                                                        j.get("engine") or "auto"))
            th.daemon = True
            th.start()
            return self._send(200, json.dumps({"id": job_id}))
        except Exception as e:
            return self._send(200, json.dumps({"error": str(e)}))

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    os.makedirs(outdir(), exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    log("tool started on %d" % PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    srv.serve_forever()
