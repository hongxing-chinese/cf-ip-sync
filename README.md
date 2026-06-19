# Cloudflare 优选 IP 同步

通过 `CloudflareSpeedTest` 定时测速 Cloudflare Anycast 节点，筛选当前网络环境下延迟低、下载速度可用的优选 IP，并自动更新到华为云 DNS 的一个或多个指定 `A` / `AAAA` 记录集。脚本执行完成后会通过飞书自定义机器人发送通知。

本项目只做一件事：直接使用 `CloudflareSpeedTest` 的测速结果，把同一组优选 Cloudflare IP 写到你指定的华为云域名记录上。记录可以来自不同 `zone_id`、不同 `recordset_id`、不同域名，数量不设脚本上限，适合解决多个根域名无法设置 CNAME、但又希望统一指向 Cloudflare 优选 IP 的场景。

## 原理简介

Cloudflare 对外提供的是 Anycast 网络，同一段 IP 在不同地区、不同运营商、不同时间的质量可能差异很大。`CloudflareSpeedTest` 会从 Cloudflare IP 段中抽样，先进行延迟测试，再对延迟排序靠前的 IP 做下载测速，最后按照下载速度和延迟输出优选结果。

本脚本负责把这个过程自动化：

```text
调用 CloudflareSpeedTest
        ↓
生成 cfst_result.csv
        ↓
读取 CSV 中排序靠前的 IP
        ↓
更新华为云 DNS 指定记录集列表
        ↓
发送飞书通知
```

### 为什么适合根域名

很多 DNS 服务不允许根域名直接设置 `CNAME`，例如 `example.com` 不能像 `www.example.com` 一样 CNAME 到某个 CDN 域名。常见绕法是：

- 使用 DNS 服务商提供的 CNAME Flattening 或 ALIAS 功能。
- 直接把根域名解析到 CDN 的可用 IP。
- 使用定时脚本更新解析记录，让 IP 保持在较优状态。

本项目采用第三种方式：定时测速 Cloudflare IP，然后把最优结果写入华为云 DNS 的一个或多个 `A` / `AAAA` 记录。

### 安全策略

脚本不会在没有测速结果时清空解析记录。如果 `CloudflareSpeedTest` 没有筛出可用 IP，脚本会直接失败，并通过飞书通知错误原因。

为了防止误更新，建议首次部署时开启：

```env
DRY_RUN=true
```

此时脚本只会测速、解析结果和发送通知，不会调用华为云 DNS 更新接口。

## 支持能力

| 功能 | 状态 | 说明 |
|---|---:|---|
| Windows 本地运行 | 支持 | 默认使用 `cfst_windows_amd64/cfst.exe` |
| Linux 定时部署 | 支持 | 通过 `CFST_BINARY` 指定 Linux 版 `cfst` |
| 华为云 DNS 更新 | 支持 | 可更新任意数量的指定 `recordset_id` |
| 飞书通知 | 支持 | 支持新变量 `FEISHU_WEBHOOK_URL_CFST` |
| IPv4 记录 | 支持 | `DNS_RECORD_TYPE=A` |
| IPv6 记录 | 支持 | `DNS_RECORD_TYPE=AAAA`，需配合 IPv6 测速数据 |
| 演练模式 | 支持 | `DRY_RUN=true` 时不更新 DNS |

## 项目结构

```text
.
├── update_cf_ip.py          # 主脚本
├── .env.example             # 环境变量配置模板
├── dns_records.example.json # 多域名记录配置模板
├── requirements.txt         # Python 依赖
├── README.md                # 项目说明文档
├── cfst_windows_amd64/      # Windows 版 CloudflareSpeedTest
│   ├── cfst.exe             # Windows 可执行文件
│   ├── ip.txt               # 默认 IPv4 测速 IP 段
│   └── ipv6.txt             # 默认 IPv6 测速 IP 段
└── CloudflareSpeedTest/     # 已克隆的 CloudflareSpeedTest 源码项目
```

## 快速开始

### 1. 安装 Python 依赖

Windows PowerShell：

```powershell
cd D:\34621\Downloads\新建文件夹
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux：

```bash
cd /home/cfst-dns-sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 创建配置文件

```bash
cp .env.example .env
cp dns_records.example.json dns_records.json
```

Windows PowerShell 可直接复制：

```powershell
Copy-Item .env.example .env
Copy-Item dns_records.example.json dns_records.json
```

