# -*- coding: utf-8 -*-
"""引擎管理中心：检测 / 安装 / 卸载 BabelDOC（pdf2zh_next）与腾讯免费引擎。

设计目标：普通用户不需要命令行，不需要预装 Python 之外的任何东西。
- 目录：%LOCALAPPDATA%\PDF翻译专家\babeldoc
- 环境：优先系统 Python 3.10~3.12 建 venv；找不到则下载官方 embeddable Python
- 国内加速：pip 走清华镜像
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile

APP_DIR_NAME = "PDF翻译专家"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
PY_EMBED_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

def base_dir():
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, APP_DIR_NAME, "babeldoc")

def env_python():
    p = base_dir()
    for c in (os.path.join(p, "env", "Scripts", "python.exe"),
              os.path.join(p, "env", "bin", "python")):
        if os.path.exists(c):
            return c
    return None

def has_babeldoc():
    py = env_python()
    if not py:
        return False
    try:
        r = subprocess.run([py, "-c", "import pdf2zh_next, babeldoc; print('ok')"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "ok" in (r.stdout or "")
    except Exception:
        return False

def has_tencent():
    py = env_python()
    if not py:
        return False
    try:
        r = subprocess.run([py, "-c", "import pdf2zh_next, pathlib; "
                            "print((pathlib.Path(pdf2zh_next.__file__).parent/'translator'/'translator_impl'/'tencentfree.py').exists())"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "True" in (r.stdout or "")
    except Exception:
        return False

def _sys_python():
    cands = []
    for name in ("python.exe", "python"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    for v in ("-3.12", "-3.11", "-3.10"):
        w = shutil.which("py")
        if w:
            cands.append(w + " " + v)
    for c in cands:
        try:
            out = subprocess.run(c.split() + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ver = (out.stdout or "").strip()
            if re.match(r"^3\.(1[0-2])$", ver):
                return c
        except Exception:
            continue
    return None

def status():
    return {"base": base_dir(), "python": env_python(),
            "babeldoc": has_babeldoc(), "tencent": has_tencent(),
            "sys_python": _sys_python()}

def _run(cmd, log, cwd=None, timeout=3600):
    log("$ " + " ".join(cmd))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    last = time.time()
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line)
            last = time.time()
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("命令失败(%d): %s" % (p.returncode, " ".join(cmd[:3])))

def install_babeldoc(log=print, progress=None):
    base = base_dir()
    os.makedirs(base, exist_ok=True)
    py = _sys_python()
    if not py:
        raise RuntimeError("未找到可用的 Python 3.10~3.12。请先安装 Python（勾选 Add to PATH）后重试。")

    def step(pct, msg):
        if progress:
            progress(pct, msg)
        log("== " + msg)

    step(5, "创建独立运行环境（venv）")
    env_dir = os.path.join(base, "env")
    if not os.path.exists(env_dir):
        _run(py.split() + ["-m", "venv", env_dir], log)
    vpy = env_python()
    if not vpy:
        raise RuntimeError("venv 创建失败")

    step(12, "升级 pip")
    try:
        _run([vpy, "-m", "pip", "install", "-q", "--upgrade", "pip", "-i", MIRROR], log, timeout=900)
    except Exception:
        log("pip 升级失败，继续尝试")

    step(25, "安装 pdf2zh_next（约 300~500MB，视网络 3~15 分钟）")
    _run([vpy, "-m", "pip", "install", "-i", MIRROR, "pdf2zh_next"], log, timeout=5400)

    step(85, "写入默认配置")
    cfg_dir = os.path.join(base, "config")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg = os.path.join(cfg_dir, "config.toml")
    if not os.path.exists(cfg):
        open(cfg, "w", encoding="utf-8").write('config_file = "null"\nreport_interval = 0.2\n')

    step(92, "安装腾讯免费翻译引擎")
    try:
        install_tencent(log=log)
    except Exception as e:
        log("腾讯引擎安装失败（不影响使用 BabelDOC）：%r" % (e,))

    step(100, "安装完成")
    return status()

def install_tencent(log=print):
    py = env_python()
    if not py:
        raise RuntimeError("BabelDOC 尚未安装")
    here = os.path.dirname(os.path.abspath(__file__))
    patch = os.path.join(here, "install_tencent_free.py")
    env = dict(os.environ)
    env["PDF2ZH_HOME"] = base_dir()
    subprocess.run([py, patch], check=True, env=env,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

def uninstall(log=print):
    base = base_dir()
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    log("已删除 %s" % base)
    return status()
