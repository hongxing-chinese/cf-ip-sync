import csv
import ipaddress
import json
import math
import os
import platform
import shlex
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

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
CFHUB_MAX_LATENCY_MS = 500
CFHUB_UNICOM_MAX_LATENCY_MS = 900
RUN_STATE_FILE = abs_path(os.environ.get("RUN_STATE_FILE", "run_state.json"))
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

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


def fetch_cfhub_ips(records):
    request = urllib.request.Request(
        CFHUB_POOLS_URL, headers={"User-Agent": "cf-ip-sync/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    pools = payload.get("pools") if isinstance(payload, dict) else None
    if not isinstance(pools, list):
        raise ValueError("CFHub API 响应缺少 pools 数组。")

    selected = {}
    pools_by_isp = {}
    configured_keys = {
        (record["cfhub_line"], record["type"])
        for record in records
    }
    isp_lines = set(CFHUB_LINES.values())
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if pool.get("published") is False:
            continue
        isp = pool.get("isp")
        if isp not in isp_lines and isp != "national":
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
            if not math.isfinite(latency) or latency < 0:
                continue
            record_type = "A" if ip_obj.version == 4 else "AAAA"
            item = {
                "ip": str(ip_obj),
                "latency_ms": latency,
                "votes": entry.get("votes", ""),
                "users": entry.get("users", ""),
                "source_pool": isp,
            }
            if isp == "national":
                lines = entry.get("lines", [])
                if not isinstance(lines, list):
                    continue
                for line in lines:
                    if not isinstance(line, str) or line not in isp_lines:
                        continue
                    key = (line, record_type)
                    max_latency = (
                        CFHUB_UNICOM_MAX_LATENCY_MS
                        if line == "unicom"
                        else CFHUB_MAX_LATENCY_MS
                    )
                    if (
                        key in configured_keys
                        and latency <= max_latency
                    ):
                        candidates = selected.setdefault(key, {})
                        previous = candidates.get(str(ip_obj))
                        if previous is None or latency < previous["latency_ms"]:
                            candidates[str(ip_obj)] = item
            else:
                key = (isp, record_type)
                if key in configured_keys:
                    candidates = pools_by_isp.setdefault(key, {})
                    previous = candidates.get(str(ip_obj))
                    if previous is None or latency < previous["latency_ms"]:
                        candidates[str(ip_obj)] = item

    # 全国池优先；某条线路或地址族没有符合阈值的全国 IP 时，
    # 改从同 ISP 的专属池取延迟最低的两条，不受全国池阈值限制。
    for key in configured_keys:
        if selected.get(key):
            continue
        candidates = list(pools_by_isp.get(key, {}).values())
        candidates.sort(key=lambda item: (item["latency_ms"], item["ip"]))
        if candidates:
            selected[key] = {
                item["ip"]: item
                for item in candidates[:2]
            }

    result = {key: list(items.values()) for key, items in selected.items() if items}
    if not result:
        raise RuntimeError(
            "CFHub 全国池及各线路专属池中均没有可用于已配置 DNS 记录的有效 IP。"
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
                f"线路={record['cfhub_line']}，national 及专属池均无匹配 IP；保留现有解析。"
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
            source_pool = items[0]["source_pool"]
            print(
                f"  演练：{record['name']} {record['type']} "
                f"线路={record['cfhub_line']} 池={source_pool} -> {ips}"
            )
            results.append(
                {
                    "name": record["name"], "type": record["type"],
                    "line": record["cfhub_line"],
                    "source_pool": source_pool,
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
            result["source_pool"] = items[0]["source_pool"]
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
                    "line": record["cfhub_line"],
                    "source_pool": items[0]["source_pool"],
                    "status": "失败", "records": ips, "error": error,
                }
            )
    return results


def run_cfhub_cycle(dns_records):
    print(f"\n正在从 CFHub 获取全国 IP 池：{CFHUB_POOLS_URL}")
    ips_by_line_type = fetch_cfhub_ips(dns_records)
    for (line, record_type), items in sorted(ips_by_line_type.items()):
        details = ", ".join(
            f"{item['ip']} ({item['latency_ms']:g} ms)" for item in items
        )
        pool = items[0]["source_pool"]
        pool_note = "（运营商专属池回退）" if pool != "national" else ""
        print(f"  {line} {record_type}{pool_note}: {details}")
    results = update_cfhub_dns_records(dns_records, ips_by_line_type)
    return results


def load_run_state():
    if not os.path.exists(RUN_STATE_FILE):
        return {"runs": [], "last_daily_report_date": ""}
    try:
        with open(RUN_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or not isinstance(state.get("runs"), list):
            raise ValueError("状态文件格式无效")
        state.setdefault("last_daily_report_date", "")
        return state
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"状态文件读取失败，将从空历史继续：{exc}")
        return {"runs": [], "last_daily_report_date": ""}


def save_run_state(state):
    state_dir = os.path.dirname(RUN_STATE_FILE)
    os.makedirs(state_dir, exist_ok=True)
    temporary_path = RUN_STATE_FILE + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(temporary_path, RUN_STATE_FILE)


def append_run(state, run):
    cutoff = datetime.now(BEIJING_TZ) - timedelta(hours=24)
    runs = state.get("runs", [])
    recent_runs = []
    for item in runs:
        try:
            item_time = datetime.fromisoformat(item["timestamp"])
            if item_time.tzinfo is None:
                item_time = item_time.replace(tzinfo=BEIJING_TZ)
            if item_time >= cutoff:
                recent_runs.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    recent_runs.append(run)
    state["runs"] = recent_runs


def send_feishu_message(title, message):
    if not FEISHU_WEBHOOK_URL:
        print("未配置 FEISHU_WEBHOOK_URL_CFST，跳过飞书通知。")
        return False

    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": message}]],
                }
            }
        },
    }
    request = urllib.request.Request(
        FEISHU_WEBHOOK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("code") == 0:
            print(f"飞书通知已发送：{title}")
            return True
        print(f"飞书通知发送失败：{result}")
    except Exception as exc:
        print(f"飞书通知发送异常：{exc}")
    return False


def build_daily_report(state, now):
    cutoff = now - timedelta(hours=24)
    runs = []
    for run in state.get("runs", []):
        try:
            run_time = datetime.fromisoformat(run["timestamp"])
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=BEIJING_TZ)
            if run_time >= cutoff:
                runs.append(run)
        except (KeyError, TypeError, ValueError):
            continue

    success_count = sum(run.get("status") == "success" for run in runs)
    failure_count = sum(run.get("status") == "failed" for run in runs)
    dry_run_count = sum(run.get("status") == "dry_run" for run in runs)
    updated_count = sum(run.get("records_updated", 0) for run in runs)
    skipped_count = sum(run.get("records_skipped", 0) for run in runs)
    failed_record_count = sum(run.get("records_failed", 0) for run in runs)
    fallback_count = sum(run.get("records_fallback", 0) for run in runs)
    sources = sorted({run.get("source", "unknown") for run in runs})
    source_summary = "，".join(
        f"{source} {sum(run.get('source') == source for run in runs)} 次"
        for source in sources
    ) if sources else "无"

    lines = [
        f"统计区间：{cutoff.strftime('%Y-%m-%d %H:%M')} 至 {now.strftime('%Y-%m-%d %H:%M')}（北京时间）",
        f"运行次数：{len(runs)}（成功 {success_count}，失败 {failure_count}，演练 {dry_run_count}）",
        f"数据来源：{source_summary}",
        f"DNS 记录处理：更新 {updated_count}，专属池回退 {fallback_count}，无匹配 IP 跳过 {skipped_count}，更新失败 {failed_record_count}",
    ]
    if runs:
        latest = runs[-1]
        lines.append(
            f"最近运行：{latest['timestamp']}，状态={latest['status']}"
        )

    failures = [run for run in runs if run.get("status") == "failed"]
    if failures:
        lines.append("失败明细：")
        for run in failures[-5:]:
            detail = run.get("error") or "；".join(run.get("failed_records", [])) or "记录更新失败"
            lines.append(f"- {run['timestamp']}：{detail}")
    else:
        lines.append("失败明细：无")
    return "\n".join(lines)