### 3. 填写环境变量

```env
HUAWEI_AK=你的 AccessKey
HUAWEI_SK=你的 SecretKey
HUAWEI_REGION=ap-southeast-1

DNS_RECORD_TYPE=A
DNS_RECORD_COUNT=1
DNS_RECORDS_FILE=dns_records.json

FEISHU_WEBHOOK_URL_CFST=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxx

DRY_RUN=true
```

### 4. 填写域名记录列表

编辑 `dns_records.json`：

```json
{
  "records": [
    {
      "name": "example.com",
      "zone_id": "你的第一个 zone_id",
      "recordset_id": "你的第一个 recordset_id",
      "type": "A",
      "ttl": 300
    },
    {
      "name": "example.net",
      "zone_id": "你的第二个 zone_id",
      "recordset_id": "你的第二个 recordset_id",
      "type": "A",
      "ttl": 300
    }
  ]
}
```

`records` 数组可以继续追加，脚本不会限制域名记录数量。所有记录会写入同一组 CFST 优选 IP。

### 5. 先演练运行

```bash
python update_cf_ip.py
```

确认输出里出现类似内容：

```text
正在运行 CloudflareSpeedTest...
已选择的优选 IP：
  104.xx.xx.xx 延迟=xx.xxms 速度=x.xxMB/s 机房=HKG
DRY_RUN=true，跳过华为云 DNS 更新。
```

确认无误后，将 `.env` 改为：

```env
DRY_RUN=false
```

再次运行即可正式更新华为云 DNS。

## 环境变量说明

### 华为云 API

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `HUAWEI_AK` | 是 | 空 | 华为云 AccessKey |
| `HUAWEI_SK` | 是 | 空 | 华为云 SecretKey |
| `HUAWEI_REGION` | 否 | `ap-southeast-1` | 华为云 DNS 服务区域 |

常见区域示例：

| 区域 | 值 |
|---|---|
| 华为云国际站新加坡 | `ap-southeast-1` |
| 华为云中国站华东上海一 | `cn-east-3` |
| 华为云中国站华北北京一 | `cn-north-1` |

### DNS 记录

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `DNS_RECORD_TYPE` | 否 | `A` | 记录类型，支持 `A` 或 `AAAA` |
| `DNS_RECORD_COUNT` | 否 | `1` | 写入前 N 个优选 IP |
| `DNS_RECORDS_FILE` | 否 | `dns_records.json` | 多域名记录配置文件 |
| `DNS_TTL` | 否 | `300` | DNS TTL，单位秒 |
| `DNS_ZONE_ID` | 否 | 空 | 单域名兼容配置：域名所在 zone 的 ID |
| `DNS_RECORDSET_ID` | 否 | 空 | 单域名兼容配置：要更新的解析记录集 ID |
| `DNS_RECORD_NAME` | 否 | 空 | 单域名兼容配置：用于日志和飞书通知的域名 |

推荐使用 `dns_records.json` 管理多域名。只要该文件存在，脚本会优先读取文件中的 `records` 数组；如果文件不存在，才会回退到 `DNS_ZONE_ID`、`DNS_RECORDSET_ID`、`DNS_RECORD_NAME` 这组三个单域名变量。

`DNS_RECORD_COUNT` 不做脚本层面的数量截断，会从 CFST 结果中按顺序取前 N 个 IP。实际能写入多少条记录，仍取决于华为云 DNS 对单个记录集的限制。

### 多域名记录文件

`dns_records.json` 支持两种写法。推荐对象写法：

```json
{
  "records": [
    {
      "name": "example.com",
      "zone_id": "ff808082xxxxxxxxxxxx",
      "recordset_id": "ff808082yyyyyyyyyyyy",
      "type": "A",
      "ttl": 300
    }
  ]
}
```

也可以直接写数组：

