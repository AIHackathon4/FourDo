/* FourDo 프론트엔드. /api/analyze 응답을 DATA에 담아 그린다. 계산은 모두 서버가 하고, 여기서는 보여 주기만 한다. */
let DATA = null;
let TRUST = [];
const STATUS = { met: "충족", caution: "주의", lacking: "부족", needs_check: "확인 필요" };
const VERDICT_BADGE = { "참여 가능": "met", "준비하면 가능": "caution", "확인 필요": "needs_check", "어려움": "lacking" };
const $ = (s, r = document) => r.querySelector(s);
/* 공고에서 온 글자는 그대로 HTML에 넣지 않는다 */
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const wait = ms => new Promise(r => setTimeout(r, ms));

/* 화면 전환 */
const SIDE_VIEWS = ["trust", "history"];
function go(id) {
  if (id === "history") renderHistory();
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === id));
  document.querySelectorAll("[data-nav]").forEach(b => b.classList.toggle("on", b.dataset.nav === id || (!SIDE_VIEWS.includes(id) && b.dataset.nav === "input")));
  window.scrollTo(0, 0);
}
document.addEventListener("click", e => {
  const t = e.target.closest("[data-go]"); if (t) go(t.dataset.go);
});

/* 입력 칩 */
function chips(el, items, multi, first = 0) {
  el.innerHTML = items.map((t, i) => `<button type="button" class="chip" aria-pressed="${!multi && i === first}">${t}</button>`).join("");
  el.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    if (multi) c.setAttribute("aria-pressed", c.getAttribute("aria-pressed") !== "true");
    else el.querySelectorAll(".chip").forEach(x => x.setAttribute("aria-pressed", x === c));
  });
}
/* 역량 칩: 누를 때마다 입문 → 가능 → 능숙 → 해제 */
const LEVELS = ["입문", "가능", "능숙"];
function skillChips(el, names) {
  el.innerHTML = names.map(n => `<button type="button" class="chip" data-name="${n}" data-level="" aria-pressed="false">${n}</button>`).join("");
  el.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    setLevel(c, LEVELS[LEVELS.indexOf(c.dataset.level) + 1] || "");
  });
}
function setLevel(c, level) {
  c.dataset.level = level; c.setAttribute("aria-pressed", String(Boolean(level)));
  c.textContent = level ? `${c.dataset.name} · ${level}` : c.dataset.name;
}
const pickedSkills = () => [...document.querySelectorAll("#skills .chip[aria-pressed=true]")].map(c => [c.dataset.name, c.dataset.level]);
const picked = id => $(`#${id} .chip[aria-pressed=true]`).textContent;
const SKILLS = ["기획", "디자인", "개발", "데이터·AI", "영상", "발표", "글쓰기"];
skillChips($("#skills"), SKILLS);
chips($("#team"), ["팀 없음", "팀원 1~2명 있음", "팀 구성 완료"], false);
chips($("#mateSkills"), SKILLS, true);
const pickedMates = () => [...document.querySelectorAll("#mateSkills .chip[aria-pressed=true]")].map(c => c.textContent);
const syncMates = () => { $("#mateBox").hidden = picked("team") === "팀 없음"; };
$("#team").addEventListener("click", syncMates);
chips($("#goal"), ["완주", "완성", "수상 도전"], false, 1);

/* 프로필은 이 브라우저에 저장해 두고 다음 공고에 다시 쓴다(공고는 저장하지 않음) */
const PROFILE_KEY = "fourdo-profile";
const PROFILE_FIELDS = ["major", "hours", "school", "region", "status", "age", "busy", "exp"];
function saveProfile() {
  const f = $("#form"), data = { skills: pickedSkills(), mates: pickedMates(), team: picked("team"), goal: picked("goal") };
  PROFILE_FIELDS.forEach(k => data[k] = f.elements[k].value);
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(data)); } catch {}
}
function loadProfile() {
  let data = null;
  try { data = JSON.parse(localStorage.getItem(PROFILE_KEY) || "null"); } catch {}
  if (!data) return;
  const f = $("#form");
  PROFILE_FIELDS.forEach(k => { if (data[k] != null) f.elements[k].value = data[k]; });
  (data.skills || []).forEach(([name, level]) => { const c = $(`#skills .chip[data-name="${name}"]`); if (c) setLevel(c, level); });
  ["team", "goal"].forEach(id => {  // 예전에 저장한 값(예: 목표 '경험 쌓기')이 지금 칩에 없으면 기본값을 둔다
    const all = [...document.querySelectorAll(`#${id} .chip`)];
    if (all.some(c => c.textContent === data[id])) all.forEach(c => c.setAttribute("aria-pressed", String(c.textContent === data[id])));
  });
  document.querySelectorAll("#mateSkills .chip").forEach(c => c.setAttribute("aria-pressed", String((data.mates || []).includes(c.textContent))));
  syncMates();
}
loadProfile();

