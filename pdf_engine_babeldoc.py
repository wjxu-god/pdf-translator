# -*- coding: utf-8 -*-
"""BabelDOC 引擎适配器：调用本机 PDF_Translator 的 pdf2zh_next（排版质量最好）"""
import glob, os, re, shutil, subprocess, sys, tempfile, time

def _find_root():
    """BabelDOC 运行环境位置：优先环境变量 PDF2ZH_HOME，其次常见安装位置。"""
    cands = []
    env = os.environ.get("PDF2ZH_HOME")
    if env:
        cands.append(env)
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    cands += [os.environ.get("PDFTRANS_BABELDOC_DIR") or "",
              os.path.join(local, "PDF翻译专家", "babeldoc"),
              r"D:\PDF_Translator",
              os.path.join(os.path.expanduser("~"), "PDF_Translator"),
              "/opt/PDF_Translator"]
    def _ok(c):
        if not c:
            return False
        return (os.path.exists(os.path.join(c, "env", "python.exe"))
                or os.path.exists(os.path.join(c, "env", "Scripts", "python.exe"))
                or os.path.exists(os.path.join(c, "env", "bin", "python")))
    for c in cands:
        if _ok(c):
            return c
    return cands[0]

ROOT = _find_root()

def _pick_python(root):
    for rel in (("env", "Scripts", "python.exe"), ("env", "python.exe"), ("env", "bin", "python")):
        p = os.path.join(root, *rel)
        if os.path.exists(p):
            return p
    return os.path.join(root, "env", "Scripts", "python.exe")

def _pick_config(root):
    for rel in (("app", "config", "config.toml"), ("config", "config.toml")):
        p = os.path.join(root, *rel)
        if os.path.exists(p):
            return p
    return ""

PY = _pick_python(ROOT)
CFG = _pick_config(ROOT)


def _workdir():
    """BabelDOC 运行目录：整合包有 app/，软件内自装的环境没有。"""
    for rel in ("app", "."):
        p = os.path.normpath(os.path.join(ROOT, rel))
        if os.path.isdir(p):
            return p
    return ROOT

STAGE_HINTS = [
    ("Parse PDF", "解析 PDF", 6), ("Parse Page Layout", "版面分析", 18),
    ("Parse Paragraphs", "段落解析", 30), ("Parse Formulas", "公式与样式", 38),
    ("Automatic Term Extraction", "术语提取", 44),
    ("Translate Paragraphs", "翻译段落", 55), ("Typesetting", "排版", 82),
    ("Add Fonts", "嵌入字体", 90), ("Generate drawing instructions", "生成绘制指令", 93),
    ("Subset font", "字体子集化", 96), ("Save PDF", "保存 PDF", 98),
]

def _handle_line(line, progress, buf):
    if not progress:
        return
    for hint, label, pct in STAGE_HINTS:
        if hint in line:
            progress({"percent": pct, "stage": label})
            return
    m = re.search(r"(\d+)/(\d+)\s*$", line)
    if m:
        done, total = int(m.group(1)), int(m.group(2))
        if total and done <= total:
            progress({"percent": min(99, int(100.0 * done / total)),
                      "page": done, "pages": total})

def _scan_text(text, progress):
    if not progress or not text:
        return
    flat = re.sub(r"\s+", " ", text)
    best = (None, -1, 0)
    for hint, label, pct in STAGE_HINTS:
        idx = flat.rfind(hint)
        if idx > best[1]:
            best = (label, idx, pct)
    if best[0]:
        progress({"percent": best[2], "stage": best[0]})
    nums = re.findall(r"(\d+)/(\d+)(?![\d/])", flat)
    if nums:
        done, total = int(nums[-1][0]), int(nums[-1][1])
        if 0 < total <= 2000 and done <= total:
            progress({"page": done, "pages": total,
                      "percent": max(int(100.0 * done / total), 1)})

