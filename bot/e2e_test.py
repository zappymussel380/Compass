"""
End-to-end test: take a raw user message, run it through the full pipeline,
push the result to Firefly III. Print everything along the way.

Usage: python3 e2e_test.py "spent 700 on movie at pvr with rewards personal"
"""

import json
import os
import sys
import requests
from dotenv import load_dotenv

from accounts import resolve_account
from firefly_client import FireflyClient, FireflyError

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def call_llm(message: str) -> dict:
    """Send a user message to Ollama with our system prompt; return parsed JSON."""
    with open("system_prompt.txt") as f:
        system_prompt = f.read()

    resp = requests.post(
        f"{os.environ['OLLAMA_URL']}/api/chat",
        json={
            "model": os.environ["OLLAMA_MODEL"],
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return json.loads(content)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 e2e_test.py \"<your expense message>\"")
        sys.exit(1)

    user_message = sys.argv[1]
    print(f"\n📥 USER:  {user_message}")
    print("─" * 60)

    # Step 1: LLM extraction
    print("🧠 Calling LLM...")
    parsed = call_llm(user_message)
    print(f"📋 LLM output:\n{json.dumps(parsed, indent=2)}")
    print("─" * 60)

    # Bail early if model said this isn't a transaction
    if parsed.get("type") == "unknown":
        print("⏭  Not a transaction. Would route to todo handler. Stopping.")
        return

    # Step 2: Resolve source account
    src_status, src_value = resolve_account(parsed.get("source_raw") or "")
    print(f"🔎 source_raw: {parsed.get('source_raw')!r:30} → {src_status}: {src_value}")

    # Deposits can have an unresolved source — that's the external payer,
    # and Firefly will auto-create a revenue account for it.
    if src_status != "match" and parsed["type"] != "deposit":
        print(f"⏸  Source needs confirmation. Bot would prompt user. Stopping.")
        return

    # Step 3: Resolve destination — only for transfers (deposits + withdrawals
    # use the destination_raw as a free-text payer/merchant)
    dst_canonical = None
    if parsed["type"] == "transfer":
        dst_status, dst_value = resolve_account(parsed.get("destination_raw") or "")
        print(f"🔎 destination_raw: {parsed.get('destination_raw')!r:25} → {dst_status}: {dst_value}")
        if dst_status != "match":
            print(f"⏸  Destination needs confirmation. Stopping.")
            return
        dst_canonical = dst_value
    elif parsed["type"] == "deposit":
        # For deposits, destination_raw is the user's account
        dst_status, dst_value = resolve_account(parsed.get("destination_raw") or "")
        print(f"🔎 destination_raw: {parsed.get('destination_raw')!r:25} → {dst_status}: {dst_value}")
        if dst_status != "match":
            print(f"⏸  Destination needs confirmation. Stopping.")
            return
        dst_canonical = dst_value

    print("─" * 60)

    # Step 4: Push to Firefly
    print("🚀 Pushing to Firefly...")
    client = FireflyClient()
    try:
        result = client.create_transaction(
            parsed=parsed,
            source_canonical=src_value,
            destination_canonical=dst_canonical,
        )
        txn_id = result["data"]["id"]
        print(f"✅ Transaction created. ID: {txn_id}")
        base_url = os.environ.get("FIREFLY_PUBLIC_URL") or os.environ.get("FIREFLY_APP_URL")
        if base_url:
            print(f"   View at: {base_url.rstrip('/')}/transactions/show/{txn_id}")
    except FireflyError as e:
        print(f"❌ Firefly error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