/* 분석 진행: 공고를 읽고 정리하는 단계(AI)는 응답이 올 때까지 기다리고, 나머지 계산은 응답과 함께 끝난다 */
const STEPS = ["공고 읽기", "제출물과 준비 방법 정리", "나에게 맞는 방법 고르기", "하루별 할 일 짜기"];
function paintSteps(i) {
  $("#stepList").innerHTML = STEPS.map((s, k) => {
    const c = k < i ? "done" : k === i ? "now" : "";
    return `<li class="${c}"><span class="tag">${k < i ? "완료" : k === i ? "진행 중" : "대기"}</span>${s}</li>`;
  }).join("");
  $("#bar").style.width = (i / STEPS.length * 100) + "%";
}
$("#form").addEventListener("submit", async e => {
  e.preventDefault(); saveProfile(); go("progress"); paintSteps(0);
  const req = analyze(new FormData(e.target));
  await wait(700); paintSteps(1);
  const r = await req;
  if (!r) return go("input");
  for (const i of [2, 3, 4]) { paintSteps(i); await wait(350); }  // 방법 고르기·배치는 응답에 함께 끝나 있다
  Object.keys(answers).forEach(k => delete answers[k]);
  show(r); go("result");
});

function show(r) {
  DATA = r; TRUST = r.trust || TRUST; dayIdx = 0; done.clear();
  remember(r); renderResult(); renderPlan(); renderDaily(); renderCompare(true); renderTrust();
}

/* 서버 호출: 실패하면 이유를 알리고 입력 화면으로 돌아간다(예시 결과로 대체하지 않는다) */
async function analyze(fd) {
  fd.set("skills", JSON.stringify(pickedSkills()));
  fd.set("team", picked("team"));
  fd.set("goal", picked("goal"));
  fd.set("mate_skills", JSON.stringify(picked("team") === "팀 없음" ? [] : pickedMates()));
  if (TT) fd.set("timetable", TT);
  try {
    const r = await fetch("/api/analyze", { method: "POST", body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert(data.error || "분석하지 못했어요. 잠시 후 다시 시도해 주세요."); return null; }
    return data;
  } catch (err) { console.warn(err); alert("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요."); return null; }
}

/* 재판정·다시 짜기: 같은 분석을 다시 계산(공고는 다시 읽지 않는다) */
async function recompute(path, body, btn) {
  const label = btn.textContent; btn.disabled = true; btn.textContent = "다시 계산하는 중…";
  try {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: DATA.session.id, ...body }) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert(data.error || "다시 계산하지 못했어요."); return null; }
    return data;
  } catch (err) { console.warn(err); alert("서버에 연결하지 못했어요."); return null; }
  finally { btn.disabled = false; btn.textContent = label; }
}

/* 자격 확인 질문: 예/아니오로 답하면 그 답으로 다시 판정 */
const answers = {};
function questionsCard() {
  const qs = DATA.session.questions || [];
  if (!qs.length) return "";
  qs.forEach(q => { if (!(q.q in answers)) answers[q.q] = q.answer; });
  return `<div class="card" id="questions"><h3>참가 자격 확인 질문</h3>
    <p class="hint" style="margin:0 0 10px">입력한 정보만으로는 알 수 없는 조건이에요. 답해 주시면 결과에 반영해요.</p>
    ${qs.map((q, i) => `<div class="req"><span class="badge ${answers[q.q] == null ? "needs_check" : answers[q.q] ? "met" : "lacking"}">${answers[q.q] == null ? "확인 필요" : answers[q.q] ? "예" : "아니오"}</span>
      <div>${esc(q.q)}</div><div class="chips">
        <button type="button" class="chip" data-q="${i}" data-a="1" aria-pressed="${answers[q.q] === true}">예</button>
        <button type="button" class="chip" data-q="${i}" data-a="0" aria-pressed="${answers[q.q] === false}">아니오</button></div></div>`).join("")}
    <div class="actions" style="margin-top:14px"><button class="btn primary" type="button" id="rejudge">답변 반영하기</button></div></div>`;
}
$("#result").addEventListener("click", async e => {
  const c = e.target.closest("[data-q]");
  if (c) {
    const q = DATA.session.questions[+c.dataset.q].q, a = c.dataset.a === "1";
    answers[q] = answers[q] === a ? null : a; renderResult(); return;
  }
  const b = e.target.closest("#rejudge");
  if (b) { const r = await recompute("/api/rejudge", { answers }, b); if (r) { show(r); go("result"); } return; }
});

