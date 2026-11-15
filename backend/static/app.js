/* FourDo 프론트엔드. /api/analyze 응답을 DATA에 담아 렌더링. 서버에 연결되지 않으면 아래 예시 데이터로 대체. */
let DATA = {
  meta: { is_demo: true },
  posting: { title: "[샘플] 대학생 아이디어 공모전", organizer: "[샘플 주최기관]", deadline: "2026-12-10T18:00:00+09:00" },
  requirements: [
    { label: "대학생 · 재학 중", criterion: "대학(원)생", status: "met", source: "code" },
    { label: "팀 구성", criterion: "2~4인 팀 · 현재 팀 없음", status: "caution", source: "code" },
    { label: "제출물: 기획서 + 시제품 영상", criterion: "영상 제작 경험 없음", status: "lacking", source: "ai" },
    { label: "수상 후 의무 사항", criterion: "공고에 명확한 기재 없음", status: "needs_check", source: "ai" }
  ],
  readiness: { label: "해볼 만해요 · 시간이 변수", reasons: ["참가 자격은 충족해요", "팀 구성과 영상 제작 준비가 필요해요"], disclaimer: "준비도는 합격 확률이 아니에요." },
  time: { min: 28, max: 36, available: 34 },
  thisWeek: { title: "주제 후보 3개 정리하고 공고 평가기준 읽기", hours: 6, note: "목~일에 몰아서 해요" },
  blockers: [
    { d: "팀원이 없어요", s: "학과·동아리 커뮤니티에서 팀원을 모집하거나, 개인 참가가 가능한지 확인해요" },
    { d: "영상 제작 경험이 없어요", s: "튜토리얼부터 보고, 영상 대신 이미지 시안으로 범위를 줄여요" }
  ],
  weeks: [
    { w: 1, label: "주제", tasks: [["주제 후보 3개 정리", 3], ["공고 평가기준 읽기", 3]] },
    { w: 2, label: "중간고사 주간", rest: true, tasks: [] },
    { w: 3, label: "기획서", tasks: [["영상 제작 튜토리얼", 4], ["기획서 초안 작성", 10]] },
    { w: 4, label: "영상, 제출", tasks: [["시제품 영상 제작", 8], ["최종 검토 및 제출", 2]] }
  ],
  fallbacks: ["팀원 구하기", "영상 대신 이미지 시안으로 범위 줄이기", "다음 회차 알림 받기"],
  compare: {
    generic: ["아이디어를 구상하고 기획서를 작성하세요", "충분한 시간을 두고 준비하세요", "팀원을 구해 역할을 나눠보세요"],
    genericNote: "언제, 얼마나, 무엇부터인지 알 수 없어요",
    mine: [["이번 주", "주제 후보 3개 · 약 6시간"], ["2주차", "중간고사 주간이라 쉬어가요"], ["3~4주차", "영상 경험이 없어 튜토리얼 4h 먼저"]],
    mineNote: "3학년 · 주 12시간 · 기획 가능 · 팀 없음 반영 (예시)"
  },
  trust: [
    { name: "공고 자격 요건 추출 정확도" },
    { name: "제출물 및 준비 작업 추출 정확도" },
    { name: "마감일 및 일정 계산 정확도" },
    { name: "개인화 실행 계획 적합도" }
  ]
};
/* 하루별 할 일 (데모). 실제 연결 시: 시간표 이미지 → 수업 시간 추출 → 코드가 남는 시간 계산 → AI가 할 일 배치 */
DATA.daily = [
  { d: "월", date: "11/16", free: 5, classes: [["09:00–10:30", "자료구조"], ["13:00–15:00", "오픈SW플랫폼"]], todos: [["19:00–20:30", "주제 후보 1차 정리", 1.5]] },
  { d: "화", date: "11/17", free: 6, classes: [["10:30–12:00", "수치해석"]], todos: [["20:00–21:00", "공고 평가기준 읽기", 1]] },
  { d: "수", date: "11/18", free: 4, classes: [["09:00–10:30", "자료구조"], ["15:00–17:00", "인공지능 개론"]], todos: [] },
  { d: "목", date: "11/19", free: 6, classes: [["10:30–12:00", "수치해석"]], todos: [["13:00–14:30", "주제 후보 3개 확정", 1.5]] },
  { d: "금", date: "11/20", free: 5, classes: [["13:00–15:00", "오픈SW플랫폼"]], todos: [] },
  { d: "토", date: "11/21", free: 8, classes: [], todos: [["10:00–12:00", "기획서 방향 메모", 2]] },
  { d: "일", date: "11/22", free: 8, classes: [], todos: [] }
];
const DEMO = DATA;
const STATUS = { met: "충족", caution: "주의", lacking: "부족", needs_check: "확인 필요" };
const SRC = { code: "코드 판정", ai: "AI 추출" };
const $ = (s, r = document) => r.querySelector(s);
const demo = () => DATA.meta.is_demo ? '<span class="badge demo">예시</span>' : "";

