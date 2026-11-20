const fileList = document.getElementById("storage-file-list-body");
const pageError = document.getElementById("storage-page-error");
const searchInput = document.getElementById("storage-search-input");
const loadMoreButton = document.getElementById("storage-load-more");
const uploadButton = document.getElementById("storage-upload-button");
const uploadInput = document.getElementById("storage-upload-input");
const folderUploadInput = document.getElementById("storage-folder-upload-input");
const uploadChoiceModal = document.getElementById("storage-upload-choice");
const uploadProgressModal = document.getElementById("storage-upload-progress");
const uploadProgressBar = document.getElementById("storage-upload-progress-bar");
const uploadProgressLabel = document.getElementById("storage-upload-progress-label");
const uploadProgressCount = document.getElementById("storage-upload-progress-count");
const metadataModal = document.getElementById("storage-modal");
const metadataForm = document.getElementById("storage-metadata-form");
const folderModal = document.getElementById("storage-folder-modal");
const folderForm = document.getElementById("storage-folder-form");
const previewModal = document.getElementById("storage-preview-modal");
let items = [];
let folders = [];
let selectedItem = null;
let pendingUpload = null;
let editingFolder = null;
let currentUser = null;
let currentFolderId = null;
let itemsRequestId = 0;
let itemsOffset = 0;
let itemsHasMore = false;
let itemsLoading = false;
let previewObjectUrl = null;
let previewRequestId = 0;
let previewTemp = null;

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

function uploadWithProgress(url, form, { onProgress = () => {} } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    });
    xhr.addEventListener("load", () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText || "{}"); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
      else reject(new Error(payload.detail || `上传失败（${xhr.status}）`));
    });
    xhr.addEventListener("error", () => reject(new Error("网络连接中断，上传失败。")));
    xhr.addEventListener("abort", () => reject(new Error("上传已取消。")));
    xhr.send(form);
  });
}

function showUploadProgress(label, count, value = 0) {
  uploadProgressLabel.textContent = label;
  uploadProgressCount.textContent = count;
  uploadProgressBar.value = value;
  uploadProgressModal.classList.remove("hidden");
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

function supportsPreview(item) {
  if (!item || item.type !== "file") return false;
  return [".pdf", ".doc", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]
    .includes(String(item.extension || "").toLowerCase());
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
    loadMoreButton.classList.add("hidden");
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
  loadMoreButton.classList.toggle("hidden", !itemsHasMore);
  loadMoreButton.disabled = itemsLoading;
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
  setHidden("storage-preview", !supportsPreview(item));
  byId("storage-download").textContent = isFolder ? "下载文件夹" : "下载文件";
}

function closePreview() {
  previewRequestId += 1;
  releasePreviewTemp();
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  previewModal.classList.add("hidden");
  byId("storage-preview-frame").src = "about:blank";
  byId("storage-preview-image").removeAttribute("src");
  byId("storage-preview-text").textContent = "";
  byId("storage-preview-error").textContent = "";
}

function releasePreviewTemp(temp = previewTemp) {
  if (!temp) return;
  if (previewTemp?.previewId === temp.previewId) previewTemp = null;
  fetch(`/api/files/${encodeURIComponent(temp.fileId)}/preview-temp/${encodeURIComponent(temp.previewId)}`, {
    method: "DELETE",
    keepalive: true,
  }).catch(() => {});
}

async function openPreview(item) {
  if (!supportsPreview(item)) return;
  releasePreviewTemp();
  const requestId = ++previewRequestId;
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  const extension = String(item.extension || "").toLowerCase();
  const url = `/api/files/${encodeURIComponent(item.id)}/preview`;
  const frame = byId("storage-preview-frame");
  const image = byId("storage-preview-image");
  const text = byId("storage-preview-text");
  byId("storage-preview-title").textContent = item.title || item.original_name;
  frame.classList.add("hidden");
  image.classList.add("hidden");
  text.classList.add("hidden");
  byId("storage-preview-error").textContent = "";
  previewModal.classList.remove("hidden");
  if ([".doc", ".docx"].includes(extension)) {
    frame.classList.remove("hidden");
    frame.src = "about:blank";
    let temp = null;
    try {
      const response = await fetch(url);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `预览请求失败（${response.status}）`);
      }
      const previewId = response.headers.get("X-Preview-Id");
      temp = previewId ? { fileId: item.id, previewId } : null;
      if (requestId === previewRequestId) previewTemp = temp;
      const pdf = await response.blob();
      if (requestId !== previewRequestId || previewModal.classList.contains("hidden")) {
        releasePreviewTemp(temp);
        return;
      }
      previewObjectUrl = URL.createObjectURL(pdf);
      frame.src = `/static/vendor/pdfjs/viewer.html?file=${encodeURIComponent(previewObjectUrl)}&disableRange=true`;
    } catch (error) {
      releasePreviewTemp(temp);
      if (requestId === previewRequestId) {
        frame.classList.add("hidden");
        byId("storage-preview-error").textContent = `Word 转 PDF 预览失败：${error.message}`;
      }
    }
    return;
  }
  if (extension === ".pdf") {
    frame.classList.remove("hidden");
    frame.src = "about:blank";
    try {
      const response = await fetch(url);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `预览请求失败（${response.status}）`);
      }
      const pdf = await response.blob();
      if (requestId !== previewRequestId || previewModal.classList.contains("hidden")) return;
      previewObjectUrl = URL.createObjectURL(pdf);
      frame.src = `/static/vendor/pdfjs/viewer.html?file=${encodeURIComponent(previewObjectUrl)}&disableRange=true`;
    } catch (error) {
      if (requestId === previewRequestId) {
        frame.classList.add("hidden");
        byId("storage-preview-error").textContent = `PDF 预览失败：${error.message}`;
      }
    }
    return;
  }
  if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"].includes(extension)) {
    image.classList.remove("hidden");
    image.src = url;
    image.alt = item.title || item.original_name;
    image.onerror = () => { byId("storage-preview-error").textContent = "图片加载失败。"; };
    return;
  }
  text.classList.remove("hidden");
  text.textContent = "正在加载预览...";
  try {
    const response = await fetch(url);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "无法加载预览");
    }
    text.textContent = await response.text();
  } catch (error) {
    text.textContent = "";
    byId("storage-preview-error").textContent = error.message;
  }
}

