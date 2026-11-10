# -*- coding: utf-8 -*-
"""PDF 保排版翻译 v3
切分：pymupdf_layout（pymupdf4llm.page_boxes）—— 版面分析 + 阅读顺序 + 段落分组
翻译：腾讯交互翻译匿名接口
回填：按原 bbox 抹除原文后写译文，字号自适应（最小 75%，可向下扩框 50%）
"""
import json, os, re, sys, time, unicodedata, urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://transmart.qq.com/api/imt"
CK = "desktop-dfc543ff7c364da490db1ddd1cd74dc9"
FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]

SKIP_CLASSES = {"formula", "image", "picture", "figure", "footnote-separator"}

# PyMuPDF 内置 CJK 字体：不依赖系统字体，任何机器都能写中文
BUILTIN_CJK = "china-s"

def _font():
    """返回可用字体文件；None 表示使用 PyMuPDF 内置 CJK 字体。"""
    if os.environ.get("PDFTRANS_NO_SYSFONT"):
        return None
    for f in FONTS:
        if os.path.exists(f):
            return f
    return None

SYMBOL_MAP = {
    "\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2217": "*",
    "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u2248": "~=",
    "\u2208": " in ", "\u2209": " not in ", "\u2211": "sum", "\u220f": "prod",
    "\u221a": "sqrt", "\u221e": "inf", "\u2192": "->", "\u2190": "<-",
    "\u21d2": "=>", "\u21d4": "<=>", "\u00d7": "x", "\u00f7": "/",
    "\u2329": "<", "\u232a": ">", "\u27e8": "<", "\u27e9": ">",
    "\u2308": "[", "\u2309": "]", "\u230a": "[", "\u230b": "]",
    "\u22a5": "|", "\u2225": "||", "\u2223": "|", "\u00b1": "+/-",
}

REFS_RE = re.compile(r"^\s*(references|bibliography|literature cited|参考文献)\s*$", re.I)

def _is_refs_heading(s):
    return bool(REFS_RE.match(s.strip()))

OPERATORS = set("=+-*/<>^_()[]{}|~")

def _mathish(t):
    t = t.strip()
    if not t or len(t) > 32:
        return False
    if re.search(r"\.\s", t):          # "y gi. y y v" style fragments
        return False
    toks = [x for x in re.split(r"\s+", t) if x]
    if len(toks) >= 3 and all(len(x) <= 2 for x in toks):
        return False
    return any(c in OPERATORS for c in t) or len(t) <= 3

def _norm_math(s):
    s = unicodedata.normalize("NFKC", s)
    for k, v in SYMBOL_MAP.items():
        s = s.replace(k, v)
    # drop any remaining char the CJK font cannot render
    out = []
    for ch in s:
        if ch in "\n\t ":
            out.append(ch); continue
        o = ord(ch)
        if o < 0x2500 or 0x4e00 <= o <= 0x9fff or 0x3000 <= o <= 0x303f or 0xff00 <= o <= 0xffef:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)

def _clean(s):
    s = s.replace("\x00", "")
    return re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", s)

def translate_texts(texts, src="en", tgt="zh", batch=12, retries=5):
    out = list(texts)
    ok = [False] * len(texts)
    idx = [i for i, t in enumerate(texts) if t.strip()]
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
        for attempt in range(retries):
            try:
                req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                res = j.get("auto_translation") or []
                if res:
                    for n, i in enumerate(chunk):
                        if n < len(res) and res[n] and res[n].strip() != texts[i].strip():
                            out[i] = res[n]
                            ok[i] = True
                    if any(ok[i] for i in chunk):
                        break
                time.sleep(1.0 + attempt * 1.5)
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.25)
    return out, ok

def _iou(a, b):
    try:
        import pymupdf
        ra, rb = pymupdf.Rect(a), pymupdf.Rect(b)
        inter = ra & rb
        if not inter or inter.is_empty:
            return 0.0
        ia = inter.get_area()
        ua = ra.get_area() + rb.get_area() - ia
        return ia / ua if ua > 0 else 0.0
    except Exception:
        return 0.0

