let selectedId = null;

const $ = (id) => document.getElementById(id);

async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function verdictPill(v) {
  if (!v) return "";
  const score = v.score === null ? "" : ` · ${v.score}`;
  return `<span class="pill ${v.verdict.toLowerCase()}">${v.verdict}${score}</span>`;
}

function renderStats(disputes) {
  const open = disputes.filter((d) => d.dispute.status === "WAITING_FOR_SELLER_RESPONSE");
  const atRisk = open.reduce((s, d) => s + d.dispute.amount, 0);
  const resolved = disputes.filter((d) => d.dispute.status.startsWith("RESOLVED"));
  const defended = disputes.filter((d) => d.dispute.status === "RESOLVED_SELLER").length;
  $("stats").innerHTML = `
    <div class="stat"><div class="num warn">${open.length}</div><div class="lbl">Open disputes awaiting response</div></div>
    <div class="stat"><div class="num">$${atRisk.toFixed(2)}</div><div class="lbl">Revenue currently under dispute</div></div>
    <div class="stat"><div class="num good">${resolved.length}</div><div class="lbl">Resolved cases</div></div>
    <div class="stat"><div class="num good">${defended}</div><div class="lbl">Defended successfully</div></div>`;
}

async function loadList() {
  const disputes = await jget("/api/disputes");
  renderStats(disputes);
  $("disputeList").innerHTML = disputes.map((d) => `
    <div class="card ${d.dispute.id === selectedId ? "selected" : ""}" data-id="${d.dispute.id}">
      <div class="row"><span class="item">${d.order.item_name}</span>
        <span class="amt">$${d.dispute.amount.toFixed(2)}</span></div>
      <div class="sub"><span>${d.dispute.id} · ${d.buyer.name} · ${d.dispute.reason.includes("NOT_RECEIVED") ? "Item not received" : "Not as described"}</span></div>
      <div class="sub"><span class="pill status">${d.dispute.status}</span>${verdictPill(d.verdict)}</div>
    </div>`).join("");
  document.querySelectorAll(".card").forEach((c) =>
    c.addEventListener("click", () => selectDispute(c.dataset.id)));

  const radar = await jget("/api/radar");
  const rb = radar.repeat_buyers.map((b) =>
    `<div class="radar-item">🔁 <b>${b.name}</b> — ${b.dispute_count} disputes on file</div>`).join("");
  const rp = radar.reused_photos.map((p) =>
    `<div class="radar-item">🖼️ Claim photo <code>${p.claim_photo_hash.slice(0, 10)}…</code> reused in ${p.n} disputes</div>`).join("");
  $("radar").innerHTML = (rb + rp) || '<div class="radar-item">No patterns detected.</div>';
}

async function selectDispute(id) {
  selectedId = id;
  await loadList();
  const d = await jget(`/api/disputes/${id}`);
  $("detailEmpty").hidden = true;
  $("detail").hidden = false;
  const dp = d.dispute, o = d.order;
  $("dTitle").textContent = `${o.item_name} — $${dp.amount.toFixed(2)}`;
  $("dStatus").textContent = dp.status;
  $("dMeta").innerHTML =
    `Dispute <b>${dp.id}</b> · Buyer <b>${d.buyer.name}</b> (${d.buyer_prior_disputes} prior disputes)` +
    ` · Reason: ${dp.reason}` +
    `<br>Ordered ${o.created_at} · Delivered ${o.delivered_at || "—"} · Tracking: ${o.tracking_status}` +
    (d.photo_reuse_count ? `<br>⚠️ This claim photo appears in ${d.photo_reuse_count} other dispute(s).` : "");

  if (d.pack) {
    $("packPhoto").style.display = "";
    $("packPhoto").src = "/" + d.pack.photo_path;
    $("packMeta").textContent = `Sealed ${d.pack.sealed_at} · ${d.pack.weight_g} g · Pack ${d.pack.id}`;
  } else {
    $("packPhoto").removeAttribute("src");
    $("packPhoto").style.display = "none";
    $("packMeta").textContent = "⚠️ No sealed evidence pack for this order.";
  }
  if (dp.claim_photo_path) {
    $("claimPhoto").style.display = "";
    $("claimPhoto").src = "/" + dp.claim_photo_path;
    $("claimMeta").textContent = `Photo taken ${dp.claim_photo_taken_at || "unknown"} · hash ${dp.claim_photo_hash.slice(0, 10)}…`;
  } else {
    $("claimPhoto").removeAttribute("src");
    $("claimPhoto").style.display = "none";
    $("claimMeta").textContent = "No claim photo (tracking-based dispute).";
  }

  $("visionBox").innerHTML = d.vision
    ? "<b>🔍 Vision comparison</b><ul>" + d.vision.notes.map((n) => `<li>${n}</li>`).join("") + "</ul>"
      + `Severity: <b>${d.vision.severity}</b> · Confidence: ${d.vision.confidence}`
    : "<b>🔍 Vision comparison</b><br>No photo comparison on this dispute path (tracking evidence instead).";

  renderVerdict(d.verdict);
  renderActions(d.actions);
  $("actResult").hidden = true;
  $("actRow").hidden = !d.verdict;
  $("btnAdjudicate").hidden = dp.status !== "UNDER_REVIEW";
}

