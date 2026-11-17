(() => {
  const view = document.getElementById("view");
  const msgEl = document.getElementById("msg");
  let tab = "batches";
  let meta = null;
  let batches = [];
  let selected = null;
  let detail = null;
  let reports = [];
  let rules = [];
  let rule = null;
  let form = { object_key: "", content_hash: "", byte_size: "100" };
  let switchForm = { min_byte_size: "64", min_hash_length: "8", required_key_prefix: "raw/", remark: "" };

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (r.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || j.detail || ("HTTP " + r.status));
    return j;
  }

  function setMsg(t) {
    msgEl.textContent = t || "";
  }

  function currentRuleNo() {
    return rule ? rule.track_no : null;
  }

  function rulePill() {
    if (!rule) return "";
    return `<span class="pill rule-pill">现行轨 ${rule.track_code}</span>`;
  }

  async function refresh() {
    meta = await api("/api/meta");
    document.getElementById("lab-brand").textContent = meta.lab_name || "湖区清单台";
    rule = meta.current_rule || null;
    const [b, rr, rt] = await Promise.all([
      api("/api/batches"),
      api("/api/visibility-reports"),
      api("/api/rule-tracks"),
    ]);
    batches = b.items || [];
    rule = b.current_rule || rule;
    reports = rr.items || [];
    rules = rt.items || [];
    rule = rt.current || rule;
  }

  async function openBatch(id) {
    selected = id;
    detail = await api("/api/batches/" + id);
    rule = detail.current_rule || rule;
    tab = "detail";
    syncNav();
    render();
  }

  async function doRegister() {
    if (!selected) return;
    setMsg("");
    try {
      await api("/api/batches/" + selected + "/objects", {
        method: "POST",
        body: JSON.stringify({
          object_key: form.object_key,
          content_hash: form.content_hash,
          byte_size: Number(form.byte_size || 0),
          rule_track_no: currentRuleNo(),
        }),
      });
      form = { object_key: "", content_hash: "", byte_size: "100" };
      await openBatch(selected);
      await refresh();
      setMsg(`已按现行轨 ${rule.track_code} 登记对象`);
      render();
    } catch (e) {
      // 轨号对不上时服务端已拦回：拉齐最新轨号，页面立即标出
      await refresh().catch(() => {});
      setMsg(e.message);
      render();
    }
  }

  async function doClose() {
    if (!selected) return;
    setMsg("");
    try {
      await api("/api/batches/" + selected + "/close", { method: "POST", body: "{}" });
      await openBatch(selected);
      await refresh();
      setMsg("清单已闭合");
      render();
    } catch (e) {
      setMsg(e.message);
    }
  }

  async function doSwitch() {
    setMsg("");
    try {
      const r = await api("/api/rule-tracks/switch", {
        method: "POST",
        body: JSON.stringify({
          expected_track_no: currentRuleNo(),
          min_byte_size: Number(switchForm.min_byte_size || 0),
          min_hash_length: Number(switchForm.min_hash_length || 1),
          required_key_prefix: switchForm.required_key_prefix,
          remark: switchForm.remark,
        }),
      });
      await refresh();
      setMsg(`规则轨已一次落成：现行轨 ${r.rule.track_code}`);
      render();
    } catch (e) {
      await refresh().catch(() => {});
      setMsg(e.message);
      render();
    }
  }

  async function doReport() {
    setMsg("");
    try {
      const r = await api("/api/visibility-reports", {
        method: "POST",
        body: JSON.stringify({ as_of_date: "2026-11-16" }),
      });
      await refresh();
      tab = "reports";
      syncNav();
      setMsg(r.report.summary);
      render();
    } catch (e) {
      setMsg(e.message);
    }
  }

  function syncNav() {
    document.querySelectorAll("nav [data-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
  }

  function renderBatches() {
    return `
      <h2>落地批次 ${rulePill()}</h2>
      <p class="muted">主链：选批 → 按现行轨登记对象 → 闭合清单 → 可见性对账</p>
      <table>
        <thead><tr><th>编号</th><th>湖区</th><th>业务日</th><th>状态</th><th></th></tr></thead>
        <tbody>
          ${batches
            .map(
              (b) => `<tr>
            <td>${b.id}</td>
            <td>${b.zone_name}<div class="muted">${b.zone_code}</div></td>
            <td>${b.biz_date}</td>
            <td><span class="pill">${b.status}</span></td>
            <td><button type="button" class="secondary" data-open="${b.id}">打开</button></td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderDetail() {
    if (!detail) {
      return `<h2>批次详情</h2><p class="muted">请先从落地批次打开一条。</p>`;
    }
    const b = detail.batch;
    const objs = detail.objects || [];
    return `
      <h2>批次详情 #${b.id} ${rulePill()}</h2>
      <p>${b.zone_name} · ${b.biz_date} · <span class="pill">${b.status}</span></p>
      <div class="row-actions">
        <button type="button" id="btn-close">闭合清单</button>
        <button type="button" class="secondary" id="btn-report-from-detail">跑可见性对账</button>
      </div>
      <div class="panel">
        <h3>按现行轨 ${rule ? rule.track_code : ""} 登记对象</h3>
        ${rule ? renderThresholds(rule) : ""}
        <label>对象键<input id="f-key" value="${form.object_key}" placeholder="${rule && rule.required_key_prefix ? "须以 " + rule.required_key_prefix + " 开头" : ""}" /></label>
        <label>内容指纹<input id="f-hash" value="${form.content_hash}" placeholder="${rule ? "长度 ≥ " + rule.min_hash_length : ""}" /></label>
        <label>字节数<input id="f-size" value="${form.byte_size}" placeholder="${rule ? "≥ " + rule.min_byte_size : ""}" /></label>
        <button type="button" id="btn-register">写入登记（轨号 ${rule ? rule.track_no : "-"}）</button>
      </div>
      <h3>对象列表</h3>
      <table>
        <thead><tr><th>键</th><th>指纹</th><th>字节</th><th>状态</th><th>准入轨号</th></tr></thead>
        <tbody>
          ${objs
            .map(
              (o) => `<tr>
            <td>${o.object_key}</td><td>${o.content_hash}</td><td>${o.byte_size}</td><td>${o.status}</td>
            <td>${o.rule_track_no ? "RULE-" + String(o.rule_track_no).padStart(4, "0") : '<span class="muted">历史数据</span>'}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderThresholds(r) {
    return `<ul class="thresholds">
      <li>最小字节数：<b>${r.min_byte_size}</b></li>
      <li>指纹最小长度：<b>${r.min_hash_length}</b></li>
      <li>对象键必填目录前缀：<b>${r.required_key_prefix || "（不限）"}</b></li>
    </ul>`;
  }

  function renderRules() {
    return `
      <h2>规则轨 ${rulePill()}</h2>
      <p class="muted">规则轨以旁路快照表物化：每次切换整段原子替换现行轨，旧轨不可变留痕；两名值班同时改轨由写锁串行，期望轨号对不上即拦回。</p>
      <div class="panel">
        <h3>切换现行轨（服务端一次落成）</h3>
        <p class="muted">当前页面持有轨号：<b>${rule ? rule.track_no : "-"}</b>，提交时一并回传做同一判定。</p>
        <label>最小字节数<input id="s-bytes" value="${switchForm.min_byte_size}" /></label>
        <label>指纹最小长度<input id="s-hashlen" value="${switchForm.min_hash_length}" /></label>
        <label>对象键必填目录前缀（留空表示不限）<input id="s-prefix" value="${switchForm.required_key_prefix}" /></label>
        <label>备注<input id="s-remark" value="${switchForm.remark}" /></label>
        <button type="button" id="btn-switch">原子切换规则轨</button>
      </div>
      <h3>轨次留痕</h3>
      <table>
        <thead><tr><th>轨号</th><th>最小字节</th><th>指纹长度</th><th>目录前缀</th><th>备注</th><th>现行</th><th>落成时间</th></tr></thead>
        <tbody>
          ${rules
            .map(
              (r) => `<tr${r.is_current ? ' class="row-current"' : ""}>
            <td>${r.track_code}</td><td>${r.min_byte_size}</td><td>${r.min_hash_length}</td>
            <td>${r.required_key_prefix || '<span class="muted">不限</span>'}</td>
            <td>${r.remark || ""}</td>
            <td>${r.is_current ? '<span class="pill">现行</span>' : '<span class="muted">已退位</span>'}</td>
            <td>${r.created_at}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderReports() {
    return `
      <h2>可见性对账 ${rulePill()}</h2>
      <button type="button" id="btn-report">生成对账</button>
      <table>
        <thead><tr><th>编号</th><th>截至</th><th>错位</th><th>摘要</th></tr></thead>
        <tbody>
          ${reports
            .map(
              (r) => `<tr>
            <td>${r.id}</td><td>${r.as_of_date}</td><td>${r.mismatch_count}</td><td>${r.summary}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function render() {
    if (tab === "batches") view.innerHTML = renderBatches();
    else if (tab === "detail") view.innerHTML = renderDetail();
    else if (tab === "rules") view.innerHTML = renderRules();
    else view.innerHTML = renderReports();

    view.querySelectorAll("[data-open]").forEach((btn) => {
      btn.onclick = () => openBatch(Number(btn.dataset.open));
    });
    const reg = document.getElementById("btn-register");
    if (reg) {
      const key = document.getElementById("f-key");
      const hash = document.getElementById("f-hash");
      const size = document.getElementById("f-size");
      key.oninput = () => (form.object_key = key.value);
      hash.oninput = () => (form.content_hash = hash.value);
      size.oninput = () => (form.byte_size = size.value);
      reg.onclick = doRegister;
    }
    const sw = document.getElementById("btn-switch");
    if (sw) {
      const bytes = document.getElementById("s-bytes");
      const hashlen = document.getElementById("s-hashlen");
      const prefix = document.getElementById("s-prefix");
      const remark = document.getElementById("s-remark");
      bytes.oninput = () => (switchForm.min_byte_size = bytes.value);
      hashlen.oninput = () => (switchForm.min_hash_length = hashlen.value);
      prefix.oninput = () => (switchForm.required_key_prefix = prefix.value);
      remark.oninput = () => (switchForm.remark = remark.value);
      sw.onclick = doSwitch;
    }
    const closeBtn = document.getElementById("btn-close");
    if (closeBtn) closeBtn.onclick = doClose;
    const r1 = document.getElementById("btn-report");
    const r2 = document.getElementById("btn-report-from-detail");
    if (r1) r1.onclick = doReport;
    if (r2) r2.onclick = doReport;
  }

  document.querySelectorAll("nav [data-tab]").forEach((btn) => {
    btn.onclick = () => {
      tab = btn.dataset.tab;
      syncNav();
      render();
    };
  });
  document.getElementById("btn-logout").onclick = () => {
    fetch("/api/logout", { method: "POST" }).then(() => (location.href = "/login"));
  };

  refresh()
    .then(render)
    .catch((e) => setMsg(e.message));
})();
