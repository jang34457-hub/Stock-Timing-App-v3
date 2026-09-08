"""PC에서 스마트폰이 붙을 수 있게 0.0.0.0 으로 API를 연다."""

from __future__ import annotations

import socket

import uvicorn


def lan_ips() -> list[str]:
    found: list[str] = []
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    if not found:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            found.append(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    return found


def main() -> None:
    ips = lan_ips() or ["(LAN IP를 확인하세요)"]
    print("Stock Timing API  (스마트폰은 127.0.0.1 이 아니라 아래 주소를 쓰세요)")
    for ip in ips:
        print(f"  http://{ip}:8000")
    print("설정 → 서버 연결에 위 URL을 넣고 '서버 연결 확인'을 누르세요.")
    print("Windows 방화벽에서 Python/8000 포트 허용이 필요할 수 있습니다.")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