def _merge_inline(page, pbox, all_boxes, texts_by_bbox):
    """Assemble paragraph text from spans, keeping sub/superscripts on their line."""
    import pymupdf
    prect = pymupdf.Rect(pbox)
    spans = []
    try:
        d = page.get_text("dict", clip=prect)
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = _clean(sp.get("text", ""))
                    if not t.strip():
                        continue
                    x0, y0, x1, y1 = sp["bbox"]
                    spans.append(((y0 + y1) / 2.0, x0, x1, t))
    except Exception:
        pass
    # inline formulas inside the paragraph (short ones only)
    height = prect.height
    for other in all_boxes:
        cls = (other.get("class") or "").lower()
        if cls in ("text", "section-header", "title", "list-item", "page-header", "page-footer"):
            continue
        orect = pymupdf.Rect(other["bbox"])
        if orect.height > height * 0.35:
            continue
        cx, cy = (orect.x0 + orect.x1) / 2.0, (orect.y0 + orect.y1) / 2.0
        if prect.contains(pymupdf.Point(cx, cy)):
            t = texts_by_bbox.get(tuple(round(v, 2) for v in other["bbox"]), "").strip()
            if t and _mathish(t):
                spans.append((cy, orect.x0, orect.x1, t))
    if not spans:
        return ""
    spans.sort(key=lambda s: s[0])
    lines = []
    for yc, x0, x1, t in spans:
        if lines and abs(yc - lines[-1]["y"]) <= 4.0:
            lines[-1]["items"].append((x0, x1, t))
            lines[-1]["y"] = (lines[-1]["y"] + yc) / 2.0
        else:
            lines.append({"y": yc, "items": [(x0, x1, t)]})
    out_lines = []
    for ln in lines:
        items = sorted(ln["items"], key=lambda s: s[0])
        buf = ""
        last_x1 = None
        for x0, x1, t in items:
            if buf and last_x1 is not None:
                gap = x0 - last_x1
                if gap > 2.0:
                    buf += " "
            buf += t.strip()
            last_x1 = x1 if last_x1 is None else max(last_x1, x1)
        out_lines.append(buf)
    text = " ".join(out_lines)
    toks = text.split()
    k = 0
    while k < len(toks) and len(toks[k].strip(".,;:")) <= 2 and k < 4:
        k += 1
    if k >= 2 and k < len(toks) - 1:
        text = " ".join(toks[k:])
    return text

def _fix_numbering(s):
    # "2.2随机图" -> "2.2 随机图"; keep 2.1 / 3.4.1 style numbering separated
    return re.sub(r"^(\d+(?:\.\d+)*)(?=[^\s\d.])", r"\1 ", s)

def _rgb(color):
    if color is None:
        return (0, 0, 0)
    return ((color >> 16) & 255) / 255.0, ((color >> 8) & 255) / 255.0, (color & 255) / 255.0

def _box_fontsize(page, rect):
    try:
        d = page.get_text("dict", clip=rect)
        sizes = []
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("text", "").strip():
                        sizes.append(sp.get("size", 10))
        if sizes:
            return max(sizes)
    except Exception:
        pass
    return max(9.0, min(rect.height * 0.85, 12.0))

def _box_color(page, rect):
    try:
        d = page.get_text("dict", clip=rect)
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("text", "").strip() and sp.get("color") is not None:
                        return sp["color"]
    except Exception:
        pass
    return 0

def _table_cells(page, rect):
    """Return [(cell_rect, text)] for a table area."""
    import pymupdf
    out = []
    try:
        tabs = page.find_tables(clip=pymupdf.Rect(rect))
    except Exception:
        return out
    for t in getattr(tabs, "tables", []):
        for cell in getattr(t, "cells", []):
            if not cell:
                continue
            cr = pymupdf.Rect(cell)
            if cr.is_empty or cr.width < 6 or cr.height < 4:
                continue
            try:
                txt = " ".join(_norm_math(_clean(page.get_textbox(cr))).split())
            except Exception:
                continue
            if not txt or len(txt) < 1:
                continue
            if not re.search(r"[A-Za-z一-鿿]", txt):
                continue
            out.append((cr, txt))
    return out

