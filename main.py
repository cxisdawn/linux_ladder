import curses
import json
import os
import secrets
import signal
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import client
from urllib.parse import quote


APP_NAME = "linux_ladder"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PROVIDER_DIR = os.path.join(DATA_DIR, "providers")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CONFIG_PATH = os.path.join(DATA_DIR, "clash.yaml")
SUBSCRIPTION_PATH = os.path.join(DATA_DIR, "subscription.txt")
PID_PATH = os.path.join(DATA_DIR, "core.pid")
LOG_PATH = os.path.join(DATA_DIR, "core.log")
ENV_SH_PATH = os.path.join(DATA_DIR, "proxy_env.sh")
ENV_FISH_PATH = os.path.join(DATA_DIR, "proxy_env.fish")
BANNER_TEXT = "提示：↑/↓ 选择，空格确认，q 返回 | Tip: Up/Down to move, Space to select, q to cancel"
DELAY_TEST_URL = "http://www.gstatic.com/generate_204"
DELAY_TIMEOUT_MS = 3000
DELAY_SKIP = {"DIRECT", "REJECT", "REJECT-DROP"}
DELAY_WORKERS = 20


DEFAULT_STATE = {
    "subscription_url": "",
    "core_path": "",
    "controller_addr": "127.0.0.1:9090",
    "secret": "",
    "mode": "rule",
    "tun": False,
    "system_proxy": False,
    "allow_lan": True,
    "mixed_port": 7890,
    "socks_port": 7891,
    "dns_port": 1053,
    "selected_group": "",
    "selected_node": "",
}


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROVIDER_DIR, exist_ok=True)


def load_state():
    ensure_dirs()
    if not os.path.exists(STATE_PATH):
        return DEFAULT_STATE.copy()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_STATE.copy()
    merged = DEFAULT_STATE.copy()
    merged.update(data)
    return merged


def save_state(state):
    ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def ensure_secret(state):
    if state.get("secret"):
        return state["secret"]
    state["secret"] = secrets.token_hex(16)
    save_state(state)
    return state["secret"]


def input_nonempty(prompt):
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty.")


def find_core_path(state):
    if state.get("core_path"):
        return state["core_path"]
    for name in ("clash", "mihomo"):
        path = shutil_which(name)
        if path:
            return path
    return ""


def shutil_which(name):
    try:
        import shutil

        return shutil.which(name) or ""
    except Exception:
        return ""


def render_config(state):
    url = state.get("subscription_url", "").strip()
    if not url:
        raise ValueError("subscription_url is empty")
    secret = ensure_secret(state)
    allow_lan = "true" if state.get("allow_lan", True) else "false"
    mode = state.get("mode", "rule")
    tun_enabled = state.get("tun", False)
    lines = []
    lines.append(f"mixed-port: {state.get('mixed_port', 7890)}")
    lines.append(f"socks-port: {state.get('socks_port', 7891)}")
    lines.append(f"allow-lan: {allow_lan}")
    lines.append("bind-address: 0.0.0.0")
    lines.append(f"mode: {mode}")
    lines.append("log-level: info")
    lines.append(f"external-controller: {state.get('controller_addr')}")
    lines.append(f"secret: {secret}")
    lines.append("profile:")
    lines.append("  store-selected: true")
    lines.append("  store-fake-ip: true")
    if tun_enabled:
        lines.append("tun:")
        lines.append("  enable: true")
        lines.append("  stack: system")
        lines.append("  auto-route: true")
        lines.append("  auto-detect-interface: true")
    lines.append("dns:")
    lines.append("  enable: true")
    lines.append(f"  listen: 0.0.0.0:{state.get('dns_port', 1053)}")
    lines.append("  enhanced-mode: fake-ip")
    lines.append("  nameserver:")
    lines.append("    - 1.1.1.1")
    lines.append("    - 8.8.8.8")
    lines.append("proxy-providers:")
    lines.append("  sub:")
    lines.append("    type: http")
    lines.append(f"    url: {url}")
    lines.append("    interval: 3600")
    lines.append("    path: ./providers/sub.yaml")
    lines.append("    health-check:")
    lines.append("      enable: true")
    lines.append("      interval: 600")
    lines.append("      url: http://www.gstatic.com/generate_204")
    lines.append("proxy-groups:")
    lines.append("  - name: Proxy")
    lines.append("    type: select")
    lines.append("    use:")
    lines.append("      - sub")
    lines.append("    proxies:")
    lines.append("      - DIRECT")
    lines.append("  - name: GLOBAL")
    lines.append("    type: select")
    lines.append("    proxies:")
    lines.append("      - Proxy")
    lines.append("      - DIRECT")
    lines.append("rules:")
    lines.append("  - MATCH,Proxy")
    return "\n".join(lines) + "\n"


