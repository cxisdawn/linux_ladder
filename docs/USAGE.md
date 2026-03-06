# 使用说明（TUI）

## 操作方式
- ↑/↓：移动选择
- 空格：确认
- q 或 Esc：返回/取消

## 延迟颜色
- 绿色：延迟较低，体验流畅
- 黄色：延迟中等，可能略卡
- 红色：延迟较高或不可用

## 延迟测试提示
- 节点较多时会先进行延迟测试，请耐心等待
- 若想更快，可临时在代码里调小 `DELAY_TIMEOUT_MS`

## 推荐流程
1. Set subscription URL
2. Write config
3. Start core
4. Set mode
5. Select node

## 系统代理
1. Toggle system proxy env
2. 让当前 shell 生效：
```bash
source data/proxy_env.sh
```

## 纯 CLI 模式
如果终端不支持 curses：
```bash
LINUX_LADDER_PLAIN=1 python3 main.py
```