def make_spread(src_path, trans_path, out_path, gap=12.0):
    """Build a single PDF where each page holds original (left) + translation (right)."""
    import pymupdf
    a = pymupdf.open(src_path)
    b = pymupdf.open(trans_path)
    duo = pymupdf.open()
    n = min(len(a), len(b))
    for i in range(n):
        pa = a[i]
        pb = b[i]
        w = pa.rect.width + pb.rect.width + gap
        h = max(pa.rect.height, pb.rect.height)
        page = duo.new_page(width=w, height=h)
        page.show_pdf_page(pymupdf.Rect(0, 0, pa.rect.width, pa.rect.height), a, i)
        page.show_pdf_page(pymupdf.Rect(pa.rect.width + gap, 0, w, h), b, i)
    try:
        duo.subset_fonts()
    except Exception:
        pass
    duo.save(out_path, garbage=4, deflate=True, clean=True)
    duo.close(); a.close(); b.close()
    return out_path

def _collect(doc, chunks, log):
    """Pass 1: gather (page_no, box) work items in reading order."""
    import pymupdf
    GID = [0]
    pages = []
    refs_mode = False
    for pno in range(len(doc)):
        page = doc[pno]
        boxes = None
        if chunks and pno < len(chunks):
            boxes = chunks[pno].get("page_boxes")
        if not boxes:
            boxes = [{"bbox": b["bbox"], "class": "text"}
                     for b in page.get_text("dict")["blocks"] if b.get("type") == 0]

        texts_by_bbox = {}
        for b in boxes:
            try:
                texts_by_bbox[tuple(round(v, 2) for v in b["bbox"])] = _clean(page.get_textbox(pymupdf.Rect(b["bbox"])))
            except Exception:
                texts_by_bbox[tuple(round(v, 2) for v in b["bbox"])] = ""

        work = []
        kept = []
        for b in boxes:
            cls = (b.get("class") or "text").lower()
            rect = pymupdf.Rect(b["bbox"])
            raw = texts_by_bbox.get(tuple(round(v, 2) for v in b["bbox"]), "")
            if cls == "section-header" and _is_refs_heading(raw):
                refs_mode = True
            if refs_mode:
                continue
            if cls in SKIP_CLASSES:
                continue
            if rect.is_empty or rect.width < 4 or rect.height < 3:
                continue
            if any(_iou(rect, kr) > 0.6 for kr in kept):
                continue
            if cls == "table":
                cells = _table_cells(page, rect)
                if cells:
                    for cr, txt in cells:
                        work.append({"rect": tuple(cr), "text": txt, "cls": "table-cell",
                                     "size": min(_box_fontsize(page, cr), 8.5), "color": 0,
                                     "gid": GID[0]})
                        GID[0] += 1
                    kept.append(rect)
                    continue
            kept.append(rect)
            text = " ".join(_norm_math(_merge_inline(page, b["bbox"], boxes, texts_by_bbox)).split())
            if len(text) < 2:
                continue
            work.append({"rect": tuple(rect), "text": text, "cls": cls,
                         "size": _box_fontsize(page, rect), "color": _box_color(page, rect),
                         "gid": GID[0]})
            GID[0] += 1
        if work:
            pages.append({"pno": pno, "work": work})
        log("  scan page %d/%d boxes=%d%s" % (pno + 1, len(doc), len(work), " [refs]" if refs_mode else ""))
    return pages

def _fit_fontsize(text, rect, base, fontfile, min_fs=4.5, line_factor=1.18):
    """Largest font size (<= base) whose wrapped text fits inside rect. None if impossible."""
    import math
    import pymupdf
    try:
        font = pymupdf.Font(fontfile=fontfile) if fontfile else pymupdf.Font("china-s")
    except Exception:
        return base
    def width_of(s, fs):
        try:
            return font.text_length(s, fontsize=fs)
        except Exception:
            return len(s) * fs * 0.6
    fs = base
    while fs >= min_fs:
        # greedy wrap to count lines
        limit = rect.width
        lines = 1
        cur = ""
        for ch in text:
            w = width_of(ch, fs)
            if width_of(cur, fs) + w > limit:
                lines += 1
                cur = ch
            else:
                cur += ch
            if lines * fs * line_factor > rect.height:
                break
        if lines * fs * line_factor <= rect.height:
            return fs
        fs -= 0.5
    return None

