# AgentRouter 自动签到（checkin-agentrouter）

AgentRouter 自动签到脚本，支持本地多账号每日签到、GitHub OAuth、签到前后余额查询、实时进度条和飞书通知。项目为每个账号独立保存 GitHub 登录状态，每次执行时重新完成 GitHub OAuth，从而触发 AgentRouter 每日签到。

English summary: Local AgentRouter daily check-in automation with multi-account GitHub OAuth, balance tracking, Rich progress and Feishu notifications.

## 功能

- 多个 AgentRouter 账号并行执行
- 每个账号独立保存 GitHub 登录状态
- 每次重新 GitHub OAuth，可靠触发每日签到
- 签到前、签到后余额分别重试，不因余额查询失败重复 OAuth
- 交互终端使用实时多账号进度条
- 非交互终端使用普通逐行日志，适合 `launchd` 和日志文件
- 飞书通知余额、签到增量和失败原因
- 仅在浏览器明确进入 GitHub 登录页时标记登录过期
- 调试模式保存详细日志和失败截图

## 运行要求

- macOS
- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 可正常访问 GitHub 和 AgentRouter 的网络环境

项目当前要求 `cloakbrowser>=0.4.10`。

## 快速开始

### 1. 安装依赖

```bash
git clone https://github.com/jiang7f/checkin-agentrouter.git
cd checkin-agentrouter
uv sync --dev
uv run python -m cloakbrowser install
```

升级 `cloakbrowser` Python 包后，需要再次执行浏览器安装命令，使 Python 包和浏览器运行时保持一致。

### 2. 创建配置

```bash
cp .env.example .env
```

推荐基础配置如下，可以直接写入 `.env`：

```dotenv
AGENTROUTER_ACCOUNTS=[]
CHECKIN_PROXY_URL=http://127.0.0.1:7890
PROVIDERS={"agentrouter":{"domain":"https://agentrouter.org","use_proxy":true}}

# 可选：配置后发送签到结果到飞书
# FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-your-token

# 可选：每次运行都发送通知。默认 false，只在失败、首次运行或余额变化时通知
# ALWAYS_NOTIFY=false
```

`add` 命令会自动把账号名称写入 `AGENTROUTER_ACCOUNTS`。`PROVIDERS` 必须保持为一行合法 JSON，并使用双引号。

`CHECKIN_PROXY_URL` 填本机代理的 HTTP 或 mixed 端口，不是订阅链接。如果代理端口不是 `7890`，修改配置后再检查连接：

```bash
nc -z 127.0.0.1 7890
curl -I --proxy http://127.0.0.1:7890 https://github.com
curl -I --proxy http://127.0.0.1:7890 https://agentrouter.org
```

`curl` 的 HTTP 状态可能受 WAF 影响，只要没有代理连接失败或超时即可。

### 3. 添加账号

```bash
uv run python checkin.py add main
```

浏览器打开后，在该浏览器中完成 GitHub 登录。需要二次验证时也在同一窗口完成。脚本确认登录成功后会保存该账号，并把 `main` 写入 `.env`。

`main` 只是本地显示名称，不需要与 GitHub 用户名相同。添加第二个账号时，要在新窗口中确认登录的是目标 GitHub 账号。

添加完成后检查状态：

```bash
uv run python checkin.py list
```

正常结果应包含：

```text
✅ main  (configured, saved, valid)
```

每个名称对应一个 GitHub 账号。添加其他账号时使用不同名称：

```bash
uv run python checkin.py add backup
```

### 4. 执行签到

```bash
uv run python checkin.py
```

首次运行没有签到前余额，可能只显示当前余额。从下一次运行开始，签到前后余额都查询成功时才会显示 `本次签到+xx`。

如果首次运行失败，先看进度停在哪个阶段：

- `等待 OAuth 槽位`：正在等待其他账号释放 OAuth 并发槽位，不是故障。
- `GitHub OAuth 登录`：优先检查 GitHub 登录状态和代理连接。
- `查询签到前余额`：首次运行或临时查询失败，不影响后续 OAuth 签到。
- `查询签到后余额`：OAuth 已成功，脚本正在独立重试余额查询。

