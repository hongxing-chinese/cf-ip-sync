# Cloudflare 优选 IP 华为云 DNS 同步

本项目支持两种 IP 来源，并把筛选结果写入华为云 DNS 已存在的记录集：

- **CFHub（默认）**：每 5 分钟读取 [全国 IP 池](https://cfhub.1molchuan.top/api/v1/pools)，筛选 `median_ms <= 500` 的 IPv4/IPv6 地址，再按运营线路更新 DNS。
- **本地 CloudflareSpeedTest**：在运行脚本的 Windows 或 Linux 网络上测速，按现有 CFST 参数选择 IP 并更新 DNS。通过 `IP_SOURCE=cfst` 启用，单次运行后退出，适合手动执行或由系统定时任务调度。

DNS 记录集必须先在华为云控制台创建。程序只更新 `recordset_id` 对应记录集的解析值，不创建、删除记录集，也不会在没有匹配 IP 时清空已有值。

## 工作流程

```text
IP_SOURCE=cfhub                         IP_SOURCE=cfst
获取 CFHub 全国池（默认每 300 秒）       运行本机 CloudflareSpeedTest
按 median_ms、线路和地址族筛选            读取 CFST CSV
                 \                       /
                   更新对应华为云记录集
                           ↓
                      可选飞书通知
```

CFHub 按以下字段分组：`cmcc`（移动）、`chinanet`（电信）、`unicom`（联通）、`cernet`（教育网）、`cloud`（全网默认）；地址属于 IPv4 时更新 `A`，属于 IPv6 时更新 `AAAA`。一个 IP 的 `lines` 有多个值时，会进入这些线路各自的地址列表。相同 IP 在同一线路去重。

## 快速开始

### 1. 安装依赖并准备配置

Windows PowerShell：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item dns_records.example.json dns_records.json
```

Linux：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp dns_records.example.json dns_records.json
```

### 2. 配置华为云凭证和记录集

在 `.env` 中填写：

```dotenv
HUAWEI_AK=你的AccessKey
HUAWEI_SK=你的SecretKey
HUAWEI_REGION=cn-east-3
IP_SOURCE=cfhub
CFHUB_UPDATE_INTERVAL_SECONDS=300
DRY_RUN=true
```

编辑 `dns_records.json`，每个运营商线路、每种记录类型各配置一条记录。示例文件已列出 `cmcc`、`chinanet`、`unicom`、`cernet` 和 `cloud` 的 A/AAAA 配置：把占位的 `zone_id`、`recordset_id` 换成华为云上对应的值即可。

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

`recordset_id` 必须是华为云上已创建的对应线路和类型记录集。`line` 使用 CFHub 线路标识；也接受 `mobile`/`移动`、`telecom`/`电信`、`联通`、`教育网`、`default`/`全网默认`等别名。`line` 缺省时按 `cloud` 处理。配置只包含你实际创建的记录即可。

将 `DRY_RUN=true` 时，脚本会显示将要写入的各条记录，但不访问华为云更新接口。检查记录无误后改为 `false`。

### 3. 启动

```bash
python update_cf_ip.py
```

`IP_SOURCE=cfhub` 会持续运行，首次启动立即同步，之后按间隔更新；默认间隔 300 秒。保持该进程运行即可，不要再用 cron 每 5 分钟重复启动同一脚本。需要停止时使用 `Ctrl+C`，Linux 服务部署可由 systemd 管理。

如果某条线路暂时没有符合阈值的 IPv4 或 IPv6 地址，该条记录会显示“跳过”，旧解析保持不变；其他匹配到 IP 的记录仍会正常更新。API 或华为云请求失败会输出错误，CFHub 模式会在下一个周期继续尝试。

## 环境变量

### IP 来源

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IP_SOURCE` | `cfhub` | `cfhub` 持续读取池数据；`cfst` 运行一次本地测速后退出。 |
| `CFHUB_POOLS_URL` | `https://cfhub.1molchuan.top/api/v1/pools` | CFHub API 地址。 |
| `CFHUB_MAX_LATENCY_MS` | `500` | CFHub 全国池 IP 的最大 `median_ms`，包含等于阈值的 IP。 |
| `CFHUB_UPDATE_INTERVAL_SECONDS` | `300` | CFHub 两次轮询的最小间隔，单位秒。 |

### 华为云和 DNS

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HUAWEI_AK` / `HUAWEI_SK` | 空 | 华为云 API 凭证；正式更新时必填。 |
| `HUAWEI_REGION` | `ap-southeast-1` | 华为云 DNS 服务区域。 |
| `DNS_RECORDS_FILE` | `dns_records.json` | DNS 记录配置文件；支持数组或包含 `records` 数组的对象。 |
| `DNS_TTL` | `300` | 记录未指定 `ttl` 时使用的 TTL。 |
| `DNS_RECORD_TYPE` | `A` | 仅本地 CFST 模式使用，指定更新 A 或 AAAA 记录。 |
| `DNS_RECORD_COUNT` | `1` | 仅本地 CFST 模式使用，选取 CSV 中前 N 个 IP。 |
| `DNS_ZONE_ID`、`DNS_RECORDSET_ID`、`DNS_RECORD_NAME` | 空 | 兼容旧版单记录配置；没有 `dns_records.json` 时使用。 |
| `DRY_RUN` | `false` | `true` 时显示操作但不更新 DNS。 |

单记录兼容配置无法按 CFHub 线路配置多组 A/AAAA 记录，CFHub 模式建议使用 `dns_records.json`。

### 本地 CloudflareSpeedTest

设置 `IP_SOURCE=cfst` 后，下列变量控制本机测速：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CFST_BINARY` | 自动查找 | CFST 可执行文件路径。Windows 默认查找 `cfst_windows_amd64/cfst.exe`；Linux 可显式指定。 |
| `CFST_WORKDIR` | 可执行文件所在目录 | CFST 工作目录，用于读取默认 IP 段文件。 |
| `CFST_RESULT_CSV` | `cfst_result.csv` | CFST 输出文件。 |
| `CFST_DOWNLOAD_COUNT` | `10` | 对应 `-dn`。 |
| `CFST_DOWNLOAD_SECONDS` | `10` | 对应 `-dt`。 |
| `CFST_MAX_LATENCY_MS` | `9999` | 对应 `-tl`。 |
| `CFST_MIN_SPEED_MB` | `0.01` | 对应 `-sl`，单位 MB/s。 |
| `CFST_EXTRA_ARGS` | 空 | 额外传给 CFST 的参数。 |
| `CFST_TIMEOUT_SECONDS` | `3600` | 测速最长运行时间。 |

CFST 使用 `DNS_RECORD_TYPE` 选择地址族。若要同时更新 A 和 AAAA，可分别设置 `DNS_RECORD_TYPE=A` 与 `DNS_RECORD_TYPE=AAAA` 执行；CFHub 模式则会在每个周期同时处理两种地址族。

Windows 本地示例：

```powershell
$env:IP_SOURCE = "cfst"
$env:DRY_RUN = "true"
python update_cf_ip.py
```

Linux amd64 可下载 [CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest/releases)，并在 `.env` 配置：

```dotenv
IP_SOURCE=cfst
CFST_BINARY=/opt/cfst/cfst
CFST_WORKDIR=/opt/cfst
```

## 可选飞书通知

在 `.env` 设置 `FEISHU_WEBHOOK_URL_CFST`。未配置时会跳过通知。CFHub 模式每个同步周期会发送一次结果通知；通知包括 IP、延迟、DNS 结果和失败信息。

## 安全与故障处理

- `.env` 和真实的 `dns_records.json` 不应提交到 Git；不要把 AK/SK 或 Webhook 写入代码。
- 建议第一次用 `DRY_RUN=true` 核对目标域名、地址族、线路和解析值。
- 华为云更新失败时，检查 AK/SK、区域、记录类型，以及 ID 是否确实对应预创建的记录集。
- 如果某个 CFHub 线路没有匹配记录，或池中没有该线路的 AAAA/A 地址，程序不会创建记录；需在华为云控制台创建相应记录集后加入配置。
- 本地 CFST 结果代表运行机器所在网络；CFHub 结果来自志愿者探测数据，按其全国池的延迟分数筛选。

## 获取华为云记录集 ID

在华为云 DNS 控制台为域名创建对应的解析记录，并为每种需要的线路和地址类型单独创建记录集。例如移动 A、移动 AAAA、全网默认 A、全网默认 AAAA。之后从控制台或华为云 DNS API 获取各记录集的 `zone_id` 与 `recordset_id`，填写到 `dns_records.json`。

## 依赖

```text
python-dotenv
huaweicloudsdkcore
huaweicloudsdkdns
```