def _apply(page, work, translated):
    import pymupdf
    font = _font()
    placements = []
    for w, t in zip(work, translated):
        if not t or not t.strip():
            continue
        base = w.get("size") or 10.0
        rect0 = pymupdf.Rect(w["rect"])
        is_cell = w.get("cls") == "table-cell"
        cap = min(base, 7.5) if is_cell else base
        fs = _fit_fontsize(t, rect0, cap, font)
        if fs is None and not is_cell:
            fs = max(base * 0.62, 4.0)
        if fs is None:
            continue
        placements.append((w, t, fs, rect0, is_cell))
    if not placements:
        return
    for w, t, fs, rect0, is_cell in placements:
        page.add_redact_annot(rect0 + (-0.4, -0.4, 0.4, 0.4))
    try:
        page.apply_redactions(images=0)
    except Exception:
        page.apply_redactions()
    for w, t, fs, rect0, is_cell in placements:
        try:
            rc = page.insert_textbox(rect0, t, fontsize=fs,
                                     fontname="cjk" if font else BUILTIN_CJK,
                                     fontfile=font if font else None,
                                     color=_rgb(w.get("color", 0)),
                                     align=3 if not is_cell else 0, lineheight=1.15)
        except Exception:
            rc = -1
        if rc < 0:
            # last resort: clip by rendering smaller
            for fs2 in (fs - 0.5, fs - 1.0, fs - 1.5):
                if fs2 < 3.5:
                    break
                try:
                    if page.insert_textbox(rect0, t, fontsize=fs2,
                                           fontname="cjk" if font else BUILTIN_CJK,
                                           fontfile=font if font else None,
                                           color=_rgb(w.get("color", 0)),
                                           align=0, lineheight=1.1) >= 0:
                        break
                except Exception:
                    pass

class Control:
    """暂停 / 继续 / 取消控制（按页粒度生效，并支持部分结果落盘）。"""
    def __init__(self):
        import threading
        self.paused = False
        self.cancelled = False
        self._cv = threading.Condition()

    def pause(self):
        with self._cv:
            self.paused = True

    def resume(self):
        with self._cv:
            self.paused = False
            self._cv.notify_all()

    def cancel(self):
        with self._cv:
            self.cancelled = True
            self.paused = False
            self._cv.notify_all()

    def checkpoint(self, log=None):
        """每页开始前调用：暂停时阻塞，取消时返回 False。"""
        import time
        with self._cv:
            if self.cancelled:
                return False
            if self.paused:
                if log:
                    log("  ⏸ 已暂停（可查看已完成的页面）")
                while self.paused and not self.cancelled:
                    self._cv.wait(0.5)
                if self.cancelled:
                    return False
                if log:
                    log("  ▶ 继续")
        return True


def _flush_partial(doc, out_path):
    """把当前进度写盘，便于暂停时查看已完成页。"""
    try:
        tmp = out_path + ".partial"
        doc.save(tmp, garbage=3, deflate=True)
        import os
        os.replace(tmp, out_path)
        return True
    except Exception:
        return False


