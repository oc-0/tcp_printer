const fileList = document.getElementById("storage-file-list-body");
const pageError = document.getElementById("storage-page-error");
const searchInput = document.getElementById("storage-search-input");
const uploadButton = document.getElementById("storage-upload-button");
const uploadInput = document.getElementById("storage-upload-input");
const metadataModal = document.getElementById("storage-modal");
const metadataForm = document.getElementById("storage-metadata-form");
const folderModal = document.getElementById("storage-folder-modal");
const folderForm = document.getElementById("storage-folder-form");
let items = [];
let folders = [];
let selectedItem = null;
let pendingUpload = null;
let editingFolder = null;
let currentUser = null;
let currentFolderId = null;
let itemsRequestId = 0;

const byId = (id) => document.getElementById(id);

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value == null ? "" : String(value);
  return element.innerHTML;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date).replaceAll("/", "-");
}

function parseTags(value) {
  return String(value || "").split(",").map((tag) => tag.trim()).filter(Boolean);
}

function renderStorageUser(user) {
  const menu = byId("storage-user-menu");
  const name = String(user?.name || user?.student_id || "用户").trim();
  const avatar = Array.from(name)[0] || "?";
  byId("storage-user-avatar").textContent = avatar;
  byId("storage-user-name").textContent = name;
  menu.title = `${name}（${user?.student_id || ""}）`;
  menu.hidden = false;
}

function fileType(item) {
  if (item.type === "folder") return "文件夹";
  const ext = (item.extension || "").replace(".", "").toUpperCase();
  return ext || "文件";
}

function iconLabel(item) {
  return item.type === "folder" ? "DIR" : fileType(item).slice(0, 4);
}

function folderPath(folderId) {
  const path = [];
  let current = folders.find((folder) => folder.id === folderId);
  while (current) {
    path.unshift(current);
    current = folders.find((folder) => folder.id === current.parent_id);
  }
  return path;
}

function renderBreadcrumb() {
  const breadcrumb = byId("storage-breadcrumb");
  breadcrumb.replaceChildren();
  if (currentFolderId) {
    const currentFolder = folders.find((folder) => folder.id === currentFolderId);
    const parentButton = document.createElement("button");
    parentButton.type = "button";
    parentButton.className = "storage-parent-button";
    parentButton.textContent = "← 返回上级";
    parentButton.setAttribute("aria-label", "返回上级目录");
    parentButton.addEventListener("click", () => openFolder(currentFolder?.parent_id || null));
    breadcrumb.append(parentButton);
  }
  const root = document.createElement("button");
  root.type = "button";
  root.textContent = "资料库";
  root.addEventListener("click", () => openFolder(null));
  breadcrumb.append(root);
  folderPath(currentFolderId).forEach((folder) => {
    const separator = document.createElement("span");
    separator.textContent = "/";
    const link = document.createElement("button");
    link.type = "button";
    link.textContent = folder.name;
    link.addEventListener("click", () => openFolder(folder.id));
    breadcrumb.append(separator, link);
  });
}

function selectItem(item) {
  selectedItem = item;
  renderItems();
  renderDetails();
}

function renderItems() {
  if (!items.length) {
    fileList.innerHTML = '<p class="storage-empty muted">当前目录为空。</p>';
    renderDetails();
    return;
  }
  fileList.replaceChildren(...items.map((item) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `storage-item-row storage-list-grid${selectedItem?.id === item.id ? " selected" : ""}`;
    const name = document.createElement("span");
    name.className = "storage-item-name";
    name.innerHTML = `<span class="storage-item-icon ${item.type}">${escapeHtml(iconLabel(item))}</span><span><strong>${escapeHtml(item.name)}</strong>${item.type === "folder" ? `<small>${item.file_count || 0} 个文件 · ${item.child_folder_count || 0} 个子文件夹</small>` : ""}</span>`;
    const updated = document.createElement("time");
    updated.className = "storage-item-date";
    updated.dateTime = item.updated_at || item.created_at || "";
    updated.textContent = formatDate(item.updated_at || item.created_at);
    row.append(name, updated);
    row.addEventListener("click", () => selectItem(item));
    row.addEventListener("dblclick", () => { if (item.type === "folder") openFolder(item.id); });
    return row;
  }));
  if (!selectedItem || !items.some((item) => item.id === selectedItem.id)) selectItem(items[0]);
}

