---
title: TROUBLESHOOT
---

# 故障排查与常见坑位（简明）

以下为项目逻辑、常见问题与高频踩坑的实事求是总结，便于快速定位问题与标准化运维流程。

## 项目逻辑（简版）
1. `main.py` 负责 TUI/CLI 菜单。
2. 用户输入订阅地址 → 生成 `data/clash.yaml`（proxy-providers + 规则）。
3. 启动 core（`mihomo`/`clash`）并写入 `data/core.pid`、日志到 `data/core.log`。
4. 通过 REST API（`127.0.0.1:9090` + `secret`）获取代理组、切换节点、切换模式。
5. 可生成 `data/proxy_env.sh` 让当前 shell 走代理。
6. 节点选择界面现在会先测速并显示延迟（绿/黄/红）。

---

## 本次遇到的问题（与成因）
1. API 401 Unauthorized
   - 原因：工具里的 `state.json` 的 `secret` 和正在运行的 core 不一致（常见于 systemd 跑着另一份配置）。
   - 结果：`Select node` 无法获取节点，报 401。

2. Connection refused
   - 原因：core 进程没有在跑，或 9090 没监听。
   - 结果：`Select node` 报连接被拒绝。

3. 启动后立刻退出（Mihomo shutting down）
   - 原因：core 被终端会话结束信号杀掉（工具退出/断开 ssh）。
   - 结果：端口瞬间消失，Select node 连不上。

4. 端口占用
   - 原因：旧的 core 或其它服务占用了 7890/7891/1053。
   - 结果：core 启动失败，日志提示 `address already in use`。

---

## 高频坑与建议的解决方式
1. systemd 和手动启动混用
   - 问题：systemd 使用 `/etc/mihomo/config.yaml`，工具使用 `/opt/linux_ladder/data/clash.yaml`，导致 `secret` 不一致 → 401。
   - 解决：只保留一种运行方式，或统一配置文件与 `secret`（推荐统一为 systemd 管理并指向同一配置文件）。

2. core 没常驻
   - 问题：在工具里直接启动 core，但退出 SSH 会话导致 core 被杀掉。
   - 解决：短期用工具的 `start_new_session`，长期建议用 systemd 保证常驻与开机自启。

3. 订阅没写进配置就启动
   - 问题：`subscription_url` 为空会导致生成的配置无效。
   - 解决：先 Set subscription URL → Write config → Start core（顺序不可颠倒）。

4. 端口被占用
   - 问题：端口冲突会直接导致 core 启动失败，日志会有 `address already in use`。
   - 解决：查看并停止占用进程或修改端口配置后重启。

5. TUI 延迟测速卡住
   - 问题：节点很多时同步测速会造成 UI 阻塞。
   - 解决：已改成并发测速并显示进度提示，进一步可限制并发量或增加超时策略。

---

## 现在稳定的最佳实践
1. 使用 `/opt/linux_ladder` 作为长期部署目录。
2. 使用 systemd 管理 core，实现开机自启与稳定常驻。
3. 只用这一套配置和 `secret`，避免系统中存在多份配置造成冲突。
4. 修改订阅后先 `Write config`，再重启 core（不要直接改完就期待生效）。

---

## 快速排查清单（排查顺序）
1. 是否能访问 `http://127.0.0.1:9090/`？（Connection refused → core 未跑或端口未监听）
2. 校验 `state.json` 中 `secret` 与 core 配置的 `secret` 是否相同（不同则 401）。
3. 检查 `data/core.log` 中关于端口或启动的错误信息（端口占用、配置错误）。
4. 若使用 systemd，确认 systemd 的配置文件路径和工具使用的配置路径一致。
5. 修改订阅后按顺序：Set subscription → Write config → Restart core。

---

如果你愿意，我可以把这段放入 `README.md` 的运维小节，或把本文件再扩展成英文版／带命令行排查步骤的版本。
