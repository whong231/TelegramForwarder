#!/bin/bash
# TelegramForwarder WebUI 版 - 一键部署脚本
set -e

echo "============================================"
echo "  TelegramForwarder WebUI 版 部署脚本"
echo "============================================"
echo ""

# 检查是否已克隆原项目
if [ ! -f "message_listener.py" ]; then
    echo "📥 正在克隆原项目..."
    if [ -d ".git" ]; then
        echo "⚠️  检测到已有 .git 目录，跳过克隆"
    else
        git clone --depth 1 https://github.com/Heavrnl/TelegramForwarder.git tmp_original
        cp -r tmp_original/* ./
        cp -r tmp_original/.[!.]* ./ 2>/dev/null || true
        rm -rf tmp_original
        echo "✅ 原项目文件已就绪"
    fi
else
    echo "✅ 检测到原项目文件已存在"
fi

# 覆盖 Web UI 文件
echo "📝 应用 Web UI 修改..."

# 复制修改后的 main.py
cp -f webui_overlay/main.py ./

# 复制 web_ui 模块
cp -rf webui_overlay/web_ui ./

# 复制更新的配置文件
cp -f webui_overlay/.env.example ./
cp -f webui_overlay/requirements.txt ./
cp -f webui_overlay/Dockerfile ./
cp -f webui_overlay/docker-compose.yml ./

echo "✅ Web UI 文件已应用"
echo ""

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "📋 创建 .env 文件..."
    cp .env.example .env
    echo "⚠️  请编辑 .env 文件，至少填入 BOT_TOKEN 和 USER_ID"
    echo "   API_ID / API_HASH / PHONE_NUMBER 可通过 Web 管理后台配置"
else
    echo "✅ .env 文件已存在"
fi

echo ""
echo "============================================"
echo "  部署准备完成！"
echo ""
echo "  启动命令："
echo "    docker-compose up -d"
echo ""
echo "  首次运行需要交互登录，使用："
echo "    docker-compose run --rm telegram-forwarder"
echo ""
echo "  Web 管理后台："
echo "    http://localhost:9805/admin"
echo "    (首次启动会自动生成密码，查看日志获取)"
echo "============================================"
