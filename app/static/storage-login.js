const loginForm = document.getElementById("storage-login-form");
const passwordForm = document.getElementById("storage-password-form");
const loginError = document.getElementById("storage-login-error");
const passwordError = document.getElementById("storage-password-error");
const adminLoginForm = document.getElementById("admin-login-form");
const adminLoginToggle = document.getElementById("storage-admin-login-toggle");
const adminLoginError = document.getElementById("admin-login-error");

async function storageRequest(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败。");
  return payload;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const studentId = document.getElementById("student-id").value.trim();
  const password = document.getElementById("storage-password").value;
  if (!studentId || !password) { loginError.textContent = "请输入学号和密码。"; return; }
  try {
    const result = await storageRequest("/api/storage/login", { method: "POST", body: JSON.stringify({ student_id: studentId, password }) });
    if (result.must_change_password) {
      loginForm.classList.add("hidden");
      passwordForm.classList.remove("hidden");
      document.getElementById("current-password").value = password;
      return;
    }
    window.location.href = "/storage/files";
  } catch (error) { loginError.textContent = error.message; }
});

function setLoginMode(mode, updateUrl = false) {
  const adminMode = mode === "admin";
  adminLoginForm.classList.toggle("hidden", !adminMode);
  loginForm.classList.toggle("hidden", adminMode);
  adminLoginToggle.textContent = adminMode ? "返回资料库登录" : "管理员登录";
  adminLoginToggle.href = adminMode ? "/storage" : "?mode=admin";
  if (updateUrl) window.history.replaceState(null, "", adminMode ? "?mode=admin" : "/storage");
}

setLoginMode(new URLSearchParams(window.location.search).get("mode"));
adminLoginToggle.addEventListener("click", (event) => {
  event.preventDefault();
  const adminMode = !adminLoginForm.classList.contains("hidden");
  setLoginMode(adminMode ? "storage" : "admin", true);
});

adminLoginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  adminLoginError.textContent = "";
  const studentId = document.getElementById("admin-login-student-id").value.trim();
  const password = document.getElementById("admin-login-password").value;
  const adminToken = document.getElementById("admin-login-token").value.trim();
  if (!studentId || !password || !adminToken) { adminLoginError.textContent = "请输入学号、密码和管理员令牌。"; return; }
  try {
    const result = await storageRequest("/api/admin/login", { method: "POST", body: JSON.stringify({ student_id: studentId, password, admin_token: adminToken }) });
    if (result.must_change_password) {
      adminLoginError.textContent = "账号需要先修改初始密码，请使用普通资料库登录完成修改后再进入管理中心。";
      return;
    }
    sessionStorage.setItem("tcp-printer-admin-token", adminToken);
    window.location.href = "/admin";
  } catch (error) { adminLoginError.textContent = error.message; }
});

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  passwordError.textContent = "";
  const currentPassword = document.getElementById("current-password").value;
  const newPassword = document.getElementById("new-password").value;
  if (newPassword.length < 6) { passwordError.textContent = "新密码至少需要 6 位。"; return; }
  try {
    await storageRequest("/api/storage/password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
    window.location.href = "/storage/files";
  } catch (error) { passwordError.textContent = error.message; }
});
