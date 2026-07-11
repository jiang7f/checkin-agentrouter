# checkin-agentrouter

本地运行的 AgentRouter 多账号每日签到工具。项目为每个账号保存独立的 GitHub 浏览器 profile，每次执行时保留 GitHub 登录态、清除 AgentRouter 登录态，再重新完成 GitHub OAuth。AgentRouter 的每日签到由这次重新登录触发。

本项目不会把“使用旧 Cookie 成功读取余额”当作签到成功。旧 Session 只用于读取签到前余额，真正签到始终由新的 GitHub OAuth 完成。

## 功能

- 多个 AgentRouter 账号并行执行
- 独立持久化 GitHub 浏览器 profile
- 每次重新 GitHub OAuth，可靠触发每日签到
- 签到前、签到后余额分别重试，不因余额查询失败重复 OAuth
- 交互终端使用实时多账号进度条
- 非交互终端使用普通逐行日志，适合 `launchd` 和日志文件
- 飞书通知余额、签到增量和失败原因
- 仅在浏览器明确进入 GitHub 登录页时标记 profile 过期
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

最小配置如下：

```dotenv
AGENTROUTER_ACCOUNTS=[]
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/replace-with-your-token
```

`add` 命令会自动把 profile 名称写入 `AGENTROUTER_ACCOUNTS`，因此也可以先保留空数组。

### 3. 添加账号

```bash
uv run python checkin.py add main
```

浏览器打开后，在该浏览器中完成 GitHub 登录。需要二次验证时也在同一窗口完成。脚本确认 GitHub 登录成功后会保存 profile，并把 `main` 写入 `.env`。

每个 profile 对应一个 GitHub 账号。添加其他账号时使用不同名称：

```bash
uv run python checkin.py add backup
```

### 4. 执行签到

```bash
uv run python checkin.py
```

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

每个 AgentRouter profile 按以下四个阶段执行：

| 阶段 | 使用的登录态 | 操作 | 重试 | 并发控制 |
| --- | --- | --- | --- | --- |
| 1. 签到前余额 | 上一次验证成功的 AgentRouter Session | 补充新的 WAF Cookie，通过 `httpx` 请求 `/api/user/self` | 最多 3 次 | 余额查询锁，普通优先级 |
| 2. 重新登录签到 | 持久化的 GitHub 浏览器 profile | 清除旧 AgentRouter 状态，重新 GitHub OAuth | 最多 6 次 | OAuth 锁，默认同时 2 个 |
| 3. 签到后余额 | 本次 OAuth 得到的新 AgentRouter Session | 补充新的 WAF Cookie，通过 `httpx` 请求 `/api/user/self` | 最多 3 次 | 余额查询锁，高优先级 |
| 4. 保存和汇总 | 已验证的新 Session | 保存 Session，计算签到增量，生成通知 | 不重试 | 不占 OAuth 锁 |

关键行为如下：

- 阶段 1 无论成功还是失败，都会继续执行 OAuth，Cookie 查询不是签到。
- 签到前余额只查询一轮。后续 OAuth 重试不会重新查询签到前余额。
- OAuth 成功后立即释放 OAuth 槽位，再执行签到后余额查询。
- 签到后余额失败只重试余额查询，不重新 OAuth。
- 只有签到后余额验证成功，才会用新 Session 覆盖本地保存的旧 Session。
- 只有签到前后余额都存在，通知才显示 `本次签到+xx`。
- 签到成功但签到后余额仍不可用时，通知显示 `余额获取失败`，并保留上一次验证成功的 Session。
- 如果本脚本当天已经成功签到，且签到前实时余额可用，签到后查询失败时可以复用该当天余额。跨天数据不会这样回退。

## Cookie、Session 和浏览器 profile

这三个概念用途不同：

- **Cookie** 是网站写入浏览器或 HTTP 客户端的小段状态数据。AgentRouter 的登录 Cookie 中包含名为 `session` 的 Cookie，WAF 还会使用 `acw_tc` 等临时 Cookie。
- **AgentRouter Session** 是 AgentRouter 的登录会话。本项目只持久化它的 `session` Cookie、`api_user` 和成功日期，用来在下次运行前后读取余额。
- **GitHub 浏览器 profile** 是完整的浏览器用户目录，保存 GitHub 登录态。真正签到时依靠它重新完成 OAuth，而不是依靠旧 AgentRouter Session。

本地 Session 很早并不会导致脚本把历史余额直接当作本次奖励。增量只使用本次运行中实时查询到的签到前余额和签到后余额。任一侧查询失败时，不显示增量。

## 并发和优先级

账号任务默认最多同时运行 3 个：

```dotenv
CHECKIN_CONCURRENCY=3
```

GitHub OAuth 默认最多同时运行 2 个：

```dotenv
CHECKIN_OAUTH_CONCURRENCY=2
```

签到前和签到后余额查询共用一个串行锁，避免同一 IP 瞬间并发请求 AgentRouter 用户接口而触发 WAF、HTML 响应、空响应或随机零值。

