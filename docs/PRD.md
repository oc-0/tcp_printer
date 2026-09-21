# TCP Printer 文件与打印门户 PRD

## 1. 文档信息

- 产品名称：TCP Printer 局域网文件与自助打印门户
- 部署位置：Windows Mini PC
- 目标用户：同一局域网内的学生、教师或实验室成员
- 文档状态：打印与文件库存储模块已实现基础版本

## 2. 产品目标

在一台 Windows Mini PC 上提供两个相互隔离的能力：

1. 开放的自助打印入口：用户无需登录即可上传文件、预览并打印。
2. 受保护的文件库：用户使用学号和密码登录后，按权限上传、检索、下载和维护资料。

打印临时文件不进入文件库，文件库原件不自动进入打印队列。两个模块共用一个 Web 服务，但使用独立目录、数据表和清理策略。

## 3. 访问入口与权限

| 入口 | 认证 | 主要能力 |
|---|---|---|
| `/` | 无需登录 | 文件上传、PDF 预览、打印 |
| `/files` | 学号 + 密码 | 文件库浏览、搜索、下载、上传和维护自己的文件 |
| `/admin` | 管理员令牌 | 用户、文件、队列、设备和系统管理 |

打印入口完全对局域网开放，但仍受文件大小、文件类型、份数和队列限制。文件库不再使用共享 Storage Token，而是使用管理员创建的学号账号。

### 3.1 用户账号

- 管理员录入姓名、学号并启用账号；管理员可在账号管理中授予或收回其他账号的管理员权限。
- 管理员登录需要学号、密码和 `TCP_PRINTER_ADMIN_TOKEN`；`TCP_PRINTER_ADMIN_STUDENT_ID` 对应的后门账号始终拥有管理员权限且不显示在账号列表。
- 新账号初始密码为 `111111`。
- 用户首次登录必须修改密码，修改前不能访问文件库内容。
- 管理员可启用/禁用账号、重置密码；不能查看当前密码明文。
- 重置密码后恢复为 `111111`，并再次要求用户修改。
- 密码只保存 Argon2id 或 bcrypt 哈希值。
- 连续登录失败需要临时锁定；密码重置后注销该用户已有会话。
- 学号唯一，姓名允许重复。

### 3.2 文件权限

- 登录成员和管理员可以查看、下载、上传、修改和删除资料库文件及文件夹。
- 文件夹删除采用软删除；管理员可以恢复文件和文件夹。
- 删除采用软删除，保留恢复周期后再物理清理。
- 恢复周期与 `TCP_PRINTER_RETENTION_HOURS` 一致；到期后删除文件版本、ZIP 清单、元数据和管理员列表记录。
- 所有上传、修改、删除、下载、密码重置和权限变更写入审计日志。

## 4. 打印模块需求

### 4.1 已实现

- 支持 PDF、DOC/DOCX、XLS/XLSX、PPT/PPTX、JPG、PNG。
- Windows 下 DOC/DOCX 默认使用 Microsoft Word COM 导出 PDF。
- Word 自动化使用独立进程、禁用宏、关闭文档、退出 Word 并释放 COM。
- 未安装 Word、无法启动 Word 或导出失败时返回明确错误。
- Ubuntu 兼容模式继续使用 LibreOffice headless。
- `TCP_PRINTER_OFFICE_CONVERTER=auto/word/libreoffice` 可配置转换器。
- Windows 打印模式使用已安装打印机驱动，支持黑白/彩色、份数、页码范围、纵向/横向方向、奇数/偶数页和取消队列作业。
- 保留 CUPS 和 dry-run 模式。
- 保留网页上传、PDF.js 预览、SQLite 队列和过期文件清理。
- 管理员可暂停/恢复后续网页任务，查看 Windows Job ID 和队列诊断。

### 4.2 打印限制

- HP CP1020/CP1025 驱动可能不向 Windows 标准接口报告缺纸、卡纸或面板灯状态。
- 作业进入打印机内部缓存后，Windows 队列和网页可能无法撤回。
- 网页状态必须区分“已提交到 Windows 队列”和“设备已实际出纸”；不能把队列提交当作硬件完成。
- 打印临时目录独立于文件库，并按保留时间自动清理。

## 5. 文件库一期需求

### 5.1 文件范围

- 支持 PDF、DOC/DOCX、PPT/PPTX、XLS/XLSX、JPG、PNG、ZIP。
- SolidWorks 资料统一以 ZIP 上传，不单独支持 `.sldprt`、`.sldasm`、`.slddrw`。
- ZIP 原文件不自动解压；可读取并展示内部文件清单，但不执行内部文件。
- ZIP 主要用于保存完整 SolidWorks 装配体及其零件、工程图。
- 不支持在线预览的文件仍必须支持下载。

### 5.2 元数据

每个文件支持编辑：

- 标题
- 简介
- 版本号
- 文件夹
- 多个标签
- 可见范围
- 版本说明

原始文件使用随机服务器文件名保存，原始显示名称单独存入数据库。编辑简介不修改 DOC、PDF 或 ZIP 内部内容。

### 5.3 文件夹、标签和搜索

- 文件夹支持父子层级，一个文件归属一个文件夹；新建、重命名、删除文件夹不会删除其中的文件。
- 标签支持多选，一个文件可以关联多个标签。
- 搜索覆盖文件名、标题、简介、版本号、扩展名、文件夹名称和标签；不搜索 ZIP 内部文件名。
- 支持关键字、文件夹筛选、标签筛选、文件类型筛选和排序。
- 文件数量较少时使用 SQLite 查询；数量增长后使用 SQLite FTS5。

