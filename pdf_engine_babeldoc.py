# -*- coding: utf-8 -*-
"""BabelDOC 引擎适配器：调用本机 PDF_Translator 的 pdf2zh_next（排版质量最好）"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

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
    for rel in (("env", "Scripts", "python.exe"), ("env", "python.exe"), ("env", "bin", "python"),
                ("py311", "python.exe"), ("python", "python.exe")):
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

def _unwrap(text):
    """rich 会把长行按 80 列折行并加缩进，这里把续行拼回上一行，还原完整报错。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            out.append("")
            continue
        if ln.startswith(" " * 8) and out and out[-1]:
            out[-1] = out[-1].rstrip() + ln.strip()
        else:
            out.append(ln.rstrip())
    return out


WIN_LIMIT = 240


def _long_path(p):
    """Windows 长路径前缀，避免 MAX_PATH(260) 限制导致写文件失败。"""
    p = os.path.abspath(p)
    if os.name != "nt":
        return p
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\" + p if len(p) >= WIN_LIMIT else p


def _copy(src, dst):
    import shutil
    shutil.copyfile(_long_path(src), _long_path(dst))
    return dst


def _write_bytes(path, data):
    with open(_long_path(path), "wb") as fh:
        fh.write(data)
    return path


def translate_texts(texts, src="en", tgt="zh", retries=3, batch=10):
    """回填用的整段翻译（腾讯免费接口，按批提交，减少往返）。"""
    out = list(texts)
    idx = [i for i, t in enumerate(texts) if t and t.strip()]
    for k in range(0, len(idx), batch):
        chunk = idx[k:k + batch]
        body = {
            "header": {"client_key": CK, "fn": "auto_translation", "session": "", "user": ""},
            "model_category": "normal",
            "reference": {"sentence_lib": [], "term_lib": []},
            "source": {"lang": src, "text_list": [texts[i] for i in chunk]},
            "target": {"lang": tgt},
            "type": "plain",
        }
        got = False
        for i in range(retries):
            try:
                req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                res = j.get("auto_translation") or []
                if res:
                    for n, t_i in enumerate(chunk):
                        if n < len(res) and res[n]:
                            out[t_i] = res[n]
                    got = True
                    break
            except Exception:
                time.sleep(1.0 * (i + 1))
        if not got:
            pass
    return out


def _translate_texts_legacy(texts, src="en", tgt="zh", retries=3):
    out = []
    for t in texts:
        body = {
            "header": {"client_key": "desktop-dfc543ff7c364da490db1ddd1cd74dc9",
                       "fn": "auto_translation", "session": "", "user": ""},
            "model_category": "normal",
            "reference": {"sentence_lib": [], "term_lib": []},
            "source": {"lang": src, "text_list": [t]},
            "target": {"lang": tgt},
            "type": "plain",
        }
        got = t
        for i in range(retries):
            try:
                req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                res = j.get("auto_translation") or []
                if res and res[0]:
                    got = res[0]
                    break
            except Exception:
                time.sleep(1.0 * (i + 1))
        out.append(got)
    return out


def quality_metrics(src_path, out_path):
    """翻译质量体检：引用保留率、兼容区汉字、页末空白。"""
    import pymupdf, re as _re
    m = {"cites_src": 0, "cites_out": 0, "compat_cjk": 0, "tail_blank_pages": 0}
    try:
        s = pymupdf.open(src_path)
        o = pymupdf.open(out_path)
        pat = _re.compile(r"\[\d{1,3}(?:[,]\s?\d{1,3})*\]")
        for p in s:
            m["cites_src"] += len(pat.findall(p.get_text()))
        for p in o:
            t = p.get_text()
            m["cites_out"] += len(pat.findall(t))
            m["compat_cjk"] += sum(1 for ch in t if 0xF900 <= ord(ch) <= 0xFAFF)
            # 页末空白：最后一个文本块的底部到页底
            blocks = [b for b in p.get_text("blocks") if b[4].strip()]
            if blocks:
                bottom = max(b[3] for b in blocks)
                if (p.rect.y1 - bottom) / max(1.0, p.rect.y1) > 0.45:
                    m["tail_blank_pages"] += 1
        s.close()
        o.close()
    except Exception:
        pass
    if m["cites_src"]:
        m["cite_ratio"] = round(m["cites_out"] / m["cites_src"], 2)
    else:
        m["cite_ratio"] = None
    return m


def _normalize_pdf(src_path, dst_path, log=print):
    """用 PyMuPDF 重写 PDF：修复内联图像等结构问题（BabelDOC 的 Rust 解析器对这类文件会直接失败）。"""
    try:
        import pymupdf
        d = pymupdf.open(src_path)
        d.save(dst_path, garbage=4, deflate=True, clean=True)
        d.close()
        log("  已规范化 PDF：%s" % os.path.basename(dst_path))
        return dst_path
    except Exception as e:
        log("  规范化失败，使用原文件：%r" % (e,))
        return src_path


