# -*- coding: utf-8 -*-
"""PDF 保排版翻译 · 本地一键工具（Web UI）
引擎：BabelDOC（Bing / 腾讯免费 / Google / 免费大模型）+ 本地自研引擎
"""
import base64, json, os, subprocess, sys, threading, time, traceback, urllib.parse, uuid, webbrowser

# 本地模块按需延迟导入（打包时用 --hidden-import 显式收集），避免拖慢启动
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
APP_VERSION = "1.1.4"

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
    ("auto",            "自动（推荐）",    "先 Bing，失败换腾讯，再不行用本地"),
    ("babeldoc_tencent","BabelDOC + 腾讯", "免费无 Key，国内网络可用"),
    ("babeldoc_bing",   "BabelDOC + Bing", "速度快"),
    ("local",           "本地引擎",        "零依赖兜底"),
]

def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass

INSTALL_STATE = {"running": False, "done": False, "error": None, "lines": [], "kind": ""}

def _inst_log(msg):
    INSTALL_STATE["lines"].append(str(msg)[-500:])
    if len(INSTALL_STATE["lines"]) > 400:
        INSTALL_STATE["lines"] = INSTALL_STATE["lines"][-400:]
    log("install: " + str(msg)[:300])

def start_install(kind):
    import engine_manager as em
    INSTALL_STATE.update({"running": True, "done": False, "error": None, "lines": [], "kind": kind})

    def work():
        try:
            if kind == "babeldoc":
                em.install_babeldoc(log=_inst_log,
                                    progress=lambda pct, msg: INSTALL_STATE.update({"percent": pct}))
            elif kind == "tencent":
                em.install_tencent(log=_inst_log)
            elif kind == "uninstall":
                em.uninstall(log=_inst_log)
            os.environ["PDF2ZH_HOME"] = em.base_dir() if em.has_babeldoc() else ""
            _BABEL_CACHE.update({"t": 0, "v": None})
            babeldoc_ready(force=True)
            INSTALL_STATE.update({"running": False, "done": True})
        except Exception as e:
            INSTALL_STATE.update({"running": False, "done": True, "error": str(e)[-400:]})
            log("install error: %r" % (e,))

    threading.Thread(target=work, daemon=True).start()
    return True

_BABEL_CACHE = {"t": 0, "v": None}

def babeldoc_ready(force=False):
    """统一判断：优先用引擎管理器（软件内安装的），其次外部环境。10 秒缓存，避免反复起子进程。"""
    now = time.time()
    if not force and _BABEL_CACHE["v"] is not None and now - _BABEL_CACHE["t"] < 10:
        return _BABEL_CACHE["v"]
    ok = False
    try:
        import engine_manager as em
        if em.has_babeldoc():
            os.environ["PDF2ZH_HOME"] = em.base_dir()   # 让适配器立即生效
            ok = True
    except Exception:
        pass
    if not ok:
        try:
            import pdf_engine_babeldoc as babeldoc_engine
            ok = babeldoc_engine.available()
        except Exception:
            ok = False
    _BABEL_CACHE.update({"t": now, "v": ok})
    return ok

def _make_probe_pdf(path):
    """生成一页极简英文 PDF，用于自检 / 模型预热。"""
    import pymupdf
    d = pymupdf.open()
    p = d.new_page(width=420, height=260)
    p.insert_text((40, 80), "This is a translation test.", fontsize=16, fontname="helv")
    p.insert_text((40, 120), "Graph randomization is a useful tool.", fontsize=12, fontname="helv")
    d.save(path)
    d.close()
    return path

