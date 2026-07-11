# checkin-agentrouter

本仓库是本地 AgentRouter 每日签到工具。它使用独立浏览器 profile 保存 GitHub 登录态，每次运行时清掉 AgentRouter 自身登录态，再通过 GitHub OAuth 重新进入 AgentRouter，从而触发签到并读取余额。

## 当前本地命令

```bash
checkin-agentrouter
checkin-agentrouter add <name>
checkin-agentrouter list
checkin-agentrouter delete <name>
```

默认本地项目目录：

```bash
$HOME/仓库/checkin-agentrouter
```

本地定时任务：

```text
com.checkin.agentrouter  每天 10:00
```

仓库里的 `launchd/` 目录保存了当前机器使用的定时任务模板和安装说明。

## 工作方式

1. `add <name>` 会打开一个独立空浏览器 profile。
2. 在浏览器里完成 GitHub 登录。
3. 脚本验证 GitHub 登录成功后保存 profile。
4. 每日运行时保留 GitHub 登录态。
5. 每日运行时清理 AgentRouter 的 cookie、localStorage 和 sessionStorage。
6. 脚本重新走 AgentRouter 的 GitHub OAuth。
7. 读取用户信息并触发签到。
8. 发送飞书通知。

profile 保存在：

```bash
.browser_profiles/agentrouter/<name>
```

profile 状态记录在：

```bash
.browser_profiles/agentrouter/<name>/.anyrouter-profile.json
```

这些目录只保存在本地，不提交到 Git。

## 环境配置

复制示例配置：

```bash
cd "$HOME/仓库/checkin-agentrouter"
cp .env.example .env
```

推荐只用浏览器 profile 账号：

