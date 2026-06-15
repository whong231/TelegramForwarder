"""
Web 管理后台 - Telegram 登录 & 代理设置
"""
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os
import asyncio
import logging
import json
from pathlib import Path
from dotenv import load_dotenv, set_key
import secrets
import hashlib
import time

logger = logging.getLogger(__name__)

# ---- 全局状态 ----
STATE_FILE = "./sessions/.webui_state.json"

tg_login_state = {
    "step": "idle",          # idle | wait_code | wait_password | done | error
    "api_id": None,
    "api_hash": None,
    "phone": None,
    "phone_code_hash": None,
    "error": None,
    "client": None,          # Telethon client 引用
    "proxy_enabled": False,
    "proxy_type": "socks5",  # socks5 | http
    "proxy_host": "",
    "proxy_port": "",
    "proxy_username": "",
    "proxy_password": "",
}


def _save_state():
    """将状态写入文件，供主进程读取"""
    try:
        state_copy = {
            k: v for k, v in tg_login_state.items()
            if k != "client"
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state_copy, f)
    except Exception:
        pass

# ---- Web 管理密码 ----
WEB_PASSWORD_HASH = None
SESSION_TOKENS = {}  # token -> expiry

def get_web_password():
    """获取或生成 Web 管理密码"""
    global WEB_PASSWORD_HASH
    pw = os.getenv("WEB_ADMIN_PASSWORD", "")
    if not pw:
        pw = secrets.token_hex(8)
        set_key(".env", "WEB_ADMIN_PASSWORD", pw)
        logger.info(f"===========================================")
        logger.info(f"  Web 管理后台密码: {pw}")
        logger.info(f"  请妥善保管！")
        logger.info(f"===========================================")
    WEB_PASSWORD_HASH = hashlib.sha256(pw.encode()).hexdigest()
    return pw


