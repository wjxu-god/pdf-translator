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
    custom = (os.environ.get("PDFTRANS_BABELDOC_DIR") or "").strip()
    if custom:
        return custom
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

    step(90, "修复 babeldoc 的 PDF 解析问题（内联图像 EI）")
    try:
        install_ei_patch(log=log)
    except Exception as e:
        log("EI 补丁未应用（不影响大多数 PDF）：%r" % (e,))

    step(91, "把资源下载源改为国内可用（modelscope 优先）")
    try:
        install_asset_upstream_patch(log=log)
    except Exception as e:
        log("资源上游补丁未应用：%r" % (e,))

    step(92, "安装腾讯免费翻译引擎")
    try:
        install_tencent(log=log)
    except Exception as e:
        log("腾讯引擎安装失败（不影响使用 BabelDOC）：%r" % (e,))

    step(96, "预热版面模型（约 50MB，走国内镜像，仅首次需要）")
    try:
        warm = os.path.join(base, "warmup.pdf")
        import pymupdf
        d = pymupdf.open(); pg = d.new_page()
        pg.insert_text((40, 80), "This is a warmup sentence.", fontsize=14, fontname="helv")
        d.save(warm); d.close()
        env2 = dict(os.environ)
        env2["HF_ENDPOINT"] = env2.get("PDFTRANS_HF_ENDPOINT", "https://hf-mirror.com")
        env2["PYTHONIOENCODING"] = "utf-8"
        outdir = os.path.join(base, "warmup_out")
        os.makedirs(outdir, exist_ok=True)
        p = subprocess.Popen([vpy, "-c", "from pdf2zh_next.main import cli; cli()",
                              warm, "--output", outdir, "--lang-in", "en", "--lang-out", "zh",
                              "--bing"], cwd=base, env=env2,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            if line.strip():
                log(line.rstrip()[:200])
        p.wait()
        if p.returncode == 0:
            log("模型预热完成")
        else:
            log("模型预热未成功（不影响安装，首次翻译时会再尝试）")
    except Exception as e:
        log("模型预热跳过：%r" % (e,))

    step(100, "安装完成")
    return status()

IMPL_NAME = "tencentfree"
IMPL_SRC = '''# -*- coding: utf-8 -*-
"""腾讯交互翻译（免费匿名接口）—— pdf2zh_next 翻译引擎插件（由 PDF翻译专家 安装）"""
import json
import logging
import time
import urllib.request

from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator

logger = logging.getLogger(__name__)

API = "https://transmart.qq.com/api/imt"
CLIENT_KEY = "desktop-dfc543ff7c364da490db1ddd1cd74dc9"


class TencentFreeTranslator(BaseTranslator):
    name = "tencentfree"
    lang_map = {"zh": "zh", "zh-cn": "zh", "zh-tw": "zh", "auto": "en",
                "en": "en", "ja": "ja", "ko": "ko", "de": "de", "fr": "fr",
                "ru": "ru", "es": "es", "pt": "pt", "it": "it"}

    def __init__(self, settings: SettingsModel, rate_limiter: BaseRateLimiter):
        super().__init__(settings, rate_limiter)

    def do_translate(self, text, rate_limit_params: dict = None) -> str:
        body = {
            "header": {"client_key": CLIENT_KEY, "fn": "auto_translation",
                       "session": "", "user": ""},
            "model_category": "normal",
            "reference": {"sentence_lib": [], "term_lib": []},
            "source": {"lang": self.lang_in, "text_list": [text]},
            "target": {"lang": self.lang_out},
            "type": "plain",
        }
        last = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    API, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=45) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                res = j.get("auto_translation") or []
                if res and res[0]:
                    return res[0]
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.0 + attempt * 1.5)
        logger.warning("tencentfree translate failed: %r", last)
        return text
'''

SETTINGS_CLASS = '''
class TencentFreeSettings(BaseModel):
    """Tencent free anonymous translation"""

    translate_engine_type: Literal["TencentFree"] = Field(default="TencentFree")

    def validate_settings(self) -> None:
        pass


'''
def _site_packages(base):
    for cand in (os.path.join(base, "env", "Lib", "site-packages"),
                 *[os.path.join(base, "env", "lib", p, "site-packages")
                   for p in (os.listdir(os.path.join(base, "env", "lib"))
                             if os.path.isdir(os.path.join(base, "env", "lib")) else [])]):
        if os.path.isdir(cand):
            return cand
    return None


EI_NEW_METHOD = '''    def _is_inline_image_ei_at(self, pos: int) -> bool:
        if self._is_delimited_keyword_at(pos, b"EI"):
            return True
        if pos > 0 and self.data[pos - 1 : pos] == b">":
            next_pos = pos + 2
            return (next_pos >= len(self.data)
                    or self.data[next_pos : next_pos + 1] in WHITESPACE)
        return False

'''

EI_OLD_CONSUME = '''    def _consume_inline_image_end(self) -> bool:
        probe = self.pos
        while probe < len(self.data) and self.data[probe : probe + 1] in WHITESPACE:
            probe += 1
'''

EI_NEW_CONSUME = '''    def _consume_inline_image_end(self) -> bool:
        probe = self.pos
        if probe < len(self.data) and self.data[probe : probe + 1] == b">":
            probe += 1
        while probe < len(self.data) and self.data[probe : probe + 1] in WHITESPACE:
            probe += 1
'''


def install_ei_patch(log=print):
    """给 babeldoc 的 PDF tokenizer 打内联图像 EI 宽容补丁。"""
    base = base_dir()
    site = _site_packages(base)
    if not site:
        raise RuntimeError("未找到 site-packages")
    target = os.path.join(site, "babeldoc", "format", "pdf", "new_parser", "tokenizer.py")
    if not os.path.isfile(target):
        raise RuntimeError("未找到 babeldoc tokenizer")
    src = open(target, encoding="utf-8").read()
    if "_is_inline_image_ei_at" in src:
        log("EI 补丁已存在，跳过")
        return True
    anchors = [
        ('            if not self._is_delimited_keyword_at(pos, b"EI"):\n',
         "            if not self._is_inline_image_ei_at(pos):\n"),
        ('        if not self._is_delimited_keyword_at(probe, b"EI"):\n',
         "        if self._is_inline_image_ei_at(probe):\n"),
        (EI_OLD_CONSUME, EI_NEW_CONSUME),
        ("    def _is_delimited_keyword_at(self, pos: int, keyword: bytes) -> bool:\n",
         EI_NEW_METHOD + "    def _is_delimited_keyword_at(self, pos: int, keyword: bytes) -> bool:\n"),
    ]
    out = src
    for old, new in anchors:
        if out.count(old) != 1:
            raise RuntimeError("锚点未命中（babeldoc 版本可能不同）")
        out = out.replace(old, new, 1)
    bak = target + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(target, bak)
    open(target, "w", encoding="utf-8").write(out)
    try:
        import py_compile
        py_compile.compile(target, doraise=True)
    except Exception as e:
        shutil.copyfile(bak, target)
        raise RuntimeError("补丁后语法校验失败，已回滚：%r" % (e,))
    log("EI 补丁已应用（备份 %s）" % os.path.basename(bak))
    return True



ASSET_PATCH_MARK = "# --- PDF翻译专家: 字体/资源上游改为国内可用优先 ---"

ASSET_PATCH_CODE = '''

# --- PDF翻译专家: 字体/资源上游改为国内可用优先 ---
# 原版只列 github/huggingface/modelscope，hf-mirror 被注释掉；国内网络下前两个常失败，
# 会导致字体下载不到，排版出现文字重叠。
_PREF_UPSTREAM = ["modelscope", "hf-mirror", "github", "huggingface"]

if "hf-mirror" not in FONT_METADATA_URL:
    FONT_METADATA_URL["hf-mirror"] = (
        "https://hf-mirror.com/datasets/awwaawwa/BabelDOC-Assets/resolve/main/"
        "font_metadata.json?download=true"
    )
    FONT_URL_BY_UPSTREAM["hf-mirror"] = (
        lambda name: f"https://hf-mirror.com/datasets/awwaawwa/BabelDOC-Assets/resolve/main/fonts/{name}?download=true"
    )
    CMAP_URL_BY_UPSTREAM["hf-mirror"] = (
        lambda name: f"https://hf-mirror.com/datasets/awwaawwa/BabelDOC-Assets/resolve/main/cmap/{name}?download=true"
    )

FONT_METADATA_URL = {k: FONT_METADATA_URL[k] for k in _PREF_UPSTREAM if k in FONT_METADATA_URL}
FONT_URL_BY_UPSTREAM = {k: FONT_URL_BY_UPSTREAM[k] for k in _PREF_UPSTREAM if k in FONT_URL_BY_UPSTREAM}
CMAP_URL_BY_UPSTREAM = {k: CMAP_URL_BY_UPSTREAM[k] for k in _PREF_UPSTREAM if k in CMAP_URL_BY_UPSTREAM}
'''


def install_asset_upstream_patch(log=print):
    """把 babeldoc 的字体/资源下载上游改成 modelscope 优先（国内可达）。"""
    base = base_dir()
    site = _site_packages(base)
    if not site:
        raise RuntimeError("未找到 site-packages")
    target = os.path.join(site, "babeldoc", "assets", "embedding_assets_metadata.py")
    if not os.path.isfile(target):
        raise RuntimeError("未找到 babeldoc 资源元数据文件")
    src = open(target, encoding="utf-8").read()
    if ASSET_PATCH_MARK in src:
        log("资源上游补丁已存在，跳过")
        return True
    bak = target + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(target, bak)
    open(target, "w", encoding="utf-8").write(src + ASSET_PATCH_CODE)
    try:
        import py_compile
        py_compile.compile(target, doraise=True)
    except Exception as e:
        shutil.copyfile(bak, target)
        raise RuntimeError("补丁后语法校验失败，已回滚：%r" % (e,))
    log("资源上游已改为 modelscope 优先（备份 %s）" % os.path.basename(bak))
    return True


def fonts_ready():
    """检查 babeldoc 字体缓存是否已就绪。"""
    cache = os.path.join(os.path.expanduser("~"), ".cache", "babeldoc", "fonts")
    try:
        return any(n.lower().endswith((".ttf", ".otf", ".ttc")) for n in os.listdir(cache))
    except Exception:
        return False


def ensure_assets(log=print, timeout=900):
    """确保字体资源可用：缺失时用 babeldoc 自身流程预下载（已改成国内上游优先）。"""
    if fonts_ready():
        return True
    base = base_dir()
    py = env_python()
    if not py:
        return False
    log("检测到字体资源缺失，开始下载（modelscope 优先，约 30MB）…")
    try:
        import tempfile
        warm = os.path.join(tempfile.mkdtemp(prefix="fontwarm_"), "w.pdf")
        try:
            import pymupdf
            d = pymupdf.open(); pg = d.new_page()
            pg.insert_text((40, 80), "Warmup for fonts.", fontsize=14, fontname="helv")
            d.save(warm); d.close()
        except Exception:
            import shutil as _sh
            _sh.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "warmup.pdf"), warm)
        env2 = dict(os.environ)
        env2["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.Popen([py, "-c", "from pdf2zh_next.main import cli; cli()", warm,
                              "--output", os.path.join(base, "warmup_out"),
                              "--lang-in", "en", "--lang-out", "zh", "--bing"],
                             cwd=base, env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            if line.strip():
                log(line.rstrip()[:180])
        p.wait()
    except Exception as e:
        log("资源下载异常：%r" % (e,))
    ok = fonts_ready()
    log("字体资源就绪" if ok else "字体资源仍缺失（排版可能出现重叠）")
    return ok


