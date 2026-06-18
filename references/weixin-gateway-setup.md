# 微信 (Weixin/WeChat) Hermes Gateway 连接设置

## 概述

Hermes Gateway 通过 Tencent iLink Bot API 连接个人微信账号。这不是 WeCom（企业微信）——iLink Bot 是一个独立的机器人身份，与扫码的个人微信账号不同。

> ⚠️ iLink bot 身份**通常无法被拉入普通微信群**。大多数部署只能可靠收到发给 bot 的私聊消息。

## 依赖安装

```bash
~/.hermes/hermes-agent/.venv/bin/pip install qrcode
```

`aiohttp` 和 `cryptography` 通常已随 Hermes 安装。`qrcode` 用于终端内显示二维码。

## 方式一：交互式向导（推荐新手）

```bash
hermes gateway setup
```

选择 **Weixin**，按照提示操作。

## 方式二：程序化 QR 登录（远程/无人值守）

当 `hermes gateway setup` 不可交互时，直接调用 Python 脚本：

```bash
cd ~/.hermes/hermes-agent
HERMES_HOME=$HOME/.hermes PYTHONPATH=. .venv/bin/python3 -c "
import asyncio, os
from gateway.platforms.weixin import qr_login
result = asyncio.run(qr_login(os.path.expanduser('~/.hermes')))
if result:
    print('OK:', result)
else:
    print('FAILED')
"
```

流程：
1. 脚本向 iLink API 请求二维码
2. 终端显示二维码（ASCII 方块码）+ URL
3. 用户用手机微信扫描
4. 手机确认登录
5. 成功则输出 `微信连接成功，account_id=xxx`

### 二维码过期处理

- iLink 二维码有效期约 **35 秒**
- 过期后自动刷新，**最多 3 次**
- 3 次都过期则退出，需重新执行
- 建议：启动后**立即**扫码，不要等

输出示例：
```
微信连接成功，account_id=xxx@im.bot
OK: {'account_id': 'xxx@im.bot', 'token': '...', 'base_url': 'https://ilinkai.weixin.qq.com', 'user_id': 'xxx@im.wechat'}
```

凭证自动保存到 `~/.hermes/weixin/accounts/<account_id>.json`。

## 配置环境变量

扫码成功后，在 `~/.hermes/.env` 中添加：

```bash
WEIXIN_ACCOUNT_ID=<account_id>    # 从 QR 登录输出获取
WEIXIN_DM_POLICY=open              # 允许所有人私聊
```

可选限制：
```bash
WEIXIN_ALLOWED_USERS=user_id_1,user_id_2   # 白名单模式
WEIXIN_GROUP_POLICY=disabled               # 群消息默认关闭
```

## 重启 Gateway 生效

```bash
systemctl --user restart hermes-gateway
sleep 3
grep -i weixin ~/.hermes/logs/gateway.log | tail -5
```

成功输出应包含：
```
✓ weixin connected
Gateway running with 1 platform(s)
```

## 验证连接

发一条微信消息给 iLink bot，检查日志：

```bash
grep "inbound.*weixin" ~/.hermes/logs/gateway.log | tail -5
```

应显示类似：
```
inbound message: platform=weixin user=o9cq8... chat=o9cq8... msg='...'
```

## 同时运行 WhatsApp + 微信

Sarah 的完整消息栈：

| 平台 | 账号 | 连接方式 | 用途 |
|------|------|----------|------|
| WhatsApp | +86 18530726580 | WaCLI (test) | 客户沟通 |
| WhatsApp | +86 19836278909 | WaCLI (business) | 业务号 |
| 微信 | 个人号 | Hermes Gateway iLink | 与 AI 助手交互 |

两个系统独立运行，互不冲突。
