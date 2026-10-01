import csv
import ipaddress
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

from dotenv import load_dotenv
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkdns.v2 import DnsClient, UpdateRecordSetReq, UpdateRecordSetRequest
from huaweicloudsdkdns.v2.region.dns_region import DnsRegion


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 加载当前目录的 .env；如果当前目录没有密钥，再兼容读取上一级目录的 .env。
load_dotenv()
if not os.environ.get("HUAWEI_AK"):
    load_dotenv(dotenv_path=os.path.join(BASE_DIR, "..", ".env"))


def env_int(name, default):
    value = os.environ.get(name, "")
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} 必须是整数，当前值：{value}")


def env_float(name, default):
    value = os.environ.get(name, "")
    if value == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} 必须是数字，当前值：{value}")


def env_bool(name, default=False):
    value = os.environ.get(name, "")
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def abs_path(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(BASE_DIR, path))


AK = os.environ.get("HUAWEI_AK", "")
SK = os.environ.get("HUAWEI_SK", "")
HUAWEI_REGION = os.environ.get("HUAWEI_REGION", "ap-southeast-1")

# 飞书 Webhook 使用本项目独立变量名。
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL_CFST", "")

IP_SOURCE = os.environ.get("IP_SOURCE", "cfhub").strip().lower()
CFHUB_POOLS_URL = os.environ.get(
    "CFHUB_POOLS_URL", "https://cfhub.1molchuan.top/api/v1/pools"
)
CFHUB_MAX_LATENCY_MS = env_int("CFHUB_MAX_LATENCY_MS", 500)
CFHUB_UPDATE_INTERVAL_SECONDS = env_int("CFHUB_UPDATE_INTERVAL_SECONDS", 300)

DNS_ZONE_ID = os.environ.get("DNS_ZONE_ID", "")
DNS_RECORDSET_ID = os.environ.get("DNS_RECORDSET_ID", "")
DNS_RECORD_NAME = os.environ.get("DNS_RECORD_NAME", "")
DNS_RECORD_TYPE = os.environ.get("DNS_RECORD_TYPE", "A").upper()
DNS_RECORD_COUNT = env_int("DNS_RECORD_COUNT", 1)
DNS_TTL = env_int("DNS_TTL", 300)
DNS_RECORDS_FILE = abs_path(os.environ.get("DNS_RECORDS_FILE", "dns_records.json"))

CFST_BINARY = os.environ.get("CFST_BINARY", "")
CFST_WORKDIR = os.environ.get("CFST_WORKDIR", "")
CFST_RESULT_CSV = abs_path(os.environ.get("CFST_RESULT_CSV", "cfst_result.csv"))
CFST_DOWNLOAD_COUNT = env_int("CFST_DOWNLOAD_COUNT", 10)
CFST_DOWNLOAD_SECONDS = env_int("CFST_DOWNLOAD_SECONDS", 10)
CFST_MAX_LATENCY_MS = env_float("CFST_MAX_LATENCY_MS", 9999)
CFST_MIN_SPEED_MB = env_float("CFST_MIN_SPEED_MB", 0.01)
CFST_EXTRA_ARGS = os.environ.get("CFST_EXTRA_ARGS", "")
CFST_TIMEOUT_SECONDS = env_int("CFST_TIMEOUT_SECONDS", 3600)

DRY_RUN = env_bool("DRY_RUN", False)


# ================= CloudflareSpeedTest 调用 =================


