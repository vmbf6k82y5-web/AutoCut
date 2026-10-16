// AutoCut GUI 前端逻辑（原生 JS）
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then(r => r.json());

// Tab 切换
document.querySelectorAll("nav button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    ["tasks", "settings", "eval"].forEach(t =>
      $("#tab-" + t).classList.toggle("hidden", t !== b.dataset.tab));
  };
});

const ROLE_NAMES = ["name_fix","outline","scout","vision","writer","reviewer","rewrite","memory","metadata","style","fixer"];

async function loadProfiles() {
  const d = await api("/api/profiles");
  $("#profile").innerHTML = d.profiles.map(p => `<option ${p===d.active?"selected":""}>${p}</option>`).join("");
  $("#profileSel").innerHTML = d.profiles.map(p => `<option ${p===d.active?"selected":""}>${p}</option>`).join("");
}

async function loadModels() {
  const d = await api("/api/models");
  const tb = document.querySelector("#modelsTable tbody");
  tb.innerHTML = d.models.map(m => `<tr>
    <td>${m.name}</td><td>${m.protocol}</td><td>${(m.capabilities||[]).join(",")}</td>
    <td>${m.price_in}/${m.price_out}</td>
    <td><button onclick="testModel('${m.name}')">测试</button></td></tr>`).join("");
}

async function testModel(name) {
  const r = await api("/api/models/test", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({name})});
  alert(r.message);
}

async function loadRoles() {
  const d = await api("/api/roles");
  const m = await api("/api/models");
  const names = m.models.map(x => x.name);
  $("#rolesEditor").innerHTML = ROLE_NAMES.map(role => {
    const cur = (d.roles[role]||[]);
    const opts = names.map(n => `<option ${cur.includes(n)?"selected":""}>${n}</option>`).join("");
    return `<div class="role-row"><label>${role}</label>
      <select data-role="${role}" multiple size="2" style="flex:1">${opts}</select></div>`;
  }).join("");
}

async function loadConfigToml() {
  const d = await api("/api/config");
  $("#cfgToml").value = d.toml;
}

$("#btnSaveCfg").onclick = async () => {
  const r = await api("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({toml: $("#cfgToml").value})});
  $("#cfgMsg").textContent = r.ok ? "已保存到 " + r.path : "保存失败";
};

$("#btnEstimate").onclick = async () => {
  const video = $("#video").value.trim();
  if (!video) return alert("请填写视频路径");
  const r = await api("/api/estimate", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({video, profile: $("#profile").value})});
  $("#estResult").textContent = `预估费用 $${r.total_cost.toFixed(4)}（时长 ${r.duration_seconds.toFixed(0)}s，预算内：${r.within_budget}）`;
};

$("#btnRun").onclick = async () => {
  const video = $("#video").value.trim();
  if (!video) return alert("请填写视频路径");
  const r = await api("/api/run", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({video, profile: $("#profile").value})});
  refreshJobs();
};

async function refreshJobs() {
  const d = await api("/api/jobs");
  if (!d.jobs.length) { $("#jobs").textContent = "暂无任务"; return; }
  $("#jobs").innerHTML = d.jobs.map(j => {
    const badge = j.status==="done"?"b-done":(j.status==="error"?"b-err":"b-run");
    const usage = j.usage && j.usage.total_cost!==undefined ? `费用 $${j.usage.total_cost.toFixed(4)}` : "";
    const preview = j.status==="done" ? `<video controls src="/api/result/${j.id}/preview"></video>` : "";
    return `<div class="job">
      <b>${j.video}</b> <span class="badge ${badge}">${j.status}</span>
      <div class="muted">${j.progress||""} ${j.error||""} ${usage}</div>${preview}</div>`;
  }).join("");
}

async function loadEval() {
  const d = await api("/api/eval/list");
  $("#evalReports").innerHTML = d.reports.length
    ? d.reports.map(r => `<div><a href="/${r}" target="_blank">${r}</a></div>`).join("")
    : "暂无评测报告（运行 autocut eval <评测集> 生成）";
}

// WebSocket 实时进度
try {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => { /* 可增量更新；此处定期刷新即可 */ };
} catch(e) {}

loadProfiles(); loadModels(); loadRoles(); loadConfigToml(); loadEval();
setInterval(refreshJobs, 2000);
