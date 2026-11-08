# PDF翻译专家（PDF Layout-Preserving Translator）

当前版本：**v1.0.0**

> 拖入 PDF → 版面分析 → 翻译 → **保留原排版**输出。纯本地运行，可离线（本地引擎），也可接入免费翻译通道。

![screenshot](docs/screenshot.png)

## 特性

- **保排版**：按原文的段落框、字号、颜色回填译文，双栏 / 图表 / 页眉页脚位置不变
- **公式保留**：公式区不参与机翻，数学字形原样保留；行内公式做 Unicode 归一化，避免丢字符和方块字
- **多引擎**
  | 引擎 | 说明 |
  |---|---|
  | BabelDOC + Bing | 快，默认 |
  | BabelDOC + 腾讯（免费） | **无需账号**，国内网络可用（需要打补丁，见下） |
  | BabelDOC + Google | 备用通道 |
  | BabelDOC + 大模型 | 译文更自然，较慢 |
  | 本地引擎 | 零外部依赖，只用 PyMuPDF |
- **双语对照**：输出「纯译文」和「左右对照」两个 PDF
- **批量**：多文件排队，逐个显示进度
- **无控制台窗口**：双击即用，全程网页界面

## 直接用（Windows）

双击 **`PDF翻译专家.exe`** —— 原生窗口，无浏览器、无命令行。

- 拖入 PDF（支持批量）→ 选引擎 → 完成即出现下载按钮
- **输出目录可改**：界面里「输出目录 → 更改」选一个文件夹即可（记住设置，重启不丢），默认是 exe 同目录的 `译文输出\`

## 从源码运行

```bash
pip install -r requirements.txt
python pdf_translator_desktop.py     # 桌面窗口
python pdf_translator_app.py         # 或纯网页模式 http://127.0.0.1:8910
```

## 自己打包 exe

```bat
build_exe.bat
```

产物：`dist\PDF翻译专家.exe`（单文件，约 60MB）。

## 可选：启用 BabelDOC 引擎（排版更好）

1. 安装 [pdf2zh_next](https://github.com/PDFMathTranslate/PDFMathTranslate)（BabelDOC 内核），
   或使用现成整合包，保证目录下有 `env/python.exe` 与 `app/config/config.toml`
2. 设置环境变量指向该目录：

```bat
set PDF2ZH_HOME=D:\PDF_Translator
```

3. （可选）安装**腾讯免费翻译**插件：

```bat
%PDF2ZH_HOME%\env\python.exe install_tencent_free.py
```

   插件是幂等的：写入 `translator_impl/tencentfree.py` 并在
   `config/translate_engine_model.py` 注册引擎；升级 pdf2zh_next 后重跑一次即可。

   之后命令行也能直接用：

```bat
pdf2zh_next input.pdf --tencentfree --lang-in en --lang-out zh
```

没有 BabelDOC 时工具自动只用本地引擎，功能不受影响。

## 工作流程

```
PDF ──► 版面分析(pymupdf_layout / BabelDOC)
     ──► 段落 + 行内公式归组
     ──► 翻译（可插拔服务）
     ──► 回填：先算字号（放不下就缩小/扩框，绝不重叠）
     ──► 纯译文 PDF + 左右对照 PDF
```

## 已知限制

- **扫描件**（纯图片 PDF）不支持，需要先做 OCR
- 表格按单元格翻译，行高不随中文变宽自动拉伸，密集表格可能偏挤
- 参考文献整节默认跳过（机翻条目基本不可读）
- 免费翻译通道有速率限制，长文档偶发个别段落回落原文

## 目录结构

```
pdf_translator_app.py    Web 服务 + 界面（批量、进度、下载）
pdf_translate.py         本地引擎：版面分析 / 翻译 / 回填 / 双语对照
babeldoc_engine.py       BabelDOC 引擎适配器（可插拔服务）
install_tencent_free.py  给 pdf2zh_next 安装腾讯免费引擎（幂等）
```

## 许可证

AGPL-3.0。本项目使用的 PyMuPDF / pymupdf4llm / BabelDOC 均为 AGPL-3.0，
因此本项目同样以 AGPL-3.0 发布。
