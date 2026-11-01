#!/usr/bin/env python3
"""容器健康检查：四个端口各探一次，任一不通就返回非 0。

用法：
    python /usr/local/bin/healthcheck.py          # 打印明细
    python /usr/local/bin/healthcheck.py --quiet  # 只返回退出码（给 HEALTHCHECK 用）
    python /usr/local/bin/healthcheck.py --json   # 给 status.sh 用
"""
import argparse
import json
import os
import socket
import sys
import urllib.request

# 服务名 -> (端口, 探活路径, 是否必需)
SERVICES = {
    "webui": (int(os.environ.get("WEBUI_PORT", 8081)), "/", True),
    "api": (int(os.environ.get("API_PORT", 8082)), "/get_sys_info", False),
    "live2d": (int(os.environ.get("LIVE2D_PORT", 12345)), "/Live2D/stage-landscape.html", False),
    "stage": (int(os.environ.get("CAPTION_PORT", 5500)), "/health", True),
}


def probe(port, path, timeout=4.0):
    """先看端口，再看 HTTP。端口不通就不再发请求。"""
    with socket.socket() as sock:
        sock.settimeout(timeout)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return False, "port closed"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return resp.status < 500, f"HTTP {resp.status}"
    except Exception as error:  # noqa: BLE001 - 探活只要结论
        return False, str(error)[:120]


def collect():
    result = {}
    for name, (port, path, required) in SERVICES.items():
        ok, detail = probe(port, path)
        result[name] = {"port": port, "ok": ok, "detail": detail, "required": required}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = collect()
    # 必需服务必须通；可选服务（主程序还没被「一键运行」拉起）不通不算不健康
    healthy = all(item["ok"] for item in result.values() if item["required"])

    if args.json:
        print(json.dumps({"healthy": healthy, "services": result}, ensure_ascii=False))
    elif not args.quiet:
        for name, item in result.items():
            flag = "OK  " if item["ok"] else ("FAIL" if item["required"] else "IDLE")
            print(f"[{flag}] {name:<8} :{item['port']:<6} {item['detail']}")
        print("HEALTHY" if healthy else "UNHEALTHY")

    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
