"use strict";

const initialParams = new URLSearchParams(location.search);
const state = {
  token: initialParams.get("token") || "",
  initialView: initialParams.get("view") || "chat",
  models: [], modes: [], sessions: [], stats: null, monthlyStats: null,
  currentMode: "coder", currentSessionId: null, forcedModel: null,
  vaultStatus: "uninitialized", attachments: [], sending: false,
  pendingModel: null, statsPeriod: "daily", selectedHistory: null,
};

if (state.token) history.replaceState({}, document.title, "/");

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[char]);

function toast(message, kind = "success") {
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  $("#toast-stack").append(item);
  setTimeout(() => item.remove(), 3600);
}

async function api(path, options = {}) {
  const headers = { "X-LocalAI-Token": state.token, ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try { detail = (await response.json()).error || detail; } catch (_) { /* empty */ }
    throw new Error(detail);
  }
  return response;
}

async function post(path, payload = {}) {
  const response = await api(path, { method: "POST", body: JSON.stringify(payload) });
  return (await response.json()).data;
}

function formatNumber(value) { return Number(value || 0).toLocaleString("zh-CN"); }
function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function renderMarkdown(source = "") {
  const escaped = escapeHtml(source);
  const blocks = escaped.split(/```/);
  return blocks.map((block, index) => {
    if (index % 2) {
      const firstBreak = block.indexOf("\n");
      const code = firstBreak >= 0 ? block.slice(firstBreak + 1) : block;
      return `<pre><code>${code}</code></pre>`;
    }
    return block
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .split(/\n{2,}/).map(part => `<p>${part.replace(/\n/g, "<br>")}</p>`).join("");
  }).join("");
}

function switchView(name) {
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  const titles = { chat: "智能对话", history: "历史记录", models: "模型管理", stats: "用量统计" };
  $("#view-heading").textContent = titles[name] || "LocalAI";
  $("#sidebar").classList.remove("open");
  if (name === "history") renderHistory();
  if (name === "models") renderModels();
  if (name === "stats") renderStats();
}

function renderSelectors() {
  $("#mode-select").innerHTML = state.modes.map(mode =>
    `<option value="${escapeHtml(mode.name)}" ${mode.name === state.currentMode ? "selected" : ""}>${escapeHtml(mode.display_name)}</option>`
  ).join("");
  const modelOptions = [`<option value="">自动路由</option>`].concat(state.models.map(model =>
    `<option value="${escapeHtml(model.name)}" ${model.name === state.forcedModel ? "selected" : ""}>${escapeHtml(model.name)}</option>`
  ));
  $("#model-select").innerHTML = modelOptions.join("");
}

function renderRecent() {
  const container = $("#recent-list");
  container.innerHTML = state.sessions.slice(0, 12).map(session => `
    <button class="recent-item ${session.id === state.currentSessionId ? "active" : ""}" data-session="${escapeHtml(session.id)}" type="button">
      <strong>${escapeHtml(session.title || "新会话")}</strong>
      <small>${escapeHtml(shortDate(session.updated_at))}</small>
    </button>`).join("") || `<div class="recent-item"><small>暂无历史会话</small></div>`;
  $$("[data-session]", container).forEach(button => button.addEventListener("click", () => openSession(button.dataset.session, true)));
}

function addMessage(role, content, model = "", streaming = false) {
  $("#empty-state").style.display = "none";
  const list = $("#message-list");
  list.classList.add("visible");
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const avatar = role === "user" ? "你" : "AI";
  article.innerHTML = `
    <div class="message-avatar">${avatar}</div>
    <div class="message-body">
      ${role === "assistant" ? `<div class="message-meta"><strong>LocalAI</strong><span class="model-badge">${escapeHtml(model || "正在路由")}</span></div>` : ""}
      <div class="message-content">${streaming ? '<span class="typing"><i></i><i></i><i></i></span>' : renderMarkdown(content)}</div>
    </div>`;
  list.append(article);
  scrollChat();
  return article;
}

function scrollChat() { const box = $("#chat-scroll"); requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; }); }

function showSessionMessages(session) {
  const list = $("#message-list");
  list.innerHTML = "";
  if (!session.messages.length) {
    list.classList.remove("visible");
    $("#empty-state").style.display = "flex";
    return;
  }
  session.messages.forEach(message => addMessage(message.role, message.content, message.model || ""));
}

async function openSession(id, goToChat = false) {
  try {
    const session = await post("/api/session/open", { id });
    state.currentSessionId = session.id;
    if (session.mode) state.currentMode = session.mode;
    showSessionMessages(session);
    renderSelectors(); renderRecent();
    if (goToChat) switchView("chat");
  } catch (error) { toast(error.message, "error"); }
}

async function newSession() {
  if (state.sending) return;
  try {
    const session = await post("/api/session/new");
    state.currentSessionId = session.id;
    state.sessions.unshift({ ...session, active: true });
    showSessionMessages({ messages: [] });
    renderRecent(); switchView("chat");
    $("#message-input").focus();
  } catch (error) { toast(error.message, "error"); }
}

function autoResize() {
  const input = $("#message-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

async function filesToPayload(files) {
  const result = [];
  let total = state.attachments.reduce((sum, item) => sum + item.size, 0);
  for (const file of files) {
    if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10 MB`);
    total += file.size;
    if (total > 40 * 1024 * 1024) throw new Error("附件总大小超过 40 MB");
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
      reader.readAsDataURL(file);
    });
    result.push({ name: file.name, content: String(dataUrl).split(",", 2)[1] || "", size: file.size });
  }
  return result;
}

