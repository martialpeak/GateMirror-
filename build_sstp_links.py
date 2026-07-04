#!/usr/bin/env python3
"""
Fetches VPN Gate's official CSV (has country info) and the SSTP host list,
matches them by hostname, and outputs sstp:// links labeled with country.
"""
import csv
import base64
import re
import urllib.request

CSV_URL = "http://www.vpngate.net/api/iphone/"
HOSTS_URL = "https://raw.githubusercontent.com/Delta-Kronecker/Vpn-Gate/refs/heads/main/sstp_hosts.txt"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")

def build_country_map():
    raw = fetch(CSV_URL)
    lines = raw.splitlines()
    # first line is a "*vpn_servers" message line, second is the header
    # find the header line (starts with #HostName)
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("#HostName"):
            start = i
            break
    reader = csv.DictReader(lines[start:])
    country_map = {}
    for row in reader:
        host = row.get("#HostName", "").strip()
        if not host:
            continue
        country_map[host] = (row.get("CountryLong", "Unknown").strip(),
                              row.get("CountryShort", "??").strip())
    return country_map

def main():
    country_map = build_country_map()
    hosts_raw = fetch(HOSTS_URL)

    output_lines = []
    for line in hosts_raw.splitlines():
        line = line.strip().replace("\r", "")
        if not line:
            continue
        if ":" in line:
            hostname, port = line.split(":", 1)
        else:
            hostname, port = line, "443"

        # hostname looks like "vpn847712325.opengw.net" -> key is "vpn847712325"
        key = hostname.split(".")[0]
        country_long, country_short = country_map.get(key, ("Unknown", "??"))

        payload = f"{hostname}:{port}@vpn:vpn"
        encoded = base64.b64encode(payload.encode()).decode()
        output_lines.append(f"{country_long} ({country_short}) - sstp://{encoded}")

    # sort alphabetically by country for readability
    output_lines.sort()

    with open("sstp_links.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"Wrote {len(output_lines)} entries")

if __name__ == "__main__":
    main()
