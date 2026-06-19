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

    if record["type"] != DNS_RECORD_TYPE:
        raise ValueError(
            f"第 {index} 条 DNS 记录类型为 {record['type']}，"
            f"但本次 CFST 结果类型为 {DNS_RECORD_TYPE}，请保持一致。"
        )

    return record


def validate_config():
    missing = []
    if not AK:
        missing.append("HUAWEI_AK")
    if not SK:
        missing.append("HUAWEI_SK")

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
    print(f"  成功：{record['name']} {record['type']} -> {ips}")
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


# ================= 飞书通知 =================


def send_feishu_notification(status, selected_rows, update_results=None, error=""):
    if not FEISHU_WEBHOOK_URL:
        print("\n未配置 FEISHU_WEBHOOK_URL_CFST，跳过飞书通知。")
        return

    ips = [item["ip"] for item in selected_rows]
    detail_lines = []
    for item in selected_rows:
        line = item["ip"]
        if item["latency_ms"]:
            line += f" | 延迟 {item['latency_ms']} ms"
        if item["speed_mb_s"]:
            line += f" | 速度 {item['speed_mb_s']} MB/s"
        if item["colo"]:
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