def maybe_send_daily_report(state, now):
    if not FEISHU_WEBHOOK_URL:
        return
    report_date = now.date().isoformat()
    if now.hour < 15 or state.get("last_daily_report_date") == report_date:
        return

    message = build_daily_report(state, now)
    if send_feishu_message("Cloudflare DNS 过去 24 小时运行汇总", message):
        state["last_daily_report_date"] = report_date
        save_run_state(state)


def run_cfhub_once(dns_records):
    now = datetime.now(BEIJING_TZ)
    state = load_run_state()
    run = {
        "timestamp": now.isoformat(timespec="seconds"),
        "source": "cfhub",
        "status": "success",
        "records_updated": 0,
        "records_fallback": 0,
        "records_skipped": 0,
        "records_failed": 0,
        "failed_records": [],
        "error": "",
    }
    try:
        results = run_cfhub_cycle(dns_records)
        failed_results = [item for item in results if item["status"] == "失败"]
        run["records_updated"] = sum(item["status"] == "成功" for item in results)
        run["records_fallback"] = sum(
            item["status"] in {"成功", "演练"}
            and item.get("source_pool") != "national"
            for item in results
        )
        run["records_skipped"] = sum(item["status"] == "跳过" for item in results)
        run["records_failed"] = len(failed_results)
        run["failed_records"] = [
            f"{item['name']} {item['type']}（{item.get('line', '')}）：{item.get('error', '')}"
            for item in failed_results
        ]
        if failed_results:
            run["status"] = "failed"
            failure_lines = "\n".join(f"- {item}" for item in run["failed_records"])
            send_feishu_message(
                "Cloudflare DNS 更新失败",
                f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n{failure_lines}",
            )
        elif DRY_RUN:
            run["status"] = "dry_run"
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        print(f"\nCFHub 同步失败：{exc}")
        send_feishu_message(
            "Cloudflare DNS 更新失败",
            f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n错误：{exc}",
        )

    append_run(state, run)
    save_run_state(state)
    maybe_send_daily_report(state, datetime.now(BEIJING_TZ))
    return 1 if run["status"] == "failed" else 0