/* 화면 전환 */
function go(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === id));
  document.querySelectorAll("[data-nav]").forEach(b => b.classList.toggle("on", b.dataset.nav === id || (id !== "trust" && b.dataset.nav === "input")));
  window.scrollTo(0, 0);
}
document.addEventListener("click", e => {
  const t = e.target.closest("[data-go]"); if (t) go(t.dataset.go);
});

/* 입력 칩 */
function chips(el, items, multi) {
  el.innerHTML = items.map((t, i) => `<button type="button" class="chip" aria-pressed="${!multi && i === 0}">${t}</button>`).join("");
  el.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    if (multi) c.setAttribute("aria-pressed", c.getAttribute("aria-pressed") !== "true");
    else el.querySelectorAll(".chip").forEach(x => x.setAttribute("aria-pressed", x === c));
  });
}
chips($("#skills"), ["기획", "디자인", "개발", "영상", "발표", "글쓰기"], true);
chips($("#team"), ["팀 없음", "팀원 1~2명 있음", "팀 구성 완료"], false);

/* 분석 진행 (데모용 시뮬레이션) */
const STEPS = ["공고 핵심 정보 파악", "지원 조건 및 일정 확인", "시간표 기반 가용 시간 계산", "하루별 맞춤 할 일 구성"];
$("#form").addEventListener("submit", e => {
  e.preventDefault(); go("progress");
  const req = analyze(new FormData(e.target));
  const list = $("#stepList"), bar = $("#bar"); let i = 0;
  const tick = () => {
    list.innerHTML = STEPS.map((s, k) => {
      const c = k < i ? "done" : k === i ? "now" : "";
      return `<li class="${c}"><span class="tag">${k < i ? "완료" : k === i ? "진행 중" : "대기"}</span>${s}</li>`;
    }).join("");
    bar.style.width = (i / STEPS.length * 100) + "%";
    if (i++ < STEPS.length) setTimeout(tick, 900);
    else req.then(r => { if (!r) return go("input"); DATA = r; dayIdx = 0; done.clear(); renderResult(); renderPlan(); renderDaily(); go("result"); });
  };
  tick();
});

