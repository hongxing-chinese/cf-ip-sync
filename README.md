# Cloudflare 优选 IP 华为云 DNS 同步

本项目支持 CFHub 和本地 CloudflareSpeedTest 两种 IP 来源。默认使用 CFHub 全国池，筛选 `median_ms <= 500` 的 IPv4/IPv6 地址，并按线路更新华为云 DNS。每次脚本单轮同步后退出，由 cron 每 5 分钟启动一次。本地测速通过 `IP_SOURCE=cfst` 启用，Windows 和 Linux 均保留支持。

程序只更新华为云上预先创建的记录集，不创建或删除记录。某条线路没有匹配 IP 时会跳过并保留旧值；跳过不算失败。

## 线路映射

CFHub `lines` 映射为：`cmcc`（移动）、`chinanet`（电信）、`unicom`（联通）、`cernet`（教育网）、`cloud`（全网默认）。IPv4 更新 `A`，IPv6 更新 `AAAA`。一个 IP 的 `lines` 包含多个值时，会分别写入这些线路；同一线路内的重复 IP 会去重。

## 安装与配置

Linux：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp dns_records.example.json dns_records.json
```

Windows PowerShell：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item dns_records.example.json dns_records.json
```

在 `.env` 填写华为云凭证与运行设置：

```dotenv
HUAWEI_AK=你的AccessKey
HUAWEI_SK=你的SecretKey
HUAWEI_REGION=cn-east-3
IP_SOURCE=cfhub
CFHUB_MAX_LATENCY_MS=500
RUN_STATE_FILE=run_state.json
DRY_RUN=true
```

编辑 `dns_records.json`，为每个线路、每种地址类型填写华为云已创建记录集的 `zone_id` 和 `recordset_id`：

```json
{
  "records": [
    {
      "name": "cdn.example.com",
      "zone_id": "华为云域名ID",
      "recordset_id": "移动线路A记录集ID",
      "type": "A",
      "line": "cmcc",
      "ttl": 300
    },
    {
      "name": "cdn.example.com",
      "zone_id": "华为云域名ID",
      "recordset_id": "移动线路AAAA记录集ID",
      "type": "AAAA",
      "line": "cmcc",
      "ttl": 300
    }
  ]
}
```

`dns_records.example.json` 列出五种线路的 A/AAAA 示例。仅保留实际创建的记录并填写正确 ID。`line` 可使用 `cmcc`、`chinanet`、`unicom`、`cernet`、`cloud`；也接受 `mobile`/`移动`、`telecom`/`电信`、`联通`、`教育网`、`default`/`全网默认`。省略 `line` 时默认为 `cloud`。

首次建议保留 `DRY_RUN=true` 并手动执行 `python update_cf_ip.py`，核对待更新内容后再设为 `false`。

## Cron 每 5 分钟执行

在 Linux 上运行 `crontab -e`，添加以下任务并按实际项目路径修改：

```cron
*/5 * * * * flock -n /tmp/cf-ip-sync.lock sh -c 'cd /home/cf-ip-sync && /home/cf-ip-sync/venv/bin/python update_cf_ip.py' >> /home/cf-ip-sync/run.log 2>&1
```

脚本每次执行会立即获取 CFHub 数据、更新匹配记录、保存运行状态，然后退出。`flock` 可避免某次运行超过 5 分钟时与下一次任务重叠。cron 每 5 分钟的调度不依赖服务器时区；每日汇总时刻由程序按北京时间判断。

手动执行一轮：

```bash
cd /home/cf-ip-sync
source venv/bin/activate
python update_cf_ip.py
```

查看日志：

```bash
tail -f /home/cf-ip-sync/run.log
```

## 飞书通知策略

在 `.env` 配置 `FEISHU_WEBHOOK_URL_CFST` 启用通知。CFHub 模式：

- 成功更新和无匹配 IP 的跳过不发送即时通知。
- CFHub API、配置或华为云 DNS 更新失败时，该轮发送一次失败通知。
- 每天北京时间 15:00 后首次运行时，发送过去 24 小时汇总，内容包含运行次数、成功/失败次数、DNS 更新/跳过/失败数量及失败明细。cron 每 5 分钟运行，通常在 15:00 那轮发送。

