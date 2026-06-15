# TelegramForwarder WebUI 版

基于 [Heavrnl/TelegramForwarder](https://github.com/Heavrnl/TelegramForwarder) 优化，新增 Web 管理后台。

## ✨ 新增功能

- **🔑 Web 界面登录 TG** — 不再需要进终端交互输验证码，全程在浏览器完成
- **🌐 Web 界面代理设置** — 支持 SOCKS5 / HTTP 代理，可视化配置 + 连接测试
- **🔐 管理密码保护** — Web 后台有独立密码，首次启动自动生成
- **📊 状态仪表盘** — 实时查看 TG 连接状态、代理状态

## 🚀 快速开始

### 方式一：全新部署

```bash
# 1. 克隆本项目
git clone <your-repo-url> telegram-forwarder
cd telegram-forwarder

# 2. 拉取原项目文件
git clone --depth 1 https://github.com/Heavrnl/TelegramForwarder.git tmp_original
cp -r tmp_original/* ./
cp -r tmp_original/.[!.]* ./ 2>/dev/null || true
rm -rf tmp_original

# 3. 应用 Web UI 覆盖
cp -f webui_overlay/main.py ./
cp -rf webui_overlay/web_ui ./
cp -f webui_overlay/.env.example .env
cp -f webui_overlay/requirements.txt ./
cp -f webui_overlay/Dockerfile ./
cp -f webui_overlay/docker-compose.yml ./

# 4. 编辑 .env，至少填入 BOT_TOKEN 和 USER_ID
vim .env

# 5. 构建并启动
docker-compose build
docker-compose up -d

# 6. 查看 Web 管理密码
docker-compose logs | grep "管理密码"
```

### 方式二：已有原项目升级

```bash
# 1. 复制 web_ui 模块和修改后的 main.py 到你的项目目录
# 2. 更新 Dockerfile、docker-compose.yml、requirements.txt
# 3. 重建容器
docker-compose down
docker-compose build
docker-compose up -d
```

## 🔧 使用说明

### Web 管理后台

访问 `http://localhost:9805/admin`

1. **登录** — 使用自动生成的管理密码（查看容器日志）
2. **TG 登录** — 填写 API ID / API Hash / 手机号 → 输入验证码 → 完成
3. **代理设置** — 开启代理 → 填写 SOCKS5/HTTP 地址 → 保存 → 测试

### TG 登录流程

```
① 填写凭据 → ② 输入验证码 → ③ (可选)两步验证密码 → ✅ 完成
```

所有步骤都在 Web 界面完成，不需要进终端。

### 端口说明

| 端口 | 用途 |
|------|------|
| 9804 | RSS 服务 |
| 9805 | Web 管理后台 |

## 📁 文件结构

```
web_ui/
├── __init__.py          # FastAPI 应用 + API 路由
├── templates/
│   ├── login.html       # 登录页
│   ├── dashboard.html   # 控制台
│   ├── setup.html       # TG 登录设置
│   └── proxy.html       # 代理设置
└── static/              # 静态资源
```

## ⚠️ 注意事项

- Web 管理密码首次启动自动生成，请妥善保管
- 代理设置保存后需要重启容器才能对 TG 客户端生效
- 原项目的所有 Telegram Bot 命令功能完全保留
- API_ID / API_HASH / PHONE_NUMBER 可通过 Web 界面配置，无需手动编辑 .env
