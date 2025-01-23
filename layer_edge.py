import asyncio
import time
import traceback
from web3.auto import w3
from eth_account.messages import encode_defunct
import aiohttp

# ------------------------------------------------------------------------------
# HARDCODED CONFIG (AS REQUESTED)
# ------------------------------------------------------------------------------
BASE_URL        = "https://dashboard.layeredge.io"
WALLET_ADDRESS  = "address"
PRIVATE_KEY     = "key"

START_ENDPOINT  = "/api/node-points/start"
UPDATE_ENDPOINT = "/api/node-points"
POLL_INTERVAL   = 5   # seconds between farming attempts
PROXY_FILE      = "proxy_list.txt"  # one http-proxy per line
TIMEOUT_SECS    = 15


def sign_message(message: str, private_key: str) -> str:
    """
    Signs the given message (Ethereum-style) and returns a hex signature.
    """
    msg_hash = encode_defunct(text=message)
    signed   = w3.eth.account.sign_message(msg_hash, private_key=private_key)
    return signed.signature.hex()


async def attempt_start_node(session: aiohttp.ClientSession, proxy_str: str) -> int:
    """
    Attempt to call /api/node-points/start once.
    If successful, return the lastStartTime from the server.
    If not, return None or raise an error. 
    We do NOT loop here; we just do one attempt.
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
        # If required: "signature": signature
    }

    url = f"{BASE_URL}{START_ENDPOINT}"
    print(f"[Proxy {proxy_str}] [*] Sending node start request: {url}")

    try:
        async with session.post(
            url,
            json=payload,
            proxy=proxy_str,
            timeout=TIMEOUT_SECS
        ) as resp:
            text = await resp.text()
            print(f"[Proxy {proxy_str}] [start_node] Status code: {resp.status}")
            print(f"[Proxy {proxy_str}] [start_node] Response   : {text}")

            # If not 200, treat as fail
            if resp.status != 200:
                return None

            # Attempt to parse JSON
            try:
                data = await resp.json()
            except Exception:
                print(f"[Proxy {proxy_str}] [!] Failed to parse JSON.")
                return None

            # Check success
            if not data.get("success"):
                print(f"[Proxy {proxy_str}] [!] success=False. data: {data}")
                return None

            # Return the lastStartTime
            return data.get("lastStartTime")
    except Exception as ex:
        print(f"[Proxy {proxy_str}] [!] Exception in start_node: {ex}")
        traceback.print_exc()
        return None


async def do_farm_once(
    session: aiohttp.ClientSession, 
    proxy_str: str, 
    last_start_time: int
) -> bool:
    """
    Perform ONE farming request to /api/node-points using the given lastStartTime.
    Returns True if the request was successful (status=200, success=True),
    otherwise False. 
    """
    url = f"{BASE_URL}{UPDATE_ENDPOINT}"
    payload = {
        "walletAddress":  WALLET_ADDRESS,
        "lastStartTime":  last_start_time
    }

    try:
        async with session.post(
            url, 
            json=payload, 
            proxy=proxy_str, 
            timeout=TIMEOUT_SECS
        ) as resp:
            text = await resp.text()
            now_str = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"[Proxy {proxy_str}] [FARM] {now_str} -> Status code: {resp.status}")
            print(f"[Proxy {proxy_str}] [FARM] Response       : {text}")

            if resp.status != 200:
                return False

            # Parse JSON
            try:
                data = await resp.json()
            except Exception:
                print(f"[Proxy {proxy_str}] [!] Could not parse JSON in farm_once.")
                return False

            if not data.get("success"):
                print(f"[Proxy {proxy_str}] [!] success=False. data: {data}")
                return False

            # success -> log nodePoints
            node_points = data.get("nodePoints", "N/A")
            print(f"[Proxy {proxy_str}] [+] Node points: {node_points}")
            return True

    except Exception as e:
        print(f"[Proxy {proxy_str}] [!] Exception in do_farm_once: {e}")
        traceback.print_exc()
        return False


async def worker(proxy_str: str):
    """
    The worker for a single proxy that NEVER stops:
     - Repeatedly tries to start the node until we get a valid lastStartTime.
     - Then we do farming requests in a loop.
       * If a farm request fails, or returns an error code that suggests 
         we need to re-login (like 400 or "Database error"), 
         we break out and attempt start_node again.
    This loop runs forever.
    """
    async with aiohttp.ClientSession() as session:
        while True:
            # 1) Keep re-trying to start node until we get a lastStartTime
            last_start_time = None
            while last_start_time is None:
                last_start_time = await attempt_start_node(session, proxy_str)
                if last_start_time is None:
                    print(f"[Proxy {proxy_str}] [!] start_node failed. Retrying in {POLL_INTERVAL}s...")
                    await asyncio.sleep(POLL_INTERVAL)

            print(f"[Proxy {proxy_str}] [*] Got lastStartTime={last_start_time}. Farming loop starts.")
            
            # 2) Farming loop
            while True:
                success = await do_farm_once(session, proxy_str, last_start_time)
                if not success:
                    # If farm request fails, we break and re-start node
                    print(f"[Proxy {proxy_str}] [!] Farm request failed. Will re-start node.")
                    break
                await asyncio.sleep(POLL_INTERVAL)


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
