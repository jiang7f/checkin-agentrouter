# launchd

这个目录保存 macOS `launchd` 定时任务模板。当前模板每天 10:00 运行一次 `checkin-agentrouter`。

安装：

```bash
mkdir -p ~/Library/Logs/checkin-agentrouter
sed -e "s#__HOME__#$HOME#g" \
  -e "s#__REPO_DIR__#$(pwd)#g" \
  launchd/com.checkin.agentrouter.plist > ~/Library/LaunchAgents/com.checkin.agentrouter.plist
plutil -lint ~/Library/LaunchAgents/com.checkin.agentrouter.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.checkin.agentrouter.plist
```

查看：

```bash
launchctl list | rg com.checkin.agentrouter
```

卸载：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.checkin.agentrouter.plist
rm ~/Library/LaunchAgents/com.checkin.agentrouter.plist
```

模板里的 `__HOME__` 和 `__REPO_DIR__` 会在安装命令里替换成本机实际路径。
