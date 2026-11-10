# -*- coding: utf-8 -*-
"""后处理：修复 BabelDOC 因排版空间不足导致的段落截断。

BabelDOC 的 Rust 排版在译文塞不进原段落框时直接截断（中文比英文宽约 1.5 倍），
表现为段落结尾断在半句、下方留出空白。

流程：找出截断段 → 重新整段翻译 → 用「可用高度 + 字号测量」精确重排 → 只有确定能放下才改。
"""
import os

CTRL_RE = None


def _clean(s):
    """去掉控制字符（源 PDF 抽取常带 NUL，渲染会变成方块）。"""
    import re as _re
    if not s:
        return ""
    s = s.replace("\x00", "")
    return _re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)


TERM_EN = (".", "!", "?", ";", ":", "”", '"', ")", "'")
TERM_ZH = ("。", "！", "？", "；", "：", "…", "”", "」", "）")


def _truncated(src_text, out_text, gap_ratio):
    if len(src_text.strip()) < 60 or gap_ratio <= 0.15:
        return False
    return (src_text.strip().endswith(TERM_EN)
            and not (out_text.rstrip().endswith(TERM_ZH) or out_text.rstrip().endswith(TERM_EN)))


def _pairs(src_page, out_page):
    sb = [b for b in src_page.get_text("blocks") if b[4].strip()]
    ob = [b for b in out_page.get_text("blocks") if b[4].strip()]
    out = []
    for s in sb:
        sx0, sy0, sx1, sy1, st = s[:5]
        best = None
        for o in ob:
            ox0, oy0, ox1, oy1, ot = o[:5]
            inter = max(0.0, min(sx1, ox1) - max(sx0, ox0))
            if inter > (sx1 - sx0) * 0.4 and oy0 >= sy0 - 6 and oy1 <= sy1 + 12:
                best = o
        if not best:
            continue
        gap = (sy1 - best[3]) / max(1.0, sy1 - sy0)
        if _truncated(st, best[4], gap):
            out.append(((sx0, sy0, sx1, sy1), st, tuple(best[:4]), best[4], gap))
    return out


_SCRATCH = {"doc": None, "page": None, "w": 0, "h": 0}


def _fit_on_scratch(text, width, height, font, start=10.0, floor=6.0):
    """在复用的临时页上真实试排，返回能放下的字号；放不下返回 None。"""
    import pymupdf
    if _SCRATCH["doc"] is None:
        _SCRATCH["doc"] = pymupdf.open()
        _SCRATCH["page"] = _SCRATCH["doc"].new_page(width=600, height=800)
    page = _SCRATCH["page"]
    w, h = max(40, width), max(40, height)
    if abs(_SCRATCH["w"] - w) > 1 or abs(_SCRATCH["h"] - h) > 1:
        _SCRATCH["doc"].delete_page(0)
        page = _SCRATCH["doc"].new_page(width=w, height=h)
        _SCRATCH["page"] = page
        _SCRATCH["w"], _SCRATCH["h"] = w, h
    fs = start
    result = None
    while fs >= floor:
        page.clean_contents()
        try:
            rc = page.insert_textbox(pymupdf.Rect(0, 0, w, h), text,
                                     fontsize=fs, fontname="cjk" if font else "china-s",
                                     fontfile=font, color=(0, 0, 0), align=3, lineheight=1.15)
        except Exception:
            rc = -1
        if rc >= 0:
            result = fs
            break
        fs -= 0.4
    return result


def _fit_size(text, rect, font, start=10.0, floor=4.6):
    """用字体度量反推：能放进 rect 的最大字号；放不下返回 None。"""
    import pymupdf
    try:
        f = pymupdf.Font(fontfile=font) if font else pymupdf.Font("china-s")
    except Exception:
        return None
    fs = start
    while fs >= floor:
        per_line = max(1.0, rect.width)
        # 逐字累加估算断行
        lines, cur = 1, 0.0
        for ch in text:
            try:
                w = f.text_length(ch, fontsize=fs)
            except Exception:
                w = fs * 0.55
            if cur + w > per_line:
                lines += 1
                cur = w
            else:
                cur += w
            if (lines - 1) * fs * 1.2 > rect.height:
                break
        if (lines - 1) * fs * 1.2 <= rect.height:
            return fs
        fs -= 0.3
    return None


