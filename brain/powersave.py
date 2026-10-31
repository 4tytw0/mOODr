"""Wi-Fi power save on the desktop only while m00Dr's transport is stopped.

With power save on, the Wi-Fi card dozes between the access point's beacons
(~100 ms), and the web stream of the mix stalls whenever another device
(the iPad waking, say) makes the AP buffer traffic for it. So this follows
m00Dr's `playing` over its web remote and switches power save off on Play,
back on on Stop and when m00Dr goes away.

`iw` needs root: systemd/m800dr-powersave.sudoers lets this run exactly those
two commands through `sudo -n`. Exits when the connection drops (m00Dr
restarted), so systemd retries; the unit's ExecStopPost turns power save back
on however this ends.
"""

import argparse
import asyncio
import json
import subprocess

import websockets


def set_power_save(iface: str, on: bool) -> None:
    state = "on" if on else "off"
    result = subprocess.run(["sudo", "-n", "/usr/bin/iw", "dev", iface, "set", "power_save", state],
                            capture_output=True, text=True)
    if result.returncode:
        print(f"couldn't set power save {state}: {result.stderr.strip()}", flush=True)
    else:
        print(f"power save {state}", flush=True)


async def follow(args) -> None:
    uri = f"ws://{args.host}:{args.port}"
    async with websockets.connect(uri) as ws:
        print(f"connected to m00Dr at {uri}", flush=True)
        playing = None
        async for text in ws:
            data = json.loads(text)
            if "playing" not in data:
                continue
            now = bool(data["playing"])
            if now != playing:
                set_power_save(args.iface, on=not now)
                playing = now


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--iface", default="wlan0")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(follow(args))
    except (ConnectionRefusedError, OSError, websockets.ConnectionClosed) as e:
        print(f"can't reach m00Dr's web remote ({e})", flush=True)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        set_power_save(args.iface, on=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