def selftest(service="bing"):
    """真跑一遍 BabelDOC：生成探针 PDF → 翻译 → 报告结果与真实报错。"""
    import tempfile, glob as _glob
    out = {"ok": False, "service": service, "steps": []}

    def step(name, ok, detail=""):
        out["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:800]})

    try:
        import pdf_engine_babeldoc as be
        step("适配器可用", be.available(), "ROOT=%s" % be.ROOT)
        tmpd = tempfile.mkdtemp(prefix="selftest_")
        src = _make_probe_pdf(os.path.join(tmpd, "probe.pdf"))
        step("探针 PDF 生成", os.path.exists(src), src)
        res = be.translate(src, tmpd, "en", "zh", "probe",
                           log=lambda m: out["steps"].append({"name": "log", "ok": True, "detail": str(m)[:300]}),
                           service=service)
        step("翻译成功", True, "输出：%s" % os.path.basename(res["mono"]))
        out["ok"] = True
        out["out"] = res["mono"]
    except Exception as e:
        step("翻译失败", False, repr(e)[:800])
    return out

def diagnose():
    """产品诊断：把引擎链路每一环的真实状态都跑一遍，供用户/开发者定位问题。"""
    import subprocess
    out = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": []}

    def add(name, ok, detail=""):
        out["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})

    try:
        import engine_manager as em
        st = em.status()
        add("引擎管理器", True, "base=%s" % st.get("base"))
        add("BabelDOC 已安装", st.get("babeldoc"), "python=%s" % (st.get("python") or "未找到"))
        add("腾讯免费引擎", st.get("tencent"))
    except Exception as e:
        add("引擎管理器", False, repr(e))
        st = {}

    try:
        import pdf_engine_babeldoc as be
        add("适配器可用", be.available(), "ROOT=%s\nPY=%s\nCFG=%s\nworkdir=%s"
            % (be.ROOT, be.PY, be.CFG or "(无)", be._workdir()))
        if be.available():
            r = subprocess.run([be.PY, "-c", "import pdf2zh_next,babeldoc;print('import-ok')"],
                               capture_output=True, text=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            add("pdf2zh_next 可导入", "import-ok" in (r.stdout or ""),
                (r.stdout or "") + (r.stderr or "")[-300:])
    except Exception as e:
        add("适配器可用", False, repr(e))

    try:
        ok = babeldoc_ready(force=True)
        add("就绪判断（界面用的）", ok, "PDF2ZH_HOME=%s" % (os.environ.get("PDF2ZH_HOME") or "(未设置)"))
    except Exception as e:
        add("就绪判断（界面用的）", False, repr(e))

    try:
        import socket
        with socket.socket() as s:
            s.settimeout(2)
            reach = s.connect_ex(("transmart.qq.com", 443)) == 0
        add("腾讯翻译接口连通", reach, "transmart.qq.com:443")
    except Exception as e:
        add("腾讯翻译接口连通", False, repr(e))

    return out

def engine_status():
    try:
        import engine_manager as em
        st = em.status()
    except Exception as e:
        st = {"base": "", "babeldoc": False, "tencent": False, "error": repr(e)}
    st.update({"running": INSTALL_STATE["running"], "done": INSTALL_STATE["done"],
               "error": INSTALL_STATE["error"], "percent": INSTALL_STATE.get("percent", 0),
               "lines": INSTALL_STATE["lines"][-120:]})
    return st

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
            import pdf_engine_babeldoc as babeldoc_engine
            return babeldoc_engine.translate(
                tmp, out_dir, src, tgt, base, log=log,
                progress=lambda ev: job.update(ev), service=service)

        def finish(res):
            mono = os.path.basename(res["mono"])
            dual = os.path.basename(res["dual"]) if res.get("dual") else None
            job.update({"stage": "done", "percent": 100, "out": mono, "dual": dual,
                        "engine_used": res.get("engine_used", job.get("engine_label", "")),
                        "elapsed": int(time.time() - job.get("t0", time.time()))})
            add_history({"name": name, "out": mono, "dual": dual,
                         "engine": job.get("engine_label", ""),
                         "time": time.strftime("%Y-%m-%d %H:%M")})
            log("done %s -> %s" % (name, mono))

        explicit = engine.startswith("babeldoc")
        chain = []
        if engine == "auto":
            chain = ["bing", "tencentfree"]
            try:
                import engine_manager as _em3
                if not _em3.has_tencent():
                    chain = [c for c in chain if c != "tencentfree"]
            except Exception:
                chain = [c for c in chain if c != "tencentfree"]
        elif explicit:
            chain = [{"babeldoc_bing": "bing",
                      "babeldoc_tencent": "tencentfree"}.get(engine, "bing")]
        if chain:
            if not babeldoc_ready(force=True):
                if explicit:
                    job.update({"stage": "error", "percent": 0,
                                "error": "你选择的是 BabelDOC 引擎，但本机未检测到可用安装。\n"
                                         "请点开底部「高级：翻译引擎组件」→「安装 BabelDOC」，"
                                         "或改选「本地引擎」/「自动」。"})
                    return
                job.update({"stage": "回退本地引擎", "percent": 4, "note": "BabelDOC 未就绪"})
            else:
                try:
                    import engine_manager as _em2
                    tencent_ok = _em2.has_tencent()
                except Exception:
                    tencent_ok = False
                if engine == "babeldoc_tencent" and not tencent_ok:
                    job.update({"stage": "error", "percent": 0,
                                "error": "腾讯免费引擎尚未安装。\n"
                                         "请点开「高级：翻译引擎组件」→「装腾讯引擎」（几秒钟），"
                                         "或把引擎改成「BabelDOC + Bing」。\n"
                                         "提示：不装腾讯引擎不影响 BabelDOC 的 Bing / Google 通道。"})
                    return
                last_err = None
                for svc in chain:
                    try:
                        job.update({"stage": "引擎启动", "service": svc, "percent": 3})
                        res = call_babeldoc(svc)
                        res["engine_used"] = "BabelDOC+" + svc
                        finish(res)
                        return
                    except Exception as e:
                        last_err = e
                        log("  !! %s failed: %r" % (svc, e))
                        job.update({"stage": "通道失败，尝试下一个", "percent": 4,
                                    "note": "BabelDOC(%s) 失败：%s" % (svc, str(e)[:150])})
                if explicit:
                    job.update({"stage": "error", "percent": 0,
                                "error": "BabelDOC 引擎调用失败：%s\n\n"
                                         "可在「高级：翻译引擎组件」里点「诊断」查看详细原因，"
                                         "或改选「自动」让它自动换通道。" % (str(last_err)[:400],)})
                    return
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
        job.update({"stage": "done", "percent": 100, "engine_used": "本地引擎",
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
#engLog{font-family:Consolas,monospace}
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

  <div class="panel" id="enginePanel" style="padding:12px 16px">
    <details id="engDetails">
      <summary style="cursor:pointer;font-size:13px;color:var(--dim);outline:none">
        高级：翻译引擎组件 <span id="engMini" style="color:#5b6b8c"></span>
      </summary>
      <div style="margin-top:12px">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div style="font-size:12px;color:var(--dim);line-height:1.7">
            BabelDOC 排版引擎可选安装，装完排版更好；不装也能用本地引擎。<br>
            安装位置：<span id="engPath" style="color:#5b6b8c"></span>
          </div>
          <div class="row" style="gap:8px">
            <button class="btn" id="btnBabel" onclick="installEngine('babeldoc')">安装 BabelDOC</button>
            <button class="btn" id="btnTc" onclick="installEngine('tencent')">装腾讯引擎</button>
            <button class="btn" onclick="changeEngineDir()">更改位置</button>
            <button class="btn" onclick="diagnose()">诊断</button>
            <button class="btn" onclick="selftest()">试翻自检</button>
            <button class="btn danger" onclick="installEngine('uninstall')">卸载</button>
          </div>
        </div>
        <div id="engState" style="margin-top:8px"></div>
        <div id="logTools" style="display:none;margin-top:10px;gap:8px">
        <button class="btn" onclick="copyLog()">复制日志</button>
        <button class="btn" onclick="saveLog()">导出日志文件</button>
      </div>
      <pre id="engLog" style="display:none;margin-top:8px;max-height:170px;overflow:auto;background:#0e1626;
             border:1px solid var(--line);border-radius:10px;padding:10px;font-size:11.5px;color:#9fb2d0;
             white-space:pre-wrap;user-select:text;cursor:text"></pre>
      </div>
    </details>
  </div>

  <div class="foot">
    <div><span class="badge" id="footEngine">检测引擎…</span></div>
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
refreshEngine();
let engTimer=null;
function refreshEngine(){
  fetch('/engine/status').then(r=>r.json()).then(s=>{
    const el=document.getElementById('engState');
    const logEl=document.getElementById('engLog');
    const b=s.babeldoc?'<span class="badge ok">已安装</span>':'<span class="badge no">未安装</span>';
    const foot=document.getElementById('footEngine');
    if(foot){
      foot.className='badge '+(s.babeldoc?'ok':'no');
      foot.textContent = s.babeldoc ? 'BabelDOC 引擎：已就绪' : 'BabelDOC 引擎：未安装（用本地引擎）';
    }
    const mini=document.getElementById('engMini');
    if(mini) mini.textContent = s.babeldoc ? '· 已安装' : '· 未安装';
    const pth=document.getElementById('engPath');
    if(pth) pth.textContent = s.base || '';
    const t=s.tencent?'<span class="badge ok">已安装</span>':'<span class="badge no">未安装</span>';
    el.innerHTML='BabelDOC '+b+'　腾讯引擎 '+t+(s.base?('　<span style="color:#5b6b8c">'+s.base+'</span>'):'');
    document.getElementById('btnBabel').style.display = s.babeldoc?'none':'';
    const tools=document.getElementById('logTools');
    if(s.running || (s.lines && s.lines.length)){
      logEl.style.display='block'; if(tools) tools.style.display='flex';
      logEl.textContent=(s.lines||[]).join('\n');
      logEl.scrollTop=logEl.scrollHeight;
    }
    if(s.error){ logEl.style.display='block'; logEl.textContent += '\n[错误] '+s.error; }
    if(s.running){
      clearTimeout(engTimer); engTimer=setTimeout(refreshEngine,1500);
    } else if(s.done){
      clearTimeout(engTimer); engTimer=setTimeout(refreshEngine,4000);
      if(s.babeldoc){
        toast('组件安装完成，已启用 BabelDOC 引擎');
        setTimeout(()=>{ refreshEngine(); }, 800);
      }
    }
  }).catch(()=>{});
}
function copyLog(){
  const t=document.getElementById('engLog').textContent||'';
  if(!t){ toast('暂无日志'); return; }
  const done=()=>toast('已复制到剪贴板（'+t.length+' 字）');
  if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(t).then(done).catch(fallback); }
  else fallback();
  function fallback(){
    const ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta);
    ta.select(); try{ document.execCommand('copy'); done(); }catch(e){ toast('复制失败，请手动选择文本'); }
    document.body.removeChild(ta);
  }
}
function saveLog(){
  const t=document.getElementById('engLog').textContent||'';
  if(!t){ toast('暂无日志'); return; }
  fetch('/save-log',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:t})}).then(r=>r.json()).then(j=>{
      toast(j.path?('已保存：'+j.path):'保存失败');
    }).catch(()=>toast('保存失败'));
}
function selftest(){
  const logEl=document.getElementById('engLog');
  logEl.style.display='block';
  logEl.textContent='正在试翻（首次会下载约 50MB 版面模型，可能需要几分钟）…';
  const svc = (document.getElementById('engine').value==='babeldoc_tencent') ? 'tencentfree' : 'bing';
  fetch('/selftest?service='+svc).then(r=>r.json()).then(d=>{
    const lines=[];
    (d.steps||[]).forEach(c=>{
      lines.push((c.ok?'[OK]  ':'[失败] ')+c.name);
      if(c.detail) lines.push('        '+String(c.detail));
    });
    lines.push('', d.ok ? '结论：引擎可以正常翻译' : '结论：引擎不可用，请把这段日志发给我');
    logEl.textContent=lines.join('\n');
  }).catch(e=>{ logEl.textContent='自检失败: '+e; });
}
function diagnose(){
  const logEl=document.getElementById('engLog');
  logEl.style.display='block';
  logEl.textContent='正在诊断…';
  fetch('/diagnose').then(r=>r.json()).then(d=>{
    const lines=[];
    (d.checks||[]).forEach(c=>{
      lines.push((c.ok?'[OK]  ':'[失败] ')+c.name);
      if(c.detail) lines.push('        '+String(c.detail).replace(/\n/g,'\n        '));
    });
    logEl.textContent=lines.join('\n');
  }).catch(e=>{ logEl.textContent='诊断失败: '+e; });
}
function changeEngineDir(){
  const cur = document.getElementById('engPath').textContent || '';
  const pick = (window.pywebview && window.pywebview.api && window.pywebview.api.choose_folder)
      ? window.pywebview.api.choose_folder().then(d=>d||null)
      : Promise.resolve(prompt('选择组件安装目录（留空恢复默认）：', cur));
  pick.then(d=>{
    if(d===null) return;
    fetch('/engine/location',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dir:d||''})}).then(r=>r.json()).then(()=>{
        toast('安装位置已更新'); refreshEngine();
      });
  });
}
function installEngine(kind){
  let tip='';
  if(kind==='babeldoc') tip='安装 BabelDOC 需要联网下载约 300~500MB，耗时 3~15 分钟，期间可以最小化窗口。继续？';
  if(kind==='tencent') tip='安装腾讯免费翻译引擎（秒级完成）。继续？';
  if(kind==='uninstall') tip='确定卸载已安装的引擎组件？将删除下载的组件，本地引擎仍可用。';
  if(tip && !confirm(tip)) return;
  fetch('/engine/install',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({kind:kind})}).then(r=>r.json()).then(()=>{
      toast('已开始：'+kind); refreshEngine();
    }).catch(()=>toast('启动失败'));
}

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
      st.textContent='完成 · '+Math.round(j.elapsed||0)+' 秒'+(j.engine_used?(' · '+j.engine_used):'');
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
        if u.path == "/selftest":
            q = urllib.parse.parse_qs(u.query)
            svc = (q.get("service") or ["bing"])[0]
            return self._send(200, json.dumps(selftest(svc), ensure_ascii=False))
        if u.path == "/diagnose":
            return self._send(200, json.dumps(diagnose(), ensure_ascii=False))
        if u.path == "/engine/status":
            babeldoc_ready(force=True)
            return self._send(200, json.dumps(engine_status(), ensure_ascii=False))
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
        if self.path == "/save-log":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                text = d.get("text") or ""
                name = "诊断日志_%s.txt" % time.strftime("%Y%m%d_%H%M%S")
                path = os.path.join(outdir(), name)
                open(path, "w", encoding="utf-8-sig").write(text)
                return self._send(200, json.dumps({"path": path}, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}, ensure_ascii=False))
        if self.path == "/engine/install":
            n = int(self.headers.get("Content-Length") or 0)
            kind = "babeldoc"
            try:
                kind = (json.loads(self.rfile.read(n).decode("utf-8")) or {}).get("kind") or "babeldoc"
            except Exception:
                pass
            start_install(kind)
            return self._send(200, json.dumps({"ok": True, "kind": kind}))
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
    try:
        _s = load_settings()
        if _s.get("babeldoc_dir"):
            os.environ["PDFTRANS_BABELDOC_DIR"] = _s["babeldoc_dir"]
        import engine_manager as _em
        if _em.has_babeldoc():
            os.environ["PDF2ZH_HOME"] = _em.base_dir()
    except Exception:
        pass
    log("tool started on %d" % PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    srv.serve_forever()
