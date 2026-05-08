"""Generate Polymarket CLOB API credentials.
Run locally: python gen_api_key.py
Requires: pip install py-clob-client
"""
import os
import json
from py_clob_client.client import ClobClient

POLY_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

def main():
    pk = os.environ.get("POLY_PRIVATE_KEY")
    if not pk:
        pk = input("Enter your wallet private key (won't be stored): ").strip()

    client = ClobClient(POLY_HOST, key=pk, chain_id=CHAIN_ID)
    creds = client.create_or_derive_api_creds()

    print("\n=== Your CLOB API Credentials ===")
    print(f"API Key:    {creds.api_key}")
    print(f"Secret:     {creds.api_secret}")
    print(f"Passphrase: {creds.api_passphrase}")
    print("\nSave these to .env file. Secret and passphrase cannot be recovered.")

if __name__ == "__main__":
    main()
