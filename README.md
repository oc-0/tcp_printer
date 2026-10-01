# TCP Printer

一款面向局域网的cp1025自助打印与资料库 Web 服务。

## 功能

### 打印服务

- PDF、图片、DOC/DOCX、XLS/XLSX、PPT/PPTX 上传与 PDF 预览。
- 黑白/彩色、份数、页码范围、纵向/横向、全部/奇数/偶数页打印。
- `dry-run`、Windows 和 Ubuntu/CUPS 三种模式。
- SQLite 打印队列、任务取消、状态查询和自动清理。
- Windows 下 DOC/DOCX 优先使用 Microsoft Word 导出 PDF，适合复杂公式。

### 资料库

- 学号登录；新账号初始密码为 `111111`，首次登录必须修改。
- 文件和文件夹统一管理，支持新建、重命名、简介、标签和搜索。
- 上传单个文件或整个文件夹，保留文件夹层级结构。
- 支持任意文件类型，不预览或转换资料库文件。
- 文件下载、文件夹 ZIP 下载、历史版本和回收站。
- 删除内容按 `TCP_PRINTER_RETENTION_HOURS` 保留，管理员可以恢复或彻底删除。
- 资料库不限制总容量和单文件大小；容量受磁盘空间限制。

## 快速开始

需要 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

浏览器访问 `http://127.0.0.1:8080`。局域网其他设备访问 `http://<Mini-PC局域网IP>:8080`。服务监听地址应设置为 `0.0.0.0`，并允许 Windows 防火墙放行 TCP 8080。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 配置


```ini
TCP_PRINTER_MODE=windows
TCP_PRINTER_HOST=0.0.0.0
TCP_PRINTER_PORT=8080
TCP_PRINTER_QUEUE=HP LaserJet Professional CP1020 Series
TCP_PRINTER_OFFICE_CONVERTER=auto
TCP_PRINTER_RETENTION_HOURS=24
TCP_PRINTER_ADMIN_TOKEN=请替换为随机令牌
TCP_PRINTER_STORAGE_SESSION_HOURS=24
```

| 配置项 | 说明 |
| --- | --- |
| `TCP_PRINTER_MODE` | `dry-run`、`windows` 或 `cups`；默认 `dry-run` |
| `TCP_PRINTER_QUEUE` | Windows 打印机名称或 CUPS 队列名称 |
| `TCP_PRINTER_OFFICE_CONVERTER` | `auto`、`word` 或 `libreoffice` |
| `TCP_PRINTER_MAX_UPLOAD_MB` | 打印服务单文件上限，默认 200 MB |
| `TCP_PRINTER_RETENTION_HOURS` | 打印任务文件和资料库回收站保留时间 |
| `TCP_PRINTER_ADMIN_TOKEN` | 管理员登录令牌；为空时禁用管理员页面 |
| `TCP_PRINTER_ADMIN_STUDENT_ID` | 隐藏后门管理员学号，可选 |
| `TCP_PRINTER_ADMIN_PASSWORD` | 隐藏后门管理员密码，可选，至少 6 位 |
| `TCP_PRINTER_RELOAD` | 开发环境代码自动重载，生产环境建议 `false` |

## Windows 部署

1. 安装 Microsoft Word 桌面版、打印机驱动和 Python。
2. 使用运行服务的 Windows 用户手动启动一次 Word，完成首次启动提示。
3. 设置 `.env`：

```ini
TCP_PRINTER_MODE=windows
TCP_PRINTER_OFFICE_CONVERTER=auto
TCP_PRINTER_QUEUE=你的 Windows 打印机名称
```

`auto` 模式会使用 Word 转换 DOC/DOCX；其他 Office 文件使用 LibreOffice。转换需要交互式桌面会话，不能使用 `LocalSystem` 账户。

注册用户登录时自动启动：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\register-windows-task.ps1
Start-ScheduledTask -TaskName "TCP Printer"
```

计划任务由 `deploy/run-windows-task.ps1` 监控服务，日志写入 `data/windows-task.stdout.log` 和 `data/windows-task.stderr.log`。删除任务：

```powershell
.\deploy\register-windows-task.ps1 -Remove
```

## Ubuntu / CUPS 部署

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libreoffice cups cups-client cups-filters
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
lpstat -p
```

`.env` 示例：

```ini
TCP_PRINTER_MODE=cups
TCP_PRINTER_QUEUE=your-cups-queue
TCP_PRINTER_OFFICE_CONVERTER=libreoffice
```

如打印机使用 `ColorModel` 选项，再设置 `TCP_PRINTER_COLOR_OPTION=ColorModel`、`TCP_PRINTER_COLOR_MONO=Gray` 和 `TCP_PRINTER_COLOR_COLOR=RGB`。

## 资料库使用

从首页选择“资料库服务”，使用学号和密码登录。首次密码为 `111111`，登录后修改密码。

- 当前目录按分页加载，打开文件夹后只读取该目录内容。
- “上传”可选择文件或文件夹；文件夹上传会保留目录结构。
- 文件夹和文件都可以编辑简介、标签、名称和删除。
- 搜索可查找文件夹和文件名，不搜索 ZIP 内部文件。
- 选中文件夹可下载包含完整层级结构的 ZIP。
- 删除后进入回收站；管理员可恢复或彻底删除。
- 资料库文件保存在 `data/repository.db` 和 `storage/repository/`，不应提交到 Git。

## 管理员

设置 `TCP_PRINTER_ADMIN_TOKEN` 后重启服务，在资料库登录页进入管理员登录。管理员登录需要学号、密码和管理员令牌。

管理中心支持查看打印机状态、打印队列和资料库统计，管理账号权限，以及修改资料库文件夹和回收站内容。管理员不提供资料下载按钮。

生成随机令牌：

```bash
openssl rand -hex 32
```

## 项目结构

```text
app/                 FastAPI 应用、转换器、队列和打印后端
app/static/          前端资源与本地 PDF.js
app/templates/       页面模板
deploy/              Windows 计划任务与 systemd 脚本
data/                SQLite 数据库和运行日志
storage/             打印临时文件和资料库原件
tests/               单元测试
```
