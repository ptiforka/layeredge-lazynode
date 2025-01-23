#!/usr/bin/env python3
import asyncio
import time
import traceback
from web3.auto import w3
from eth_account.messages import encode_defunct
import aiohttp


BASE_URL        = "https://dashboard.layeredge.io"  # The main domain
WALLET_ADDRESS  = "address"
PRIVATE_KEY     = "key"

START_ENDPOINT  = "/api/node-points/start"
UPDATE_ENDPOINT = "/api/node-points"
POLL_INTERVAL   = 5   # seconds
PROXY_FILE      = "proxy_list.txt"  # file with one http-proxy per line
TIMEOUT_SECS    = 15

def sign_message(message: str, private_key: str) -> str:
    """
    Signs the given message (Ethereum-style) and returns a hex signature.
    """
    msg_hash = encode_defunct(text=message)
    signed   = w3.eth.account.sign_message(msg_hash, private_key=private_key)
    return signed.signature.hex()

async def start_node(session: aiohttp.ClientSession, proxy_str: str):
    """
    1) Create message to sign (for node activation).
    2) Sign with the private key.
    3) POST to /api/node-points/start using the given proxy, returning server JSON or None.
    """
    now_ms = int(time.time() * 1000)
    msg    = f"Node activation request for {WALLET_ADDRESS} at {now_ms}"

    try:
        signature = sign_message(msg, PRIVATE_KEY)
    except Exception as e:
        print(f"[Proxy {proxy_str}] [!] Error signing message: {e}")
        traceback.print_exc()
        return None

    payload = {
        "walletAddress": WALLET_ADDRESS
    }

    url = f"{BASE_URL}{START_ENDPOINT}"
    print(f"[Proxy {proxy_str}] [*] Sending node start request: {url}")

    try:
        async with session.post(url, json=payload, proxy=proxy_str, timeout=TIMEOUT_SECS) as resp:
            text = await resp.text()
            print(f"[Proxy {proxy_str}] [start_node] Status code: {resp.status}")
            print(f"[Proxy {proxy_str}] [start_node] Response   : {text}")
            if resp.status != 200:
                return None

            data = {}
            try:
                data = await resp.json()
            except Exception:
                print(f"[Proxy {proxy_str}] [!] Failed to parse JSON from start_node.")
                return None

            if not data.get("success"):
                print(f"[Proxy {proxy_str}] [!] success=False from start_node. Data: {data}")
                return None

            return data
    except Exception as ex:
        print(f"[Proxy {proxy_str}] [!] Exception in start_node(): {ex}")
        traceback.print_exc()
        return None

async def farm_loop(session: aiohttp.ClientSession, proxy_str: str, last_start_time: int):
    """
    Infinite loop sending POST to /api/node-points every POLL_INTERVAL seconds.
    Uses the same proxy. Logs responses. Runs indefinitely.
    """
    print(f"[Proxy {proxy_str}] [*] Entering infinite farm loop.")
    url = f"{BASE_URL}{UPDATE_ENDPOINT}"

    while True:
        try:
            payload = {
                "walletAddress": WALLET_ADDRESS,
                "lastStartTime": last_start_time
            }
            async with session.post(url, json=payload, proxy=proxy_str, timeout=TIMEOUT_SECS) as resp:
                text = await resp.text()
                print(f"[Proxy {proxy_str}] [FARM] {time.strftime('%Y-%m-%d %H:%M:%S')} -> "
                      f"Status code: {resp.status}")
                print(f"[Proxy {proxy_str}] [FARM] Response           : {text}")

                if resp.status != 200:
                    # Non-200 => skip processing
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                data = {}
                try:
                    data = await resp.json()
                except Exception:
                    print(f"[Proxy {proxy_str}] [!] Could not parse JSON in farm loop.")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                if not data.get("success"):
                    print(f"[Proxy {proxy_str}] [!] success=False. Data: {data}")
                else:
                    node_points = data.get("nodePoints", "N/A")
                    print(f"[Proxy {proxy_str}] [+] Node points: {node_points}")

        except Exception as e:
            print(f"[Proxy {proxy_str}] [!] Exception during farming loop: {e}")
            traceback.print_exc()

        await asyncio.sleep(POLL_INTERVAL)

async def worker(proxy_str: str):
    """
    Single proxy worker:
    1) Attempt to start node (get lastStartTime).
    2) If success, loop forever in farm_loop.
    """
    async with aiohttp.ClientSession() as session:
        start_data = await start_node(session, proxy_str)
        if not start_data:
            print(f"[Proxy {proxy_str}] [!] start_node() returned no valid data; stopping worker.")
            return

        last_start_time = start_data.get("lastStartTime")
        if last_start_time is None:
            print(f"[Proxy {proxy_str}] [!] No lastStartTime in start_node() response; stopping worker.")
            return

        # Now loop
        await farm_loop(session, proxy_str, last_start_time)

async def main():
    # Read proxies from file
    proxies = []
    try:
        with open(PROXY_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    proxies.append(line)
    except Exception as exc:
        print(f"[!] Cannot read proxy file '{PROXY_FILE}': {exc}")

    # If no proxies, just exit
    if not proxies:
        print("[!] No proxies found. Exiting.")
        return

    print(f"[+] Found {len(proxies)} proxies. Spawning workers...")

    # Launch one async task per proxy
    tasks = []
    for p in proxies:
        task = asyncio.create_task(worker(p))
        tasks.append(task)

    # Wait for all tasks (they run forever)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] KeyboardInterrupt -> Shutting down.")
    except Exception as e:
        print("[!] Unexpected exception in main():", e)
        traceback.print_exc()