## 本地短命令

当前机器可以使用以下短命令：

```bash
checkin-agentrouter
checkin-agentrouter add <name>
checkin-agentrouter list
checkin-agentrouter delete <name>
```

它等价于在项目目录运行 `uv run python checkin.py`。新机器可以创建同样的本地包装命令：

```bash
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/checkin-agentrouter" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$PWD"
exec uv run python checkin.py "\$@"
EOF
chmod +x "$HOME/.local/bin/checkin-agentrouter"
```

确认 `~/.local/bin` 已加入 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 签到流程

1. 查询签到前余额，最多 3 次。
2. 重新 GitHub OAuth 登录并触发签到，最多 6 次。
3. 查询签到后余额，最多 3 次。余额查询失败不会重新 OAuth。
4. 签到前后余额都可用时计算 `本次签到+xx`，否则只显示当前余额。

## 并发和优先级

账号任务默认最多同时运行 3 个：

```dotenv
CHECKIN_CONCURRENCY=3
```

GitHub OAuth 默认最多同时运行 2 个：

```dotenv
CHECKIN_OAUTH_CONCURRENCY=2
```

余额查询会串行执行，签到后余额优先。余额查询不会占用 OAuth 并发槽位。

建议先使用默认值。提高并发不一定更快，同一 IP 下反而可能增加 OAuth 或 WAF 失败率。

## 进度显示

多个账号在交互终端运行时，每个账号固定占一行：

```text
main    ━━━━━━━━━╺━━━━━━━━ step 2/4 try 1/6 GitHub OAuth 登录 0:00:12
backup  ━━━━━━━━━━━━━╺━━━━ step 3/4         查询签到后余额 1/3 0:00:17
spare   ━━━━━━━━━━━━━━━━━━ step 0/4         等待
```

- 等待账号并发槽位时不开始计时。
- 账号真正开始执行后才显示耗时。
- 成功后保留最终余额和签到增量。
- 失败后在进度条结束时输出该账号的详细日志。
- `DEBUG_MODE=true` 时，成功账号的缓冲日志也会输出。

以下情况使用普通逐行日志，不显示动态进度条：

- `launchd` 定时运行
- 输出重定向到文件
- 单账号运行
- `CHECKIN_CONCURRENCY=1`

因此，在 `launchd` 日志中看不到进度条是正常行为。

## 账号管理

添加或重新登录：

```bash
checkin-agentrouter add main
```

查看状态：

```bash
checkin-agentrouter list
```

删除账号配置和本地数据：

```bash
checkin-agentrouter delete main
```

`list` 中的状态含义：

| 状态 | 含义 |
| --- | --- |
| `valid` | GitHub 登录有效，可以正常签到 |
| `expired` | GitHub 登录已失效，需要重新执行 `add` |
| `saved` | 本地账号数据存在 |
| `configured` | 账号名称已写入 `.env` |

网络错误、WAF 失败、HTTP 429 或 OAuth 临时失败不会把账号标记为 `expired`。重新登录成功后会自动恢复为 `valid`。

## 环境变量

常用配置：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AGENTROUTER_ACCOUNTS` | 无 | AgentRouter 账号名称数组 |
| `FEISHU_WEBHOOK` | 无 | 飞书机器人 Webhook |
| `CHECKIN_PROXY_URL` | 无 | AgentRouter 和 GitHub OAuth 使用的 HTTP 代理 |
| `PROVIDERS` | 内置配置 | AgentRouter 地址和代理开关 |
| `CHECKIN_CONCURRENCY` | `3` | 同时执行的账号数 |
| `CHECKIN_OAUTH_CONCURRENCY` | `2` | 同时执行的 GitHub OAuth 数 |
| `ALWAYS_NOTIFY` | `false` | 每次运行都发送通知 |
| `DEBUG_MODE` | `false` | 输出详细日志并保存调试截图 |

AgentRouter 推荐明确配置代理地址和 Provider 开关：

```dotenv
CHECKIN_PROXY_URL=http://127.0.0.1:7890
PROVIDERS={"agentrouter":{"domain":"https://agentrouter.org","use_proxy":true}}
```

签到脚本读取的是本地代理地址 `CHECKIN_PROXY_URL`，不读取代理订阅链接。

## 飞书通知

配置 Webhook：

```dotenv
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-your-token
```

通知示例：

```text
每日签到成功
时间: 2026-07-12 10:00:32
结果: 3/3

