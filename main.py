"""
TelegramForwarder - 带 Web 管理后台版本
基于 Heavrnl/TelegramForwarder 优化
"""
from telethon import TelegramClient, types
from telethon.tl.types import BotCommand
from telethon.tl.functions.bots import SetBotCommandsRequest
from models.models import init_db
from dotenv import load_dotenv
from message_listener import setup_listeners
import os
import asyncio
import logging
import uvicorn
import multiprocessing
import json
import time
from models.db_operations import DBOperations
from scheduler.summary_scheduler import SummaryScheduler
from scheduler.chat_updater import ChatUpdater
from handlers.bot_handler import send_welcome_message
from rss.main import app as rss_app
from utils.log_config import setup_logging
from web_ui import create_app, get_web_password

os.environ.setdefault('DOCKER_LOG_MAX_SIZE', '10m')
os.environ.setdefault('DOCKER_LOG_MAX_FILE', '3')

setup_logging()
logger = logging.getLogger(__name__)
load_dotenv()

db_ops = None
scheduler = None
chat_updater = None

STATE_FILE = "./sessions/.webui_state.json"


async def init_db_ops():
    global db_ops
    if db_ops is None:
        db_ops = await DBOperations.create()
    return db_ops


os.makedirs('./sessions', exist_ok=True)
os.makedirs('./temp', exist_ok=True)


def clear_temp_dir():
    for file in os.listdir('./temp'):
        os.remove(os.path.join('./temp', file))