def repair(src_path, out_path, translate_batch, log=print, max_per_page=3,
           progress=None, budget=150, max_total=12):
    import pymupdf
    src_doc = pymupdf.open(src_path)
    out_doc = pymupdf.open(out_path)
    font = None
    for f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(f):
            font = f
            break

    import time as _t
    t0 = _t.time()
    total = 0
    skipped = 0
    n_pages = min(len(src_doc), len(out_doc))
    if max_total and total >= max_total:
        log("  截断修复达到上限")
        n_pages = 0
    for pno in range(n_pages):
        if progress:
            progress("修复截断段落", pno + 1, n_pages)
        if _t.time() - t0 > budget or (max_total and total >= max_total):
            log("  截断修复到限（时间/数量），剩余页面跳过")
            break
        sp, op = src_doc[pno], out_doc[pno]
        pairs = _pairs(sp, op)[:max_per_page]
        if not pairs:
            continue
        try:
            trs = translate_batch([p[1] for p in pairs], "en", "zh")
        except Exception as e:
            log("  回填翻译失败 p%d: %r" % (pno + 1, e))
            continue

        ob = [b for b in op.get_text("blocks") if b[4].strip()]
        jobs = []
        for (srect, stext, orect, otext, gap), tr in zip(pairs, trs):
            tr = _clean(tr or "").strip()
            if not tr:
                continue
            rect = pymupdf.Rect(orect)
            # 下方可用空白：同栏下一个块的上边界
            limit = op.rect.y1 - 6
            for b in ob:
                bx0, by0, bx1, by1, bt = b[:5]
                if by0 > rect.y1 - 2 and max(0.0, min(rect.x1, bx1) - max(rect.x0, bx0)) > 20:
                    limit = min(limit, by0 - 3)
            probe = pymupdf.Rect(rect)
            probe.y1 = min(limit, rect.y1 + max(0.0, limit - rect.y1))
            fs = _fit_on_scratch(tr, probe.width, probe.height, font, start=10.5, floor=6.0)
            if fs is None:
                log("  p%d 跳过（版面空间不足，保留原样）" % (pno + 1))
                skipped += 1
                continue
            # 需要抹掉的区域：译文块 ∪ 原文块（BabelDOC 截断时会把英文留在页面上）
            r_out = pymupdf.Rect(orect)
            r_src = pymupdf.Rect(srect)
            erase = r_out | r_src
            jobs.append((erase, tr, probe, fs))

        if not jobs:
            continue
        for erase, tr, probe, fs in jobs:
            try:
                op.add_redact_annot(erase + (-1.5, -1.5, 1.5, 1.5))
            except Exception:
                pass
        try:
            op.apply_redactions(images=0)
        except Exception:
            op.apply_redactions()

        fixed = 0
        for erase, tr, probe, fs in jobs:
            try:
                rc = op.insert_textbox(probe, tr, fontsize=fs,
                                       fontname="cjk" if font else "china-s",
                                       fontfile=font, color=(0, 0, 0),
                                       align=3, lineheight=1.15)
            except Exception:
                rc = -1
            if rc < 0:
                for f2 in (fs - 0.4, fs - 0.8, fs - 1.2):
                    if f2 < 4.2:
                        break
                    try:
                        if op.insert_textbox(probe, tr, fontsize=f2,
                                             fontname="cjk" if font else "china-s",
                                             fontfile=font, color=(0, 0, 0),
                                             align=0, lineheight=1.12) >= 0:
                            rc = 0
                            break
                    except Exception:
                        pass
            if rc >= 0:
                fixed += 1
        if fixed:
            log("  第 %d 页修复 %d 段截断" % (pno + 1, fixed))
            total += fixed

    if total:
        try:
            out_doc.subset_fonts()
        except Exception:
            pass
        out_doc.saveIncr()
    src_doc.close()
    out_doc.close()
    log("  截断修复完成：修复 %d 段，空间不足 %d 段" % (total, skipped))
    return total, skipped



def _coverage(page, rect, words, nx=8, ny=8):
    x0, y0, x1, y1 = rect
    hit = total = 0
    for i in range(nx):
        for j in range(ny):
            cx0 = x0 + (x1 - x0) * i / nx
            cx1 = x0 + (x1 - x0) * (i + 1) / nx
            cy0 = y0 + (y1 - y0) * j / ny
            cy1 = y0 + (y1 - y0) * (j + 1) / ny
            total += 1
            for bx0, by0, bx1, by1 in words:
                if bx0 < cx1 and bx1 > cx0 and by0 < cy1 and by1 > cy0:
                    hit += 1
                    break
    return hit / max(1, total)