def run_cfst_once(dns_records):
    now = datetime.now(BEIJING_TZ)
    state = load_run_state()
    run = {
        "timestamp": now.isoformat(timespec="seconds"),
        "source": "cfst",
        "status": "success",
        "records_updated": 0,
        "records_fallback": 0,
        "records_skipped": 0,
        "records_failed": 0,
        "failed_records": [],
        "error": "",
    }
    try:
        selected_rows = run_cfst()
        results = update_huawei_dns_records(dns_records, [item["ip"] for item in selected_rows])
        failed_results = [item for item in results if item["status"] == "失败"]
        run["records_updated"] = sum(item["status"] == "成功" for item in results)
        run["records_failed"] = len(failed_results)
        run["failed_records"] = [
            f"{item['name']} {item['type']}：{item.get('error', '')}"
            for item in failed_results
        ]
        if failed_results:
            run["status"] = "failed"
            send_feishu_message(
                "Cloudflare DNS 更新失败",
                f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n"
                + "\n".join(f"- {item}" for item in run["failed_records"]),
            )
        elif DRY_RUN:
            run["status"] = "dry_run"
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        print(f"\nCloudflareSpeedTest 更新失败：{exc}")
        send_feishu_message(
            "Cloudflare DNS 更新失败",
            f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n错误：{exc}",
        )

    append_run(state, run)
    save_run_state(state)
    maybe_send_daily_report(state, datetime.now(BEIJING_TZ))
    return 1 if run["status"] == "failed" else 0


# ================= 入口 =================


def main():
    try:
        dns_records = validate_config()
        if IP_SOURCE == "cfhub":
            return run_cfhub_once(dns_records)

        dns_records = [record for record in dns_records if record["type"] == DNS_RECORD_TYPE]
        if not dns_records:
            raise ValueError(
                f"配置文件中没有 type={DNS_RECORD_TYPE} 的 DNS 记录，无法写入本地测速结果。"
            )
        return run_cfst_once(dns_records)
    except Exception as exc:
        error = str(exc)
        print(f"\n错误：{error}")
        now = datetime.now(BEIJING_TZ)
        state = load_run_state()
        run = {
            "timestamp": now.isoformat(timespec="seconds"),
            "source": IP_SOURCE,
            "status": "failed",
            "records_updated": 0,
            "records_fallback": 0,
            "records_skipped": 0,
            "records_failed": 0,
            "failed_records": [],
            "error": error,
        }
        append_run(state, run)
        save_run_state(state)
        send_feishu_message(
            "Cloudflare DNS 更新失败",
            f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）\n错误：{error}",
        )
        maybe_send_daily_report(state, datetime.now(BEIJING_TZ))
        return 1


if __name__ == "__main__":
    sys.exit(main())
