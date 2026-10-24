#!/usr/bin/env python3
"""
Fetches VPN Gate's official CSV (has country info, OpenVPN configs & live SoftEther hosts)
and supplementary SSTP host lists, resolves unknown endpoints via DNS & GeoIP,
and outputs:
  - sstp:// links labeled with country:
      sstp_links.txt, docs/sstp_links.txt
  - ovpn:// links labeled with country:
      ovpn-repo.txt, ovpn_links.txt, openvpn_links.txt (and in docs/)
  - OVPN display names & endpoints:
      ovpn-repo-names.txt (and in docs/)
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
LOCAL_CSV_CACHE = "vpngate_cache.csv"

# Configure direct opener to bypass stale/broken local system proxies
direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(direct_opener)


def fetch(url, data=None, headers=None, timeout=15):
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


def parse_ovpn_profile(b64_str):
    """
    Decodes the OpenVPN Base64 config, validates it, and extracts metadata.
    Returns (norm_b64, remote_host, remote_port, proto) or None if invalid.
    """
    try:
        clean = "".join(b64_str.split())
        raw_bytes = base64.b64decode(clean, validate=False)
        if not raw_bytes or b"remote " not in raw_bytes:
            return None

        norm_b64 = base64.b64encode(raw_bytes).decode("ascii")
        text = raw_bytes.decode("utf-8", errors="replace")

        remote_host = None
        remote_port = "443"
        proto = "udp"

        for line in text.splitlines():
            s = line.strip()
            if s.startswith("remote ") and remote_host is None:
                parts = s.split()
                if len(parts) >= 3:
                    remote_host, remote_port = parts[1], parts[2]
                    if len(parts) >= 4:
                        proto = parts[3].lower()
            elif s.startswith("proto "):
                parts = s.split()
                if len(parts) >= 2:
                    proto = parts[1].lower()

        return norm_b64, remote_host or "server", remote_port, proto
    except Exception:
        return None


def fetch_and_parse_csv():
    """
    Fetches the VPN Gate CSV (with local disk fallback), parses country mapping,
    extracts active server hostnames for SSTP and OpenVPN server profiles.
    """
    country_map = {}
    ovpn_raw_list = []
    csv_servers = []
    raw = None

    try:
        raw = fetch(CSV_URL)
        # Save local copy for offline/fallback use
        try:
            with open(LOCAL_CSV_CACHE, "w", encoding="utf-8") as f:
                f.write(raw)
        except Exception:
            pass
    except Exception as e:
        print(f"Warning: Failed to fetch live VPN Gate CSV: {e}")
        if os.path.exists(LOCAL_CSV_CACHE):
            print(f"Loading cached CSV from {LOCAL_CSV_CACHE}...")
            try:
                with open(LOCAL_CSV_CACHE, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as read_err:
                print(f"Warning: Failed to read local CSV cache: {read_err}")

    if not raw:
        return country_map, ovpn_raw_list, csv_servers

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

        if host and not host.startswith("*"):
            csv_servers.append({
                "host": host,
                "ip": ip,
                "country_long": country_long,
                "country_short": country_short,
            })
            if country_long != "Unknown":
                country_map[host] = (country_long, country_short)
            if ip and country_long != "Unknown":
                country_map[ip] = (country_long, country_short)

        b64 = (row.get("OpenVPN_ConfigData_Base64") or "").strip()
        if b64 and b64 != "*":
            ovpn_raw_list.append({
                "host": host,
                "ip": ip,
                "country_long": country_long,
                "country_short": country_short,
                "b64": b64,
            })

    return country_map, ovpn_raw_list, csv_servers


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


def write_file_multi(rel_path, content):
    """
    Writes content to root rel_path and also to docs/rel_path if docs exists.
    """
    with open(rel_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    if os.path.isdir("docs"):
        docs_path = os.path.join("docs", rel_path)
        os.makedirs(os.path.dirname(docs_path), exist_ok=True)
        with open(docs_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)


def main():
    socket.setdefaulttimeout(3)
    cache = load_cache()
    country_map, ovpn_raw_list, csv_servers = fetch_and_parse_csv()
    print(f"Loaded {len(country_map)} country mappings, {len(csv_servers)} live SoftEther servers and {len(ovpn_raw_list)} OpenVPN candidates from VPN Gate CSV")

    # Update cache with live CSV data
    for k, v in country_map.items():
        cache[k] = list(v)

    # ----------------------------------------------------
    # 1. Prepare SSTP hosts:
    #    First, collect directly from live VPN Gate CSV (always fresh!)
    # ----------------------------------------------------
    sstp_hosts_dict = {}  # normalized key -> (hostname, port, country_long, country_short)
    hosts_to_resolve = {}  # hostname -> ip

    for item in csv_servers:
        host = item["host"]
        if not host:
            continue
        domain = f"{host}.opengw.net" if ".opengw.net" not in host else host
        norm_key = domain.lower()
        sstp_hosts_dict[norm_key] = (domain, "443", item["country_long"], item["country_short"])

    # ----------------------------------------------------
    # Next, merge supplementary hosts from HOSTS_URL (if reachable)
    # ----------------------------------------------------
    try:
        hosts_raw = fetch(HOSTS_URL)
        for line in hosts_raw.splitlines():
            line = line.strip().replace("\r", "")
            # Skip comments, section headers, or empty lines
            if not line or line.startswith("#") or line.startswith("=") or "===" in line:
                continue
            if ":" in line:
                hostname, port = line.split(":", 1)
            else:
                hostname, port = line, "443"

            hostname = hostname.strip()
            port = port.strip()
            if not hostname or "." not in hostname:
                continue

            norm_key = hostname.lower()
            if norm_key in sstp_hosts_dict:
                # If custom non-standard port was specified in hosts list, update port
                if port != "443":
                    orig_h, _, c_l, c_s = sstp_hosts_dict[norm_key]
                    sstp_hosts_dict[norm_key] = (orig_h, port, c_l, c_s)
            else:
                # New host not in CSV, add it
                key = hostname.split(".")[0]
                c_l, c_s = ("Unknown", "??")
                if key in cache:
                    c_l, c_s = cache[key]
                elif hostname in cache:
                    c_l, c_s = cache[hostname]
                elif key in country_map:
                    c_l, c_s = country_map[key]

                sstp_hosts_dict[norm_key] = (hostname, port, c_l, c_s)

                # If country still unknown, schedule for DNS/GeoIP resolution
                if c_l == "Unknown" and key not in cache and hostname not in cache:
                    try:
                        ip = socket.gethostbyname(hostname)
                        hosts_to_resolve[hostname] = ip
                    except Exception:
                        pass
    except Exception as e:
        print(f"Warning: Failed to fetch supplementary SSTP hosts: {e}")

    # ----------------------------------------------------
    # 2. Check any unresolved OpenVPN server IPs
    # ----------------------------------------------------
    for item in ovpn_raw_list:
        c_long = item["country_long"]
        ip = item["ip"]
        if (not c_long or c_long == "Unknown") and ip and ip not in cache:
            hosts_to_resolve[ip] = ip

    # ----------------------------------------------------
    # 3. Resolve unknown hosts/IPs via GeoIP batch
    # ----------------------------------------------------
    if hosts_to_resolve:
        print(f"Resolving {len(hosts_to_resolve)} unknown endpoints via DNS & GeoIP batch...")
        unique_ips = list(set(hosts_to_resolve.values()))
        ip_geo_map = resolve_unknown_ips_batch(unique_ips)

        for endpoint, ip in hosts_to_resolve.items():
            if ip in ip_geo_map:
                c_long, c_short = ip_geo_map[ip]
                key = endpoint.split(".")[0]
                cache[key] = [c_long, c_short]
                cache[endpoint] = [c_long, c_short]
                cache[ip] = [c_long, c_short]

    # ----------------------------------------------------
    # 4. Generate SSTP links
    # ----------------------------------------------------
    sstp_lines = []
    sstp_unknown = 0

    for norm_key, (hostname, port, country_long, country_short) in sstp_hosts_dict.items():
        key = hostname.split(".")[0]
        if not country_long or country_long == "Unknown":
            if key in cache:
                country_long, country_short = cache[key]
            elif hostname in cache:
                country_long, country_short = cache[hostname]
            elif key in country_map:
                country_long, country_short = country_map[key]
            else:
                country_long, country_short = ("Unknown", "??")
                sstp_unknown += 1

        payload = f"{hostname}:{port}@vpn:vpn"
        encoded = base64.b64encode(payload.encode()).decode()
        sstp_lines.append(f"{country_long} ({country_short}) - sstp://{encoded}")

    sstp_lines.sort()

    write_file_multi("sstp_links.txt", "\n".join(sstp_lines) + "\n")
    print(f"[SSTP] Wrote {len(sstp_lines)} entries (Unknown: {sstp_unknown}, Resolved: {len(sstp_lines) - sstp_unknown})")

    # ----------------------------------------------------
    # 5. Generate OpenVPN links (ovpn://)
    # ----------------------------------------------------
    ovpn_items = []
    for item in ovpn_raw_list:
        parsed = parse_ovpn_profile(item["b64"])
        if not parsed:
            continue
        norm_b64, remote_host, remote_port, proto = parsed

        # Resolve country
        c_long = item["country_long"]
        c_short = item["country_short"]
        if not c_long or c_long == "Unknown":
            if item["host"] in cache:
                c_long, c_short = cache[item["host"]]
            elif item["ip"] in cache:
                c_long, c_short = cache[item["ip"]]
            elif remote_host in cache:
                c_long, c_short = cache[remote_host]

        if not c_long or c_long == "Unknown":
            c_long, c_short = ("Unknown", "??")

        ovpn_items.append({
            "country_long": c_long,
            "country_short": c_short,
            "norm_b64": norm_b64,
            "remote_host": remote_host,
            "remote_port": remote_port,
            "proto": proto,
        })

    # Sort OpenVPN entries alphabetically by country, then host
    ovpn_items.sort(key=lambda x: (x["country_long"], x["country_short"], x["remote_host"]))

    ovpn_links = []
    ovpn_names = []
    for idx, item in enumerate(ovpn_items, 1):
        c_label = f"{item['country_long']} ({item['country_short']})"
        ovpn_links.append(f"{c_label} - ovpn://{item['norm_b64']}")
        display_name = f"OVPN-{item['remote_host']}-{idx}"
        ovpn_names.append(f"{display_name} | {c_label} | {item['remote_host']}:{item['remote_port']}/{item['proto']}")

    ovpn_content = "\n".join(ovpn_links) + "\n"
    ovpn_names_content = "\n".join(ovpn_names) + "\n"

    # Write ovpn-repo.txt (matching f:\cloud\pur\rea-work\ovpn-link\ovpn-repo.txt)
    write_file_multi("ovpn-repo.txt", ovpn_content)
    # Write ovpn_links.txt & openvpn_links.txt for naming consistency with sstp_links.txt
    write_file_multi("ovpn_links.txt", ovpn_content)
    write_file_multi("openvpn_links.txt", ovpn_content)
    # Write ovpn-repo-names.txt companion file
    write_file_multi("ovpn-repo-names.txt", ovpn_names_content)

    print(f"[OpenVPN] Wrote {len(ovpn_links)} entries to ovpn-repo.txt, ovpn_links.txt, and docs/")
    print(f"[OpenVPN] Wrote {len(ovpn_names)} entries to ovpn-repo-names.txt")

    # ----------------------------------------------------
    # 6. Save cache
    # ----------------------------------------------------
    save_cache(cache)


if __name__ == "__main__":
    main()