def translate_pdf(src_path, out_path, src="en", tgt="zh", log=print, progress=None, workers=6,
                  control=None, start_page=0):
    """逐页流水：扫描 → 翻译 → 回填 → 落盘。支持暂停/继续/取消，暂停时已完成页可直接查看。"""
    import pymupdf
    import pymupdf4llm
    from concurrent.futures import ThreadPoolExecutor

    font = _font()
    # 续翻：以已有的部分结果为底稿
    if start_page and os.path.exists(out_path):
        try:
            doc = pymupdf.open(out_path)
            log("  从第 %d 页继续（已载入部分结果）" % (start_page + 1))
        except Exception:
            doc = pymupdf.open(src_path)
            start_page = 0
    else:
        doc = pymupdf.open(src_path)
    try:
        chunks = pymupdf4llm.to_markdown(src_path, page_chunks=True)
    except Exception:
        chunks = None

    total_pages = len(doc)
    if not start_page:
        _flush_partial(doc, out_path)   # 先落盘一份，之后逐页覆盖
    done_pages = start_page
    boxes_total = 0
    failed_total = 0
    refs_mode = False

    for pno in range(start_page, total_pages):
        if control and not control.checkpoint(log):
            log("  ⏹ 已取消（完成 %d/%d 页）" % (done_pages, total_pages))
            break

        # --- 单页扫描 ---
        page = doc[pno]
        boxes = None
        if chunks and pno < len(chunks):
            boxes = chunks[pno].get("page_boxes")
        if not boxes:
            boxes = [{"bbox": b["bbox"], "class": "text"}
                     for b in page.get_text("dict")["blocks"] if b.get("type") == 0]

        texts_by_bbox = {}
        for b in boxes:
            try:
                texts_by_bbox[tuple(round(v, 2) for v in b["bbox"])] = \
                    _clean(page.get_textbox(pymupdf.Rect(b["bbox"])))
            except Exception:
                texts_by_bbox[tuple(round(v, 2) for v in b["bbox"])] = ""

        work = []
        kept = []
        for b in boxes:
            cls = (b.get("class") or "text").lower()
            rect = pymupdf.Rect(b["bbox"])
            raw = texts_by_bbox.get(tuple(round(v, 2) for v in b["bbox"]), "")
            if cls == "section-header" and _is_refs_heading(raw):
                refs_mode = True
            if refs_mode or cls in SKIP_CLASSES:
                continue
            if rect.is_empty or rect.width < 4 or rect.height < 3:
                continue
            if any(_iou(rect, kr) > 0.6 for kr in kept):
                continue
            if cls == "table":
                cells = _table_cells(page, rect)
                if cells:
                    for cr, txt in cells:
                        work.append({"rect": tuple(cr), "text": txt, "cls": "table-cell",
                                     "size": min(_box_fontsize(page, cr), 8.5), "color": 0})
                    kept.append(rect)
                    continue
            kept.append(rect)
            text = " ".join(_norm_math(_merge_inline(page, b["bbox"], boxes, texts_by_bbox)).split())
            if len(text) < 2:
                continue
            work.append({"rect": tuple(rect), "text": text, "cls": cls,
                         "size": _box_fontsize(page, rect), "color": _box_color(page, rect)})

        if work:
            boxes_total += len(work)
            texts = [w["text"] for w in work]
            tr, ok = translate_texts(texts, src, tgt)
            keep_idx = [i for i in range(len(work)) if ok[i]]
            failed_total += len(work) - len(keep_idx)
            keep_work = [work[i] for i in keep_idx]
            keep_tr = [_fix_numbering(tr[i]) for i in keep_idx]
            _apply(page, keep_work, keep_tr)

        done_pages += 1
        _flush_partial(doc, out_path)      # 每页落盘：暂停时即可查看
        if progress:
            progress(done_pages, total_pages)
        log("  page %d/%d boxes=%d" % (done_pages, total_pages, len(work)))

    try:
        doc.subset_fonts()
    except Exception:
        pass
    try:
        tmp_out = out_path + ".final"
        doc.save(tmp_out, garbage=4, deflate=True, clean=True)
        import os as _os2
        _os2.replace(tmp_out, out_path)
    except Exception as e:
        log("  !! 保存失败：%r" % (e,))

    # 双语对照（左右并排）
    dual = None
    try:
        dual = out_path[:-4] + "_左右对照.pdf"
        make_spread(src_path, out_path, dual)
    except Exception as e:
        log("  !! dual output failed: %r" % (e,))

    doc.close()
    return {"pages": done_pages, "boxes": boxes_total, "failed": failed_total,
            "out": out_path, "dual": dual}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: pdf_translate.py <src.pdf> <out.pdf> [src] [tgt]")
        sys.exit(1)
    st = translate_pdf(sys.argv[1], sys.argv[2],
                       sys.argv[3] if len(sys.argv) > 3 else "en",
                       sys.argv[4] if len(sys.argv) > 4 else "zh")
    print(st)
