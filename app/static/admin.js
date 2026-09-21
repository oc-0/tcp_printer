const tokenInput = document.getElementById("admin-token");
const authPanel = document.getElementById("auth-panel");
const dashboard = document.getElementById("dashboard");
const authError = document.getElementById("auth-error");
const tokenKey = "tcp-printer-admin-token";
const accountPageSize = 3;
let adminToken = sessionStorage.getItem(tokenKey) || "";
let accountsExpanded = false;
let editingStorageFile = null;
let storageFiles = [];
let storageFolders = [];
let adminStorageFolderId = null;
const storageEditModal = document.getElementById("storage-edit-modal");
const storageEditForm = document.getElementById("storage-edit-form");
const storageEditError = document.getElementById("storage-edit-error");
const folderEditModal = document.getElementById("folder-edit-modal");
const folderEditForm = document.getElementById("folder-edit-form");
const folderEditError = document.getElementById("folder-edit-error");
const accountCreateModal = document.getElementById("account-create-modal");
const accountCreateForm = document.getElementById("account-create-form");
const accountCreateError = document.getElementById("account-create-error");

const adminPages = new Set(["overview", "storage", "recycle-bin", "accounts", "printer"]);

function switchAdminPage(page) {
  const selectedPage = adminPages.has(page) ? page : "overview";
  document.querySelectorAll("[data-admin-view]").forEach((view) => {
    view.classList.toggle("hidden", view.dataset.adminView !== selectedPage);
  });
  document.querySelectorAll("[data-admin-page]").forEach((link) => {
    const active = link.dataset.adminPage === selectedPage;
    link.classList.toggle("active", active);
    link.setAttribute("aria-current", active ? "page" : "false");
  });
  if (window.location.hash !== `#${selectedPage}`) {
    window.history.replaceState(null, "", `#${selectedPage}`);
  }
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
}

function syncAdminPageFromHash() {
  switchAdminPage(window.location.hash.slice(1));
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function formatBytes(value) {
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", { hour12: false });
}

function activityLabel(activity) {
  return {
    "file.upload": "上传了资料",
    "file.delete": "删除了资料",
    "file.purge": "彻底删除了资料",
    "file.restore": "恢复了资料",
    "file.metadata": "修改了资料信息",
    "file.version": "上传了新版本",
    "file.download": "下载了资料",
    "folder.create": "创建了文件夹",
    "folder.metadata": "修改了文件夹",
    "folder.delete": "删除了文件夹",
    "folder.purge": "彻底删除了文件夹",
    "folder.restore": "恢复了文件夹",
    "system.cleanup": "清理了任务缓存",
  }[activity] || activity;
}

function formatState(state) {
  return { pending: "等待打印", printing: "正在打印", completed: "已发送至打印机", cancelled: "已取消", stopped: "已停止", failed: "失败", ready: "待确认", converting: "转换中" }[state] || state;
}

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}), "X-Admin-Token": adminToken };
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败。");
  return payload;
}

function metric(label, value) {
  const element = document.createElement("div");
  element.className = "metric";
  element.innerHTML = `<span class="muted">${label}</span><strong>${value}</strong>`;
  return element;
}