function cacheFolders(nextItems) {
  nextItems.filter((item) => item.type === "folder").forEach((folder) => {
    const index = folders.findIndex((item) => item.id === folder.id);
    if (index === -1) folders.push(folder);
    else folders[index] = folder;
  });
}

async function loadItems({ append = false } = {}) {
  const requestId = ++itemsRequestId;
  if (itemsLoading) return;
  itemsLoading = true;
  loadMoreButton.disabled = true;
  try {
    const params = new URLSearchParams();
    if (currentFolderId) params.set("parent_id", currentFolderId);
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
    params.set("limit", "100");
    params.set("offset", append ? String(itemsOffset) : "0");
    const response = await fetch(`/api/storage/items?${params}`);
    const nextItems = await response.json().catch(() => []);
    if (!response.ok) throw new Error(nextItems.detail || "请求失败");
    if (requestId !== itemsRequestId) return;
    items = append ? [...items, ...nextItems] : nextItems;
    cacheFolders(nextItems);
    itemsOffset = items.length;
    itemsHasMore = response.headers.get("X-Has-More") === "1";
    pageError.textContent = "";
    renderBreadcrumb();
    renderItems();
  } catch (error) {
    if (requestId !== itemsRequestId) return;
    pageError.textContent = error.message;
    fileList.innerHTML = '<p class="storage-empty muted">无法加载资料。</p>';
  } finally {
    itemsLoading = false;
    loadMoreButton.disabled = false;
    loadMoreButton.classList.toggle("hidden", !itemsHasMore);
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
  itemsOffset = 0;
  itemsHasMore = false;
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

function closeUploadChoice() { uploadChoiceModal.classList.add("hidden"); }

async function uploadFolderFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const folderIds = new Map();
  const folderKey = (parentId, name) => `${parentId || "root"}/${name}`;
  const getOrCreateFolder = async (name, parentId) => {
    const key = folderKey(parentId, name);
    if (folderIds.has(key)) return folderIds.get(key);
    const existing = folders.find((folder) => folder.parent_id === (parentId || null) && folder.name === name);
    const folder = existing || await request("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: parentId || null, description: "", tags: [] }),
    });
    folderIds.set(key, folder.id);
    return folder.id;
  };
  closeUploadChoice();
  showUploadProgress("正在准备上传...", `0 / ${files.length} 个文件`);
  pageError.textContent = "";
  try {
    let completed = 0;
    for (const file of files) {
      const relativePath = String(file.webkitRelativePath || file.name).replaceAll("\\", "/");
      const parts = relativePath.split("/").filter(Boolean);
      const fileName = parts.pop() || file.name;
      let parentId = currentFolderId || null;
      for (const folderName of parts) parentId = await getOrCreateFolder(folderName, parentId);
      const form = new FormData();
      form.append("file", file, fileName);
      form.set("title", fileName.replace(/\.[^.]+$/, ""));
      form.set("description", "");
      form.set("version", "1.0");
      form.set("group_id", parentId || "");
      form.set("tags", "[]");
      uploadProgressLabel.textContent = `正在上传：${fileName}`;
      await uploadWithProgress("/api/files", form, {
        onProgress: (loaded, total) => {
          const filePercent = Math.min(100, loaded / total * 100);
          uploadProgressBar.value = Math.round(((completed + filePercent / 100) / files.length) * 100);
          uploadProgressCount.textContent = `${completed} / ${files.length} 个文件 · ${Math.round(filePercent)}%`;
        },
      });
      completed += 1;
      uploadProgressBar.value = Math.round((completed / files.length) * 100);
      uploadProgressCount.textContent = `${completed} / ${files.length} 个文件`;
    }
    uploadProgressLabel.textContent = "上传完成";
    pageError.textContent = `文件夹上传完成，共 ${files.length} 个文件。`;
    await loadItems();
  } catch (error) {
    uploadProgressLabel.textContent = "上传未完成";
    pageError.textContent = `文件夹上传未完成：${error.message}`;
    await loadItems();
  } finally {
    uploadProgressModal.classList.add("hidden");
    folderUploadInput.value = "";
  }
}