def _region_state(page, rect, words):
    """返回 (覆盖率, 汉字比例)。用于区分：空白 / 仍是英文 / 已翻译。"""
    cov = _coverage(page, rect, [w[:4] for w in words])
    x0, y0, x1, y1 = rect
    texts = [w[4] for w in words if w[0] < x1 and w[2] > x0 and w[1] < y1 and w[3] > y0]
    s = "".join(texts)
    if not s.strip():
        return cov, 0.0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cov, cjk / max(1, len(s))


def _refs_page(page):
    for b in page.get_text("blocks")[:6]:
        t = b[4].strip().lower()
        if t in ("references", "bibliography") or t.startswith("references"):
            return True
    return False


def _is_citation_line(txt):
    import re as _re
    return bool(_re.search(r"\b(19|20)\d{2}\b", txt)) and ("doi" in txt.lower() or txt.count("[") > 1)


def fill_missing(src_path, out_path, translate_batch, log=print, min_chars=80, min_fs=6.5,
                 progress=None, budget=150, max_total=20):
    """把 BabelDOC 整段丢弃、留下空白的地方补回来（在空白处绘制译文）。"""
    import pymupdf
    src_doc = pymupdf.open(src_path)
    out_doc = pymupdf.open(out_path)
    font = None
    for f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(f):
            font = f
            break

    import time as _t
    t0 = _t.time()
    filled = 0
    bad_removed = 0
    n_pages2 = min(len(src_doc), len(out_doc))
    for pno in range(n_pages2):
        if progress:
            progress("补齐缺失段落", pno + 1, n_pages2)
        if _t.time() - t0 > budget or (max_total and filled >= max_total):
            log("  补缺到限（时间/数量），剩余页面跳过")
            break
        sp, op = src_doc[pno], out_doc[pno]
        # 先处理「输出页里是控制字符方块」的块：抹掉 + 用原文重译
        bad = []
        for ob in op.get_text("blocks"):
            t = ob[4]
            if not t.strip():
                continue
            nulls = t.count("\x00") + sum(1 for ch in t if ord(ch) < 32 and ch not in "\n\t")
            if nulls > max(8, len(t) * 0.3):
                bad.append(ob)
        if bad:
            log("  第 %d 页发现 %d 处方块乱码，覆盖并重译" % (pno + 1, len(bad)))
            for ob in bad:
                r0 = pymupdf.Rect(ob[:4]) + (-1.5, -1.5, 1.5, 1.5)
                try:
                    op.draw_rect(r0, color=(1, 1, 1), fill=(1, 1, 1), width=0.5)
                except Exception:
                    pass
            bad_removed += len(bad)
        if _refs_page(sp):
            log("  第 %d 页是参考文献，跳过补缺" % (pno + 1))
            continue
        words = [(w[0], w[1], w[2], w[3]) for w in op.get_text("words")]
        cands = []
        for b in bad:
            for sb in sp.get_text("blocks"):
                inter = max(0.0, min(b[2], sb[2]) - max(b[0], sb[0]))
                vinter = max(0.0, min(b[3], sb[3]) - max(b[1], sb[1]))
                if inter > (b[2] - b[0]) * 0.5 and vinter > 0:
                    clean = _clean(sb[4]).strip()
                    if len(clean) >= min_chars:
                        cands.append((b, clean))
                    break
        all_words = [(w[0], w[1], w[2], w[3], w[4]) for w in op.get_text("words")]
        for b in sp.get_text("blocks"):
            txt = b[4].strip()
            if len(txt) < min_chars:
                continue
            if _is_citation_line(txt):
                continue
            cov, cjk = _region_state(op, b[:4], all_words)
            if cov < 0.30:
                clean = _clean(txt).strip()
                if len(clean) >= min_chars:
                    cands.append((b, clean, "empty"))
            elif cjk < 0.20 and cov > 0.45:
                clean = _clean(txt).strip()
                if len(clean) >= min_chars:
                    cands.append((b, clean, "english"))
        if not cands:
            continue
        trs = translate_batch([c[1] for c in cands], "en", "zh")
        page_fill = 0
        for (b, txt, mode), tr in zip(cands, trs):
            tr = _clean(tr or "").strip()
            if not tr:
                continue
            rect = pymupdf.Rect(b[:4])
            if mode == "english":
                try:
                    op.add_redact_annot(rect + (-1.5, -1.5, 1.5, 1.5))
                    op.apply_redactions(images=0)
                except Exception:
                    pass
            fs = _fit_on_scratch(tr, rect.width, rect.height, font, start=10.0, floor=min_fs)
            if fs is None:
                log("  第 %d 页有 1 段空间不足，未补" % (pno + 1))
                continue
            try:
                rc = op.insert_textbox(rect, tr, fontsize=fs,
                                       fontname="cjk" if font else "china-s",
                                       fontfile=font, color=(0, 0, 0),
                                       align=3, lineheight=1.15)
            except Exception:
                rc = -1
            if rc >= 0:
                page_fill += 1
        if page_fill:
            log("  第 %d 页补回 %d 段" % (pno + 1, page_fill))
            filled += page_fill

    if filled:
        try:
            out_doc.subset_fonts()
        except Exception:
            pass
        out_doc.saveIncr()
    src_doc.close()
    out_doc.close()
    log("  补缺完成：共 %d 段" % filled)
    return filled