def write_config(state):
    ensure_dirs()
    content = render_config(state)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def do_download_subscription(state):
    url = state.get("subscription_url", "").strip()
    if not url:
        return False, "Subscription URL is empty."
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        with open(SUBSCRIPTION_PATH, "wb") as f:
            f.write(data)
        return True, f"Downloaded subscription: {len(data)} bytes."
    except urllib.error.URLError as exc:
        return False, f"Download failed: {exc}"


def is_pid_running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid():
    if not os.path.exists(PID_PATH):
        return 0
    try:
        with open(PID_PATH, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_pid(pid):
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(pid))


def do_write_config(state):
    try:
        write_config(state)
        return True, f"Config written: {CONFIG_PATH}"
    except ValueError as exc:
        return False, f"Config error: {exc}"


def do_start_core(state):
    core_path = find_core_path(state)
    if not core_path:
        return False, "Clash core not found. Set core path first."
    ok, msg = do_write_config(state)
    if not ok:
        return False, msg
    pid = read_pid()
    if pid and is_pid_running(pid):
        return False, f"Core already running with PID {pid}."
    ensure_dirs()
    log_file = open(LOG_PATH, "ab")
    cmd = [core_path, "-d", DATA_DIR, "-f", CONFIG_PATH]
    try:
        if os.name == "nt":
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file)
        else:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, start_new_session=True)
    except OSError as exc:
        return False, f"Failed to start core: {exc}"
    write_pid(proc.pid)
    return True, f"Core started with PID {proc.pid}."


def do_stop_core():
    pid = read_pid()
    if not pid:
        return False, "No PID found."
    if not is_pid_running(pid):
        return False, "Core not running."
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, f"Failed to stop core: {exc}"
    time.sleep(0.5)
    return True, "Stop signal sent."


def api_request(state, method, path, body=None):
    controller = state.get("controller_addr", "127.0.0.1:9090")
    secret = state.get("secret", "")
    host, port = controller.split(":")
    conn = client.HTTPConnection(host, int(port), timeout=5)
    headers = {}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"HTTP {resp.status}: {raw[:200]}")
    if raw:
        return json.loads(raw.decode("utf-8"))
    return None