def find_cfst_binary():
    if CFST_BINARY:
        candidate = abs_path(CFST_BINARY)
        if os.path.exists(candidate):
            return candidate
        path_candidate = shutil.which(CFST_BINARY)
        if path_candidate:
            return path_candidate
        raise FileNotFoundError(f"未找到 CFST_BINARY 指定的文件：{CFST_BINARY}")

    candidates = []
    if platform.system().lower().startswith("win"):
        candidates.append(os.path.join(BASE_DIR, "cfst_windows_amd64", "cfst.exe"))
        candidates.append(os.path.join(BASE_DIR, "CloudflareSpeedTest", "cfst.exe"))
    else:
        candidates.append(os.path.join(BASE_DIR, "cfst_linux_amd64", "cfst"))
        candidates.append(os.path.join(BASE_DIR, "CloudflareSpeedTest", "cfst"))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    path_candidate = shutil.which("cfst")
    if path_candidate:
        return path_candidate

    raise FileNotFoundError("未找到 CloudflareSpeedTest 可执行文件，请在 .env 中设置 CFST_BINARY。")


def command_workdir(cfst_binary):
    if CFST_WORKDIR:
        workdir = abs_path(CFST_WORKDIR)
    elif os.path.isabs(cfst_binary):
        workdir = os.path.dirname(cfst_binary)
    else:
        workdir = BASE_DIR

    if not os.path.isdir(workdir):
        raise FileNotFoundError(f"未找到 CFST_WORKDIR 指定的目录：{workdir}")
    return workdir


def format_number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_cfst_command(cfst_binary):
    cmd = [
        cfst_binary,
        "-dn",
        str(CFST_DOWNLOAD_COUNT),
        "-dt",
        str(CFST_DOWNLOAD_SECONDS),
        "-tl",
        format_number(CFST_MAX_LATENCY_MS),
        "-sl",
        format_number(CFST_MIN_SPEED_MB),
    ]

    if CFST_EXTRA_ARGS.strip():
        cmd.extend(shlex.split(CFST_EXTRA_ARGS))

    # 固定把结果写入脚本指定的 CSV，避免附加参数覆盖输出路径。
    cmd.extend(["-p", "0", "-o", CFST_RESULT_CSV])
    return cmd


def run_cfst():
    cfst_binary = find_cfst_binary()
    workdir = command_workdir(cfst_binary)
    cmd = build_cfst_command(cfst_binary)

    if os.path.exists(CFST_RESULT_CSV):
        os.remove(CFST_RESULT_CSV)

    print("正在运行 CloudflareSpeedTest...")
    print(f"  可执行文件：{cfst_binary}")
    print(f"  工作目录：{workdir}")
    print(f"  结果文件：{CFST_RESULT_CSV}")
    print(f"  执行命令：{subprocess.list2cmdline(cmd)}")

    completed = subprocess.run(
        cmd,
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CFST_TIMEOUT_SECONDS,
    )

    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip())

    if completed.returncode != 0:
        raise RuntimeError(f"CloudflareSpeedTest 退出码异常：{completed.returncode}")

    if not os.path.exists(CFST_RESULT_CSV):
        raise FileNotFoundError(f"未找到 CFST 结果 CSV：{CFST_RESULT_CSV}")

    return parse_cfst_result(CFST_RESULT_CSV)


# ================= CFST 结果解析 =================