```json
[
  {
    "name": "example.com",
    "zone_id": "ff808082xxxxxxxxxxxx",
    "recordset_id": "ff808082yyyyyyyyyyyy",
    "type": "A",
    "ttl": 300
  }
]
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `name` | 是 | 域名名称，仅用于日志和飞书通知 |
| `zone_id` | 是 | 华为云 DNS 域名区域 ID |
| `recordset_id` | 是 | 华为云 DNS 解析记录集 ID |
| `type` | 否 | 记录类型，默认使用 `DNS_RECORD_TYPE` |
| `ttl` | 否 | 记录 TTL，默认使用 `DNS_TTL` |

所有记录的 `type` 必须与 `DNS_RECORD_TYPE` 保持一致。例如本次测速筛选的是 IPv4 `A` 记录，就不能把某条记录配置成 `AAAA`。

### 飞书通知

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `FEISHU_WEBHOOK_URL_CFST` | 否 | 空 | 飞书自定义机器人 Webhook |
如果该变量没配置，脚本会跳过飞书通知。

### CloudflareSpeedTest

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `CFST_BINARY` | 否 | 自动查找 | CFST 可执行文件路径 |
| `CFST_WORKDIR` | 否 | 可执行文件所在目录 | CFST 工作目录 |
| `CFST_RESULT_CSV` | 否 | `cfst_result.csv` | CFST 输出结果 CSV |
| `CFST_DOWNLOAD_COUNT` | 否 | `10` | 对应 CFST 的 `-dn` |
| `CFST_DOWNLOAD_SECONDS` | 否 | `10` | 对应 CFST 的 `-dt` |
| `CFST_MAX_LATENCY_MS` | 否 | `9999` | 对应 CFST 的 `-tl` |
| `CFST_MIN_SPEED_MB` | 否 | `0.01` | 对应 CFST 的 `-sl` |
| `CFST_EXTRA_ARGS` | 否 | 空 | 追加传给 CFST 的参数 |
| `CFST_TIMEOUT_SECONDS` | 否 | `3600` | 脚本等待 CFST 完成的最长秒数 |

脚本会固定追加以下参数：

```bash
-p 0 -o cfst_result.csv
```

这样可以让 CFST 不打印最终表格，只写入 CSV，方便脚本稳定解析。

### 演练模式

| 变量 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `DRY_RUN` | 否 | `false` | 设置为 `true` 时不更新华为云 DNS |

## 获取华为云 AK/SK

1. 登录华为云控制台。
2. 进入右上角头像菜单。
3. 打开“我的凭证”。
4. 进入“访问密钥”。
5. 创建 AccessKey。
6. 保存得到的 `AccessKey` 和 `SecretKey`。
7. 分别填入 `.env` 的 `HUAWEI_AK` 和 `HUAWEI_SK`。

注意：`.env` 包含敏感信息，不要提交到 Git 仓库。

## 获取 zone_id 和 recordset_id

脚本更新的是“已有记录集”，因此需要先在华为云 DNS 控制台为每个域名创建好对应记录。

示例：

| 字段 | 示例 |
|---|---|
| 主机记录 | `@` 或 `www` |
| 类型 | `A` |
| 线路 | 默认 |
| TTL | `300` |
| 值 | 可先填任意临时 IP，例如 `1.1.1.1` |

每条记录创建完成后，都需要拿到：

- `zone_id`：域名区域 ID。
- `recordset_id`：这条解析记录的 ID。

常见获取方式：

1. 在华为云 DNS 控制台打开对应域名。
2. 进入解析记录列表。
3. 打开浏览器开发者工具。
4. 在 Network 面板中刷新记录列表或编辑记录。
5. 从接口请求或响应中复制 `zone_id` 和 `recordset_id`。

也可以通过华为云 API 或 CLI 查询，只要最终把 ID 填入 `dns_records.json` 即可。

## 配置飞书机器人

1. 打开目标飞书群。
2. 进入群设置。
3. 选择“添加机器人”。
4. 添加“自定义机器人”。
5. 复制 Webhook 地址。
6. 填入 `.env`：

```env
FEISHU_WEBHOOK_URL_CFST=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxx
```

如果飞书机器人启用了“自定义关键词”安全策略，建议添加关键词：

```text
Cloudflare 优选 IP DNS 更新
```

## CFST 使用说明

### Windows 本地

项目已经包含 Windows 版：

```text
cfst_windows_amd64/cfst.exe
```

因此 Windows 本地运行时可以不设置 `CFST_BINARY`，脚本会自动查找。

### Linux 服务器

Linux 上需要下载对应系统架构的 CFST。amd64 示例：

```bash
mkdir -p /opt/cfst
cd /opt/cfst
wget -N https://github.com/XIU2/CloudflareSpeedTest/releases/latest/download/cfst_linux_amd64.tar.gz
tar -zxf cfst_linux_amd64.tar.gz
chmod +x cfst
```

`.env` 中设置：

```env
CFST_BINARY=/opt/cfst/cfst
CFST_WORKDIR=/opt/cfst
```

`CFST_WORKDIR` 很重要，因为 CFST 默认会读取工作目录里的 `ip.txt` 或 `ipv6.txt`。

### 常用测速参数

#### 更快完成测速

```env
CFST_DOWNLOAD_COUNT=5
CFST_DOWNLOAD_SECONDS=5
```

适合低配服务器或频繁执行的定时任务。

#### 更严格筛选

```env
CFST_MAX_LATENCY_MS=200
CFST_MIN_SPEED_MB=1
```

表示只保留平均延迟低于 `200ms` 且下载速度高于 `1MB/s` 的结果。

#### 使用自定义 IP 段文件

```env
CFST_EXTRA_ARGS=-f ip.txt
```

#### IPv6 测速

```env
DNS_RECORD_TYPE=AAAA
CFST_EXTRA_ARGS=-f ipv6.txt
```

注意：IPv6 是否可用取决于服务器网络环境、CFST 数据文件和目标访问链路。

#### HTTPing 模式

```env
CFST_EXTRA_ARGS=-httping
```

HTTPing 会实际发起 HTTP 请求，结果可能更贴近真实访问，但也更容易受到目标地址、网络限制和频率策略影响。

## Windows 测试运行

建议先手动跑一次：

```powershell
$env:DRY_RUN="true"
python update_cf_ip.py
```

如果 `.env` 中已经设置了 `DRY_RUN=true`，也可以直接执行：

```powershell
python update_cf_ip.py
```

运行成功后，项目目录下会生成：

```text
cfst_result.csv
```

该文件已加入 `.gitignore`，不会被提交。

## Linux 定时部署

假设项目目录为：

```text
/home/cfst-dns-sync
```

安装依赖：

```bash
cd /home/cfst-dns-sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

