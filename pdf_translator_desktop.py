# -*- coding: utf-8 -*-
"""PDF 保排版翻译 · 桌面版（原生窗口，无浏览器）
把内置 Web 服务装进 WebView2 窗口，双击即用。
"""
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pdf_translator_app as core  # noqa: E402  内置服务


def free_port(start=8910):
    for p in range(start, start + 60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def serve(port):
    core.PORT = port
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", port), core.H)
    srv.serve_forever()


def main():
    port = free_port()
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    import webview

    class Api:
        def open_folder(self):
            try:
                os.startfile(core.outdir())
            except Exception:
                pass

        def choose_folder(self):
            try:
                import webview as _wv
                w = _wv.windows[0]
                res = w.create_file_dialog(_wv.FOLDER_DIALOG)
                if res:
                    return res[0] if isinstance(res, (list, tuple)) else res
            except Exception:
                pass
            return None

    window = webview.create_window(
        "PDF翻译专家",
        "http://127.0.0.1:%d/" % port,
        width=1020,
        height=780,
        min_size=(880, 620),
        background_color="#0b1020",
        js_api=Api(),
    )
    try:
        webview.start(private_mode=False, debug=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