function renderFiles() {
  $("#file-chips").innerHTML = state.attachments.map((file, index) => `
    <span class="file-chip"><b>▤</b>${escapeHtml(file.name)} <small>${(file.size / 1024).toFixed(1)} KB</small><button data-remove-file="${index}" type="button">×</button></span>
  `).join("");
  $$('[data-remove-file]').forEach(button => button.addEventListener("click", () => {
    state.attachments.splice(Number(button.dataset.removeFile), 1); renderFiles();
  }));
}

async function sendMessage() {
  const input = $("#message-input");
  const text = input.value.trim();
  if (state.sending || (!text && !state.attachments.length)) return;
  state.sending = true;
  $("#send-btn").disabled = true;
  addMessage("user", text || `已上传 ${state.attachments.length} 个附件`);
  const assistant = addMessage("assistant", "", "正在路由", true);
  const contentNode = $(".message-content", assistant);
  const badge = $(".model-badge", assistant);
  let answer = "";
  input.value = ""; autoResize();
  const attachments = state.attachments.map(({ name, content }) => ({ name, content }));
  state.attachments = []; renderFiles();
  try {
    const response = await api("/api/chat", {
      method: "POST", body: JSON.stringify({ message: text, attachments }),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n"); buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "meta") { badge.textContent = `${event.model} · ${event.tag}`; contentNode.innerHTML = ""; }
        if (event.type === "chunk") { answer += event.content; contentNode.innerHTML = renderMarkdown(answer); scrollChat(); }
        if (event.type === "status") { badge.textContent = event.content; }
        if (event.type === "done") {
          badge.textContent = event.model;
          state.stats = event.stats; state.sessions = event.sessions;
          state.currentSessionId = event.session_id; renderRecent();
        }
        if (event.type === "error") throw new Error(event.content);
      }
    }
    if (!answer) contentNode.innerHTML = `<p>模型没有返回文本内容。</p>`;
  } catch (error) {
    contentNode.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    assistant.classList.add("error");
    toast(error.message, "error");
    if (/主密码|解密|密钥库/.test(error.message)) openVault(state.vaultStatus === "uninitialized");
  } finally {
    state.sending = false; $("#send-btn").disabled = false; input.focus();
  }
}

function renderHistory() {
  const query = $("#history-search").value.trim().toLowerCase();
  const sessions = state.sessions.filter(session => (session.title || "").toLowerCase().includes(query));
  $("#history-cards").innerHTML = sessions.map(session => `
    <button class="history-card ${session.id === state.selectedHistory ? "active" : ""}" data-history-id="${escapeHtml(session.id)}" type="button">
      <strong>${escapeHtml(session.title || "新会话")}</strong><small><span>${escapeHtml(session.mode || "default")}</span><span>${escapeHtml(shortDate(session.updated_at))}</span></small>
    </button>`).join("") || `<div class="preview-placeholder"><span>⌕</span><strong>没有匹配的会话</strong></div>`;
  $$('[data-history-id]').forEach(button => button.addEventListener("click", () => previewHistory(button.dataset.historyId)));
}