function setHidden(id, hidden) { byId(id).classList.toggle("hidden", hidden); }

function renderDetails() {
  const item = selectedItem;
  const isFolder = item?.type === "folder";
  byId("file-detail-title").textContent = item ? item.name : "选择一个项目";
  byId("file-detail-type").textContent = item ? (isFolder ? "文件夹" : `${fileType(item)} 文件`) : "资料库项目";
  byId("file-detail-icon").textContent = item ? iconLabel(item) : "--";
  byId("file-detail-icon").className = `storage-detail-icon${item ? ` ${item.type}` : ""}`;
  byId("file-detail-description").textContent = item?.description || (item ? "暂无简介" : "选择文件或文件夹后查看简介。");
  const tags = byId("file-detail-tags");
  tags.replaceChildren(...(item?.tags || []).map((tag) => { const node = document.createElement("span"); node.textContent = tag; return node; }));
  const properties = byId("file-detail-properties");
  properties.replaceChildren();
  if (item) {
    const rows = isFolder ? [["创建时间", formatDate(item.created_at)], ["更新时间", formatDate(item.updated_at)], ["包含内容", `${item.file_count || 0} 个文件，${item.child_folder_count || 0} 个子文件夹`]] : [["发布者", `${item.owner_name || "-"} · ${item.owner_student_id || "-"}`], ["更新时间", formatDate(item.updated_at)], ["文件大小", `${(Number(item.size || 0) / 1024 / 1024).toFixed(2)} MB`]];
    rows.forEach(([label, value]) => { const term = document.createElement("dt"); term.textContent = label; const detail = document.createElement("dd"); detail.textContent = value; properties.append(term, detail); });
  }
  setHidden("storage-folder-open", !isFolder); setHidden("storage-edit-folder", !isFolder); setHidden("storage-edit", !item || isFolder); setHidden("storage-delete", !item); setHidden("storage-download", !item);
  byId("storage-download").textContent = isFolder ? "下载文件夹" : "下载文件";
}

async function loadFolders() {
  folders = await request("/api/folders");
  const selects = [byId("folder-parent")];
  selects.forEach((select) => {
    const previous = select.value;
    select.replaceChildren(new Option("根目录", ""), ...folders.map((folder) => new Option(folder.name, folder.id)));
    if (folders.some((folder) => folder.id === previous)) select.value = previous;
  });
}

async function loadItems() {
  const requestId = ++itemsRequestId;
  try {
    const params = new URLSearchParams();
    if (currentFolderId) params.set("parent_id", currentFolderId);
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
    const nextItems = await request(`/api/storage/items${params.toString() ? `?${params}` : ""}`);
    if (requestId !== itemsRequestId) return;
    items = nextItems;
    pageError.textContent = "";
    renderBreadcrumb();
    renderItems();
  } catch (error) {
    if (requestId !== itemsRequestId) return;
    pageError.textContent = error.message;
    fileList.innerHTML = '<p class="storage-empty muted">无法加载资料。</p>';
  }
}

async function openFolder(folderId) {
  clearTimeout(window.storageSearchTimer);
  window.storageSearchTimer = null;
  currentFolderId = folderId;
  selectedItem = null;
  searchInput.value = "";
  itemsRequestId += 1;
  items = [];
  fileList.innerHTML = '<p class="storage-empty muted">正在加载资料...</p>';
  await loadItems();
}

function openMetadataModal(file = null) {
  pendingUpload = file ? null : uploadInput.files[0];
  byId("storage-modal-title").textContent = file ? "编辑资料" : "上传文件";
  byId("storage-selected-upload").textContent = pendingUpload ? `文件：${pendingUpload.name}` : "修改资料信息";
  byId("metadata-title").value = file?.title || pendingUpload?.name.replace(/\.[^.]+$/, "") || "";
  byId("metadata-description").value = file?.description || "";
  byId("metadata-version").value = file?.version || "1.0";
  byId("metadata-tags").value = (file?.tags || []).join(", ");
  byId("storage-modal-error").textContent = "";
  metadataModal.classList.remove("hidden");
  byId("metadata-title").focus();
}

function closeMetadataModal() { metadataModal.classList.add("hidden"); pendingUpload = null; uploadInput.value = ""; }