/* 서버 호출: 입력 오류(4xx)는 안내하고, 서버 장애·연결 실패 시 예시 데이터로 대체 */
async function analyze(fd) {
  fd.set("skills", JSON.stringify([...document.querySelectorAll("#skills .chip[aria-pressed=true]")].map(c => c.textContent)));
  fd.set("team", $("#team .chip[aria-pressed=true]").textContent);
  if (TT) fd.set("timetable", TT);
  try {
    const r = await fetch("/api/analyze", { method: "POST", body: fd });
    if (r.status >= 400 && r.status < 500) { alert((await r.json()).error || "입력을 확인해 주세요."); return null; }
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (err) { console.warn(err); return DEMO; }
}

/* 결과 */
function renderResult() {
  const d = DATA, dl = new Date(d.posting.deadline);
  const days = Math.max(0, Math.ceil((dl - new Date()) / 864e5));
  const t = d.time, pct = Math.min(100, Math.round(t.available / t.max * 100));
  $("#result").innerHTML = `
  <div class="head dark"><div>${DATA.meta.is_demo ? '<span class="badge demo">데모 예시 결과</span>' : ''}
    <h2>${d.posting.title}</h2><p>${d.posting.organizer} · 마감 ${dl.getFullYear()}.${dl.getMonth() + 1}.${dl.getDate()}</p></div>
    <div class="dday">D-${days}</div></div>
  <div class="wrap"><div class="grid">
    <div class="card"><h3>나의 도전 준비도 ${demo()}</h3>
      <div class="level">${d.readiness.label}</div>
      <ul class="plain">${d.readiness.reasons.map(r => `<li>${r}</li>`).join("")}</ul>
      <p class="hint">${d.readiness.disclaimer}</p>
      <h3 style="margin-top:22px">시간 예산</h3>
      <div class="meter"><i style="width:${pct}%"></i></div>
      <p class="hint">필요 ${t.min}~${t.max}시간 / 가용 ${t.available}시간 · 코드 계산</p></div>
    <div class="card"><h3>지원 조건 충족 여부</h3>
      ${d.requirements.map(r => `<div class="req"><span class="badge ${r.status}">${STATUS[r.status]}</span>
        <div>${r.label}<small>${r.criterion}</small></div><span class="badge src ${r.source}">${SRC[r.source]}</span></div>`).join("")}</div>
  </div>
  <div class="grid" style="margin-top:0">
    <div class="card lime-card"><h3>이번 주 추천 작업</h3><b>${d.thisWeek.title}</b>
      <p class="hint" style="color:var(--deep)">약 ${d.thisWeek.hours}시간 · ${d.thisWeek.note}</p></div>
    <div class="card"><h3>도전을 막는 요소와 해결 방법</h3>
      ${d.blockers.map(b => `<p><b>${b.d}</b><br><span class="hint">${b.s}</span></p><br>`).join("")}</div>
  </div>
  <div class="actions"><button class="btn primary" data-go="plan">나만의 실행 계획 확인하기</button>
    <button class="btn" data-go="input">다시 분석하기</button></div></div>`;
}

/* 실행 계획 */
function renderPlan() {
  const d = DATA, t = d.time;
  $("#plan").innerHTML = `
  <div class="head dark"><div><h2>마감일까지, 나에게 맞는 준비 계획.</h2>
    <p>총 예상 ${t.min}~${t.max}시간 · 확보 가능 ${t.available}시간 ${demo()}</p></div></div>
  <div class="wrap"><div class="grid one"><div class="card"><h3>주차별 작업</h3>
    <div class="weeks">${d.weeks.map(w => `<div class="week ${w.rest ? "rest" : ""}"><b>${w.w}주 · ${w.label}</b>
      <ul>${w.tasks.map(k => `<li><span>${k[0]}</span><span>${k[1]}h</span></li>`).join("") || "<li>쉬어가요</li>"}</ul></div>`).join("")}</div></div>
    <div class="card"><h3>시간이 모자라면</h3><ul class="plain">${d.fallbacks.map(f => `<li>${f}</li>`).join("")}</ul></div></div>
  <div class="actions"><button class="btn primary" data-go="daily">하루별 할 일 보기</button>
    <button class="btn" data-go="compare">개인화 비교 보기</button>
    <button class="btn" data-go="result">결과로 돌아가기</button></div></div>`;
}

/* 개인화 비교 */
function renderCompare(on) {
  const c = DATA.compare;
  $("#cmp").innerHTML = `
  <div class="pane gen ${on ? "dim" : ""}"><h3>일반 AI 답변 <span class="badge src ai">프로필 없음</span></h3>
    ${c.generic.map(x => `<p>· ${x}</p>`).join("")}<footer>${c.genericNote}</footer></div>
  <div class="pane me ${on ? "" : "dim"}"><h3>FourDo · 내 프로필 반영 <span class="badge met">프로필 반영</span></h3>
    ${c.mine.map(m => `<div class="row"><b>${m[0]}</b><span>${m[1]}</span></div>`).join("")}<footer>${c.mineNote}</footer></div>`;
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
    ${DATA.trust.map(m => `<div class="metric"><b>${m.name}</b>
      <span class="v">${m.real ?? "측정 전"}</span>
      <span class="v">${m.synth ?? "측정 전"}</span></div>`).join("")}
    <p class="hint">검수가 끝나는 대로 결과를 공개해요.</p></div></div>`;
}
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

/* 하루별 할 일 */
function renderDaily() {
  const D = DATA.daily, d = D[dayIdx];
  const total = D.reduce((s, x) => s + x.todos.length, 0);
  $("#daily").innerHTML = `
  <div class="head dark"><div>${DATA.meta.is_demo ? '<span class="badge demo">데모 예시</span>' : ''}
    <h2>하루별 할 일</h2><p>수업 시간을 피해서 짠 이번 주 ToDo</p></div>
    <div class="dday" style="font-size:34px">${done.size}/${total}</div></div>
  <div class="wrap"><div class="card" style="margin-top:-48px">
    <p class="hint" style="margin:0 0 14px">${DATA.meta.is_demo ? "서버에 연결되지 않아 예시 시간표 기준이에요." : DATA.meta.timetable ? "올린 시간표를 기준으로 짰어요." : "시간표를 올리지 않아서 하루 가용 시간을 12시간으로 가정했어요."}</p>
    <div class="chips">${D.map((x, i) => `<button type="button" class="chip" data-day="${i}" aria-pressed="${i === dayIdx}">${x.d} ${x.date}</button>`).join("")}</div>
    <div class="cols">
      <div><h3>수업 · 일정</h3>
        ${d.classes.map(c => `<div class="slot"><b>${c[0]}</b><span>${c[1]}</span></div>`).join("") || '<p class="hint">수업이 없는 날이에요</p>'}
        <p class="hint">남는 시간 약 ${d.free}시간 <span class="badge src">코드 계산</span></p></div>
      <div><h3>이날 할 일 <span class="badge src ai">AI 생성</span></h3>
        ${d.todos.map((t, k) => `<label class="todo"><input type="checkbox" data-k="${k}" ${done.has(dayIdx + "-" + k) ? "checked" : ""}>
          <span>${t[1]}<small>${t[0]} · ${t[2]}시간</small></span></label>`).join("") || '<p class="hint">할 일이 없어요. 쉬어가요.</p>'}</div>
    </div></div>
  <div class="actions"><button class="btn primary" data-go="compare">개인화 비교 보기</button>
    <button class="btn" data-go="plan">주차별 계획으로</button></div></div>`;
}
$("#daily").addEventListener("click", e => {
  const b = e.target.closest("[data-day]"); if (b) { dayIdx = +b.dataset.day; renderDaily(); }
});
$("#daily").addEventListener("change", e => {
  const c = e.target.closest("[data-k]"); if (!c) return;
  const key = dayIdx + "-" + c.dataset.k; c.checked ? done.add(key) : done.delete(key); renderDaily();
});

renderTrust(); renderCompare(true); go("input");