function renderAccounts(accounts) {
  const table = document.getElementById("account-table");
  const moreButton = document.getElementById("accounts-more");
  const rows = Array.isArray(accounts) ? accounts : [];
  if (!rows.length) {
    table.innerHTML = '<tr><td colspan="6" class="muted">暂无账号数据</td></tr>';
    moreButton.hidden = true;
    accountsExpanded = false;
    moreButton.setAttribute("aria-expanded", "false");
    return;
  }
  const visibleRows = accountsExpanded ? rows : rows.slice(0, accountPageSize);
  table.replaceChildren(...visibleRows.map((account) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(account.name || "-")}</td><td>${escapeHtml(account.student_id || "-")}</td><td>${account.is_admin ? "管理员" : "成员"}</td><td>${escapeHtml(account.status || "-")}</td><td>${escapeHtml(formatDate(account.last_login_at))}</td><td></td>`;
    const actionCell = row.lastElementChild;
    actionCell.append(
      action("重置密码", () => resetAccount(account.id)),
      action(account.is_admin ? "收回管理员" : "设为管理员", () => toggleAdminRole(account), actionOptions("secondary-action")),
      action(account.status === "已禁用" ? "启用" : "禁用", () => toggleAccount(account)),
      action("删除", () => deleteAccount(account.id)),
    );
    return row;
  }));
  const hasMore = rows.length > accountPageSize;
  moreButton.hidden = !hasMore;
  moreButton.textContent = accountsExpanded ? "收起" : `更多（${rows.length - accountPageSize}）`;
  moreButton.setAttribute("aria-expanded", String(accountsExpanded));
}

async function deleteAccount(id) {
  if (!window.confirm("确定删除这个账号吗？仍拥有资料库文件的账号无法删除。")) return;
  try {
    await request(`/api/admin/accounts/${encodeURIComponent(id)}`, { method: "DELETE" });
    showAccountMessage("账号已删除。");
    await refresh();
  } catch (error) { showAccountMessage(error.message); }
}

function showAccountMessage(message) {
  authError.textContent = message;
}

function adminFolderPath(folderId) {
  const path = [];
  let current = storageFolders.find((folder) => folder.id === folderId);
  while (current) {
    path.unshift(current);
    current = storageFolders.find((folder) => folder.id === current.parent_id);
  }
  return path;
}

function renderAdminStoragePath() {
  const target = document.getElementById("admin-storage-path");
  if (!target) return;
  target.replaceChildren();
  const query = String(document.getElementById("admin-storage-search")?.value || "").trim();
  if (query) {
    const label = document.createElement("span");
    label.textContent = `搜索结果：${query}`;
    target.append(label);
    return;
  }
  const root = document.createElement("button");
  root.type = "button";
  root.textContent = "资料库";
  root.addEventListener("click", () => openAdminStorageFolder(null));
  target.append(root);
  adminFolderPath(adminStorageFolderId).forEach((folder) => {
    const separator = document.createElement("span");
    separator.textContent = "/";
    const link = document.createElement("button");
    link.type = "button";
    link.textContent = folder.name;
    link.addEventListener("click", () => openAdminStorageFolder(folder.id));
    target.append(separator, link);
  });
}

function openAdminStorageFolder(folderId) {
  adminStorageFolderId = folderId;
  const search = document.getElementById("admin-storage-search");
  if (search) search.value = "";
  renderStorageFiles(storageFiles, storageFolders);
}

function storageItemMatches(item, query) {
  return `${item.name || ""} ${item.title || ""} ${item.original_name || ""} ${item.description || ""} ${item.owner_name || ""} ${item.owner_student_id || ""} ${(item.tags || []).join(" ")}`.toLowerCase().includes(query);
}

function storageItemIcon(item) {
  if (item.type === "folder") return "DIR";
  const extension = String(item.extension || "").replace(".", "").toUpperCase();
  return (extension || "FILE").slice(0, 4);
}

function renderStorageFiles(files, folders) {
  const table = document.getElementById("storage-file-table");
  if (Array.isArray(files)) storageFiles = files;
  if (Array.isArray(folders)) storageFolders = folders;
  const query = String(document.getElementById("admin-storage-search")?.value || "").trim().toLowerCase();
  const folderRows = storageFolders
    .filter((folder) => query ? storageItemMatches(folder, query) : folder.parent_id === adminStorageFolderId)
    .map((folder) => ({ ...folder, type: "folder", name: folder.name }));
  const fileRows = storageFiles
    .filter((file) => query ? storageItemMatches(file, query) : file.group_id === adminStorageFolderId)
    .map((file) => ({ ...file, type: "file", name: file.original_name }));
  const rows = [...folderRows, ...fileRows].sort((left, right) => {
    if (left.type !== right.type) return left.type === "folder" ? -1 : 1;
    return String(left.name || "").localeCompare(String(right.name || ""), "zh-CN");
  });
  renderAdminStoragePath();
  if (!rows.length) {
    table.innerHTML = '<tr><td colspan="6" class="muted">当前目录没有内容</td></tr>';
    return;
  }
  table.replaceChildren(...rows.map((file) => {
    const row = document.createElement("tr");
    const isFolder = file.type === "folder";
    const deleted = Boolean(file.deleted_at);
    const tags = Array.isArray(file.tags) && file.tags.length ? file.tags.join("、") : "-";
    const owner = isFolder ? "-" : (file.owner_name ? `${file.owner_name}（${file.owner_student_id || "-"}）` : (file.owner_id || "-"));
    const name = isFolder ? file.name : (file.title || file.original_name);
    const subline = isFolder ? `${file.file_count || 0} 个文件 · ${file.child_folder_count || 0} 个子文件夹` : file.original_name;
    const type = isFolder ? (deleted ? "已删除" : "文件夹") : (deleted ? "已删除" : storageItemIcon(file));
    row.className = isFolder ? `admin-storage-folder-row${deleted ? " deleted" : ""}` : "";
    row.innerHTML = `<td><span class="admin-storage-item-icon ${isFolder ? "folder" : "file"}">${escapeHtml(storageItemIcon(file))}</span><strong>${escapeHtml(name)}</strong><div class="muted">${escapeHtml(subline)}</div></td><td>${escapeHtml(owner)}</td><td>${escapeHtml(tags)}</td><td>${type}</td><td>${file.updated_at ? new Date(file.updated_at).toLocaleString() : "-"}</td><td></td>`;
    const cell = row.lastElementChild;
    if (isFolder && deleted) {
      cell.append(action("恢复", () => restoreStorageFolder(file.id), actionOptions("secondary-action")));
    } else if (isFolder) {
      row.title = "双击打开文件夹";
      row.addEventListener("dblclick", () => openAdminStorageFolder(file.id));
      cell.append(action("编辑", () => editStorageFolder(file), actionOptions("secondary-action")));
      cell.append(action("删除", () => deleteStorageFolder(file.id)));
    } else if (!deleted) {
      cell.append(action("编辑资料", () => editStorageFile(file), actionOptions("secondary-action")));
      cell.append(action("删除", () => deleteStorageFile(file.id)));
    } else {
      cell.append(action("恢复", () => restoreStorageFile(file.id), actionOptions("secondary-action")));
    }
    return row;
  }));
}

function renderRecycleBin(recycleBin) {
  const table = document.getElementById("recycle-bin-table");
  if (!table) return;
  const files = Array.isArray(recycleBin?.files) ? recycleBin.files : [];
  const allFolders = Array.isArray(recycleBin?.folders) ? recycleBin.folders : [];
  const deletedFolderIds = new Set(allFolders.map((folder) => folder.id));
  const folders = allFolders.filter((folder) => !deletedFolderIds.has(folder.parent_id));
  const rows = [
    ...folders.map((folder) => ({ ...folder, type: "folder", name: folder.name })),
    ...files.map((file) => ({ ...file, type: "file", name: file.title || file.original_name })),
  ].sort((left, right) => String(right.deleted_at || "").localeCompare(String(left.deleted_at || "")));
  if (!rows.length) {
    table.innerHTML = '<tr><td colspan="5" class="muted">回收站为空</td></tr>';
    return;
  }
  table.replaceChildren(...rows.map((item) => {
    const row = document.createElement("tr");
    const isFolder = item.type === "folder";
    const displayName = isFolder ? item.name : (item.title || item.original_name);
    const detail = isFolder ? "文件夹" : (item.original_name || "文件");
    row.innerHTML = `<td><span class="admin-storage-item-icon ${isFolder ? "folder" : "file"}">${escapeHtml(isFolder ? "DIR" : storageItemIcon(item))}</span><strong>${escapeHtml(displayName || "-")}</strong><div class="muted">${escapeHtml(detail)}</div></td><td>${isFolder ? "文件夹" : "文件"}</td><td>${escapeHtml(formatDate(item.deleted_at))}</td><td>${isFolder ? "-" : formatBytes(Number(item.size || 0))}</td><td></td>`;
    row.lastElementChild.append(
      action("恢复", () => restoreRecycleItem(item)),
      action("彻底删除", () => purgeRecycleItem(item), actionOptions("danger-action")),
    );
    return row;
  }));
}

function actionOptions(className) { return { className }; }

function editStorageFolder(folder) {
  document.getElementById("admin-folder-name").value = folder.name || "";
  document.getElementById("admin-folder-description").value = folder.description || "";
  document.getElementById("admin-folder-tags").value = (folder.tags || []).join(", ");
  const parent = document.getElementById("admin-folder-parent");
  parent.replaceChildren(new Option("根目录", ""), ...storageFolders.filter((item) => item.id !== folder.id && !item.deleted_at).map((item) => new Option(item.name, item.id)));
  parent.value = folder.parent_id || "";
  folderEditError.textContent = "";
  folderEditModal.dataset.folderId = folder.id;
  folderEditModal.classList.remove("hidden");
  document.getElementById("admin-folder-name").focus();
}

function closeStorageFolderEdit() {
  folderEditModal.dataset.folderId = "";
  folderEditModal.classList.add("hidden");
  folderEditError.textContent = "";
}

async function saveStorageFolder(event) {
  event.preventDefault();
  const folderId = folderEditModal.dataset.folderId;
  if (!folderId) return;
  const values = new FormData(folderEditForm);
  const name = String(values.get("name") || "").trim();
  if (!name) {
    folderEditError.textContent = "文件夹名称不能为空。";
    return;
  }
  const submit = document.getElementById("folder-edit-submit");
  submit.disabled = true;
  try {
    await request(`/api/admin/folders/${encodeURIComponent(folderId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: values.get("description"), tags: String(values.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean), parent_id: values.get("parent_id") || null }),
    });
    closeStorageFolderEdit();
    await refresh();
  } catch (error) {
    folderEditError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

async function deleteStorageFolder(id) {
  if (!window.confirm("确定删除这个文件夹吗？文件夹将在保留期内进入回收状态。")) return;
  try { await request(`/api/admin/folders/${encodeURIComponent(id)}`, { method: "DELETE" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function resetAccount(id) {
  try { await request(`/api/admin/accounts/${encodeURIComponent(id)}/reset-password`, { method: "POST" }); showAccountMessage("密码已重置为初始密码。 "); await refresh(); }
  catch (error) { showAccountMessage(error.message); }
}

async function toggleAccount(account) {
  try { await request(`/api/admin/accounts/${encodeURIComponent(account.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: account.status === "已禁用" }) }); await refresh(); }
  catch (error) { showAccountMessage(error.message); }
}

async function toggleAdminRole(account) {
  try {
    await request(`/api/admin/accounts/${encodeURIComponent(account.id)}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_admin: !account.is_admin }) });
    await refresh();
  } catch (error) { showAccountMessage(error.message); }
}

function openAccountCreate() {
  accountCreateForm.reset();
  document.getElementById("account-initial-password").value = "111111";
  accountCreateError.textContent = "";
  accountCreateModal.classList.remove("hidden");
  document.getElementById("account-name").focus();
}

function closeAccountCreate() {
  accountCreateModal.classList.add("hidden");
  accountCreateError.textContent = "";
}

async function addAccount(event) {
  event.preventDefault();
  const values = new FormData(accountCreateForm);
  const name = String(values.get("name") || "").trim();
  const studentId = String(values.get("student_id") || "").trim();
  if (!name || !studentId) {
    accountCreateError.textContent = "姓名和学号不能为空。";
    return;
  }
  const submitButton = document.getElementById("account-create-submit");
  submitButton.disabled = true;
  accountCreateError.textContent = "";
  try {
    await request("/api/admin/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, student_id: studentId, password: "111111" }) });
    showAccountMessage("账号已创建，初始密码为 111111。 ");
    closeAccountCreate();
    await refresh();
  } catch (error) { accountCreateError.textContent = error.message; }
  finally { submitButton.disabled = false; }
}

function renderStatus(payload) {
  const metrics = document.getElementById("metrics");
  const queue = payload.queue || {};
  const storage = payload.storage || {};
  const repository = payload.repository || {};
  metrics.replaceChildren(
    metric("服务器状态", payload.server ? "在线" : "未知"),
    metric("内存", payload.server?.memory?.percent == null ? "-" : `${payload.server.memory.percent}%`),
    metric("磁盘可用", formatBytes(storage.free || 0)),
    metric("资料库文件", repository.active_files || 0),
    metric("等待任务", queue.pending || 0),
    metric("打印中", queue.printing || 0),
  );
  document.getElementById("pause-queue").disabled = Boolean(queue.paused);
  document.getElementById("resume-queue").disabled = !queue.paused;
  document.getElementById("storage-detail").textContent = `已用 ${formatBytes(storage.used || 0)} · 可用 ${formatBytes(storage.free || 0)}`;
  renderAccounts(payload.accounts);
  renderStorageFiles(payload.repository_files, payload.repository_folders);
  renderRecycleBin(payload.recycle_bin || {});
  const detail = payload.printer_details || {};
  document.getElementById("printer-detail").textContent = detail.error
    ? detail.error
    : `打印机队列：${detail.queue_name || "未配置"} · 原始状态：${detail.raw_status ?? "不适用"}`;
  const printerTable = document.getElementById("printer-job-table");
  printerTable.replaceChildren();
  (detail.jobs || []).forEach((job) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(job.id)}</td><td>${escapeHtml(job.document)}</td><td>${escapeHtml(job.user)}</td><td>${escapeHtml(job.status)}</td><td>${escapeHtml(`${job.pages_printed}/${job.total_pages}`)}</td><td></td>`;
    row.lastElementChild.append(action("取消", () => cancelPrinterJob(job.id)));
    printerTable.append(row);
  });
  if (!detail.jobs?.length) printerTable.innerHTML = '<tr><td colspan="6" class="muted">当前没有 Windows 打印作业</td></tr>';
  const table = document.getElementById("job-table");
  table.replaceChildren();
  payload.jobs.forEach((job) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(job.public_id)}</td><td>${escapeHtml(job.file_name)}</td><td>${formatState(job.state)}<div class="muted">${escapeHtml(job.message || "")}</div></td><td>${escapeHtml(job.printer_job_id || "-")}</td><td>${new Date(job.created_at).toLocaleString()}</td><td></td>`;
    const actionCell = row.lastElementChild;
    if (["pending", "ready", "converting"].includes(job.state)) {
      actionCell.append(action("取消", () => changeJob(job.id, "cancel")));
    } else if (job.state === "printing") {
      actionCell.append(action("停止", () => changeJob(job.id, "stop")));
    }
    table.append(row);
  });
  document.getElementById("last-updated").textContent = `上次更新：${new Date().toLocaleString()} · 自动保留 ${payload.retention_hours} 小时`;
  renderActivity(payload.activities || []);
  renderRepositorySummary(payload.repository || {});
  renderPrinterSummary(payload);
}

function renderActivity(activities) {
  const target = document.getElementById("activity-list");
  if (!activities.length) {
    target.innerHTML = '<p class="muted">暂无资料库活动记录</p>';
    return;
  }
  target.replaceChildren(...activities.slice(0, 8).map((item) => {
    const row = document.createElement("div");
    row.className = "admin-activity-item";
    const actor = item.actor_name || "管理员";
    const subject = item.file_name || item.target_user_name || item.details || "资料库对象";
    row.innerHTML = `<span class="activity-avatar">${escapeHtml(Array.from(actor)[0] || "管")}</span><div><p><strong>${escapeHtml(actor)}</strong> ${escapeHtml(activityLabel(item.action))} <span>${escapeHtml(subject)}</span></p><time>${escapeHtml(formatDate(item.created_at))}</time></div>`;
    return row;
  }));
}

function renderRepositorySummary(repository) {
  const target = document.getElementById("repository-summary");
  const quota = Number(repository.quota || 0);
  const used = Number(repository.used || 0);
  const percent = quota ? Math.min(100, Math.round((used / quota) * 100)) : 0;
  const capacity = quota ? ` / ${formatBytes(quota)}` : " · 不设容量上限";
  const meter = quota ? `<div class="repository-meter"><span style="width:${percent}%"></span></div>` : "";
  target.innerHTML = `<div class="repository-summary-count"><strong>${repository.active_files || 0}</strong><span>个有效文件</span><strong>${repository.folders || 0}</strong><span>个文件夹</span></div>${meter}<p class="muted">已使用 ${formatBytes(used)}${capacity} · 回收站 ${repository.deleted_files || 0} 个文件、${repository.deleted_folders || 0} 个文件夹</p>`;
}

function renderPrinterSummary(payload) {
  const target = document.getElementById("printer-status-summary");
  const printer = payload.printer || {};
  const detail = payload.printer_details || {};
  target.innerHTML = `<div class="printer-summary-status"><span class="status-dot" data-state="${escapeHtml(printer.state || "offline")}"></span><strong>${escapeHtml(printer.label || "状态未知")}</strong><span class="muted">队列：${payload.queue?.paused ? "已暂停" : "运行中"} · 原始状态：${escapeHtml(String(detail.raw_status ?? "-"))}</span></div>`;
}

function action(label, handler, options = {}) {
  const button = document.createElement("button");
  button.className = `table-action${options.className ? ` ${options.className}` : ""}`;
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function editStorageFile(file) {
  editingStorageFile = file;
  document.getElementById("admin-file-title").value = file.title || file.original_name || "";
  document.getElementById("admin-file-description").value = file.description || "";
  document.getElementById("admin-file-version").value = file.version || "1.0";
  document.getElementById("admin-file-tags").value = (file.tags || []).join(", ");
  document.getElementById("storage-edit-file-name").textContent = file.original_name || "";
  storageEditError.textContent = "";
  storageEditModal.classList.remove("hidden");
  document.getElementById("admin-file-title").focus();
}

function closeStorageEdit() {
  editingStorageFile = null;
  storageEditModal.classList.add("hidden");
  storageEditError.textContent = "";
}

async function saveStorageFile(event) {
  event.preventDefault();
  if (!editingStorageFile) return;
  const values = new FormData(storageEditForm);
  const title = String(values.get("title") || "").trim();
  if (!title) {
    storageEditError.textContent = "标题不能为空。";
    return;
  }
  const tags = String(values.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean);
  const submitButton = document.getElementById("storage-edit-submit");
  submitButton.disabled = true;
  storageEditError.textContent = "";
  try {
    await request(`/api/admin/files/${encodeURIComponent(editingStorageFile.id)}/metadata`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        description: values.get("description"),
        version: String(values.get("version") || "1.0").trim() || "1.0",
        tags,
        visibility: editingStorageFile.visibility || "team",
      }),
    });
    closeStorageEdit();
    await refresh();
  } catch (error) {
    storageEditError.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

async function deleteStorageFile(id) {
  if (!window.confirm("确定删除这个资料库文件吗？文件会进入回收状态。")) return;
  try { await request(`/api/admin/files/${encodeURIComponent(id)}`, { method: "DELETE" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function restoreStorageFile(id) {
  try { await request(`/api/admin/files/${encodeURIComponent(id)}/restore`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function restoreStorageFolder(id) {
  try { await request(`/api/admin/folders/${encodeURIComponent(id)}/restore`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function restoreRecycleItem(item) {
  try {
    const path = item.type === "folder" ? `/api/admin/folders/${encodeURIComponent(item.id)}/restore` : `/api/admin/files/${encodeURIComponent(item.id)}/restore`;
    await request(path, { method: "POST" });
    await refresh();
  } catch (error) { authError.textContent = error.message; }
}

async function purgeRecycleItem(item) {
  const label = item.type === "folder" ? "文件夹及其内容" : "文件";
  if (!window.confirm(`确定彻底删除这个${label}吗？删除后无法恢复。`)) return;
  try {
    const path = item.type === "folder" ? `/api/admin/folders/${encodeURIComponent(item.id)}/purge` : `/api/admin/files/${encodeURIComponent(item.id)}/purge`;
    await request(path, { method: "DELETE" });
    await refresh();
  } catch (error) { authError.textContent = error.message; }
}

async function refresh() {
  try { renderStatus(await request("/api/admin/status")); }
  catch (error) { authError.textContent = error.message; }
}

async function changeJob(id, operation) {
  try { await request(`/api/admin/jobs/${id}/${operation}`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function cancelPrinterJob(id) {
  try { await request(`/api/admin/printer/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function setQueuePaused(paused) {
  try { await request(`/api/admin/queue/${paused ? "pause" : "resume"}`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function enter(useExistingSession = false) {
  const studentId = document.getElementById("admin-student-id").value.trim();
  const password = document.getElementById("admin-password").value;
  adminToken = tokenInput.value.trim();
  if (!adminToken || (!useExistingSession && (!studentId || !password))) { authError.textContent = "请输入管理员学号、密码和令牌。"; return; }
  try {
    sessionStorage.setItem(tokenKey, adminToken);
    if (!useExistingSession) {
      const result = await fetch("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ student_id: studentId, password, admin_token: adminToken }) });
      const payload = await result.json().catch(() => ({}));
      if (!result.ok) throw new Error(payload.detail || "管理员登录失败。");
      if (payload.must_change_password) {
        authError.textContent = "管理员账号需要先修改初始密码，请前往资料库登录页完成修改。";
        sessionStorage.removeItem(tokenKey);
        return;
      }
    }
    renderStatus(await request("/api/admin/status"));
    authPanel.classList.add("hidden");
    dashboard.classList.remove("hidden");
    syncAdminPageFromHash();
    authError.textContent = "";
  } catch (error) {
    sessionStorage.removeItem(tokenKey);
    authError.textContent = error.message;
  }
}

document.getElementById("save-token").addEventListener("click", enter);
document.querySelectorAll("[data-admin-page]").forEach((link) => {
  link.addEventListener("click", () => switchAdminPage(link.dataset.adminPage));
});
window.addEventListener("hashchange", syncAdminPageFromHash);
syncAdminPageFromHash();
document.getElementById("refresh-status").addEventListener("click", refresh);
document.getElementById("pause-queue").addEventListener("click", () => setQueuePaused(true));
document.getElementById("resume-queue").addEventListener("click", () => setQueuePaused(false));
async function cleanupStorage() {
  try {
    const result = await request("/api/admin/cleanup", { method: "POST" });
    authError.textContent = result.message || `已清理 ${result.removed_jobs || 0} 个已结束任务`;
    await refresh();
  }
  catch (error) { authError.textContent = error.message; }
}
document.getElementById("run-cleanup-storage").addEventListener("click", cleanupStorage);
document.getElementById("refresh-recycle-bin").addEventListener("click", refresh);
document.getElementById("accounts-more").addEventListener("click", () => {
  accountsExpanded = !accountsExpanded;
  refresh();
});
document.getElementById("add-account").addEventListener("click", openAccountCreate);
document.getElementById("account-create-close").addEventListener("click", closeAccountCreate);
document.getElementById("account-create-cancel").addEventListener("click", closeAccountCreate);
accountCreateForm.addEventListener("submit", addAccount);
accountCreateModal.addEventListener("click", (event) => { if (event.target === accountCreateModal) closeAccountCreate(); });
document.getElementById("refresh-storage-files").addEventListener("click", refresh);
document.getElementById("admin-storage-search").addEventListener("input", () => renderStorageFiles(storageFiles, storageFolders));
document.getElementById("storage-edit-close").addEventListener("click", closeStorageEdit);
document.getElementById("storage-edit-cancel").addEventListener("click", closeStorageEdit);
storageEditForm.addEventListener("submit", saveStorageFile);
storageEditModal.addEventListener("click", (event) => { if (event.target === storageEditModal) closeStorageEdit(); });
document.getElementById("folder-edit-close").addEventListener("click", closeStorageFolderEdit);
document.getElementById("folder-edit-cancel").addEventListener("click", closeStorageFolderEdit);
folderEditForm.addEventListener("submit", saveStorageFolder);
folderEditModal.addEventListener("click", (event) => { if (event.target === folderEditModal) closeStorageFolderEdit(); });
tokenInput.addEventListener("keydown", (event) => { if (event.key === "Enter") enter(); });
if (adminToken) { tokenInput.value = adminToken; enter(true); }