def install_tencent(log=print):
    """在已安装的 venv 里写入腾讯翻译引擎并注册（不依赖外部脚本，exe 内可直接执行）。"""
    base = base_dir()
    site = None
    for cand in (os.path.join(base, "env", "Lib", "site-packages"),
                 *[os.path.join(base, "env", "lib", p, "site-packages")
                   for p in (os.listdir(os.path.join(base, "env", "lib"))
                             if os.path.isdir(os.path.join(base, "env", "lib")) else [])]):
        if os.path.isdir(cand):
            site = cand
            break
    if not site:
        raise RuntimeError("未找到 site-packages，请先安装 BabelDOC")
    pkg = os.path.join(site, "pdf2zh_next")
    if not os.path.isdir(pkg):
        raise RuntimeError("未找到 pdf2zh_next，请先安装 BabelDOC")

    impl_dir = os.path.join(pkg, "translator", "translator_impl")
    os.makedirs(impl_dir, exist_ok=True)
    impl_path = os.path.join(impl_dir, IMPL_NAME + ".py")
    open(impl_path, "w", encoding="utf-8").write(IMPL_SRC)
    log("写入翻译器: %s" % impl_path)

    model_file = os.path.join(pkg, "config", "translate_engine_model.py")
    src = open(model_file, encoding="utf-8").read()
    if "TencentFreeSettings" not in src:
        anchor = "TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = ("
        if anchor not in src:
            raise RuntimeError("引擎注册锚点未找到，pdf2zh_next 版本可能不兼容")
        src = src.replace(anchor, SETTINGS_CLASS + anchor, 1)
        if "| CLISettings\n)" in src:
            src = src.replace("| CLISettings\n)", "| CLISettings\n    | TencentFreeSettings\n)", 1)
        else:
            raise RuntimeError("引擎联合类型锚点未找到")
        bak = model_file + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(model_file, bak)
        open(model_file, "w", encoding="utf-8").write(src)
        log("注册引擎: TencentFree（备份 %s）" % os.path.basename(bak))
    else:
        log("注册已存在，跳过")
    return True

def uninstall(log=print):
    base = base_dir()
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    log("已删除 %s" % base)
    return status()