签到后余额查询优先于尚未开始的签到前查询。已经开始的查询不会被中断，结束后会先执行等待中的签到后查询，再继续其他签到前查询。余额锁和 OAuth 锁互相独立，因此账号等待或重试余额时不会占住 OAuth 槽位。

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

## Profile 管理

添加或重新登录：

```bash
checkin-agentrouter add main
```

查看状态：

```bash
checkin-agentrouter list
```

删除账号配置、浏览器 profile 和对应的本地 Session：

```bash
checkin-agentrouter delete main
```

`list` 中的状态含义：

| 状态 | 含义 |
| --- | --- |
| `valid` | GitHub profile 已验证，可以用于 OAuth |
| `expired` | 浏览器明确进入 GitHub 登录页，需要重新执行 `add` |
| `saved` | 本地 profile 目录存在 |
| `configured` | profile 名称存在于 `.env` 的 `AGENTROUTER_ACCOUNTS` |

网络错误、WAF 失败、HTTP 429、OAuth 临时失败或连续重试失败都不会自行把 profile 标记为 `expired`。只有浏览器明确显示 GitHub 登录页时才会标记过期。重新登录成功后会自动恢复为 `valid`。

Profile 默认保存在：

```text
.browser_profiles/agentrouter/<name>/
```

状态标记文件为：

```text
.browser_profiles/agentrouter/<name>/.anyrouter-profile.json
```

## 环境变量

常用配置：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AGENTROUTER_ACCOUNTS` | 无 | AgentRouter 浏览器 profile 名称数组 |
| `FEISHU_WEBHOOK` | 无 | 飞书机器人 Webhook |
| `CHECKIN_PROXY_URL` | 无 | AgentRouter 和 GitHub OAuth 使用的 HTTP 代理 |
| `CHECKIN_CONCURRENCY` | `3` | 同时执行的账号数 |
| `CHECKIN_OAUTH_CONCURRENCY` | `2` | 同时执行的 GitHub OAuth 数 |
| `ALWAYS_NOTIFY` | `false` | 即使余额未变化且没有失败也发送通知 |
| `DEBUG_MODE` | `false` | 输出详细日志并保存调试截图 |
| `CHECKIN_HEADLESS` | `true` | 每日签到时使用无头浏览器 |
| `CHECKIN_HUMANIZE` | `true` | 启用浏览器拟人化行为 |
| `CHECKIN_HUMANIZE_AGENTROUTER` | 继承 `CHECKIN_HUMANIZE` | 单独控制 AgentRouter 拟人化行为 |
| `CHECKIN_WAIT_TIMEOUT_MS` | `60000` | 浏览器登录总超时，单位为毫秒 |
| `CHECKIN_BROWSER_PROFILE_DIR` | `.browser_profiles` | 浏览器 profile 根目录 |
| `CHECKIN_LAST_SESSIONS_FILE` | `last_sessions.json` | 已验证 AgentRouter Session 状态文件 |
| `CHECKIN_SCREENSHOT_DIR` | `checkin_screenshots` | 调试截图目录 |
| `CLOAKBROWSER_BINARY_PATH` | 自动管理 | 手动指定 CloakBrowser 浏览器路径 |

如果本地网络访问 AgentRouter 需要代理：

```dotenv
CHECKIN_PROXY_URL=http://127.0.0.1:7890
```

`PROXY_SUBSCRIPTION_URL` 可以保留在本地配置中供代理工具使用，但签到脚本实际读取的是 `CHECKIN_PROXY_URL`。

高级用户仍可通过 `ANYROUTER_ACCOUNTS` 和 `PROVIDERS` 使用通用 Provider 配置。AgentRouter 浏览器 profile 推荐只使用 `AGENTROUTER_ACCOUNTS`，避免把旧 Cookie 账号和 OAuth profile 混在一起。

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

GitHub 登录态已经明确失效。重新添加同名 profile：

```bash
checkin-agentrouter add <name>
```

### 显示签到成功，但余额获取失败

OAuth 已经成功，签到仍然成立。失败的是独立的签到后余额查询。脚本会使用新 Session 最多重试 3 次，不会再次 OAuth，也不会保存未经余额验证的新 Session。

### 没有显示“本次签到 +25”

只有签到前和签到后余额都实时获取成功时才能计算增量。首次运行、旧 Session 无效或任一余额查询失败时会省略括号，避免用历史快照产生错误的大额增量。

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

即使仓库是私有仓库，也不要提交 Cookie、飞书 Webhook、代理订阅地址或浏览器 profile。

## 开发和测试

```bash
uv sync --dev
uv run ruff check .
uv run pytest -q
```

测试覆盖账号并发、OAuth 并发、余额查询优先级、Session 保存安全、profile 过期判断、进度输出和通知格式。

## 第三方来源

本地版基于 [`millylee/anyrouter-check-in`](https://github.com/millylee/anyrouter-check-in) 修改。参考项目中稳定的 Cookie 加新 WAF Cookie 查询方式只用于余额读取，AgentRouter 签到仍使用本项目的重新 GitHub OAuth 流程。

原项目版权和许可证声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
