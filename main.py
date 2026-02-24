import webview
import threading
from haofan_helper import app  # 导入你的 Flask app

def start_server():
    app.run(host='127.0.0.1', port=8080, debug=False, use_reloader=False)

if __name__ == '__main__':
    # 后台启动 Flask 服务
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()

    # 用 PyWebView 打开本地页面
    webview.create_window('好返助手', 'http://127.0.0.1:8080', width=375, height=667)
    webview.start()