### 5.4 推荐数据表

```text
students
groups
tags
files
file_versions
file_tags
zip_entries
sessions
audit_logs
```

`files` 与 `file_versions` 保存 owner、原始名称、随机存储名、扩展名、MIME、大小、SHA-256、元数据、可见性和时间字段。

### 5.5 推荐接口

```text
POST   /api/storage/login
POST   /api/storage/logout
GET    /api/storage/me

GET    /api/files
POST   /api/files
GET    /api/files/{id}
PATCH  /api/files/{id}/metadata
POST   /api/files/{id}/versions
GET    /api/files/{id}/download
DELETE /api/files/{id}

GET    /api/groups
POST   /api/groups
PATCH  /api/groups/{id}
DELETE /api/groups/{id}
```

### 5.6 安全和容量

- 所有文件通过 API 鉴权读取，不直接暴露 `storage` 目录。
- 拒绝路径穿越、绝对路径和 ZIP 符号链接。
- 不限制资料库总容量和单文件上传大小；仍限制 ZIP 内文件数量、展开总大小，并拒绝路径穿越和符号链接。
- 文件下载使用原始显示文件名和正确的 `Content-Disposition`；登录成员还可以将整个文件夹（包括嵌套子文件夹）下载为 ZIP。
- 存储文件与打印文件使用独立根目录和生命周期。
- 管理员页显示资料库实际使用量，不显示固定配额。
- 建议每日备份 SQLite 数据库及 `repository` 原始文件。

## 6. 存储目录规划

```text
data/
  printer.db
  repository.db       # 可选；也可与 printer.db 共用数据库

storage/
  print-jobs/         # 打印上传和转换临时文件，自动清理
  repository/
    originals/        # 文件库原始 PDF、Office、图片、ZIP
    derived/          # 文件库预览或 ZIP 清单等派生数据
```

文件库删除不能删除打印任务文件，打印清理不能删除文件库原件。

## 7. HTTPS 部署方案

推荐使用 Caddy 作为本机反向代理：

```text
手机/浏览器 -- HTTPS:443 --> Caddy -- HTTP:127.0.0.1:8080 --> FastAPI
```

原因：Caddy 自动管理证书和反向代理，FastAPI 不需要处理 TLS；应用继续使用现有 `run.py` 和 Word COM 逻辑。

### 7.1 局域网无公网域名

可使用 Caddy 的内部 CA：

```text
https://tcp-printer.local
```

但手机和其他电脑需要安装并信任 Caddy 根证书，否则浏览器会显示证书不受信任。根证书通常位于：

```text
C:\ProgramData\Caddy\pki\authorities\local\root.crt
```

局域网 DNS 或每台设备的 hosts 文件需要把 `tcp-printer.local` 指向 Mini PC 的局域网 IP。

### 7.2 有域名且可进行 DNS 验证

使用一个域名，例如 `printer.example.com`，将 DNS 指向可访问的反向代理或使用 DNS challenge。这样可以使用受操作系统和手机信任的公有证书，但需要域名和证书服务配置。

### 7.3 Caddyfile 示例

```caddyfile
{
    auto_https disable_redirects
}

https://tcp-printer.local {
    tls internal
    reverse_proxy 127.0.0.1:8080
}
```

FastAPI 的 `.env` 保持：

```ini
TCP_PRINTER_HOST=127.0.0.1
TCP_PRINTER_PORT=8080
```

如果仍要让未配置 HTTPS 的局域网设备直接访问，可暂时保留 `0.0.0.0`，但正式运行建议只让 Caddy 对外开放 443，并用 Windows 防火墙限制 8080 仅本机访问。

### 7.4 Windows 启动顺序

1. Caddy 作为 Windows 服务或任务计划程序启动。
2. TCP Printer 任务在用户登录时启动，以保证 Word COM 有交互式桌面会话。
3. Caddy 反向代理到 `127.0.0.1:8080`。
4. 通过 `https://tcp-printer.local/health` 验证。

Caddy 可以作为系统服务启动，但 TCP Printer 不建议用 `SYSTEM` 服务运行；Word COM 需要实际用户会话。

## 8. 验收标准

### 打印

- DOC/DOCX 公式转换与 Word 手工导出结果一致。
- 黑白、彩色、份数、页码范围、纵向/横向方向以及奇数/偶数页均可正确打印。
- 打印队列为空时不会误报旧作业。
- HP 驱动弹窗出现时，网页至少显示在线、停滞或需要检查设备等可读状态。

### 文件库

- 未登录不能访问文件库 API 和下载链接。
- 首次使用 `111111` 后必须修改密码。
- 发布者只能修改/删除自己的文件，管理员可管理全部文件。
- ZIP、PDF、Office、图片可上传、下载、编辑简介、文件夹和标签。
- 关键字、文件夹和标签筛选结果正确。
- 文件库文件不会出现在打印任务目录，打印临时文件不会出现在文件库列表。

### 运行维护

- Windows 重启并登录后服务自动启动。
- HTTPS 地址可从手机访问，证书信任方式有明确文档。
- 管理员可查看磁盘、队列、设备 Job ID 和审计日志。
- 数据库和文件库原件可按计划备份和恢复。

## 9. 分期计划

1. 文件库基础：账号、登录、上传、下载、删除、磁盘配额。
2. 元数据管理：简介、文件夹、标签、搜索和审计日志。
3. ZIP 管理：安全检查、内部文件清单、版本管理。
4. HTTPS 和备份：Caddy、证书信任、自动备份和恢复演练。
5. 与打印模块联调：保持两个目录、权限和清理策略完全隔离。