手动测试：

```bash
cd /home/cfst-dns-sync
source venv/bin/activate
python update_cf_ip.py
```

添加定时任务：

```bash
crontab -e
```

每天 00:45 执行：

```cron
45 0 * * * cd /home/cfst-dns-sync && /home/cfst-dns-sync/venv/bin/python /home/cfst-dns-sync/update_cf_ip.py >> /home/cfst-dns-sync/run.log 2>&1
```

查看日志：

```bash
tail -f /home/cfst-dns-sync/run.log
```

## 输出示例

演练模式：

```text
正在运行 CloudflareSpeedTest...
  可执行文件：/opt/cfst/cfst
  工作目录：/opt/cfst
  结果文件：/home/cfst-dns-sync/cfst_result.csv
  执行命令：/opt/cfst/cfst -dn 10 -dt 10 -tl 9999 -sl 0.01 -p 0 -o /home/cfst-dns-sync/cfst_result.csv

已选择的优选 IP：
  104.19.58.207 延迟=16.87ms 速度=0.07MB/s 机房=HKG

DRY_RUN=true，跳过华为云 DNS 更新。以下记录将使用同一组优选 IP：
  演练：example.com A -> ['104.19.58.207']
  演练：example.net A -> ['104.19.58.207']
飞书通知发送成功。
```

正式更新：

```text
已选择的优选 IP：
  104.19.58.207 延迟=16.87ms 速度=0.07MB/s 机房=HKG

开始同步华为云 DNS 记录：
  成功：example.com A -> ['104.19.58.207']
  成功：example.net A -> ['104.19.58.207']
飞书通知发送成功。
```

## 飞书通知内容

飞书通知会包含：

- 执行状态：成功、演练完成或失败。
- 记录数量：本次同步的 DNS 记录数量。
- 优选 IP：本次写入或准备写入的 IP。
- 测速详情：延迟、下载速度、机房代码。
- DNS 同步详情：每条域名记录的成功、演练或失败结果。
- 执行时间。
- 错误信息：失败时附带。

## 设计取舍

| 项目 | 当前策略 | 说明 |
|---|---|---|
| IP 来源 | CloudflareSpeedTest 测速结果 | 直接使用本机网络环境下的实测结果 |
| 更新范围 | 任意数量指定华为云记录集 | 多个域名共用同一组优选 IP |
| IPv4 | `DNS_RECORD_TYPE=A` | 默认推荐用于根域名解析 |
| IPv6 | `DNS_RECORD_TYPE=AAAA` | 需要服务器和访问链路支持 IPv6 |
| 失败保护 | 不清空原记录 | 没有可用 IP 时直接失败并通知 |
| 通知方式 | 飞书机器人 | 执行成功、演练和失败都会通知 |