```dotenv
AGENTROUTER_ACCOUNTS=["main","backup"]
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

如果需要代理：

```dotenv
PROXY_SUBSCRIPTION_URL=https://example.com/mihomo.yaml
CHECKIN_PROXY_URL=http://127.0.0.1:7890
```

如果只在本地运行，`.env` 放本地即可，不要放到 GitHub。

## 添加账号

```bash
checkin-agentrouter add main
```

脚本会打开浏览器。你只需要完成 GitHub 登录。如果 GitHub 要求二次验证，也在这个浏览器里完成。

添加成功后，`.env` 里的 `AGENTROUTER_ACCOUNTS` 会加入这个名字。名字和 profile 绑定。删除时会一起删除配置和本地 profile：

```bash
checkin-agentrouter delete main
```

查看状态：

```bash
checkin-agentrouter list
```

状态含义：

```text
valid    GitHub profile 已验证
expired  GitHub 已明确要求重新登录，或连续 6 次 OAuth 失败
saved    本地 profile 目录存在
configured  .env 中配置了这个名字
```

单次 OAuth 或 AgentRouter 接口临时失败不会把 profile 标记为 `expired`。如果 OAuth 弹窗明确停在 GitHub 登录页，脚本会立即标记过期并停止无意义的重试。后续登录成功时，历史上的错误过期标记会自动恢复为 `valid`。

如果同名 profile 仍是 `valid`，`add` 会询问是否覆盖。如果已经 `expired`，会直接覆盖。

## 手动运行

```bash
checkin-agentrouter
```

账号会并发执行，默认并发数是 3，可以通过环境变量调整：

```bash
CHECKIN_CONCURRENCY=3
```

GitHub OAuth 授权默认最多同时运行 2 个，避免同一 IP 在短时间内同时发起过多授权请求。余额查询和其他阶段仍按账号并发数执行：

```bash
CHECKIN_OAUTH_CONCURRENCY=2
```

手动在交互终端运行多个账号时，每个账号固定显示一行实时进度。等待并发槽位的账号不会提前计时，真正开始执行后才显示已用时间。成功账号保留最终余额，失败账号会在进度结束后展开详细日志。开启 `DEBUG_MODE` 时，成功账号的完整缓冲日志也会在进度结束后展开。

`launchd`、重定向输出、单账号运行和并发数为 1 时继续输出普通逐行日志。单个账号失败时最多重试 5 次，最终通知只显示最后结果，不显示中间重试过程。

GitHub OAuth 成功后会优先复用浏览器已返回的真实余额。如果浏览器只返回了零值占位数据，脚本仅用新 session cookie 快速直查一次。余额查询失败不会重新执行 OAuth，本次通知会显示“余额获取失败”，新 session 仍会保存给下次签到前查询使用。

## 飞书通知

通知标题：

```text
AgentRouter Check-in
```

通知示例：

每日签到成功
时间: 2026-07-08 16:25:11
结果: 3/3

余额：

```text
✅ main    $31.80  （本次签到+25）
✅ backup  $50.20
❌ spare   获取失败  （可能需要重新登录: checkin-agentrouter add spare）
```

`本次签到+xx` 的判断来自上一次保存的 AgentRouter 登录态：

- 每次成功后只保存上一次 AgentRouter 的 `session` 和 `api_user`
- 下次运行先用旧登录态查签到前余额，临时失败时最多尝试 3 次
- 余额查询完成后固定这次结果，再用 GitHub profile 重新 OAuth 登录 AgentRouter 触发签到
- OAuth 签到最多执行 6 次，重试时不会再次查询签到前余额
- OAuth 成功后直接复用浏览器内验证过的用户信息，避免立即通过同一代理重复请求接口
- 登录页按按钮和页面 DOM 是否就绪推进，不等待后台请求全部结束，也不使用固定的 3 秒或 5 秒停顿
- AgentRouter 在新浏览器页打开 GitHub OAuth 时，脚本会跟踪新页；GitHub 显示 `Authorize` 或 `Reauthorize` 时会自动确认
- OAuth 过程跟踪 AgentRouter session 相对登录页基线的变化，并以控制台用户数据完成最终验证
- 到达控制台后并行观察接口响应、主动查询和当前页面写入的 `localStorage.user`，不等待 `networkidle`
- 用户数据短暂返回零值占位时继续等待；浏览器数据最终仍同时为零时改走 HTTP fallback，不保存错误的 `$0.00`
- 最后查签到后余额，两者差值显示为 `本次签到+xx`
- 首次运行或旧登录态连续 3 次都查不到余额时，只显示当前余额，不显示增量

这个行为有回归测试覆盖。

## 本地定时任务

定时任务不是 Python 代码自动创建的，而是 macOS `launchd` 配置。仓库里的模板在：

```text
launchd/com.checkin.agentrouter.plist
launchd/README.md
```

查看任务：

```bash
launchctl list | rg checkin
```

AgentRouter 当前每天 10:00 运行：

```text
~/Library/LaunchAgents/com.checkin.agentrouter.plist
```

日志在：

```bash
~/Library/Logs/checkin-agentrouter/stdout.log
~/Library/Logs/checkin-agentrouter/stderr.log
```

## 依赖

```bash
uv sync --dev
uv run python -m cloakbrowser install
```

项目要求 `cloakbrowser>=0.4.10`。它的浏览器运行时由自己管理。升级 Python 包后需要再运行一次安装命令更新对应的浏览器运行时，日常运行只需要使用 `checkin-agentrouter` 命令。

## 测试

```bash
uv run ruff check .
uv run pytest -q
```

## 安全说明

以下内容不会提交到 Git：

- `.env`
- `.env.*`
- `.browser_profiles/`
- `.venv/`
- `balance_hash.txt`
- `last_sessions.json`
- `checkin_screenshots/`
- coverage 和缓存目录

GitHub 仓库是 private，但仍然不要提交 cookies、webhook、订阅地址、浏览器 profile 或任何登录态。

## 第三方来源

本地版基于 `millylee/anyrouter-check-in` 修改。原项目版权和许可证声明见 `THIRD_PARTY_NOTICES.md`。
