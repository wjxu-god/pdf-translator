# 腾讯免费翻译插件（pdf2zh_next）

## 它解决什么

pdf2zh_next 内置的腾讯引擎是腾讯云 TMT（需要 SecretId/SecretKey，付费）。
本插件接入腾讯交互翻译的**匿名免费接口**，无需任何账号，适合国内网络。

## 安装

```bat
%PDF2ZH_HOME%\env\python.exe install_tencent_free.py
```

补丁做两件事（幂等，改动前自动备份 `.bak-时间戳`）：

1. 写入 `pdf2zh_next/translator/translator_impl/tencentfree.py`
2. 在 `pdf2zh_next/config/translate_engine_model.py` 注册 `TencentFreeSettings`

## 使用

```bat
pdf2zh_next input.pdf --tencentfree --lang-in en --lang-out zh
```

在本工具的界面里选「BabelDOC + 腾讯」即可。

## 注意

- 升级 pdf2zh_next 会覆盖上述两个文件，重跑一次补丁即可
- 免费接口有速率限制，长文档偶发段落回落原文（可重试）