运行历史存储于 `RUN_STATE_FILE`（默认 `run_state.json`），包含 CFHub 和本地 CFST 运行，且已加入 `.gitignore`。状态文件保留滚动 24 小时记录；不要在统计周期内删除它，否则汇总历史会丢失。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IP_SOURCE` | `cfhub` | `cfhub` 单轮更新后退出；`cfst` 本地测速后退出。 |
| `CFHUB_POOLS_URL` | `https://cfhub.1molchuan.top/api/v1/pools` | CFHub API 地址。 |
| `CFHUB_MAX_LATENCY_MS` | `500` | 全国池 IP 的最大 `median_ms`，包含等于阈值的 IP。 |
| `RUN_STATE_FILE` | `run_state.json` | CFHub/CFST 运行历史及每日汇总状态。 |
| `HUAWEI_AK` / `HUAWEI_SK` | 空 | 正式更新时必填的华为云 API 凭证。 |
| `HUAWEI_REGION` | `ap-southeast-1` | 华为云 DNS 服务区域。 |
| `DNS_RECORDS_FILE` | `dns_records.json` | DNS 记录文件，支持数组或含 `records` 数组的对象。 |
| `DNS_TTL` | `300` | 记录未指定 `ttl` 时采用的值。 |
| `DNS_RECORD_TYPE` | `A` | 仅 CFST 模式使用，选择更新 A 或 AAAA。 |
| `DNS_RECORD_COUNT` | `1` | 仅 CFST 模式使用，选取 CSV 排序后的前 N 个 IP。 |
| `DNS_ZONE_ID`、`DNS_RECORDSET_ID`、`DNS_RECORD_NAME` | 空 | 兼容旧版单记录配置；没有记录文件时使用。 |
| `DRY_RUN` | `false` | `true` 时只显示待更新值，不更新华为云 DNS。 |
| `FEISHU_WEBHOOK_URL_CFST` | 空 | 飞书自定义机器人 Webhook。 |

单记录环境变量不能表达多条 CFHub 线路，CFHub 模式建议使用 `dns_records.json`。

## 本地 CloudflareSpeedTest

设置 `IP_SOURCE=cfst` 后，本地测速参数如下：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CFST_BINARY` | 自动查找 | 可执行文件路径；Windows 默认查找 `cfst_windows_amd64/cfst.exe`。 |
| `CFST_WORKDIR` | 可执行文件所在目录 | CFST 工作目录，用于读取 IP 段文件。 |
| `CFST_RESULT_CSV` | `cfst_result.csv` | CFST 输出文件。 |
| `CFST_DOWNLOAD_COUNT` | `10` | 对应 `-dn` 参数。 |
| `CFST_DOWNLOAD_SECONDS` | `10` | 对应 `-dt` 参数。 |
| `CFST_MAX_LATENCY_MS` | `9999` | 对应 `-tl` 参数。 |
| `CFST_MIN_SPEED_MB` | `0.01` | 对应 `-sl` 参数，单位 MB/s。 |
| `CFST_EXTRA_ARGS` | 空 | 附加 CFST 参数。 |
| `CFST_TIMEOUT_SECONDS` | `3600` | CFST 最长运行时间。 |

CFST 每次只更新 `DNS_RECORD_TYPE` 指定的地址族。Windows PowerShell 示例：

```powershell
$env:IP_SOURCE = "cfst"
$env:DRY_RUN = "true"
python update_cf_ip.py
```

Linux 可在 `.env` 设置 `CFST_BINARY=/opt/cfst/cfst` 和 `CFST_WORKDIR=/opt/cfst`。

## 故障处理与安全

- 无匹配 IP 的线路会跳过，保留旧 DNS 值；CFHub API 和华为云更新失败会输出错误并返回非零退出码，下一次 cron 会重试。
- 检查华为云更新失败时，核对 AK/SK、区域、`zone_id`、`recordset_id` 和记录类型。
- `.env` 包含密钥，真实 `dns_records.json` 包含域名和云端 ID，请勿提交到 Git。
- 本地 CFST 结果代表运行机器所在网络；CFHub 数据来自志愿者探测池。

## 依赖

```text
python-dotenv
huaweicloudsdkcore
huaweicloudsdkdns
```