def ensure_shim(log=print):
    import socket
    s = socket.socket()
    try:
        s.settimeout(1.0)
        s.connect(("127.0.0.1", 8921))
        s.close()
        return True
    except Exception:
        pass
    try:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        subprocess.Popen([exe, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tencent_openai_shim.py")],
                         creationflags=flags, close_fds=True)
        import time as _t
        for _ in range(20):
            _t.sleep(0.3)
            try:
                s2 = socket.socket(); s2.settimeout(0.5); s2.connect(("127.0.0.1", 8921)); s2.close()
                log("  tencent shim started")
                return True
            except Exception:
                continue
    except Exception as e:
        log("  !! shim start failed: %r" % (e,))
    return False
    return os.path.exists(PY) and os.path.exists(CFG)

def _error_digest(text, limit=18):
    """从引擎日志里提炼真正有用的报错行（ERROR/Traceback/Exception），再附尾部。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    key = []
    for i, ln in enumerate(lines):
        low = ln.lower()
        if ("error" in low or "traceback" in low or "exception" in low
                or "failed" in low or "cannot" in low or "timeout" in low
                or "huggingface" in low or "connection" in low or "model" in low):
            key.extend(lines[i:i + 3])
        if len(key) > 40:
            break
    tail = lines[-limit:]
    out = []
    seen = set()
    for ln in key + tail:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return chr(10).join(out[-40:])

def available():
    return os.path.exists(PY)

SHIM_URL = "http://127.0.0.1:8921/v1"
SERVICE_FLAGS = {"tencentfree": ["--tencentfree"],
                 "bing": ["--bing"], "google": ["--google"],
                 "siliconflowfree": ["--siliconflowfree"], "none": [],
                 "tencent": ["--openai", "--openai-model", "tencent-free",
                             "--openai-base-url", SHIM_URL,
                             "--openai-api-key", "sk-local",
                             "--openai-timeout", "60"]}

def translate(src_pdf, out_dir, src="en", tgt="zh", base_name=None, log=print, progress=None, service="bing"):
    if not available():
        raise RuntimeError("PDF_Translator 未安装：%s" % ROOT)
    if service == "tencent":
        ensure_shim(log)
    tmp = tempfile.mkdtemp(prefix="babeldoc_")
    log_path = os.path.join(tmp, "engine.log")
    cmd = [PY, "-u", "-c", "from pdf2zh_next.main import cli; cli()",
           src_pdf, "--output", tmp,
           "--lang-in", src, "--lang-out", tgt,
           ] + (["--config-file", CFG] if CFG else []) + SERVICE_FLAGS.get(service, ["--bing"])
    log("  babeldoc start: %s (service=%s)" % (os.path.basename(src_pdf), service))
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["COLUMNS"] = "200"
    env["LINES"] = "60"
    # 版面模型默认从 HuggingFace 下载，国内直连常失败 → 默认走镜像，可覆盖
    env.setdefault("HF_ENDPOINT", os.environ.get("PDFTRANS_HF_ENDPOINT", "https://hf-mirror.com"))
    with open(log_path, "wb") as fh:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, cwd=_workdir(),
                                stdout=fh, stderr=subprocess.STDOUT, env=env,
                                creationflags=flags)
        pos = 0
        acc = ""
        while proc.poll() is None:
            time.sleep(0.6)
            try:
                with open(log_path, "rb") as rf:
                    rf.seek(pos)
                    data = rf.read()
                    pos = rf.tell()
            except Exception:
                continue
            if not data:
                continue
            acc += data.decode("utf-8", "replace")
            if len(acc) > 200000:
                acc = acc[-200000:]
            _scan_text(acc, progress)
        proc.wait()
    try:
        text = open(log_path, "rb").read().decode("utf-8", "replace")
    except Exception:
        text = ""
    if proc.returncode != 0:
        raise RuntimeError("pdf2zh_next 退出码 %d" % proc.returncode + chr(10) + _error_digest(text))
    mono = sorted(glob.glob(os.path.join(tmp, "**", "*.mono.pdf"), recursive=True))
    dual = sorted(glob.glob(os.path.join(tmp, "**", "*.dual.pdf"), recursive=True))
    if not mono:
        # 有些版本写在当前工作目录
        mono = sorted(glob.glob(os.path.join(_workdir(), "**", "*.mono.pdf"), recursive=True))
        dual = sorted(glob.glob(os.path.join(_workdir(), "**", "*.dual.pdf"), recursive=True))
    if not mono:
        tail = chr(10).join([ln for ln in text.splitlines() if ln.strip()][-12:])
        digest = _error_digest(text)
        hint = ""
        if "huggingface" in text.lower() or "model" in text.lower() or "connect" in text.lower():
            hint = ("\n\n【很可能是版面模型下载失败】首次运行需要下载约 50MB 模型。\n"
                    "请检查网络，或设置环境变量 PDFTRANS_HF_ENDPOINT 指向可用镜像后重试。")
        raise RuntimeError("pdf2zh_next 未产出文件。" + hint + chr(10) + digest)
    os.makedirs(out_dir, exist_ok=True)
    base = base_name or os.path.splitext(os.path.basename(src_pdf))[0]
    out_mono = os.path.join(out_dir, "%s_%s.pdf" % (base, tgt))
    out_dual = os.path.join(out_dir, "%s_%s_对照.pdf" % (base, tgt))
    shutil.copyfile(mono[0], out_mono)
    if dual:
        shutil.copyfile(dual[0], out_dual)
    else:
        out_dual = None
    shutil.rmtree(tmp, ignore_errors=True)
    return {"mono": out_mono, "dual": out_dual}
