#!/usr/bin/env python3
"""
Fetches VPN Gate's official CSV (has country info) and the SSTP host list,
matches them by hostname, resolves unknown hosts via DNS & GeoIP,
and outputs sstp:// links labeled with country.
"""
import base64
import csv
import json
import os
import socket
import urllib.request

CSV_URL = "http://www.vpngate.net/api/iphone/"
HOSTS_URL = "https://raw.githubusercontent.com/Delta-Kronecker/Vpn-Gate/refs/heads/main/sstp_hosts.txt"
CACHE_FILE = "country_cache.json"

# Configure direct opener to bypass stale/broken local system proxies
direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(direct_opener)

def fetch(url, data=None, headers=None, timeout=25):
    """
    Fetches URL content using direct connection.
    """
    req_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    
    with direct_opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load cache: {e}")
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save cache: {e}")

def build_country_map_from_csv():
    country_map = {}
    try:
        raw = fetch(CSV_URL)
        lines = raw.splitlines()
        start = 0
        for i, line in enumerate(lines):
            if line.startswith("#HostName"):
                start = i
                break
        reader = csv.DictReader(lines[start:])
        for row in reader:
            host = (row.get("#HostName") or "").strip()
            ip = (row.get("IP") or "").strip()
            country_long = (row.get("CountryLong") or "Unknown").strip()
            country_short = (row.get("CountryShort") or "??").strip()
            if host and country_long != "Unknown":
                country_map[host] = (country_long, country_short)
            if ip and country_long != "Unknown":
                country_map[ip] = (country_long, country_short)
    except Exception as e:
        print(f"Warning: Failed to fetch/parse VPN Gate CSV: {e}")
    return country_map

def resolve_unknown_ips_batch(ips):
    """
    Resolves a list of IP addresses to countries using ip-api batch API in chunks of up to 100.
    """
    results = {}
    if not ips:
        return results

    chunk_size = 100
    for i in range(0, len(ips), chunk_size):
        chunk = ips[i:i + chunk_size]
        payload = json.dumps([{"query": ip, "fields": "status,country,countryCode,query"} for ip in chunk]).encode("utf-8")
        try:
            raw = fetch(
                "http://ip-api.com/batch",
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            data = json.loads(raw)
            for item in data:
                if item.get("status") == "success" and item.get("country"):
                    results[item.get("query")] = (item.get("country"), item.get("countryCode", "??"))
        except Exception as e:
            print(f"Warning: Batch GeoIP request failed: {e}")
    return results

def main():
    cache = load_cache()
    country_map = build_country_map_from_csv()
    print(f"Loaded {len(country_map)} entries from live VPN Gate CSV")

    # Update cache with live CSV data
    for k, v in country_map.items():
        cache[k] = list(v)

    # Fetch SSTP host list
    hosts_raw = fetch(HOSTS_URL)
    hosts_entries = []
    hosts_to_resolve = {}  # hostname -> ip

    for line in hosts_raw.splitlines():
        line = line.strip().replace("\r", "")
        if not line:
            continue
        if ":" in line:
            hostname, port = line.split(":", 1)
        else:
            hostname, port = line, "443"

        key = hostname.split(".")[0]
        hosts_entries.append((hostname, port, key))

        # If not known in country_map or cache, schedule for DNS + GeoIP
        if key not in cache and hostname not in cache:
            try:
                ip = socket.gethostbyname(hostname)
                hosts_to_resolve[hostname] = ip
            except Exception:
                pass

    if hosts_to_resolve:
        print(f"Resolving {len(hosts_to_resolve)} unknown hosts via DNS & GeoIP batch...")
        unique_ips = list(set(hosts_to_resolve.values()))
        ip_geo_map = resolve_unknown_ips_batch(unique_ips)

        for hostname, ip in hosts_to_resolve.items():
            if ip in ip_geo_map:
                c_long, c_short = ip_geo_map[ip]
                key = hostname.split(".")[0]
                cache[key] = [c_long, c_short]
                cache[hostname] = [c_long, c_short]
                cache[ip] = [c_long, c_short]

    output_lines = []
    unknown_count = 0

    for hostname, port, key in hosts_entries:
        # Check cache/country_map
        if key in cache:
            country_long, country_short = cache[key]
        elif hostname in cache:
            country_long, country_short = cache[hostname]
        elif key in country_map:
            country_long, country_short = country_map[key]
        else:
            country_long, country_short = ("Unknown", "??")
            unknown_count += 1

        payload = f"{hostname}:{port}@vpn:vpn"
        encoded = base64.b64encode(payload.encode()).decode()
        output_lines.append(f"{country_long} ({country_short}) - sstp://{encoded}")

    # sort alphabetically by country for readability
    output_lines.sort()

    with open("sstp_links.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    # If docs folder exists, keep docs/sstp_links.txt updated as well
    if os.path.isdir("docs"):
        with open(os.path.join("docs", "sstp_links.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines) + "\n")

    save_cache(cache)

    print(f"Wrote {len(output_lines)} entries (Unknown: {unknown_count}, Resolved: {len(output_lines) - unknown_count})")

if __name__ == "__main__":
    main()