async function previewHistory(id) {
  try {
    const response = await api(`/api/sessions/${encodeURIComponent(id)}`);
    const session = await response.json(); state.selectedHistory = id; renderHistory();
    $("#history-preview").innerHTML = `
      <div class="preview-header"><div><span class="eyebrow">${escapeHtml(session.mode || "DEFAULT")}</span><h3>${escapeHtml(session.title)}</h3><small>${escapeHtml(shortDate(session.updated_at))}</small></div>
      <div class="preview-actions"><button data-continue="${escapeHtml(id)}" type="button">继续对话</button><button data-export="md" type="button">导出 MD</button><button data-export="json" type="button">导出 JSON</button></div></div>
      <div>${session.messages.map(message => `<div class="preview-message"><strong>${escapeHtml(message.role)}${message.model ? ` · ${escapeHtml(message.model)}` : ""}</strong><div>${escapeHtml(message.content)}</div></div>`).join("") || '<div class="preview-placeholder"><small>此会话暂无消息</small></div>'}</div>`;
    $("[data-continue]").addEventListener("click", () => openSession(id, true));
    $$('[data-export]').forEach(button => button.addEventListener("click", () => exportSession(id, button.dataset.export)));
  } catch (error) { toast(error.message, "error"); }
}

async function exportSession(id, format) {
  try {
    const response = await api(`/api/sessions/${encodeURIComponent(id)}/export?format=${format}`);
    const blob = await response.blob(); const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = `${id}.${format}`; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) { toast(error.message, "error"); }
}

