# Linux Ladder（Clash 终端）

在 Linux 服务器上管理 Clash/Mihomo 内核的最小终端工具（TUI/CLI）。

## 功能
- 保存订阅地址
- 生成 Clash 配置（proxy-providers）
- 启动/停止 core，查看状态
- 切换模式（direct/global/rule）
- 通过 REST API 选择节点
- 生成系统代理环境变量脚本（bash/fish）
- 开关 TUN 配置段

## 依赖
- Python 3.8+
- 服务器上已安装 Clash 或 Mihomo 内核

## 快速开始（Linux）
```bash
python3 main.py
```

## 说明
- TUI 操作：↑/↓ 移动，空格确认，`q` 返回。
- 纯 CLI 模式：`LINUX_LADDER_PLAIN=1 python3 main.py`
- 配置输出：`data/clash.yaml`
- 订阅作为 proxy provider 使用
- 选择节点：启动 core 后用 “Select node”
- 启用系统代理：
```bash
source data/proxy_env.sh
```

## 内核路径
如果 `clash` 或 `mihomo` 在 `$PATH`，可不填。
否则在菜单里设置完整路径。