/* 나의 맞춤 전략: 제출물마다 고른 방법과 이유 */
const TIER_BADGE = { "수상 도전형": "met", "완성형": "caution", "완주형": "needs_check" };
function strategyCard() {
  const S = DATA.strategy || [];
  if (!S.length) return "";
  const who = w => Object.entries(w || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ");
  return `<div class="card" id="strategy"><h3>나의 맞춤 전략</h3>
    <p class="hint" style="margin:0 0 10px">제출물마다 내 목표·역량·시간에 맞는 방법을 하나씩 골랐어요.</p>
    ${S.map(s => `<div class="req" style="align-items:start;grid-template-columns:96px 1fr">
      <span class="badge ${s.method ? TIER_BADGE[s.tier] || "caution" : "lacking"}" style="white-space:nowrap;justify-self:start">${s.tier || "어려움"}</span>
      <div><b>${esc(s.submission)}</b> · ${s.method ? `${esc(s.method)} (${s.hours}시간)` : "지금 역량으로는 만들 수 없어요"}
        <small>${esc(s.requirement)}</small>
        ${s.how ? `<p style="margin:6px 0 0">${esc(s.how)}</p>` : ""}
        ${s.steps.length ? `<small>순서: ${s.steps.map(x => `${esc(x[0])} ${x[1]}시간`).join(" → ")}</small>` : ""}
        ${s.reasons.length ? `<ul class="plain" style="margin:6px 0 0">${s.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
        ${who(s.who) ? `<small>맡는 사람 · ${who(s.who)}</small>` : ""}
      </div></div>`).join("")}</div>`;
}

/* 결과 */
function renderResult() {
  const d = DATA, dl = d.posting.deadline ? new Date(d.posting.deadline) : null;
  const days = dl ? Math.round((new Date(dl).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 864e5) : null;  // 날짜 차이(마감 당일 = D-0)
  const t = d.time, pct = t.available ? Math.min(100, Math.round(t.need / t.available * 100)) : 100;  // 내 시간 중 쓰는 비율
  $("#result").innerHTML = `
  <div class="head dark"><div>
    <h2>${esc(d.posting.title)}</h2><p>${esc(d.posting.organizer)} · ${dl ? `마감 ${dl.getFullYear()}.${dl.getMonth() + 1}.${dl.getDate()}` : "마감일 확인 필요"}</p></div>
    <div class="dday">${days == null ? "" : days < 0 ? "마감" : days === 0 ? "D-DAY" : "D-" + days}</div></div>
  <div class="wrap"><div class="grid">
    <div class="card"><h3>나의 도전 준비도</h3>
      <div class="level">${esc(d.readiness.label)}</div>
      <p style="margin:0 0 10px">${esc(d.readiness.summary)}</p>
      <ul class="plain">${d.readiness.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
      <p class="hint">${esc(d.readiness.disclaimer)}</p>
      <h3 style="margin-top:22px">시간 예산</h3>
      <div class="meter"><i style="width:${pct}%"></i></div>
      <p class="hint">마감까지 쓸 수 있는 ${t.available}시간 중 ${t.need}시간이 필요해요.</p></div>
    <div class="card"><h3>지원 조건 충족 여부</h3>
      ${d.requirements.map(r => `<div class="req"><span class="badge ${r.status}">${STATUS[r.status]}</span>
        <div>${esc(r.label)}<small>${esc(r.criterion)}</small></div></div>`).join("")}</div>
  </div>
  ${strategyCard() ? `<div class="grid one" style="margin-top:0">${strategyCard()}</div>` : ""}
  ${questionsCard() ? `<div class="grid one" style="margin-top:0">${questionsCard()}</div>` : ""}
  <div class="grid" style="margin-top:0">
    <div class="card lime-card"><h3>${esc(d.today.when)} 할 일</h3><b>${esc(d.today.title)}</b>
      <p class="hint" style="color:var(--deep)">${d.today.hours ? `약 ${d.today.hours}시간 · ` : ""}${esc(d.today.note)}</p></div>
    <div class="card"><h3>도전을 막는 요소와 해결 방법</h3>
      ${d.blockers.map(b => `<p><b>${esc(b.d)}</b><br><span class="hint">${esc(b.s)}</span></p><br>`).join("")}</div>
  </div>
  <div class="actions"><button class="btn primary" data-go="daily">하루별 할 일 보기</button>
    <button class="btn" data-go="plan">마감까지 전체 일정 보기</button>
    <button class="btn" data-go="input">다른 공고 분석하기</button></div></div>`;
}

/* 전체 일정(주차별) */
function renderPlan() {
  const d = DATA, t = d.time;
  $("#plan").innerHTML = `
  <div class="head dark"><div><h2>마감일까지, 나에게 맞는 준비 계획.</h2>
    <p>필요한 시간 ${t.need}시간 · 쓸 수 있는 시간 ${t.available}시간</p></div></div>
  <div class="wrap"><div class="grid one"><div class="card"><h3>주차별 작업</h3>
    <div class="weeks">${d.weeks.map(w => `<div class="week ${w.rest ? "rest" : ""}"><b>${w.w}주 · ${esc(w.label)}</b>
      <ul style="padding-left:0">${w.tasks.map(k => `<li><span>${esc(k[0])}</span><span style="white-space:nowrap">${k[1]}시간</span></li>`).join("") || "<li>쉬어가요</li>"}
        ${w.goal ? `<li style="margin-top:6px;font-weight:700">${esc(w.goal)}</li>` : ""}</ul></div>`).join("")}</div></div></div>
  <div class="actions"><button class="btn primary" data-go="daily">하루별 할 일 보기</button>
    <button class="btn" data-go="compare">개인화 비교 보기</button>
    <button class="btn" data-go="result">결과로 돌아가기</button></div></div>`;
}

/* 개인화 비교: 같은 공고를 왼쪽은 프로필 없이(완성형 기준), 오른쪽은 내 프로필로 계산한 결과 */
function renderCompare(on) {
  if (!DATA) return;
  const c = DATA.compare;
  $("#cmp").innerHTML = `
  <div class="pane gen ${on ? "dim" : ""}"><h3>누구에게나 같은 계획 <span class="badge src ai">프로필 없음</span></h3>
    ${c.generic.map(m => `<div class="row"><b style="color:inherit">${esc(m[0])}</b><span>${esc(m[1])}</span></div>`).join("")}<footer>${esc(c.genericNote)}</footer></div>
  <div class="pane me ${on ? "" : "dim"}"><h3>나에게 맞춘 계획 <span class="badge met">내 프로필 반영</span></h3>
    ${c.mine.map(m => `<div class="row"><b>${esc(m[0])}</b><span>${esc(m[1])}</span></div>`).join("")}<footer>${esc(c.mineNote)}</footer></div>`;
  document.querySelectorAll(".toggle button").forEach(b => b.classList.toggle("on", (b.dataset.p === "on") === on));
}
document.querySelector(".toggle").addEventListener("click", e => {
  const b = e.target.closest("button"); if (b) renderCompare(b.dataset.p === "on");
});

/* 서비스 신뢰도: 실제 검수 결과가 없으면 '측정 전'으로 표시 (수치를 지어내지 않아요) */
function renderTrust() {
  $("#trust").innerHTML = `
  <div class="hero dark"><h1>AI의 분석, 어디까지 믿을 수 있을까요?</h1>
    <p class="lime-line">실제 검수 결과와 합성 테스트 결과는 따로 보여드려요.</p></div>
  <div class="wrap page-pad"><div class="card" style="margin-top:-56px">
    <div class="metric mh"><span>항목</span><span>실제 검수</span><span>합성 테스트</span></div>
    ${TRUST.map(m => `<div class="metric"><b>${esc(m.name)}</b>
      <span class="v">${esc(m.real ?? "측정 전")}</span>
      <span class="v">${esc(m.synth ?? "측정 전")}</span></div>`).join("")}
    <p class="hint">검수가 끝나는 대로 결과를 공개해요.</p></div></div>`;
}
fetch("/api/trust").then(r => r.json()).then(t => { TRUST = t; renderTrust(); }).catch(() => {});

/* 에브리타임 시간표 업로드 */
let TT = null, dayIdx = 0; const done = new Set();
function setTT(f) {
  if (f && !f.type.startsWith("image/")) { alert("이미지 파일(PNG, JPG)만 올릴 수 있어요."); return; }
  const prev = $("#ttPrev");
  if ($("#ttImg").src.startsWith("blob:")) URL.revokeObjectURL($("#ttImg").src);
  TT = f || null; prev.hidden = !TT;
  if (TT) { $("#ttImg").src = URL.createObjectURL(TT); $("#ttName").textContent = TT.name; }
  else { $("#tt").value = ""; $("#ttImg").removeAttribute("src"); }
}
$("#drop").addEventListener("click", () => $("#tt").click());
$("#tt").addEventListener("change", e => setTT(e.target.files[0]));
$("#ttDel").addEventListener("click", () => setTT(null));
["dragover", "dragleave", "drop"].forEach(ev => $("#drop").addEventListener(ev, e => {
  e.preventDefault(); $("#drop").classList.toggle("over", ev === "dragover");
  if (ev === "drop") setTT(e.dataTransfer.files[0]);
}));

/* 하루별 할 일: 오늘부터 마감일까지, 한 주씩 넘겨 본다 */
const TIMETABLE_NOTE = {
  ok: "시간표의 수업 시간을 피해서 짰어요.",
  failed: "시간표를 읽지 못해서 수업 시간은 반영하지 못했어요. 평일 저녁과 주말 낮을 기준으로 짰어요.",
  none: "시간표가 없어서 평일 저녁과 주말 낮을 기준으로 짰어요. 시간표를 올리면 수업 시간을 피해 드려요.",
};
function renderDaily() {
  const D = DATA.daily, d = D[dayIdx], from = Math.floor(dayIdx / 7) * 7, hasTT = DATA.meta.timetable === "ok";
  const total = D.reduce((s, x) => s + x.todos.length, 0);
  $("#daily").innerHTML = `
  <div class="head dark"><div>
    <h2>하루별 할 일</h2><p>오늘부터 마감일까지, 날마다 할 일을 정리했어요</p></div>
    <div class="dday" style="font-size:34px">${done.size}/${total}</div></div>
  <div class="wrap"><div class="card" style="margin-top:-48px">
    <p class="hint" style="margin:0 0 14px">${TIMETABLE_NOTE[DATA.meta.timetable]}</p>
    <div class="chips">
      ${from > 0 ? `<button type="button" class="chip" data-day="${from - 7}">‹ 이전 주</button>` : ""}
      ${D.slice(from, from + 7).map((x, i) => `<button type="button" class="chip" data-day="${from + i}" aria-pressed="${from + i === dayIdx}" title="${x.todos.length ? `할 일 ${x.todos.length}개` : "할 일 없음"}">${x.d} ${x.date}${x.todos.length ? " •" : ""}</button>`).join("")}
      ${from + 7 < D.length ? `<button type="button" class="chip" data-day="${from + 7}">다음 주 ›</button>` : ""}</div>
    ${d.note ? `<p class="hint" style="color:var(--violet);font-weight:700">${esc(d.note)}</p>` : ""}
    <div class="cols">
      <div><h3>수업 · 일정</h3>
        ${d.classes.map(c => `<div class="slot"><b>${esc(c[0])}</b><span>${esc(c[1])}</span></div>`).join("") || `<p class="hint">${hasTT ? "수업이 없는 날이에요." : "시간표를 올리면 수업도 함께 보여요."}</p>`}
        <p class="hint">${d.done ? `${d.done}시간 작업했어요 · 남은 시간 ${d.free}시간` : `쓸 수 있는 시간 ${d.free}시간`}${DATA.meta.team > 1 ? " (팀 전체)" : ""}</p></div>
      <div><h3>이날 할 일</h3>
        ${d.todos.map((t, k) => `<label class="todo"><input type="checkbox" data-k="${k}" ${done.has(dayIdx + "-" + k) ? "checked" : ""}>
          <span>${esc(t[1])}<small>${esc(t[0])} · ${t[2]}시간</small></span></label>`).join("") || `<p class="hint">${d.done ? "이날 할 일을 끝냈어요." : "할 일이 없어요. 쉬어가요."}</p>`}</div>
    </div></div>
  ${progressNote()}
  <div class="actions"><button class="btn primary" type="button" id="replan" ${done.size ? "" : "disabled"}>끝낸 일 반영해서 다시 짜기</button>
    <button class="btn" data-go="plan">마감까지 전체 일정 보기</button>
    <button class="btn" data-go="result">결과로 돌아가기</button></div></div>`;
}
function progressNote() {
  const p = DATA.session.progress;
  if (!p || !p.hours) return "";
  return `<p class="hint" style="text-align:center">끝낸 작업 ${p.hours}시간을 반영해서 남은 일정을 다시 짰어요.</p>`;
}
$("#daily").addEventListener("click", async e => {
  const day = e.target.closest("[data-day]");
  if (day) { dayIdx = +day.dataset.day; renderDaily(); return; }
  const b = e.target.closest("#replan"); if (!b) return;
  const doneItems = [...done].map(key => { const [d, k] = key.split("-").map(Number); const t = DATA.daily[d].todos[k]; return [t[3], t[4]]; });
  const r = await recompute("/api/replan", { done: doneItems }, b);
  if (r) { show(r); go("daily"); }
});
$("#daily").addEventListener("change", e => {
  const c = e.target.closest("[data-k]"); if (!c) return;
  const key = dayIdx + "-" + c.dataset.k; c.checked ? done.add(key) : done.delete(key); renderDaily();
});

/* 내 도전 기록: 분석한 공고 목록은 이 브라우저에, 분석 재료는 서버에 있다. 다시 열면 오늘 기준으로 일정을 다시 계산한다 */
const HISTORY_KEY = "fourdo-history";
function records() { try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; } }
function keep(list) { try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, 30))); } catch {} }
function remember(r) {
  const item = { id: r.session.id, title: r.posting.title, organizer: r.posting.organizer, deadline: r.posting.deadline, verdict: r.meta.verdict };
  keep([item, ...records().filter(x => x.id !== item.id)]);
}
function renderHistory() {
  const list = records(), day = iso => { const d = new Date(iso); return `${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}`; };
  $("#history").innerHTML = `
  <div class="head dark"><div><h2>내 도전 기록</h2><p>다시 열면 오늘 날짜에 맞춰 일정을 새로 짜 드려요</p></div></div>
  <div class="wrap"><div class="card" style="margin-top:-48px">
    ${list.map(x => `<div class="req" style="grid-template-columns:110px 1fr auto"><span class="badge ${VERDICT_BADGE[x.verdict] || "caution"}" style="justify-self:start">${esc(x.verdict)}</span>
      <div>${esc(x.title)}<small>${esc(x.organizer)}${x.deadline ? ` · 마감 ${day(x.deadline)}` : ""}</small></div>
      <div class="chips"><button type="button" class="chip" data-open="${esc(x.id)}">열기</button><button type="button" class="chip" data-drop="${esc(x.id)}">지우기</button></div></div>`).join("")
      || '<p class="hint" style="margin:0">아직 분석한 공고가 없어요.</p>'}
    <p class="hint">기록은 이 브라우저에만 저장돼요.</p></div>
  <div class="actions"><button class="btn primary" data-go="input">공고 분석하기</button></div></div>`;
}
$("#history").addEventListener("click", async e => {
  const drop = e.target.closest("[data-drop]");
  if (drop) { keep(records().filter(x => x.id !== drop.dataset.drop)); renderHistory(); return; }
  const open = e.target.closest("[data-open]"); if (!open) return;
  open.disabled = true;
  try {
    const r = await fetch("/api/session/" + open.dataset.open), data = await r.json().catch(() => ({}));
    if (r.status === 410) { alert(data.error); keep(records().filter(x => x.id !== open.dataset.open)); renderHistory(); return; }
    if (!r.ok) { alert(data.error || "기록을 열지 못했어요."); return; }
    Object.keys(answers).forEach(k => delete answers[k]);
    show(data); go("result");
  } catch (err) { console.warn(err); alert("서버에 연결하지 못했어요."); }
  finally { open.disabled = false; }
});

renderTrust(); go("input");