const providerColors = { openai: "#34d399", deepseek: "#60a5fa", kimi: "#a78bfa", qwen: "#fbbf24" };
function renderModels() {
  const configured = state.models.filter(model => model.configured).length;
  const enabled = state.models.filter(model => model.enabled).length;
  $("#model-summary").innerHTML = `<span class="summary-chip"><b>${state.models.length}</b> 个模型</span><span class="summary-chip"><b>${configured}</b> 已配置密钥</span><span class="summary-chip"><b>${enabled}</b> 已启用</span>`;
  $("#model-grid").innerHTML = state.models.map(model => {
    const color = providerColors[model.provider] || "#8b5cf6";
    return `<article class="model-card" style="--provider-color:${color}">
      <div class="model-card-head"><div class="provider-mark">${escapeHtml(model.provider.slice(0, 2).toUpperCase())}</div><div><h3>${escapeHtml(model.name)}</h3><small>${escapeHtml(model.model)} · ${escapeHtml(model.base_url)}</small></div><span class="model-status ${model.configured && model.enabled ? "ready" : ""}">${model.configured ? (model.enabled ? "可用" : "已停用") : "未配置密钥"}</span></div>
      <div class="model-tags">${model.capabilities.map(tag => `<span class="model-tag">${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="model-meta-row"><span>优先级 <b>${model.priority}</b></span><span>输入费用 <b>$${Number(model.cost_per_1k_input).toFixed(6)}</b></span><span>输出费用 <b>$${Number(model.cost_per_1k_output).toFixed(6)}</b></span></div>
      <div class="model-actions"><button data-model-test="${escapeHtml(model.name)}" type="button">测试连接</button><button data-model-edit="${escapeHtml(model.name)}" type="button">编辑</button><button data-model-toggle="${escapeHtml(model.name)}" type="button">${model.enabled ? "停用" : "启用"}</button><span class="spacer"></span><button class="danger" data-model-delete="${escapeHtml(model.name)}" type="button">删除</button></div>
    </article>`;
  }).join("") || `<div class="panel preview-placeholder"><span>⌘</span><strong>尚未添加模型</strong><small>点击右上角添加第一个模型</small></div>`;
  $$('[data-model-edit]').forEach(button => button.addEventListener("click", () => openModelModal(button.dataset.modelEdit)));
  $$('[data-model-test]').forEach(button => button.addEventListener("click", () => testModel(button.dataset.modelTest, button)));
  $$('[data-model-toggle]').forEach(button => button.addEventListener("click", () => toggleModel(button.dataset.modelToggle)));
  $$('[data-model-delete]').forEach(button => button.addEventListener("click", () => deleteModel(button.dataset.modelDelete)));
}

function openModelModal(name = "") {
  const form = $("#model-form"); form.reset(); form.elements.enabled.checked = true;
  const model = state.models.find(item => item.name === name);
  $("#model-modal-title").textContent = model ? `编辑 ${model.name}` : "添加模型";
  form.elements.name.readOnly = Boolean(model);
  if (model) {
    form.elements.name.value = model.name; form.elements.provider.value = model.provider;
    form.elements.base_url.value = model.base_url; form.elements.model.value = model.model;
    form.elements.priority.value = model.priority; form.elements.capabilities.value = model.capabilities.join(", ");
    form.elements.cost_input.value = model.cost_per_1k_input; form.elements.cost_output.value = model.cost_per_1k_output;
    form.elements.enabled.checked = model.enabled;
  }
  openModal("model-modal");
}

function modelFormPayload() {
  const form = $("#model-form");
  return {
    name: form.elements.name.value.trim(), provider: form.elements.provider.value,
    base_url: form.elements.base_url.value.trim(), model: form.elements.model.value.trim(),
    api_key: form.elements.api_key.value, priority: Number(form.elements.priority.value),
    capabilities: form.elements.capabilities.value.split(",").map(item => item.trim()).filter(Boolean),
    cost_per_1k_input: Number(form.elements.cost_input.value || 0), cost_per_1k_output: Number(form.elements.cost_output.value || 0),
    enabled: form.elements.enabled.checked,
  };
}

async function saveModel(payload) {
  try {
    const saved = await post("/api/model/save", payload);
    const index = state.models.findIndex(item => item.name === saved.name);
    if (index >= 0) state.models[index] = saved; else state.models.push(saved);
    closeModal("model-modal"); renderModels(); renderSelectors();
    toast(`模型 ${saved.name} 已保存`);
  } catch (error) {
    if (/LOCALAI_MASTER_PASSWORD|主密码|keyring/.test(error.message) && payload.api_key) {
      state.pendingModel = payload; openVault(state.vaultStatus === "uninitialized"); return;
    }
    toast(error.message, "error");
  }
}

async function testModel(name, button) {
  const old = button.textContent; button.textContent = "测试中…"; button.disabled = true;
  try { await post("/api/model/test", { name }); toast(`${name} 连接正常`); }
  catch (error) { toast(`${name}: ${error.message}`, "error"); }
  finally { button.textContent = old; button.disabled = false; }
}
async function toggleModel(name) {
  const model = state.models.find(item => item.name === name); if (!model) return;
  try { const updated = await post("/api/model/toggle", { name, enabled: !model.enabled }); Object.assign(model, updated); renderModels(); }
  catch (error) { toast(error.message, "error"); }
}
async function deleteModel(name) {
  if (!confirm(`确认删除模型“${name}”？此操作不会删除服务商账户。`)) return;
  try { await post("/api/model/delete", { name }); state.models = state.models.filter(item => item.name !== name); renderModels(); renderSelectors(); toast(`模型 ${name} 已删除`); }
  catch (error) { toast(error.message, "error"); }
}

function renderStats() {
  const report = state.statsPeriod === "monthly" ? state.monthlyStats : state.stats;
  if (!report) return;
  const metrics = [
    ["输入 Token", formatNumber(report.total_input), "进入模型的上下文", "↘"],
    ["输出 Token", formatNumber(report.total_output), "模型生成的内容", "↗"],
    ["调用次数", formatNumber(report.total_calls), "完成的模型请求", "⌁"],
    ["预估费用", `$${Number(report.total_cost || 0).toFixed(6)}`, "按本地配置价格", "$"],
  ];
  $("#metric-grid").innerHTML = metrics.map(item => `<div class="metric-card"><span class="metric-accent">${item[3]}</span><small>${item[0]}</small><strong>${item[1]}</strong><span>${item[2]}</span></div>`).join("");
  $("#stats-period-label").textContent = state.statsPeriod === "monthly" ? `${report.year}-${String(report.month).padStart(2, "0")}` : report.date;
  $("#stats-table").innerHTML = report.models.map(row => `<tr><td>${escapeHtml(row.model)}</td><td>${formatNumber(row.input_tokens)}</td><td>${formatNumber(row.output_tokens)}</td><td>${formatNumber(row.calls)}</td><td>$${Number(row.cost || 0).toFixed(6)}</td></tr>`).join("") || `<tr><td colspan="5">当前周期暂无调用记录</td></tr>`;
}

function openModal(id) { const modal = $(`#${id}`); modal.classList.add("open"); modal.setAttribute("aria-hidden", "false"); }
function closeModal(id) { const modal = $(`#${id}`); modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); }
function openVault(creating) {
  state.vaultStatus = creating ? "uninitialized" : "locked";
  $("#vault-title").textContent = creating ? "创建本地主密码" : "解锁本地密钥库";
  $("#vault-description").textContent = creating ? "系统凭据管理器不可用。请创建至少 12 个字符的密码，用于保护本机 API Key。" : "请输入此前设置的本地主密码。密码只保留在当前进程内存中。";
  $("#vault-confirm-row").style.display = creating ? "grid" : "none";
  $("#vault-form").reset(); openModal("vault-modal");
}

async function unlockVault(event) {
  event.preventDefault(); const form = event.currentTarget;
  const password = form.elements.password.value; const confirmation = form.elements.confirmation.value;
  if (state.vaultStatus === "uninitialized" && password !== confirmation) { toast("两次输入的密码不一致", "error"); return; }
  try {
    const result = await post("/api/vault/unlock", { password });
    state.vaultStatus = result.status; closeModal("vault-modal"); toast("本地密钥库已解锁");
    if (state.pendingModel) { const pending = state.pendingModel; state.pendingModel = null; await saveModel(pending); }
  } catch (error) { toast(error.message, "error"); }
}

async function refreshBootstrap() {
  try {
    const response = await api("/api/bootstrap"); const data = await response.json();
    Object.assign(state, {
      models: data.models, modes: data.modes, sessions: data.sessions,
      stats: data.stats, monthlyStats: data.monthly_stats,
      currentMode: data.current_mode, currentSessionId: data.current_session_id,
      forcedModel: data.forced_model, vaultStatus: data.vault_status,
    });
    renderSelectors(); renderRecent(); renderModels(); renderStats();
    if (state.vaultStatus === "locked") openVault(false);
  } catch (error) {
    toast(error.message, "error");
    $("#empty-state .hero-copy p").textContent = "无法连接本地服务，请刷新页面或重新启动 localai gui。";
  }
}

function bindEvents() {
  $$(".nav-item").forEach(item => item.addEventListener("click", () => switchView(item.dataset.view)));
  $("#new-chat-btn").addEventListener("click", newSession);
  $("#refresh-btn").addEventListener("click", refreshBootstrap);
  $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#theme-btn").addEventListener("click", () => {
    document.body.classList.toggle("light"); localStorage.setItem("localai-theme", document.body.classList.contains("light") ? "light" : "dark");
  });
  $("#mode-select").addEventListener("change", async event => {
    try { await post("/api/mode", { mode: event.target.value }); state.currentMode = event.target.value; }
    catch (error) { toast(error.message, "error"); renderSelectors(); }
  });
  $("#model-select").addEventListener("change", async event => {
    try { await post("/api/model/force", { name: event.target.value || null }); state.forcedModel = event.target.value || null; toast(state.forcedModel ? `已固定使用 ${state.forcedModel}` : "已恢复自动路由"); }
    catch (error) { toast(error.message, "error"); renderSelectors(); }
  });
  $("#send-btn").addEventListener("click", sendMessage);
  $("#message-input").addEventListener("input", autoResize);
  $("#message-input").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });
  $("#attach-btn").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", async event => {
    try {
      const remaining = 8 - state.attachments.length; const files = [...event.target.files].slice(0, remaining);
      state.attachments.push(...await filesToPayload(files)); renderFiles();
      if (event.target.files.length > remaining) toast("一次最多添加 8 个文件", "error");
    } catch (error) { toast(error.message, "error"); }
    event.target.value = "";
  });
  $$(".suggestion").forEach(button => button.addEventListener("click", () => { $("#message-input").value = button.dataset.prompt; autoResize(); $("#message-input").focus(); }));
  $("#history-search").addEventListener("input", renderHistory);
  $("#add-model-btn").addEventListener("click", () => openModelModal());
  $("#model-form").addEventListener("submit", event => { event.preventDefault(); saveModel(modelFormPayload()); });
  $("#vault-form").addEventListener("submit", unlockVault);
  $$('[data-close]').forEach(button => button.addEventListener("click", () => closeModal(button.dataset.close)));
  $$(".modal-backdrop").forEach(backdrop => backdrop.addEventListener("mousedown", event => { if (event.target === backdrop && backdrop.id !== "vault-modal") closeModal(backdrop.id); }));
  $$("[data-period]").forEach(button => button.addEventListener("click", () => { state.statsPeriod = button.dataset.period; $$("[data-period]").forEach(item => item.classList.toggle("active", item === button)); renderStats(); }));
  document.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") { event.preventDefault(); newSession(); } });
}

if (localStorage.getItem("localai-theme") === "light") document.body.classList.add("light");
bindEvents();
refreshBootstrap().then(() => {
  if (["chat", "history", "models", "stats"].includes(state.initialView)) {
    switchView(state.initialView);
  }
});