def read_webui_state():
    """读取 Web UI 写入的状态文件"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def build_proxy():
    """从环境变量构建代理配置 (Telethon 格式)"""
    if os.getenv('PROXY_ENABLED', '').lower() != 'true':
        return None
    proxy_type = os.getenv('PROXY_TYPE', 'socks5')
    host = os.getenv('PROXY_HOST', '')
    port = int(os.getenv('PROXY_PORT', '1080'))
    username = os.getenv('PROXY_USERNAME', '') or None
    password = os.getenv('PROXY_PASSWORD', '') or None
    if not host:
        return None
    return (proxy_type, host, port, True, username, password)


def _get_api_creds():
    """获取 API 凭据：优先环境变量，其次 .env 文件，最后 Web UI 状态"""
    load_dotenv(override=True)
    api_id = os.getenv('API_ID', '').strip()
    api_hash = os.getenv('API_HASH', '').strip()
    phone = os.getenv('PHONE_NUMBER', '').strip()
    return api_id, api_hash, phone


def create_user_client():
    api_id, api_hash, _ = _get_api_creds()
    proxy = build_proxy()
    return TelegramClient('./sessions/user', api_id, api_hash, proxy=proxy)


def create_bot_client():
    api_id, api_hash, _ = _get_api_creds()
    bot_token = os.getenv('BOT_TOKEN', '').strip()
    proxy = build_proxy()
    return TelegramClient('./sessions/bot', api_id, api_hash, proxy=proxy)


engine = init_db()


def run_rss_server(host: str, port: int):
    uvicorn.run(rss_app, host=host, port=port)


def run_web_ui(host: str, port: int):
    web_app = create_app()
    uvicorn.run(web_app, host=host, port=port)


async def wait_for_webui_login():
    """
    等待 Web UI 完成 TG 登录。
    检测条件：session 文件存在 且 .env 中有 API_ID
    """
    logger.warning("=" * 55)
    logger.warning("  ⏳ 等待 Web UI 完成 TG 登录...")
    logger.warning("  请打开浏览器访问: http://localhost:9805/admin/setup")
    logger.warning("=" * 55)

    session_file = './sessions/user.session'
    waited = 0
    while True:
        load_dotenv(override=True)
        api_id = os.getenv('API_ID', '').strip()
        has_session = os.path.exists(session_file)

        if api_id and has_session:
            logger.info("✅ 检测到 Web UI 已完成 TG 登录！")
            return True

        # 检查 Web UI 状态文件
        state = read_webui_state()
        if state.get("step") == "done":
            logger.info("✅ Web UI 报告登录完成！")
            # 等待 session 文件写入
            await asyncio.sleep(1)
            return True

        await asyncio.sleep(3)
        waited += 3
        if waited % 30 == 0:
            logger.info(f"  仍在等待... (已等待 {waited}s)")


async def start_clients():
    global db_ops, scheduler, chat_updater
    db_ops = await DBOperations.create()

    try:
        api_id, api_hash, phone = _get_api_creds()
        bot_token = os.getenv('BOT_TOKEN', '').strip()
        session_file = './sessions/user.session'
        has_session = os.path.exists(session_file)

        # 没有凭据也没有 session → 等待 Web UI 登录
        if (not api_id or not api_hash) and not has_session:
            await wait_for_webui_login()
            api_id, api_hash, phone = _get_api_creds()

        # 启动用户客户端
        user_client = create_user_client()
        try:
            await user_client.start(phone=phone)
            me_user = await user_client.get_me()
            print(f'✅ 用户客户端已启动: {me_user.first_name} (@{me_user.username})')
        except Exception as e:
            logger.error(f"用户客户端启动失败: {e}")
            logger.info("等待 Web UI 重新登录...")
            await wait_for_webui_login()
            user_client = create_user_client()
            await user_client.start(phone=phone)
            me_user = await user_client.get_me()
            print(f'✅ 用户客户端已启动: {me_user.first_name} (@{me_user.username})')

        # 启动机器人客户端
        bot_client = create_bot_client()
        if bot_token:
            await bot_client.start(bot_token=bot_token)
            me_bot = await bot_client.get_me()
            print(f'✅ 机器人客户端已启动: {me_bot.first_name} (@{me_bot.username})')
        else:
            logger.warning("⚠️  BOT_TOKEN 未配置，机器人功能不可用")

        await setup_listeners(user_client, bot_client)
        await register_bot_commands(bot_client)

        scheduler = SummaryScheduler(user_client, bot_client)
        await scheduler.start()

        chat_updater = ChatUpdater(user_client)
        await chat_updater.start()

        rss_process = None
        if os.getenv('RSS_ENABLED', '').lower() == 'true':
            try:
                rss_host = os.getenv('RSS_HOST', '0.0.0.0')
                rss_port = int(os.getenv('RSS_PORT', '8000'))
                logger.info(f"📰 启动 RSS 服务 (host={rss_host}, port={rss_port})")
                rss_process = multiprocessing.Process(
                    target=run_rss_server, args=(rss_host, rss_port)
                )
                rss_process.start()
            except Exception as e:
                logger.error(f"RSS 服务启动失败: {e}")

        await send_welcome_message(bot_client)

        await asyncio.gather(
            user_client.run_until_disconnected(),
            bot_client.run_until_disconnected()
        )
    finally:
        if db_ops and hasattr(db_ops, 'close'):
            await db_ops.close()
        if scheduler:
            scheduler.stop()
        if chat_updater:
            chat_updater.stop()
        if 'rss_process' in locals() and rss_process and rss_process.is_alive():
            rss_process.terminate()
            rss_process.join()


async def register_bot_commands(bot):
    commands = [
        BotCommand("bind", "绑定源聊天"),
        BotCommand("unbind", "解绑源聊天"),
        BotCommand("settings", "打开设置菜单"),
        BotCommand("add", "添加关键词"),
        BotCommand("remove_keyword", "删除关键词"),
        BotCommand("list_keywords", "列出关键词"),
        BotCommand("replace", "添加替换规则"),
        BotCommand("list_replace", "列出替换规则"),
        BotCommand("remove_replace", "删除替换规则"),
        BotCommand("help", "帮助"),
    ]
    try:
        await bot(SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code='',
            commands=commands
        ))
    except Exception as e:
        logger.error(f"注册命令失败: {e}")


def main():
    pw = get_web_password()
    print(f"\n{'='*55}")
    print(f"  📨 TelegramForwarder WebUI 版")
    print(f"  Web 管理后台: http://localhost:9805/admin")
    print(f"  管理密码:     {pw}")
    print(f"{'='*55}\n")

    web_process = multiprocessing.Process(
        target=run_web_ui, args=('0.0.0.0', 9805)
    )
    web_process.start()

    asyncio.run(start_clients())

    web_process.terminate()
    web_process.join()


if __name__ == '__main__':
    main()