def read_csv_rows(path):
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", newline="", encoding=encoding) as f:
                return list(csv.reader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error


def parse_cfst_result(path):
    rows = read_csv_rows(path)
    parsed = []

    for row in rows[1:]:
        if not row:
            continue
        ip = row[0].strip()
        if not ip:
            continue
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue

        if DNS_RECORD_TYPE == "A" and ip_obj.version != 4:
            continue
        if DNS_RECORD_TYPE == "AAAA" and ip_obj.version != 6:
            continue

        parsed.append(
            {
                "ip": ip,
                "sent": row[1].strip() if len(row) > 1 else "",
                "received": row[2].strip() if len(row) > 2 else "",
                "loss": row[3].strip() if len(row) > 3 else "",
                "latency_ms": row[4].strip() if len(row) > 4 else "",
                "speed_mb_s": row[5].strip() if len(row) > 5 else "",
                "colo": row[6].strip() if len(row) > 6 else "",
            }
        )

    limit = max(DNS_RECORD_COUNT, 1)
    selected = parsed[:limit]

    if not selected:
        raise RuntimeError(f"CFST 结果 CSV 中没有可用的 {DNS_RECORD_TYPE} 记录。")

    print("\n已选择的优选 IP：")
    for item in selected:
        suffix = ""
        if item["latency_ms"] or item["speed_mb_s"] or item["colo"]:
            suffix = (
                f" 延迟={item['latency_ms']}ms"
                f" 速度={item['speed_mb_s']}MB/s"
                f" 机房={item['colo']}"
            )
        print(f"  {item['ip']}{suffix}")

    return selected


# ================= 配置校验 =================


def load_dns_records():
    """读取需要同步的华为云 DNS 记录集列表；优先使用 JSON 文件，未配置时兼容单记录环境变量。"""
    if DNS_RECORDS_FILE and os.path.exists(DNS_RECORDS_FILE):
        with open(DNS_RECORDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            records = data.get("records", [])
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError("DNS 记录配置文件必须是数组，或包含 records 数组的对象。")

        if not isinstance(records, list):
            raise ValueError("DNS 记录配置文件中的 records 必须是数组。")

        return [normalize_dns_record(item, idx + 1) for idx, item in enumerate(records)]

    if DNS_ZONE_ID or DNS_RECORDSET_ID or DNS_RECORD_NAME:
        return [
            normalize_dns_record(
                {
                    "name": DNS_RECORD_NAME,
                    "zone_id": DNS_ZONE_ID,
                    "recordset_id": DNS_RECORDSET_ID,
                    "type": DNS_RECORD_TYPE,
                    "ttl": DNS_TTL,
                },
                1,
            )
        ]

    return []


def normalize_dns_record(item, index):
    """把单条 DNS 记录配置整理为统一结构，并做基础校验。"""
    if not isinstance(item, dict):
        raise ValueError(f"第 {index} 条 DNS 记录配置必须是对象。")

    record = {
        "name": str(item.get("name") or item.get("domain") or "").strip(),
        "zone_id": str(item.get("zone_id") or "").strip(),
        "recordset_id": str(item.get("recordset_id") or item.get("id") or "").strip(),
        "type": str(item.get("type") or DNS_RECORD_TYPE).strip().upper(),
        "ttl": int(item.get("ttl") or DNS_TTL),
        "cfhub_line": normalize_cfhub_line(
            item.get("cfhub_line") or item.get("line") or "cloud", index
        ),
    }

    missing = [
        key
        for key in ("name", "zone_id", "recordset_id")
        if not record[key]
    ]
    if missing:
        raise ValueError(f"第 {index} 条 DNS 记录配置缺少字段：" + ", ".join(missing))

    if record["type"] not in {"A", "AAAA"}:
        raise ValueError(f"第 {index} 条 DNS 记录配置的 type 必须是 A 或 AAAA。")

    return record


CFHUB_LINES = {
    "cmcc": "cmcc", "mobile": "cmcc", "移动": "cmcc",
    "cernet": "cernet", "教育网": "cernet",
    "cloud": "cloud", "default": "cloud", "全网默认": "cloud",
    "chinanet": "chinanet", "telecom": "chinanet", "电信": "chinanet",
    "unicom": "unicom", "联通": "unicom",
}


def normalize_cfhub_line(value, index):
    line = str(value).strip().lower()
    if line not in CFHUB_LINES:
        raise ValueError(
            f"第 {index} 条 DNS 记录线路 {value!r} 无法识别；"
            "请使用 cmcc、cernet、cloud、chinanet 或 unicom。"
        )
    return CFHUB_LINES[line]


def validate_config():
    missing = []
    if not AK:
        missing.append("HUAWEI_AK")
    if not SK:
        missing.append("HUAWEI_SK")

    if IP_SOURCE not in {"cfhub", "cfst"}:
        raise ValueError("IP_SOURCE 必须是 cfhub 或 cfst")
    if DNS_RECORD_TYPE not in {"A", "AAAA"}:
        raise ValueError("DNS_RECORD_TYPE 必须是 A 或 AAAA")
    if CFHUB_UPDATE_INTERVAL_SECONDS <= 0:
        raise ValueError("CFHUB_UPDATE_INTERVAL_SECONDS 必须大于 0")
    if CFHUB_MAX_LATENCY_MS < 0:
        raise ValueError("CFHUB_MAX_LATENCY_MS 不能小于 0")

    if missing and not DRY_RUN:
        raise ValueError("缺少必要的环境变量：" + ", ".join(missing))

    records = load_dns_records()
    if not records:
        raise ValueError(
            "未配置任何 DNS 记录。请创建 dns_records.json，"
            "或继续使用 DNS_ZONE_ID、DNS_RECORDSET_ID、DNS_RECORD_NAME 单记录配置。"
        )

    return records


# ================= 华为云 DNS 更新 =================


def update_single_huawei_dns(client, record, ips):
    request = UpdateRecordSetRequest()
    request.zone_id = record["zone_id"]
    request.recordset_id = record["recordset_id"]
    request.body = UpdateRecordSetReq(records=ips, ttl=record["ttl"])

    client.update_record_set(request)
    line_detail = f" 线路={record['cfhub_line']}" if record.get("cfhub_line") else ""
    print(f"  成功：{record['name']} {record['type']}{line_detail} -> {ips}")
    return {
        "name": record["name"],
        "type": record["type"],
        "status": "成功",
        "records": ips,
        "error": "",
    }


def update_huawei_dns_records(records, ips):
    if DRY_RUN:
        print("\nDRY_RUN=true，跳过华为云 DNS 更新。以下记录将使用同一组优选 IP：")
        results = []
        for record in records:
            print(f"  演练：{record['name']} {record['type']} -> {ips}")
            results.append(
                {
                    "name": record["name"],
                    "type": record["type"],
                    "status": "演练",
                    "records": ips,
                    "error": "",
                }
            )
        return results

    credentials = BasicCredentials(AK, SK)
    client = (
        DnsClient.new_builder()
        .with_credentials(credentials)
        .with_region(DnsRegion.value_of(HUAWEI_REGION))
        .build()
    )

    print("\n开始同步华为云 DNS 记录：")
    results = []
    for record in records:
        try:
            results.append(update_single_huawei_dns(client, record, ips))
        except Exception as exc:
            error = str(exc)
            print(f"  失败：{record['name']} {record['type']}，错误：{error}")
            results.append(
                {
                    "name": record["name"],
                    "type": record["type"],
                    "status": "失败",
                    "records": ips,
                    "error": error,
                }
            )

    return results


# ================= CFHub 数据源 =================


def fetch_cfhub_ips():
    request = urllib.request.Request(
        CFHUB_POOLS_URL, headers={"User-Agent": "cf-ip-sync/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    pools = payload.get("pools") if isinstance(payload, dict) else None
    if not isinstance(pools, list):
        raise ValueError("CFHub API 响应缺少 pools 数组。")

    selected = {}
    supported_lines = set(CFHUB_LINES.values())
    for pool in pools:
        if not isinstance(pool, dict) or pool.get("isp") != "national":
            continue
        if pool.get("published") is False:
            continue
        entries = pool.get("ips", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                latency = float(entry.get("median_ms"))
                ip_obj = ipaddress.ip_address(str(entry.get("ip", "")).strip())
            except (TypeError, ValueError):
                continue
            if latency > CFHUB_MAX_LATENCY_MS:
                continue
            record_type = "A" if ip_obj.version == 4 else "AAAA"
            lines = entry.get("lines", [])
            if not isinstance(lines, list):
                continue
            for line in lines:
                if line not in supported_lines:
                    continue
                key = (line, record_type)
                selected.setdefault(key, {})[str(ip_obj)] = {
                    "ip": str(ip_obj),
                    "latency_ms": latency,
                    "votes": entry.get("votes", ""),
                    "users": entry.get("users", ""),
                    "lines": lines,
                }

    result = {key: list(items.values()) for key, items in selected.items()}
    if not result:
        raise RuntimeError(
            f"CFHub national 池中没有 median_ms <= {CFHUB_MAX_LATENCY_MS} 的有效 IP。"
        )
    return result


def update_cfhub_dns_records(records, ips_by_line_type):
    """按 CFHub 线路及地址族更新华为云上预先创建的记录集。"""
    applicable = []
    results = []
    for record in records:
        items = ips_by_line_type.get((record["cfhub_line"], record["type"]), [])
        ips = [item["ip"] for item in items]
        if not ips:
            print(
                f"  跳过：{record['name']} {record['type']} "
                f"线路={record['cfhub_line']}，CFHub 当前没有符合条件的 IP；保留现有解析。"
            )
            results.append(
                {
                    "name": record["name"], "type": record["type"],
                    "line": record["cfhub_line"], "status": "跳过", "records": [],
                    "error": "该线路和地址族当前没有符合条件的 IP，已保留现有解析。",
                }
            )
        else:
            applicable.append((record, items))

    if DRY_RUN:
        print("\nDRY_RUN=true，跳过华为云 DNS 更新：")
        for record, items in applicable:
            ips = [item["ip"] for item in items]
            print(
                f"  演练：{record['name']} {record['type']} "
                f"线路={record['cfhub_line']} -> {ips}"
            )
            results.append(
                {
                    "name": record["name"], "type": record["type"],
                    "line": record["cfhub_line"],
                    "status": "演练", "records": ips, "error": "",
                }
            )
        return results

    credentials = BasicCredentials(AK, SK)
    client = (
        DnsClient.new_builder()
        .with_credentials(credentials)
        .with_region(DnsRegion.value_of(HUAWEI_REGION))
        .build()
    )
    print("\n开始同步华为云 DNS 记录：")
    for record, items in applicable:
        ips = [item["ip"] for item in items]
        try:
            result = update_single_huawei_dns(client, record, ips)
            result["line"] = record["cfhub_line"]
            results.append(result)
        except Exception as exc:
            error = str(exc)
            print(
                f"  失败：{record['name']} {record['type']} "
                f"线路={record['cfhub_line']}，错误：{error}"
            )
            results.append(
                {
                    "name": record["name"], "type": record["type"],
                    "status": "失败", "records": ips, "error": error,
                }
            )
    return results


def run_cfhub_cycle(dns_records):
    print(f"\n正在从 CFHub 获取全国 IP 池：{CFHUB_POOLS_URL}")
    ips_by_line_type = fetch_cfhub_ips()
    selected_rows = [item for items in ips_by_line_type.values() for item in items]
    for (line, record_type), items in sorted(ips_by_line_type.items()):
        details = ", ".join(
            f"{item['ip']} ({item['latency_ms']:g} ms)" for item in items
        )
        print(f"  {line} {record_type}: {details}")
    results = update_cfhub_dns_records(dns_records, ips_by_line_type)
    failed = any(result["status"] == "失败" for result in results)
    status = "dry_run" if DRY_RUN else ("failed" if failed else "success")
    send_feishu_notification(status, selected_rows, results)
    return 1 if failed else 0


def run_cfhub_forever(dns_records):
    print(
        f"CFHub 自动更新已启动，每 {CFHUB_UPDATE_INTERVAL_SECONDS} 秒获取并同步一次。"
    )
    while True:
        started = time.monotonic()
        try:
            run_cfhub_cycle(dns_records)
        except Exception as exc:
            error = str(exc)
            print(f"\n本轮 CFHub 同步失败：{error}")
            send_feishu_notification("failed", [], [], error=error)
        elapsed = time.monotonic() - started
        time.sleep(max(0, CFHUB_UPDATE_INTERVAL_SECONDS - elapsed))


# ================= 飞书通知 =================


def send_feishu_notification(status, selected_rows, update_results=None, error=""):
    if not FEISHU_WEBHOOK_URL:
        print("\n未配置 FEISHU_WEBHOOK_URL_CFST，跳过飞书通知。")
        return

    ips = [item["ip"] for item in selected_rows]
    detail_lines = []
    for item in selected_rows:
        line = item["ip"]
        if item.get("latency_ms") not in (None, ""):
            line += f" | 延迟 {item['latency_ms']} ms"
        if item.get("speed_mb_s"):
            line += f" | 速度 {item['speed_mb_s']} MB/s"
        if item.get("colo"):
            line += f" | 机房 {item['colo']}"
        detail_lines.append(line)

    status_text = {
        "success": "成功",
        "dry_run": "演练完成",
        "failed": "失败",
    }.get(status, status)

    update_results = update_results or []
    result_lines = []
    for result in update_results:
        line = f"{result['status']}：{result['name']} {result['type']}"
        if result.get("line"):
            line += f"（{result['line']}）"
        if result.get("error"):
            line += f" | {result['error']}"
        result_lines.append(line)

    content = [
        [{"tag": "text", "text": f"执行状态：{status_text}\n"}],
        [{"tag": "text", "text": f"记录数量：{len(update_results)}\n"}],
        [{"tag": "text", "text": f"优选 IP：{', '.join(ips) if ips else '无'}\n"}],
        [
            {
                "tag": "text",
                "text": "测速详情：\n" + ("\n".join(detail_lines) if detail_lines else "无"),
            }
        ],
        [
            {
                "tag": "text",
                "text": "\nDNS 同步详情：\n" + ("\n".join(result_lines) if result_lines else "无"),
            }
        ],
        [{"tag": "text", "text": f"\n执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}],
    ]

    if error:
        content.append([{"tag": "text", "text": f"\n错误信息：{error}"}])

    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "Cloudflare 优选 IP DNS 更新",
                    "content": content,
                }
            }
        },
    }

    max_retries = 5
    retry_delay = 120

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                FEISHU_WEBHOOK_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            response = urllib.request.urlopen(req, timeout=10)
            resp_data = json.loads(response.read().decode("utf-8"))

            if resp_data.get("code") == 0:
                print("\n飞书通知发送成功。")
                return

            if resp_data.get("code") == 11232 and attempt < max_retries:
                print(
                    f"\n飞书触发频率限制，当前第 {attempt}/{max_retries} 次，"
                    f"{retry_delay} 秒后重试..."
                )
                time.sleep(retry_delay)
                continue

            print(f"\n飞书通知发送失败：{resp_data}")
            return
        except Exception as exc:
            print(f"\n飞书通知发送异常（{attempt}/{max_retries}）：{exc}")
            if attempt < max_retries:
                time.sleep(retry_delay)


# ================= 入口 =================


def main():
    selected_rows = []
    update_results = []
    try:
        dns_records = validate_config()
        if IP_SOURCE == "cfhub":
            run_cfhub_forever(dns_records)
            return 0

        dns_records = [record for record in dns_records if record["type"] == DNS_RECORD_TYPE]
        if not dns_records:
            raise ValueError(
                f"配置文件中没有 type={DNS_RECORD_TYPE} 的 DNS 记录，无法写入本地测速结果。"
            )
        selected_rows = run_cfst()
        ips = [item["ip"] for item in selected_rows]
        update_results = update_huawei_dns_records(dns_records, ips)

        has_failed = any(result["status"] == "失败" for result in update_results)
        status = "dry_run" if DRY_RUN else ("failed" if has_failed else "success")
        send_feishu_notification(status, selected_rows, update_results)
        return 1 if has_failed else 0
    except Exception as exc:
        error = str(exc)
        print(f"\n错误：{error}")
        send_feishu_notification("failed", selected_rows, update_results, error=error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