function openFolderModal(folder = null) {
  editingFolder = folder;
  byId("storage-folder-modal-title").textContent = folder ? "编辑文件夹" : "新建文件夹";
  byId("folder-name").value = folder?.name || "";
  byId("folder-description").value = folder?.description || "";
  byId("folder-tags").value = (folder?.tags || []).join(", ");
  byId("folder-parent").value = folder?.parent_id || currentFolderId || "";
  byId("storage-folder-error").textContent = "";
  folderModal.classList.remove("hidden");
  byId("folder-name").focus();
}

function closeFolderModal() { folderModal.classList.add("hidden"); editingFolder = null; }

searchInput.addEventListener("input", () => { clearTimeout(window.storageSearchTimer); window.storageSearchTimer = setTimeout(loadItems, 250); });
uploadButton.addEventListener("click", () => uploadInput.click());
uploadInput.addEventListener("change", () => { if (uploadInput.files[0]) openMetadataModal(); });
byId("storage-new-folder").addEventListener("click", () => openFolderModal());
byId("storage-folder-open").addEventListener("click", () => { if (selectedItem?.type === "folder") openFolder(selectedItem.id); });
byId("storage-edit-folder").addEventListener("click", () => { if (selectedItem?.type === "folder") openFolderModal(selectedItem); });
byId("storage-edit").addEventListener("click", () => { if (selectedItem?.type === "file") openMetadataModal(selectedItem); });
byId("storage-download").addEventListener("click", () => {
  if (!selectedItem) return;
  const endpoint = selectedItem.type === "folder"
    ? `/api/folders/${encodeURIComponent(selectedItem.id)}/download`
    : `/api/files/${encodeURIComponent(selectedItem.id)}/download`;
  window.location.href = endpoint;
});
byId("storage-delete").addEventListener("click", async () => {
  if (!selectedItem || !window.confirm(`确定删除这个${selectedItem.type === "folder" ? "文件夹" : "文件"}吗？删除后将在保留期内进入回收状态。`)) return;
  try {
    const endpoint = selectedItem.type === "folder" ? `/api/folders/${encodeURIComponent(selectedItem.id)}` : `/api/files/${encodeURIComponent(selectedItem.id)}`;
    await request(endpoint, { method: "DELETE" });
    const nextFolder = selectedItem.type === "folder" ? (selectedItem.parent_id || null) : currentFolderId;
    selectedItem = null;
    await loadFolders();
    await openFolder(nextFolder);
  } catch (error) { pageError.textContent = error.message; }
});
byId("storage-modal-close").addEventListener("click", closeMetadataModal); byId("storage-modal-cancel").addEventListener("click", closeMetadataModal);
byId("storage-folder-close").addEventListener("click", closeFolderModal); byId("storage-folder-cancel").addEventListener("click", closeFolderModal);

metadataForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(metadataForm);
  const tags = parseTags(values.get("tags"));
  try {
    if (pendingUpload) {
      values.append("file", pendingUpload); values.set("tags", JSON.stringify(tags)); values.set("group_id", currentFolderId || "");
      await request("/api/files", { method: "POST", body: values });
    } else if (selectedItem?.type === "file") {
      await request(`/api/files/${encodeURIComponent(selectedItem.id)}/metadata`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: values.get("title"), description: values.get("description"), version: values.get("version"), group_id: selectedItem.group_id || null, tags }) });
    }
    closeMetadataModal(); await loadFolders(); await loadItems();
  } catch (error) { byId("storage-modal-error").textContent = error.message; }
});

folderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(folderForm);
  const payload = { name: String(values.get("name") || "").trim(), description: values.get("description") || "", tags: parseTags(values.get("tags")), parent_id: values.get("parent_id") || null };
  try {
    const url = editingFolder ? `/api/folders/${encodeURIComponent(editingFolder.id)}` : "/api/folders";
    await request(url, { method: editingFolder ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    closeFolderModal(); await loadFolders(); await loadItems();
  } catch (error) { byId("storage-folder-error").textContent = error.message; }
});

Promise.all([loadFolders(), request("/api/storage/me").then((user) => { currentUser = user; renderStorageUser(user); })]).then(loadItems).catch((error) => { pageError.textContent = error.message; });