function renderVerdict(v) {
  const badge = $("verdictBadge");
  if (!v) {
    badge.textContent = "—";
    badge.className = "verdict-badge";
    $("scoreBadge").textContent = "";
    $("scoreMarker").hidden = true;
    $("breakdown").innerHTML = '<div class="subpanel">Run the verdict engine to see the full signal breakdown — every point is auditable.</div>';
    return;
  }
  badge.textContent = v.verdict;
  badge.className = "verdict-badge " + v.verdict.toLowerCase();
  if (v.score === null) {
    $("scoreBadge").innerHTML = `<small>path: ${v.path}</small>`;
    $("scoreMarker").hidden = true;
  } else {
    $("scoreBadge").innerHTML = `${v.score}<small>/100</small>`;
    const marker = $("scoreMarker");
    marker.hidden = false;
    marker.style.left = `calc(${v.score}% - 2px)`;
  }
  $("breakdown").innerHTML = v.breakdown.map((s) => `
    <div class="sig"><span><span class="sname">${s.signal}</span> — <span class="snote">${s.note}</span></span>
    <span class="pts ${s.points > 0 ? "pos" : s.points < 0 ? "neg" : ""}">${s.points > 0 ? "+" : ""}${s.points || "—"}</span></div>`).join("");
}

function renderActions(actions) {
  $("actionLog").innerHTML = actions.length
    ? actions.map((a) => `<div class="log-row"><span><code>${a.action}</code> [${a.mode}] — ${a.detail.endpoint || a.detail.reason || ""}</span><span class="when">${a.created_at.slice(0, 10)}</span></div>`).join("")
    : '<div class="log-row">No PayPal actions taken yet.</div>';
}

$("btnEvaluate").addEventListener("click", async () => {
  const res = await jpost(`/api/disputes/${selectedId}/evaluate`);
  renderVerdict({ ...res, created_at: "" });
  $("actRow").hidden = false;
  await loadList();
});

document.querySelectorAll("[data-act]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const res = await jpost(`/api/disputes/${selectedId}/act`, { action: btn.dataset.act });
    $("actResult").hidden = false;
    $("actResult").innerHTML = `<b>Action executed:</b> <code>${btn.dataset.act}</code> → status <b>${res.status}</b>`
      + (res.offer_amount ? ` · offer $${res.offer_amount.toFixed(2)}` : "")
      + (res.followed_verdict ? " · followed the agent verdict" : " · ⚠️ overrode the agent verdict");
    await selectDispute(selectedId);
  }));

$("btnAdjudicate").addEventListener("click", async () => {
  const res = await jpost(`/api/disputes/${selectedId}/adjudicate`, { outcome: "SELLER" });
  $("actResult").hidden = false;
  $("actResult").innerHTML = `<b>Adjudicated (sandbox):</b> → <b>${res.status}</b>`;
  await selectDispute(selectedId);
});

(async () => {
  const h = await jget("/api/health");
  const badge = $("modeBadge");
  badge.textContent = h.mode === "live" ? "● LIVE · PayPal sandbox" : "● SIM · local simulator";
  if (h.mode !== "live") badge.classList.add("sim");
  await loadList();
})();
