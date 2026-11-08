# -*- coding: utf-8 -*-
"""给 pdf2zh_next 增加「腾讯免费翻译」引擎（TencentFree）
幂等：已安装则跳过；改动前自动备份
用法： D:\PDF_Translator\env\python.exe install_tencent_free.py
"""
import os, shutil, sys, time
from pathlib import Path

ENV = Path(r"D:\PDF_Translator\env")
SP = ENV / "Lib" / "site-packages" / "pdf2zh_next"
MODEL_FILE = SP / "config" / "translate_engine_model.py"
IMPL_DIR = SP / "translator" / "translator_impl"
IMPL_FILE = IMPL_DIR / "tencentfree.py"

IMPL_CODE = '''# -*- coding: utf-8 -*-
"""腾讯交互翻译（免费匿名接口）—— pdf2zh_next 翻译引擎插件"""
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


def backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copyfile(p, bak)


def install_impl() -> None:
    IMPL_DIR.mkdir(parents=True, exist_ok=True)
    if IMPL_FILE.exists():
        print("impl exists:", IMPL_FILE.name)
        return
    IMPL_FILE.write_text(IMPL_CODE, encoding="utf-8")
    print("impl written:", IMPL_FILE.name)


def install_settings() -> None:
    src = MODEL_FILE.read_text(encoding="utf-8")
    if "TencentFreeSettings" in src:
        print("settings already patched")
        return
    if "TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = (" not in src:
        raise SystemExit("union alias not found; abort")
    cls = '''
class TencentFreeSettings(BaseModel):
    """Tencent free anonymous translation"""

    translate_engine_type: Literal["TencentFree"] = Field(default="TencentFree")

    def validate_settings(self) -> None:
        pass


'''
    src = src.replace("TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = (", cls + "TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = (", 1)
    if "| CLISettings" not in src:
        raise SystemExit("CLISettings anchor not found; abort")
    src = src.replace("    | CLISettings\n)", "    | CLISettings\n    | TencentFreeSettings\n)", 1)
    backup(MODEL_FILE)
    MODEL_FILE.write_text(src, encoding="utf-8")
    print("settings patched")


if __name__ == "__main__":
    if not MODEL_FILE.exists():
        raise SystemExit("pdf2zh_next not found under %s" % ENV)
    install_impl()
    install_settings()
    print("done. CLI flag: --tencentfree")