余额：
✅ main    $56.80  （本次签到+25）
✅ backup  $75.20  （本次签到+0）
✅ spare   余额获取失败
```

默认在以下情况发送通知：

- 有账号失败
- 当前余额相对上次运行记录发生变化
- 首次运行尚无余额记录

设置 `ALWAYS_NOTIFY=true` 后每次运行都会通知。

## macOS 定时任务

仓库提供 `launchd` 模板，默认每天 10:00 执行：

```text
launchd/com.checkin.agentrouter.plist
```

`launchd` 只启动签到脚本，不会自动启动 Clash、Mihomo 等代理程序。定时执行时必须保证 `CHECKIN_PROXY_URL` 对应的本地代理仍在运行，否则 GitHub OAuth 和 AgentRouter 请求会失败。

先确认 `checkin-agentrouter` 短命令可以正常运行，然后安装任务：

```bash
mkdir -p ~/Library/Logs/checkin-agentrouter
sed -e "s#__HOME__#$HOME#g" \
  -e "s#__REPO_DIR__#$(pwd)#g" \
  launchd/com.checkin.agentrouter.plist > ~/Library/LaunchAgents/com.checkin.agentrouter.plist
plutil -lint ~/Library/LaunchAgents/com.checkin.agentrouter.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.checkin.agentrouter.plist
```

查看任务：

```bash
launchctl list | rg com.checkin.agentrouter
```

日志位置：

```text
~/Library/Logs/checkin-agentrouter/stdout.log
~/Library/Logs/checkin-agentrouter/stderr.log
```

完整安装和卸载说明见 [`launchd/README.md`](launchd/README.md)。

## 常见问题

### `list` 显示 `expired`

GitHub 登录已经失效。重新添加同名账号：

```bash
checkin-agentrouter add <name>
```

### 显示签到成功，但余额获取失败

OAuth 已经成功，签到仍然成立。失败的是签到后余额查询，脚本会最多重试 3 次，不会再次 OAuth。

### 没有显示“本次签到 +25”

只有签到前和签到后余额都获取成功时才能计算增量。首次运行或任一余额查询失败时会省略括号。

### OAuth 很慢或容易失败

先保持默认并发：

```dotenv
CHECKIN_CONCURRENCY=3
CHECKIN_OAUTH_CONCURRENCY=2
```

同一 IP 下继续增加 OAuth 并发通常不会更快。检查代理是否稳定，并使用 `DEBUG_MODE=true` 查看详细阶段日志。

### 定时日志里没有进度条

这是预期行为。动态进度条只在交互 TTY 中启用，`launchd` 和重定向日志使用逐行输出，避免日志中出现重复刷新字符。

### CloakBrowser 提示有新版本

同步更新依赖和浏览器运行时：

```bash
uv sync --upgrade
uv run python -m cloakbrowser install
```

## 本地状态和安全

以下内容包含登录态或本机状态，不应提交到 Git：

- `.env`
- `.env.*`
- `.browser_profiles/`
- `last_sessions.json`
- `balance_hash.txt`
- `checkin_screenshots/`
- `.venv/`
- coverage 和缓存目录

仓库是公开的，提交前确认没有包含登录信息、飞书 Webhook 或代理配置。

## 开发和测试

```bash
uv sync --dev
uv run ruff check .
uv run pytest -q
```

测试覆盖账号并发、OAuth 并发、余额查询优先级、账号状态、进度输出和通知格式。

## 第三方来源

本地版基于 [`millylee/anyrouter-check-in`](https://github.com/millylee/anyrouter-check-in) 修改。

原项目版权和许可证声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