def get_delay(state, proxy_name, timeout_ms=DELAY_TIMEOUT_MS, url=DELAY_TEST_URL):
    if proxy_name in DELAY_SKIP:
        return None
    encoded_name = quote(proxy_name, safe="")
    encoded_url = quote(url, safe="")
    path = f"/proxies/{encoded_name}/delay?timeout={timeout_ms}&url={encoded_url}"
    try:
        data = api_request(state, "GET", path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    delay = data.get("delay")
    if delay is None:
        return None
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        return None
    if delay < 0:
        return None
    return delay


def measure_delays(state, nodes, progress_cb=None):
    delays = {}
    total = len(nodes)
    if total == 0:
        return delays
    workers = min(DELAY_WORKERS, total)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_node = {executor.submit(get_delay, state, node): node for node in nodes}
        done = 0
        for future in as_completed(future_to_node):
            node = future_to_node[future]
            try:
                delays[node] = future.result()
            except Exception:
                delays[node] = None
            done += 1
            if progress_cb:
                progress_cb(done, total)
    return delays


def list_selectors(proxies):
    selectors = {}
    for name, meta in proxies.items():
        if isinstance(meta, dict) and meta.get("type") == "Selector":
            selectors[name] = meta
    return selectors


def get_selectors(state):
    try:
        data = api_request(state, "GET", "/proxies")
    except Exception as exc:
        return None, f"API error: {exc}"
    proxies = data.get("proxies", {})
    selectors = list_selectors(proxies)
    if not selectors:
        return None, "No selectable proxy groups found."
    return selectors, ""


def do_set_mode(state, mode):
    state["mode"] = mode
    save_state(state)
    try:
        api_request(state, "PUT", "/configs", {"mode": mode})
        return True, f"Mode set via API: {mode}"
    except Exception:
        return True, f"Mode saved locally: {mode}"


def do_select_node(state, group, node):
    api_request(state, "PUT", f"/proxies/{quote(group, safe='')}", {"name": node})
    state["selected_group"] = group
    state["selected_node"] = node
    save_state(state)
    return True, f"Selected {node} in {group}."


def toggle_tun(state):
    state["tun"] = not state.get("tun", False)
    save_state(state)
    return True, f"TUN enabled: {state['tun']}"


def toggle_system_proxy(state):
    state["system_proxy"] = not state.get("system_proxy", False)
    save_state(state)
    if state["system_proxy"]:
        write_proxy_env(state)
        return True, "Proxy env scripts written. Use: source data/proxy_env.sh"
    write_proxy_env(state, disable=True)
    return True, "Proxy env scripts written to unset variables."


def write_proxy_env(state, disable=False):
    http_port = state.get("mixed_port", 7890)
    socks_port = state.get("socks_port", 7891)
    if disable:
        sh = "\n".join(
            [
                "unset http_proxy",
                "unset https_proxy",
                "unset all_proxy",
                "unset no_proxy",
                "",
            ]
        )
        fish = "\n".join(
            [
                "set -e http_proxy",
                "set -e https_proxy",
                "set -e all_proxy",
                "set -e no_proxy",
                "",
            ]
        )
    else:
        sh = "\n".join(
            [
                f"export http_proxy=http://127.0.0.1:{http_port}",
                f"export https_proxy=http://127.0.0.1:{http_port}",
                f"export all_proxy=socks5://127.0.0.1:{socks_port}",
                "export no_proxy=localhost,127.0.0.1,::1",
                "",
            ]
        )
        fish = "\n".join(
            [
                f"set -x http_proxy http://127.0.0.1:{http_port}",
                f"set -x https_proxy http://127.0.0.1:{http_port}",
                f"set -x all_proxy socks5://127.0.0.1:{socks_port}",
                "set -x no_proxy localhost,127.0.0.1,::1",
                "",
            ]
        )
    with open(ENV_SH_PATH, "w", encoding="utf-8") as f:
        f.write(sh)
    with open(ENV_FISH_PATH, "w", encoding="utf-8") as f:
        f.write(fish)


def build_status_lines(state):
    pid = read_pid()
    running = pid and is_pid_running(pid)
    lines = [
        f"Subscription URL: {state.get('subscription_url') or '(empty)'}",
        f"Core path: {find_core_path(state) or '(not set)'}",
        f"Mode: {state.get('mode')}",
        f"TUN: {state.get('tun')}",
        f"System proxy: {state.get('system_proxy')}",
        f"Controller: {state.get('controller_addr')}",
        f"PID: {pid} (running: {bool(running)})",
    ]
    if running:
        try:
            version = api_request(state, "GET", "/version")
            lines.append(f"Core version: {version.get('version', 'unknown')}")
        except Exception:
            lines.append("Core version: unavailable")
    return lines


def cli_set_subscription(state):
    url = input_nonempty("Enter subscription URL: ")
    state["subscription_url"] = url
    save_state(state)
    print("Subscription saved.")


def cli_set_core_path(state):
    path = input_nonempty("Enter clash core path (binary): ")
    state["core_path"] = path
    save_state(state)
    print("Core path saved.")


def cli_set_mode(state):
    print("1) direct")
    print("2) global")
    print("3) rule")
    choice = input("Select mode: ").strip()
    mapping = {"1": "direct", "2": "global", "3": "rule"}
    if choice not in mapping:
        print("Invalid choice.")
        return
    ok, msg = do_set_mode(state, mapping[choice])
    print(msg)


def cli_select_node(state):
    selectors, err = get_selectors(state)
    if not selectors:
        print(err)
        return
    names = list(selectors.keys())
    for idx, name in enumerate(names, start=1):
        current = selectors[name].get("now", "")
        print(f"{idx}) {name} (now: {current})")
    group_choice = input("Select group: ").strip()
    if not group_choice.isdigit() or not (1 <= int(group_choice) <= len(names)):
        print("Invalid group.")
        return
    group = names[int(group_choice) - 1]
    nodes = selectors[group].get("all", [])
    if not nodes:
        print("No nodes in this group.")
        return
    print("Measuring latency...")
    delays = measure_delays(state, nodes, progress_cb=lambda d, t: print(f"{d}/{t}", end="\r", flush=True))
    print("")
    for idx, node in enumerate(nodes, start=1):
        delay = delays.get(node)
        delay_text = "N/A" if delay is None else f"{delay} ms"
        print(f"{idx}) {node} [{delay_text}]")
    node_choice = input("Select node: ").strip()
    if not node_choice.isdigit() or not (1 <= int(node_choice) <= len(nodes)):
        print("Invalid node.")
        return
    node = nodes[int(node_choice) - 1]
    try:
        ok, msg = do_select_node(state, group, node)
        print(msg)
    except Exception as exc:
        print(f"Failed to set node: {exc}")


def cli_show_status(state):
    for line in build_status_lines(state):
        print(line)


def menu_cli():
    print("")
    print("Linux Ladder - Clash Terminal")
    print("1) Set subscription URL")
    print("2) Download subscription")
    print("3) Write config")
    print("4) Set core path")
    print("5) Start core")
    print("6) Stop core")
    print("7) Set mode")
    print("8) Select node")
    print("9) Toggle system proxy env")
    print("10) Toggle TUN")
    print("11) Status")
    print("0) Exit")


def run_cli():
    state = load_state()
    print("\033[32m" + BANNER_TEXT + "\033[0m")
    while True:
        menu_cli()
        choice = input("Select: ").strip()
        if choice == "1":
            cli_set_subscription(state)
        elif choice == "2":
            ok, msg = do_download_subscription(state)
            print(msg)
        elif choice == "3":
            ok, msg = do_write_config(state)
            print(msg)
        elif choice == "4":
            cli_set_core_path(state)
        elif choice == "5":
            ok, msg = do_start_core(state)
            print(msg)
        elif choice == "6":
            ok, msg = do_stop_core()
            print(msg)
        elif choice == "7":
            cli_set_mode(state)
        elif choice == "8":
            cli_select_node(state)
        elif choice == "9":
            ok, msg = toggle_system_proxy(state)
            print(msg)
        elif choice == "10":
            ok, msg = toggle_tun(state)
            print(msg)
        elif choice == "11":
            cli_show_status(state)
        elif choice == "0":
            break
        else:
            print("Invalid choice.")


def tui_message(stdscr, title, message):
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    y = 0
    if title:
        stdscr.addstr(y, 0, title[: width - 1], curses.A_BOLD)
        y += 1
    for line in textwrap.wrap(message, width - 1):
        if y >= height - 2:
            break
        stdscr.addstr(y, 0, line)
        y += 1
    stdscr.addstr(height - 1, 0, "Press any key to continue.")
    stdscr.refresh()
    stdscr.getch()


def tui_prompt(stdscr, prompt, default=""):
    stdscr.clear()
    stdscr.addstr(0, 0, prompt)
    if default:
        stdscr.addstr(2, 0, f"Default: {default}")
    stdscr.addstr(4, 0, "> ")
    stdscr.refresh()
    curses.echo()
    curses.curs_set(1)
    value = stdscr.getstr(4, 2, 512).decode("utf-8", errors="ignore").strip()
    curses.noecho()
    curses.curs_set(0)
    return value or default


def tui_menu(stdscr, title, options, footer="Up/Down to move, Space to select, q to cancel"):
    idx = 0
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        if curses.has_colors():
            stdscr.attron(curses.color_pair(1))
            stdscr.addstr(0, 0, BANNER_TEXT[: width - 1])
            stdscr.attroff(curses.color_pair(1))
        else:
            stdscr.addstr(0, 0, BANNER_TEXT[: width - 1])
        stdscr.addstr(1, 0, title[: width - 1], curses.A_BOLD)
        for i, option in enumerate(options):
            if isinstance(option, tuple):
                text, color_pair = option
            else:
                text, color_pair = option, 0
            y = 3 + i
            if y >= height - 2:
                break
            if i == idx:
                attrs = curses.A_REVERSE
                if color_pair:
                    attrs |= curses.color_pair(color_pair)
                stdscr.addstr(y, 0, text[: width - 1], attrs)
            else:
                if color_pair:
                    stdscr.addstr(y, 0, text[: width - 1], curses.color_pair(color_pair))
                else:
                    stdscr.addstr(y, 0, text[: width - 1])
        stdscr.addstr(height - 1, 0, footer[: width - 1])
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(options)
        elif key in (ord(" "), 10, 13):
            return idx
        elif key in (ord("q"), 27):
            return None


def tui_select_node(stdscr, state):
    selectors, err = get_selectors(state)
    if not selectors:
        tui_message(stdscr, "Select Node", err)
        return
    names = list(selectors.keys())
    options = [f"{name} (now: {selectors[name].get('now', '')})" for name in names]
    group_idx = tui_menu(stdscr, "Select Group", options)
    if group_idx is None:
        return
    group = names[group_idx]
    nodes = selectors[group].get("all", [])
    if not nodes:
        tui_message(stdscr, "Select Node", "No nodes in this group.")
        return
    def _progress(done, total):
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        title = "Testing latency..."
        stdscr.addstr(0, 0, title[: width - 1], curses.A_BOLD)
        stdscr.addstr(2, 0, f"{done}/{total}"[: width - 1])
        stdscr.addstr(height - 1, 0, "Please wait..."[: width - 1])
        stdscr.refresh()

    delays = measure_delays(state, nodes, progress_cb=_progress)
    options = []
    for node in nodes:
        delay = delays.get(node)
        if delay is None:
            delay_text = "N/A"
            color = 4
        elif delay <= 200:
            delay_text = f"{delay} ms"
            color = 2
        elif delay <= 800:
            delay_text = f"{delay} ms"
            color = 3
        else:
            delay_text = f"{delay} ms"
            color = 4
        options.append((f"{node} [{delay_text}]", color))
    node_idx = tui_menu(stdscr, f"Select Node - {group}", options)
    if node_idx is None:
        return
    node = nodes[node_idx]
    try:
        ok, msg = do_select_node(state, group, node)
        tui_message(stdscr, "Select Node", msg)
    except Exception as exc:
        tui_message(stdscr, "Select Node", f"Failed to set node: {exc}")


def tui_set_mode(stdscr, state):
    options = ["direct", "global", "rule"]
    idx = tui_menu(stdscr, "Select Mode", options)
    if idx is None:
        return
    ok, msg = do_set_mode(state, options[idx])
    tui_message(stdscr, "Set Mode", msg)


def tui_show_status(stdscr, state):
    lines = build_status_lines(state)
    tui_message(stdscr, "Status", "\n".join(lines))


def run_tui():
    state = load_state()

    def _loop(stdscr):
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
        curses.curs_set(0)
        while True:
            options = [
                "Set subscription URL",
                "Download subscription",
                "Write config",
                "Set core path",
                "Start core",
                "Stop core",
                "Set mode",
                "Select node",
                "Toggle system proxy env",
                "Toggle TUN",
                "Status",
                "Exit",
            ]
            idx = tui_menu(stdscr, "Linux Ladder - Clash Terminal", options)
            if idx is None or options[idx] == "Exit":
                break
            if options[idx] == "Set subscription URL":
                url = tui_prompt(stdscr, "Enter subscription URL:", state.get("subscription_url", ""))
                if url:
                    state["subscription_url"] = url
                    save_state(state)
                    tui_message(stdscr, "Subscription", "Subscription saved.")
            elif options[idx] == "Download subscription":
                ok, msg = do_download_subscription(state)
                tui_message(stdscr, "Download", msg)
            elif options[idx] == "Write config":
                ok, msg = do_write_config(state)
                tui_message(stdscr, "Write Config", msg)
            elif options[idx] == "Set core path":
                path = tui_prompt(stdscr, "Enter clash core path (binary):", state.get("core_path", ""))
                if path:
                    state["core_path"] = path
                    save_state(state)
                    tui_message(stdscr, "Core Path", "Core path saved.")
            elif options[idx] == "Start core":
                ok, msg = do_start_core(state)
                tui_message(stdscr, "Start Core", msg)
            elif options[idx] == "Stop core":
                ok, msg = do_stop_core()
                tui_message(stdscr, "Stop Core", msg)
            elif options[idx] == "Set mode":
                tui_set_mode(stdscr, state)
            elif options[idx] == "Select node":
                tui_select_node(stdscr, state)
            elif options[idx] == "Toggle system proxy env":
                ok, msg = toggle_system_proxy(state)
                tui_message(stdscr, "System Proxy", msg)
            elif options[idx] == "Toggle TUN":
                ok, msg = toggle_tun(state)
                tui_message(stdscr, "TUN", msg)
            elif options[idx] == "Status":
                tui_show_status(stdscr, state)

    curses.wrapper(_loop)


def main():
    if os.environ.get("LINUX_LADDER_PLAIN") == "1" or not sys.stdin.isatty():
        run_cli()
        return
    try:
        run_tui()
    except Exception as exc:
        print(f"TUI failed: {exc}")
        run_cli()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("")
        sys.exit(0)