def clean_artifacts(path, log=print):
    """最后一道：把 BabelDOC 画出来的控制字符方块白覆盖掉（全量保存，确保生效）。"""
    import pymupdf
    doc = pymupdf.open(path)
    hits = 0
    for page in doc:
        bad = [b for b in page.get_text("blocks")
               if b[4].count(chr(0)) > max(8, len(b[4]) * 0.3)]
        for b in bad:
            try:
                page.draw_rect(pymupdf.Rect(b[:4]) + (-1.5, -1.5, 1.5, 1.5),
                               color=(1, 1, 1), fill=(1, 1, 1), width=0.5)
                hits += 1
            except Exception:
                pass
    if hits:
        try:
            doc.subset_fonts()
        except Exception:
            pass
        tmp = path + ".tmp"
        doc.save(tmp, garbage=4, deflate=True, clean=True)
        doc.close()
        import os as _os
        _os.replace(tmp, path)
        log("  清理方块乱码：%d 处" % hits)
    else:
        doc.close()
    return hits


def _find_refs_page(doc):
    import re as _re
    for i in range(len(doc)):
        text = doc[i].get_text()
        low = text.lower()
        if _re.search(r"^\s*(references|bibliography)\s*$", low, _re.M):
            return i
        cites = len(_re.findall(r"\[\d{1,3}\]", text))
        dois = low.count("doi.org")
        if cites > 15 and dois >= 2:
            return i
        # 标题和正文在同一块里的情况
        for b in doc[i].get_text("blocks"):
            t = b[4].strip().lower()
            if t.startswith("references") or t.startswith("bibliography"):
                return i
    return None


def restore_fixed_zones(src_path, out_path, log=print, band_ratio=0.055):
    """把不该翻的区域恢复成原文：
       1) 参考文献起始页之后的页面 —— 整页用原文（期刊名/作者/DOI 不该翻译）
       2) 每页页眉、页脚 —— 用原文覆盖（页眉有作者名与期刊名）
    """
    import pymupdf
    src = pymupdf.open(src_path)
    out = pymupdf.open(out_path)
    ref_start = _find_refs_page(src)
    if ref_start is not None:
        log("  参考文献从第 %d 页开始，后续页面保留原文" % (ref_start + 1))

    band = out[0].rect.height * band_ratio
    for i in range(min(len(src), len(out))):
        if ref_start is not None and i >= ref_start:
            continue
        page, spage = out[i], src[i]
        for rect in (pymupdf.Rect(0, 0, page.rect.width, band),
                     pymupdf.Rect(0, page.rect.height - band, page.rect.width, page.rect.height)):
            try:
                page.show_pdf_page(rect, src, i, clip=rect)
            except Exception:
                pass

    tmp = out_path + ".tmp"
    if ref_start is not None and ref_start < len(out):
        merged = pymupdf.open()
        for i in range(len(out)):
            if i >= ref_start:
                merged.insert_pdf(src, from_page=i, to_page=i)
            else:
                merged.insert_pdf(out, from_page=i, to_page=i)
        merged.save(tmp, garbage=4, deflate=True)
        merged.close()
    else:
        try:
            out.subset_fonts()
        except Exception:
            pass
        out.save(tmp, garbage=4, deflate=True, clean=True)
    src.close()
    out.close()
    import os as _os
    _os.replace(tmp, out_path)
    return ref_start