如果你需要把多个根域名或子域名统一指向同一组 Cloudflare 优选 IP，只需要继续往 `dns_records.json` 的 `records` 数组里追加记录。

## 常见问题

### 1. 提示没有找到 CloudflareSpeedTest

错误示例：

```text
未找到 CloudflareSpeedTest 可执行文件，请在 .env 中设置 CFST_BINARY。
```

处理方法：

- Windows：确认 `cfst_windows_amd64/cfst.exe` 存在。
- Linux：确认已经下载并解压 CFST。
- 在 `.env` 中显式配置：

```env
CFST_BINARY=/opt/cfst/cfst
CFST_WORKDIR=/opt/cfst
```

### 2. CFST 没有筛出可用 IP

错误示例：

```text
CFST 结果 CSV 中没有可用的 A 记录。
```

可能原因：

- 当前网络无法连通 Cloudflare 测试 IP。
- `CFST_MAX_LATENCY_MS` 设置过低。
- `CFST_MIN_SPEED_MB` 设置过高。
- 下载测速地址不可用。
- 使用了 IPv6 但服务器没有可用 IPv6 网络。

排查建议：

```env
CFST_MAX_LATENCY_MS=9999
CFST_MIN_SPEED_MB=0
CFST_EXTRA_ARGS=-debug
```

重新运行后查看 CFST 输出。

### 3. 下载测速长期为 0.00 MB/s

可能原因：

- 默认下载测速地址在当前网络不可用。
- 测试 IP 是不可用的回源 IP。
- 网络运营商或服务器环境限制了访问。
- TLS 或 HTTP 请求失败。

建议先开启调试：

```env
CFST_EXTRA_ARGS=-debug
```

必要时使用自建测速地址，并通过 `CFST_EXTRA_ARGS` 追加 CFST 的 `-url` 参数。

### 4. 华为云 DNS 更新失败

常见原因：

- `HUAWEI_AK` 或 `HUAWEI_SK` 错误。
- `HUAWEI_REGION` 与账号或服务区域不匹配。
- `dns_records.json` 中某条记录的 `zone_id` 错误。
- `dns_records.json` 中某条记录的 `recordset_id` 错误。
- 记录类型不匹配，例如华为云上是 `A`，但脚本配置成 `AAAA`。

建议先设置：

```env
DRY_RUN=true
```

确认测速和解析正常后，再核对华为云相关 ID。

### 5. 飞书通知失败

可能原因：

- Webhook 地址填写错误。
- 飞书机器人安全关键词不匹配。
- 飞书接口触发频率限制。

脚本遇到飞书频率限制 `11232` 时，会等待 `120` 秒后重试，最多重试 `5` 次。

### 6. Windows 控制台出现中文乱码

脚本文件本身是 UTF-8。PowerShell 或旧版控制台如果使用非 UTF-8 代码页，可能会出现显示乱码，但不影响脚本执行。

可以尝试：

```powershell
chcp 65001
```

或者使用 Windows Terminal / PowerShell 7。

## 注意事项

- `.env` 包含密钥和 Webhook，不要提交到 Git。
- `dns_records.json` 可能包含真实域名 ID，默认也已加入 `.gitignore`。
- 首次运行建议使用 `DRY_RUN=true`。
- 不建议把 `DNS_RECORD_COUNT` 设置过大，多个 Cloudflare IP 会由 DNS 轮询返回，不一定总是命中最快 IP。
- 定时任务频率不宜过高，Cloudflare IP 质量一般不需要分钟级更新。
- 如果服务器环境与真实访问用户不在同一网络，测速结果只代表服务器自身网络质量。
- 如果你希望优化中国大陆家宽访问，最好在接近目标用户网络的位置运行测速。
- 如果目标域名已经接入 Cloudflare，需要确认源站、证书、回源规则和 Cloudflare 配置均正确。

## 参考命令

手动运行 CFST：

```bash
./cfst -dn 10 -dt 10 -tl 9999 -sl 0.01 -p 10
```

仅输出 CSV：

```bash
./cfst -dn 10 -dt 10 -tl 9999 -sl 0.01 -p 0 -o cfst_result.csv
```

查看 CSV：

```bash
head -n 5 cfst_result.csv
```

停止 Linux 定时任务：

```bash
crontab -e
```

删除对应的 `45 0 * * * ...` 行即可。