def create_app():
    app = FastAPI(title="TelegramForwarder Web UI")

    # 从 .env 初始化代理状态
    _init_proxy_from_env()

    # 静态文件 & 模板
    base_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    # ---- 认证依赖 ----
    def check_auth(request: Request):
        token = request.cookies.get("tf_token", "")
        if token not in SESSION_TOKENS or SESSION_TOKENS[token] < time.time():
            raise HTTPException(status_code=401, detail="未登录")
        return True

    # ---- 页面路由 ----
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_login_page(request: Request):
        """登录页"""
        return templates.TemplateResponse("login.html", {"request": request})

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, _=Depends(check_auth)):
        """管理面板"""
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "state": tg_login_state,
        })

    @app.get("/admin/setup", response_class=HTMLResponse)
    async def setup_page(request: Request, _=Depends(check_auth)):
        """TG 登录设置页"""
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "state": tg_login_state,
        })

    @app.get("/admin/proxy", response_class=HTMLResponse)
    async def proxy_page(request: Request, _=Depends(check_auth)):
        """代理设置页"""
        return templates.TemplateResponse("proxy.html", {
            "request": request,
            "state": tg_login_state,
        })

    # ---- API 路由 ----
    @app.post("/api/login")
    async def api_login(request: Request):
        data = await request.json()
        pw = data.get("password", "")
        if hashlib.sha256(pw.encode()).hexdigest() == WEB_PASSWORD_HASH:
            token = secrets.token_hex(32)
            SESSION_TOKENS[token] = time.time() + 86400  # 24h
            resp = JSONResponse({"ok": True})
            resp.set_cookie("tf_token", token, max_age=86400, httponly=True)
            return resp
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)

    @app.post("/api/logout")
    async def api_logout(request: Request):
        token = request.cookies.get("tf_token", "")
        SESSION_TOKENS.pop(token, None)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("tf_token")
        return resp

    @app.get("/api/status")
    async def api_status(_=Depends(check_auth)):
        """获取当前 TG 连接状态"""
        return {
            "step": tg_login_state["step"],
            "phone": tg_login_state.get("phone", ""),
            "error": tg_login_state.get("error", ""),
            "proxy_enabled": tg_login_state["proxy_enabled"],
            "proxy_type": tg_login_state["proxy_type"],
            "proxy_host": tg_login_state["proxy_host"],
            "proxy_port": tg_login_state["proxy_port"],
        }

    # ---- TG 登录 API ----
    class CredentialsRequest(BaseModel):
        api_id: str
        api_hash: str
        phone: str

    @app.post("/api/tg/init")
    async def tg_init(data: CredentialsRequest, _=Depends(check_auth)):
        """第一步：初始化登录，发送验证码"""
        global tg_login_state
        try:
            from telethon import TelegramClient
            from telethon.errors import FloodWaitError

            api_id = int(data.api_id)
            api_hash = data.api_hash.strip()
            phone = data.phone.strip()

            # 保存到 .env
            set_key(".env", "API_ID", str(api_id))
            set_key(".env", "API_HASH", api_hash)
            set_key(".env", "PHONE_NUMBER", phone)

            # 构建代理
            proxy = None
            if tg_login_state["proxy_enabled"]:
                proxy = _build_proxy()

            # 创建客户端
            client = TelegramClient(
                f"./sessions/user",
                api_id,
                api_hash,
                proxy=proxy,
            )
            await client.connect()

            if await client.is_user_authorized():
                tg_login_state["step"] = "done"
                tg_login_state["client"] = client
                tg_login_state["api_id"] = api_id
                tg_login_state["api_hash"] = api_hash
                tg_login_state["phone"] = phone
                _save_state()
                await client.disconnect()
                return {"ok": True, "step": "done", "message": "已登录，无需重新验证"}

            # 发送验证码
            sent = await client.send_code_request(phone)
            tg_login_state["step"] = "wait_code"
            tg_login_state["api_id"] = api_id
            tg_login_state["api_hash"] = api_hash
            tg_login_state["phone"] = phone
            tg_login_state["phone_code_hash"] = sent.phone_code_hash
            tg_login_state["client"] = client
            tg_login_state["error"] = None
            _save_state()

            return {"ok": True, "step": "wait_code", "message": "验证码已发送"}

        except FloodWaitError as e:
            tg_login_state["step"] = "error"
            tg_login_state["error"] = f"操作太频繁，请等待 {e.seconds} 秒"
            return {"ok": False, "error": tg_login_state["error"]}
        except Exception as e:
            tg_login_state["step"] = "error"
            tg_login_state["error"] = str(e)
            return {"ok": False, "error": str(e)}

    class CodeRequest(BaseModel):
        code: str

    @app.post("/api/tg/verify-code")
    async def tg_verify_code(data: CodeRequest, _=Depends(check_auth)):
        """第二步：输入验证码"""
        global tg_login_state
        client = tg_login_state.get("client")
        if not client:
            return {"ok": False, "error": "请先初始化登录"}

        try:
            await client.sign_in(
                phone=tg_login_state["phone"],
                code=data.code.strip(),
                phone_code_hash=tg_login_state["phone_code_hash"],
            )

            if await client.is_user_authorized():
                tg_login_state["step"] = "done"
                tg_login_state["error"] = None
                _save_state()
                await client.disconnect()
                return {"ok": True, "step": "done", "message": "登录成功！"}
            else:
                return {"ok": False, "error": "登录未完成"}

        except Exception as e:
            err_str = str(e)
            # 需要两步验证密码
            if "password" in err_str.lower() or "2fa" in err_str.lower():
                tg_login_state["step"] = "wait_password"
                _save_state()
                return {"ok": True, "step": "wait_password", "message": "需要两步验证密码"}
            tg_login_state["step"] = "error"
            tg_login_state["error"] = err_str
            _save_state()
            return {"ok": False, "error": err_str}

    class PasswordRequest(BaseModel):
        password: str

    @app.post("/api/tg/verify-password")
    async def tg_verify_password(data: PasswordRequest, _=Depends(check_auth)):
        """第三步：输入两步验证密码"""
        global tg_login_state
        client = tg_login_state.get("client")
        if not client:
            return {"ok": False, "error": "请先初始化登录"}

        try:
            await client.sign_in(password=data.password.strip())

            if await client.is_user_authorized():
                tg_login_state["step"] = "done"
                tg_login_state["error"] = None
                _save_state()
                await client.disconnect()
                return {"ok": True, "step": "done", "message": "登录成功！"}
            else:
                return {"ok": False, "error": "登录未完成"}

        except Exception as e:
            tg_login_state["step"] = "error"
            tg_login_state["error"] = str(e)
            return {"ok": False, "error": str(e)}

    @app.post("/api/tg/logout")
    async def tg_logout(_=Depends(check_auth)):
        """登出 TG"""
        global tg_login_state
        try:
            client = tg_login_state.get("client")
            if client:
                await client.log_out()
                await client.disconnect()
        except Exception:
            pass

        # 清除 session 文件
        import glob
        for f in glob.glob("./sessions/user*"):
            os.remove(f)

        tg_login_state["step"] = "idle"
        tg_login_state["client"] = None
        tg_login_state["error"] = None
        _save_state()
        return {"ok": True, "message": "已登出"}

    # ---- 代理设置 API ----
    class ProxyRequest(BaseModel):
        enabled: bool
        proxy_type: str = "socks5"
        host: str = ""
        port: str = ""
        username: str = ""
        password: str = ""

    @app.post("/api/proxy")
    async def save_proxy(data: ProxyRequest, _=Depends(check_auth)):
        """保存代理设置"""
        global tg_login_state
        tg_login_state["proxy_enabled"] = data.enabled
        tg_login_state["proxy_type"] = data.proxy_type
        tg_login_state["proxy_host"] = data.host
        tg_login_state["proxy_port"] = data.port
        tg_login_state["proxy_username"] = data.username
        tg_login_state["proxy_password"] = data.password
        _save_state()

        # 保存到 .env
        if data.enabled:
            set_key(".env", "PROXY_ENABLED", "true")
            set_key(".env", "PROXY_TYPE", data.proxy_type)
            set_key(".env", "PROXY_HOST", data.host)
            set_key(".env", "PROXY_PORT", data.port)
            set_key(".env", "PROXY_USERNAME", data.username)
            set_key(".env", "PROXY_PASSWORD", data.password)
        else:
            set_key(".env", "PROXY_ENABLED", "false")

        return {"ok": True}

    @app.get("/api/proxy")
    async def get_proxy(_=Depends(check_auth)):
        """获取代理设置"""
        return {
            "enabled": tg_login_state["proxy_enabled"],
            "proxy_type": tg_login_state["proxy_type"],
            "host": tg_login_state["proxy_host"],
            "port": tg_login_state["proxy_port"],
            "username": tg_login_state["proxy_username"],
            "password": tg_login_state["proxy_password"],
        }

    @app.post("/api/proxy/test")
    async def test_proxy(data: ProxyRequest, _=Depends(check_auth)):
        """测试代理连接"""
        import socket
        try:
            if data.proxy_type == "socks5":
                try:
                    import socks
                    s = socks.socksocket()
                    s.set_proxy(
                        socks.SOCKS5,
                        data.host,
                        int(data.port),
                        username=data.username or None,
                        password=data.password or None,
                    )
                except ImportError:
                    return {"ok": False, "error": "PySocks 未安装，无法测试 SOCKS5 代理"}
            else:
                s = socket.socket()

            s.settimeout(10)
            s.connect(("api.telegram.org", 443))
            s.close()
            return {"ok": True, "message": "代理连接成功"}
        except Exception as e:
            return {"ok": False, "error": f"代理连接失败: {str(e)}"}

    return app


def _build_proxy():
    """根据当前状态构建代理配置 (Telethon 格式)"""
    if not tg_login_state["proxy_enabled"]:
        return None

    proxy_type = tg_login_state["proxy_type"]
    host = tg_login_state["proxy_host"]
    port = int(tg_login_state["proxy_port"]) if tg_login_state["proxy_port"] else 1080
    username = tg_login_state["proxy_username"] or None
    password = tg_login_state["proxy_password"] or None

    # Telethon 代理格式: (type, host, port, rdns, username, password)
    return (proxy_type, host, port, True, username, password)


def _init_proxy_from_env():
    """从 .env 文件初始化代理状态"""
    if os.getenv('PROXY_ENABLED', '').lower() == 'true':
        tg_login_state["proxy_enabled"] = True
        tg_login_state["proxy_type"] = os.getenv('PROXY_TYPE', 'socks5')
        tg_login_state["proxy_host"] = os.getenv('PROXY_HOST', '')
        tg_login_state["proxy_port"] = os.getenv('PROXY_PORT', '1080')
        tg_login_state["proxy_username"] = os.getenv('PROXY_USERNAME', '')
        tg_login_state["proxy_password"] = os.getenv('PROXY_PASSWORD', '')