function openFolderModal(folder = null) {
  editingFolder = folder;
  byId("storage-folder-modal-title").textContent = folder ? "编辑文件夹" : "新建文件夹";
  byId("folder-name").value = folder?.name || "";
  byId("folder-description").value = folder?.description || "";
  byId("folder-tags").value = (folder?.tags || []).join(", ");
  byId("storage-folder-error").textContent = "";
  folderModal.classList.remove("hidden");
  byId("folder-name").focus();
}

function closeFolderModal() { folderModal.classList.add("hidden"); editingFolder = null; }

searchInput.addEventListener("input", () => {
  clearTimeout(window.storageSearchTimer);
  window.storageSearchTimer = setTimeout(() => {
    itemsOffset = 0;
    itemsHasMore = false;
    loadItems();
  }, 250);
});
loadMoreButton.addEventListener("click", () => loadItems({ append: true }));
uploadButton.addEventListener("click", () => uploadChoiceModal.classList.remove("hidden"));
byId("storage-upload-choice-close").addEventListener("click", closeUploadChoice);
byId("storage-upload-files-choice").addEventListener("click", () => { closeUploadChoice(); uploadInput.click(); });
byId("storage-upload-folder-choice").addEventListener("click", () => { closeUploadChoice(); folderUploadInput.click(); });
uploadChoiceModal.addEventListener("click", (event) => { if (event.target === uploadChoiceModal) closeUploadChoice(); });
uploadInput.addEventListener("change", () => { if (uploadInput.files[0]) openMetadataModal(); });
folderUploadInput.addEventListener("change", () => uploadFolderFiles(folderUploadInput.files));
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
byId("storage-preview").addEventListener("click", () => openPreview(selectedItem));
byId("storage-preview-close").addEventListener("click", closePreview);
previewModal.addEventListener("click", (event) => { if (event.target === previewModal) closePreview(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !previewModal.classList.contains("hidden")) closePreview(); });
byId("storage-delete").addEventListener("click", async () => {
  if (!selectedItem || !window.confirm(`确定删除这个${selectedItem.type === "folder" ? "文件夹" : "文件"}吗？删除后将在保留期内进入回收状态。`)) return;
  try {
    const endpoint = selectedItem.type === "folder" ? `/api/folders/${encodeURIComponent(selectedItem.id)}` : `/api/files/${encodeURIComponent(selectedItem.id)}`;
    await request(endpoint, { method: "DELETE" });
    const nextFolder = selectedItem.type === "folder" ? (selectedItem.parent_id || null) : currentFolderId;
    selectedItem = null;
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
      const fileName = pendingUpload.name;
      closeMetadataModal();
      showUploadProgress(`正在上传：${fileName}`, `0 / 1 个文件 · 0%`);
      pageError.textContent = "";
      try {
        await uploadWithProgress("/api/files", values, {
          onProgress: (loaded, total) => {
            const percent = Math.min(100, Math.round(loaded / total * 100));
            uploadProgressBar.value = percent;
            uploadProgressCount.textContent = `0 / 1 个文件 · ${percent}%`;
          },
        });
        uploadProgressBar.value = 100;
        uploadProgressLabel.textContent = "上传完成";
        uploadProgressCount.textContent = "1 / 1 个文件";
        await loadItems();
      } catch (error) {
        uploadProgressLabel.textContent = "上传未完成";
        pageError.textContent = `文件上传未完成：${error.message}`;
      } finally {
        uploadProgressModal.classList.add("hidden");
      }
      return;
    } else if (selectedItem?.type === "file") {
      await request(`/api/files/${encodeURIComponent(selectedItem.id)}/metadata`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: values.get("title"), description: values.get("description"), version: values.get("version"), group_id: selectedItem.group_id || null, tags }) });
    }
    closeMetadataModal(); await loadItems();
  } catch (error) { byId("storage-modal-error").textContent = error.message; }
});

folderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(folderForm);
  const payload = { name: String(values.get("name") || "").trim(), description: values.get("description") || "", tags: parseTags(values.get("tags")), parent_id: editingFolder ? (editingFolder.parent_id || null) : (currentFolderId || null) };
  try {
    const url = editingFolder ? `/api/folders/${encodeURIComponent(editingFolder.id)}` : "/api/folders";
    await request(url, { method: editingFolder ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    closeFolderModal(); await loadItems();
  } catch (error) { byId("storage-folder-error").textContent = error.message; }
});

request("/api/storage/me").then((user) => { currentUser = user; renderStorageUser(user); return loadItems(); }).catch((error) => { pageError.textContent = error.message; });