def _dump_log(text, out_dir):
    """把引擎完整日志写到输出目录，方便用户直接发给我们。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "引擎日志_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        with open(p, "w", encoding="utf-8-sig") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + chr(10))
            f.write(text)
        return p
    except Exception as e:
        return "(保存失败: %r)" % (e,)


def _error_digest(text, limit=18):
    """从引擎日志里提炼真正有用的报错行（ERROR/Traceback/Exception），再附尾部。"""
    lines = [ln for ln in _unwrap(text) if ln.strip()]
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

def _ensure_fonts(log=print):
    """字体缺失时先下载（否则中文字体回退，排版会重叠）。"""
    try:
        import engine_manager as em
        if em.fonts_ready():
            return True
        log("  字体资源缺失，正在下载…")
        return em.ensure_assets(log=log)
    except Exception as e:
        log("  字体检查跳过：%r" % (e,))
        return False


_CURRENT_PROC = {"p": None}


def kill_current():
    """停止正在跑的 BabelDOC 子进程（用于「停止」按钮）。"""
    p = _CURRENT_PROC.get("p")
    if p and p.poll() is None:
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        _CURRENT_PROC["p"] = None
        return True
    return False


def translate(src_pdf, out_dir, src="en", tgt="zh", base_name=None, log=print, progress=None, service="bing"):
    if not available():
        raise RuntimeError("PDF_Translator 未安装：%s" % ROOT)
    if service == "tencent":
        ensure_shim(log)
    _ensure_fonts(log)

    tmp = tempfile.mkdtemp(prefix="babeldoc_")
    log_path = os.path.join(tmp, "engine.log")

    # 关键：先用 PyMuPDF 重写一遍，规避 BabelDOC Rust 解析器对内联图像的严格校验
    # （必须在拼命令行之前完成，否则命令里还是原始文件）
    norm = os.path.join(tmp, "normalized.pdf")
    src_pdf = _normalize_pdf(src_pdf, norm, log)

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
        _CURRENT_PROC["p"] = proc
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
    dump = _dump_log(text, out_dir) if (proc.returncode != 0) else ""
    if proc.returncode != 0:
        raise RuntimeError("pdf2zh_next 退出码 %d" % proc.returncode + chr(10)
                           + _error_digest(text) + chr(10) + "完整日志：" + dump)
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
        dump = _dump_log(text, out_dir)
        raise RuntimeError("pdf2zh_next 未产出文件。" + hint + chr(10) + digest
                           + chr(10) + "完整日志：" + dump)
    os.makedirs(out_dir, exist_ok=True)
    base = (base_name or os.path.splitext(os.path.basename(src_pdf))[0])[:60]
    out_mono = os.path.join(out_dir, "%s_%s.pdf" % (base, tgt))
    out_dual = os.path.join(out_dir, "%s_%s_对照.pdf" % (base, tgt))
    _copy(mono[0], out_mono)

    def _post_progress(label, i, n):
        if progress:
            progress({"stage": label, "percent": 90 + int(9.0 * i / max(1, n)),
                      "page": i, "pages": n})

    # 修复 BabelDOC 因空间不足造成的段落截断
    try:
        import postfix
        log("  检查段落截断…")
        fixed, skipped = postfix.repair(norm, out_mono, translate_batch=translate_texts,
                                        log=log, progress=_post_progress)
        if skipped:
            log("  !! %d 段因版面空间不足仍为截断" % skipped)
        try:
            filled = postfix.fill_missing(norm, out_mono, translate_batch=translate_texts,
                                          log=log, progress=_post_progress)
            if filled:
                log("  .. 补回整段缺失 %d 处" % filled)
        except Exception as e:
            log("  补缺跳过：%r" % (e,))
    except Exception as e:
        log("  截断修复跳过：%r" % (e,))

    # 最后清理 BabelDOC 画出的方块乱码（独立全量保存，确保生效）
    try:
        postfix.clean_artifacts(out_mono, log=log)
    except Exception as e:
        log("  乱码清理跳过：%r" % (e,))

    # 参考文献 / 页眉页脚 恢复原文（这些不该翻译）
    try:
        postfix.restore_fixed_zones(norm, out_mono, log=log)
    except Exception as e:
        log("  原样区恢复跳过：%r" % (e,))

    if dual:
        _copy(dual[0], out_dual)
    else:
        out_dual = None

    metrics = {}
    try:
        metrics = quality_metrics(norm, out_mono)
        log("  质量体检：引用 %s/%s（%s）、兼容区汉字 %s、页末大空白 %s 页"
            % (metrics.get("cites_out"), metrics.get("cites_src"), metrics.get("cite_ratio"),
               metrics.get("compat_cjk"), metrics.get("tail_blank_pages")))
    except Exception as e:
        log("  质量体检跳过：%r" % (e,))

    shutil.rmtree(tmp, ignore_errors=True)
    out = {"mono": out_mono, "dual": out_dual, "truncated": 0, "metrics": metrics}
    try:
        out["truncated"] = skipped
    except Exception:
        pass
    return out
