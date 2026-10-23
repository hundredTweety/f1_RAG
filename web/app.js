// F1 레이스 노트 — 화면 동작 (F2013)
// 서버 API: GET /api/status, GET /api/races, GET /api/races/{id}/docs,
//           POST /api/ask (NDJSON 스트림), POST /api/explore
// 화면 콘셉트는 피트월 타이밍 화면이다. 답변 진행(NDJSON progress 이벤트의 stage)을
// 랩의 세 섹터로 보여 준다: collect·collect_done → S1 문서 수집, retrieve → S2 근거 검색, generate → S3 답변 생성.
"use strict";

const $ = (sel) => document.querySelector(sel);
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const SECTORS = [
  { key: "collect", name: "S1 문서 수집", stages: ["collect", "collect_done"] },
  { key: "retrieve", name: "S2 근거 검색", stages: ["retrieve"] },
  { key: "generate", name: "S3 답변 생성", stages: ["generate"] },
];
const state = {
  status: null,
  races: [],
  season: null,
  filter: "",
  current: null,      // 선택한 경기
  mode: "qa",
  histories: {},      // 경기별 대화 기록 (F2009)
  busy: false,
};

// ---------- 저장 (브라우저 저장소가 막혀 있어도 동작하도록)
const STORE_KEY = "f1-race-notes-history";
function loadHistories() {
  try { state.histories = JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}"); } catch { state.histories = {}; }
}
function saveHistories() {
  try { sessionStorage.setItem(STORE_KEY, JSON.stringify(state.histories)); } catch { /* 무시 */ }
}

// ---------- 공통
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) node.append(c);
  return node;
}
function showBanner(message, kind = "warn") {
  const b = $("#banner");
  if (!message) { b.hidden = true; return; }
  b.textContent = message;
  b.className = "banner" + (kind === "error" ? " error" : "");
  b.hidden = false;
}

// 답변 본문: 소제목(###), 목록(-), 굵게(**), 근거 번호([근거 n])만 표시용으로 바꾼다
function renderAnswer(text, used) {
  const usedSet = new Set(used.map((e) => e.n));
  // [근거 3], [근거 1, 2], [근거 2-4] 외에 모델이 가끔 쓰는 [위키백과 근거 4], [경기 메타데이터·근거 1]도 읽는다
  // (f1rag/rag/citations.py 와 같은 규칙). '근거'가 없는 대괄호는 그대로 둔다.
  const expand = (nums) => nums.split(/\s*,\s*/).filter(Boolean).flatMap((p) => {
    const r = p.split(/\s*[~\-–]\s*/).map(Number);
    if (r.length === 2 && r[0] && r[1] && r[1] - r[0] > 0 && r[1] - r[0] < 30) return Array.from({ length: r[1] - r[0] + 1 }, (_, i) => r[0] + i);
    return r.filter((n) => n);
  });
  const cite = (s) => s.replace(/\[([^\[\]\n]{1,120})\]/g, (m, inner) => {
    if (!inner.includes("근거")) return m;
    const nums = [...inner.matchAll(/근거\s*[·:#]?\s*([0-9][0-9\s,~\-–]*)/g)].flatMap((x) => expand(x[1].trim()));
    if (!nums.length) return m;
    return nums.filter((n) => usedSet.has(n))
      .map((n) => `<button type="button" class="cite" data-n="${n}" aria-label="근거 ${n} 보기">근거 ${n}</button>`).join("");
  });
  const inline = (s) => cite(esc(s)).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const out = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (/^[-*]\s+/.test(line)) {
      if (!list) { list = []; }
      list.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (!line) continue;
    const h = line.match(/^#{1,4}\s+(.*)$/);
    out.push(h ? `<h4>${inline(h[1])}</h4>` : `<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

// ---------- 상태·목록
async function loadStatus() {
  const res = await fetch("/api/status");
  const s = await res.json();
  state.status = s;
  $("#service-name").textContent = s.service_name;
  $("#tagline").textContent = s.tagline;
  $("#count-items").textContent = s.items.toLocaleString("ko-KR");
  $("#count-docs").textContent = s.docs.toLocaleString("ko-KR");
  if (!s.ready) showBanner(s.message, "error");
  else if (s.fake_models) showBanner("가짜 모델 모드입니다. 답변은 화면 확인용 예시이며 사실이 아닙니다.");
  const tyreName = { race_meta: "하드", wikipedia: "미디엄", fia_regulation: "인터미디엇", fia_decision: "웻" };
  $("#legend").replaceChildren(...Object.entries(s.source_labels).map(([k, v]) =>
    el("li", {}, [el("span", { class: "tyre", "data-src": k, "aria-hidden": "true" }), el("span", { text: v }),
      el("span", { class: "muted", text: tyreName[k] ? `(${tyreName[k]})` : "" })])));
  $("#examples").replaceChildren(...(s.examples || []).slice(0, 4).map((q) => el("li", { text: q })));
  renderModeTabs();
}

async function loadRaces() {
  const res = await fetch("/api/races");
  if (!res.ok) return;
  state.races = await res.json();
  const seasons = [...new Set(state.races.map((r) => r.season))].sort();
  state.season = seasons[seasons.length - 1] ?? null;
  const tabs = $("#season-tabs");
  tabs.replaceChildren(...seasons.map((s) => el("button", {
    type: "button", role: "tab", "aria-selected": String(s === state.season), text: s,
    onclick: () => { state.season = s; [...tabs.children].forEach((b) => b.setAttribute("aria-selected", String(b.textContent == s))); renderRaceList(); },
  })));
  renderRaceList();
}

function renderRaceList() {
  const q = state.filter.trim().toLowerCase();
  const rows = state.races.filter((r) => q
    ? `${r.label} ${r.title} ${r.winner} ${r.circuit} ${r.country}`.toLowerCase().includes(q)
    : r.season === state.season);
  const list = $("#race-list");
  if (!rows.length) { list.replaceChildren(el("li", { class: "muted", text: "맞는 경기가 없습니다." })); return; }
  list.replaceChildren(...rows.map((r) => el("li", {}, el("button", {
    type: "button", "aria-current": String(state.current?.item_id === r.item_id),
    onclick: () => selectRace(r.item_id),
  }, [
    el("span", { class: "rnd", text: q ? `${String(r.season).slice(2)}·${String(r.round).padStart(2, "0")}` : `R${String(r.round).padStart(2, "0")}` }),
    el("span", { class: "name" }, [document.createTextNode(r.title.replace(/^\d{4}\s/, "")), el("span", { class: "winner", text: r.winner ? `우승 ${r.winner}` : "" })]),
  ]))));
}

// ---------- 경기 선택
async function selectRace(id) {
  const race = state.races.find((r) => r.item_id === id);
  if (!race) return;
  state.current = race;
  $("#empty-state").hidden = true;
  $("#race-view").hidden = false;
  const total = Math.max(...state.races.filter((r) => r.season === race.season).map((r) => r.round));
  const flag = race.round === total ? " (시즌 최종전)" : race.round === 1 ? " (개막전)" : "";
  $("#race-round-num").textContent = `R${String(race.round).padStart(2, "0")}`;
  $("#race-round").textContent = `${race.season} 시즌 ${race.round}라운드`;
  $("#race-title").textContent = race.title.replace(/^\d{4}\s/, "");
  const facts = [["라운드", `${race.round} / ${total}${flag}`], ["날짜", race.date], ["서킷", race.circuit], ["나라", race.country]];
  renderTiming(race);
  loadCarPhoto(race);
  $("#race-facts").replaceChildren(...facts.filter(([, v]) => v).map(([k, v]) => el("div", {}, [el("dt", { text: k }), el("dd", { text: v })])));
  renderRaceList();
  renderThread();
  loadCollected();
  closeDrawer(false);
  playRaceTransition(race);
  if (matchMedia("(max-width: 900px)").matches) $("#race-view").scrollIntoView({ block: "start" });
  $("#question").focus({ preventScroll: true });
}

// 기록 스트립: 우승, 예선 1위, 1번 그리드, 패스티스트 랩, 랩, 완주. 값이 없는 칸은 뺀다.
function renderTiming(race) {
  const diff = !!race.pole && !!race.grid_one && race.pole !== race.grid_one;
  const cells = [
    { k: "우승", cls: "win", v: race.winner, sub: race.winner_team },
    { k: "예선 1위", v: race.pole },
    { k: "1번 그리드", v: race.grid_one, sub: diff ? "예선 1위와 다름" : "", cls: diff ? "diff" : "" },
    { k: "패스티스트 랩", v: race.fastest_lap },
    { k: "랩", v: race.laps, num: true },
    { k: "완주", v: race.starters ? `${race.starters - (race.retirements || 0)}/${race.starters}` : "", num: true },
  ].filter((c) => c.v);
  // 넓은 화면: 우승 칸은 넓게, 숫자 칸은 좁게
  $("#timing-strip").style.setProperty("--cols", cells.map((c) => (c.cls === "win" ? "minmax(0,1.5fr)" : c.num ? "minmax(0,.7fr)" : "minmax(0,1.15fr)")).join(" "));
  $("#timing-strip").replaceChildren(...cells.map((c) => el("div", { class: c.cls || "" }, [
    el("dt", { text: c.k }),
    el("dd", {}, [c.num ? el("span", { class: "num", text: String(c.v) }) : document.createTextNode(c.v), c.sub ? el("small", { text: c.sub }) : null]),
  ])));
}

// 우승 차 사진: 서버가 위키미디어 공용에서 찾은 자유 라이선스 사진. 없으면 칸을 숨긴다.
async function loadCarPhoto(race) {
  const fig = $("#car-photo");
  fig.hidden = true;
  let data = { found: false };
  try {
    const res = await fetch(`/api/races/${encodeURIComponent(race.item_id)}/car`);
    if (res.ok) data = await res.json();
  } catch { /* 사진 없이 표시 */ }
  if (state.current?.item_id !== race.item_id || !data.found) return;
  const img = $("#car-img");
  img.alt = `${data.car}: ${race.season} 시즌 우승 팀 ${race.winner_team || ""}의 차량 사진`;
  img.onload = () => {
    if (state.current?.item_id !== race.item_id) return;
    fig.hidden = false;
    if (!reduceMotion()) fig.animate([{ opacity: 0, clipPath: "inset(0 0 0 100%)" }, { opacity: 1, clipPath: "inset(0 0 0 0)" }], { duration: 420, easing: "cubic-bezier(.3,.7,.2,1)" });
  };
  img.src = data.image;
  $("#car-name").textContent = data.car;
  const credit = $("#car-credit");
  credit.replaceChildren(
    document.createTextNode("사진: "),
    el("a", { href: data.file_page, target: "_blank", rel: "noopener", text: data.artist }),
    document.createTextNode(", "),
    data.license_url ? el("a", { href: data.license_url, target: "_blank", rel: "noopener", text: data.license }) : document.createTextNode(data.license),
    document.createTextNode(", 위키미디어 공용. "),
    document.createTextNode(data.photo_of ? `${data.car} 사진이 없어 기본형(${data.photo_of}) 사진입니다.` : "이 경기에서 찍은 사진이 아닐 수 있습니다."),
  );
}

// 텔레메트리 전환: 경기 패널이 왼쪽부터 열리며 빨간 스캔 선이 지나가고,
// 라운드 번호가 R01부터 올라가 멈춘 뒤 경기 정보가 한 줄씩 들어온다.
function playRaceTransition(race) {
  if (reduceMotion()) return;
  const head = $("#race-head");
  head.classList.remove("scan");
  void head.offsetWidth;
  head.classList.add("scan");
  head.animate([{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0 0 0)" }], { duration: 460, easing: "cubic-bezier(.3,.7,.2,1)" });
  const num = $("#race-round-num");
  const target = race.round;
  const t0 = performance.now();
  const dur = 380;
  const tick = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    num.textContent = `R${String(Math.max(1, Math.round(target * (1 - Math.pow(1 - k, 3))))).padStart(2, "0")}`;
    if (k < 1 && state.current?.item_id === race.item_id) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  const rows = [$("#race-round"), $("#race-title"), ...$("#race-facts").children];
  rows.forEach((d, i) => d.animate([{ opacity: 0, transform: "translateX(-10px)" }, { opacity: 1, transform: "none" }], { duration: 220, delay: 160 + i * 45, easing: "ease-out", fill: "backwards" }));
  [...$("#timing-strip").children].forEach((d, i) => d.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 240, delay: 380 + i * 60, easing: "ease-out", fill: "backwards" }));
}

async function loadCollected() {
  if (!state.current) return;
  const res = await fetch(`/api/races/${encodeURIComponent(state.current.item_id)}/docs`);
  const docs = res.ok ? await res.json() : [];
  $("#collected-summary").textContent = `이 경기에 수집된 문서 ${docs.length}건`;
  $("#collected-list").replaceChildren(...(docs.length ? docs.map((d) => el("li", {}, [
    el("a", { href: d.url, target: "_blank", rel: "noopener", text: d.title }),
    document.createTextNode(d.is_item_doc ? " (경기 문서)" : ""),
  ])) : [el("li", { class: "muted", text: "아직 없습니다. 질문하면 관련 문서를 수집합니다." })]));
}

// ---------- 기능 탭 (질문하기 / 판정 해설 / 사실 판정)
function renderModeTabs() {
  const tabs = $("#mode-tabs");
  tabs.replaceChildren(...state.status.modes.map((m) => el("button", {
    type: "button", role: "tab", "aria-selected": String(m.key === state.mode), text: m.label,
    onclick: () => { state.mode = m.key; renderModeTabs(); },
  })));
  const mode = state.status.modes.find((m) => m.key === state.mode);
  $("#question").placeholder = mode.placeholder;
  const notice = $("#mode-notice");
  notice.textContent = mode.notice;
  notice.hidden = !mode.notice;
}

// ---------- 대화
function renderThread() {
  const thread = $("#thread");
  const history = state.histories[state.current.item_id] || [];
  thread.replaceChildren(...history.map(renderMessage));
  // 방금 받은 답변만 짧게 들어오게 한다 (경기를 다시 열 때 다시 움직이지 않음)
  if (state.freshId && !reduceMotion()) {
    const fresh = thread.querySelector(`[data-id="${state.freshId}"]`);
    if (fresh) fresh.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: "cubic-bezier(.2,.7,.2,1)" });
  }
  state.freshId = null;
}

function renderMessage(msg) {
  if (msg.role === "user") {
    return el("div", { class: "msg msg-question" }, [el("span", { class: "mode-tag", text: msg.modeLabel }), el("span", { text: msg.text })]);
  }
  if (msg.role === "error") return el("div", { class: "notices" }, el("p", { text: msg.text }));
  const r = msg.result;
  const node = $("#tpl-answer").content.firstElementChild.cloneNode(true);
  node.dataset.id = msg.id;
  node.querySelector(".answer-body").innerHTML = renderAnswer(r.answer, r.used_evidence);
  const notices = node.querySelector(".notices");
  for (const n of r.notices) notices.append(el("p", { text: n }));
  if (r.mode_notice) notices.append(el("p", { class: "ruling", text: r.mode_notice }));
  if (r.risk_disclaimer) notices.append(el("p", { text: r.risk_disclaimer }));
  const evTitle = node.querySelector(".evidence-title");
  const evList = node.querySelector(".evidence-list");
  if (r.used_evidence.length) {
    evTitle.textContent = `답변에 사용한 근거 ${r.used_evidence.length}건 (검색된 근거 ${r.evidence_count}건 중)`;
    for (const e of r.used_evidence) {
      const isUrl = /^https?:/.test(e.url);
      evList.append(el("li", { id: `ev-${msg.id}-${e.n}` }, [
        el("div", { class: "ev-head" }, [
          el("span", { class: "tyre", "data-src": e.source_type, "aria-hidden": "true" }),
          el("span", { class: "ev-n", text: `근거 ${e.n}` }),
          el("span", { text: e.title }),
          el("span", { class: "ev-src", text: e.source_label + (e.source_type.startsWith("fia") && e.location ? `, ${e.location}` : "") }),
          isUrl ? el("a", { href: e.url, target: "_blank", rel: "noopener", text: "원문 보기" }) : null,
        ]),
        el("details", {}, [el("summary", { text: "근거 내용" }), el("pre", { text: e.text })]),
      ]));
    }
  } else {
    node.querySelector(".evidence").hidden = true;
  }
  node.querySelectorAll(".cite").forEach((b) => b.addEventListener("click", () => {
    const e = r.used_evidence.find((x) => String(x.n) === b.dataset.n);
    if (!e) return;
    openDrawer(e, b);
    const target = node.querySelector(`#ev-${msg.id}-${b.dataset.n}`);
    if (target) {
      target.classList.add("flash");
      setTimeout(() => target.classList.remove("flash"), 1600);
    }
  }));
  // 답변 아래: 섹터 기록(S1 수집 / S2 검색 / S3 생성)과 처리 시간
  const t = r.timings || {};
  const fmt = (v) => (typeof v === "number" ? v.toFixed(2) : "-");
  const meta = node.querySelector(".meta-line");
  meta.append(el("span", { class: "splits", "aria-label": "단계별 처리 시간" }, [
    el("span", { title: "S1 문서 수집", text: `S1 ${fmt(t.collect)}` }),
    el("span", { title: "S2 근거 검색", text: `S2 ${fmt(t.retrieve)}` }),
    el("span", { title: "S3 답변 생성", text: `S3 ${fmt(t.generate)}` }),
    el("span", { class: "lap", title: "전체", text: fmt(t.total) }),
  ]));
  const parts = [`처리 ${t.total ?? "-"}초`];
  if (r.collected_titles.length) parts.push(`새로 수집: ${r.collected_titles.join(", ")}`);
  if (r.reused_queries) parts.push(`이미 수집한 검색어 ${r.reused_queries}개 재사용`);
  meta.append(el("span", { text: parts.join(" / ") }));
  if (msg.log?.length) {
    node.append(el("details", { class: "answer-log" }, [
      el("summary", { text: `분석 로그 ${msg.log.length}줄` }),
      el("ol", { class: "run-log" }, msg.log.map(logRow)),
    ]));
  }
  return node;
}

async function submitQuestion(ev) {
  ev.preventDefault();
  if (state.busy || !state.current) return;
  const input = $("#question");
  const question = input.value.trim();
  if (!question) return;
  const raceId = state.current.item_id;
  const mode = state.status.modes.find((m) => m.key === state.mode);
  const history = (state.histories[raceId] ||= []);
  history.push({ role: "user", text: question, modeLabel: mode.label });
  saveHistories();
  input.value = "";
  renderThread();

  // 진행 상태 (F2012): 랩의 세 섹터
  const board = createLapBoard(question, mode.label);
  $("#thread").append(board.node);
  board.node.scrollIntoView({ block: "nearest" });
  const addStep = (text, stage) => board.update(stage, text);
  addStep("요청을 보냈습니다", "collect");

  state.busy = true;
  $("#ask-button").disabled = true;
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: raceId, question, mode: state.mode }),
    });
    if (!res.ok) throw new Error(res.status === 422 ? "질문은 1~500자로 입력하세요." : `서버 응답 오류 (${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === "progress") addStep(event.message, event.stage);
        else if (event.type === "result") {
          const id = Date.now();
          board.finish(event.data.timings?.total);
          history.push({ role: "assistant", id, result: event.data, log: board.lines });
          state.freshId = id;
          finished = true;
        }
        else if (event.type === "error") { history.push({ role: "error", text: event.message }); finished = true; }
      }
    }
  } catch (err) {
    history.push({ role: "error", text: err.message || "요청을 보내지 못했습니다. 서버가 켜져 있는지 확인하세요." });
  } finally {
    board.stop();
    state.busy = false;
    $("#ask-button").disabled = false;
    saveHistories();
    if (state.current?.item_id === raceId) { renderThread(); loadCollected(); }
    loadStatus().catch(() => {});
  }
}

// ---------- 라이브 분석: 진행 이벤트를 섹터와 시각이 찍힌 로그로 표시
const fmtClock = (ms) => {
  const t = Math.max(0, ms) / 1000;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(1).padStart(4, "0")}`;
};
function logRow(line) {
  return el("li", {}, [
    el("span", { class: "ts", text: line.t }),
    el("span", { class: "sec", "data-s": String(line.s), text: line.s ? `S${line.s}` : "" }),
    el("span", { text: line.text }),
  ]);
}

function createLapBoard(question, modeLabel) {
  const t0 = performance.now();
  const cells = SECTORS.map((sec) => {
    const time = el("span", { class: "s-time", text: "--" });
    const node = el("div", { class: "sector" }, [el("span", { class: "s-name", text: sec.name }), time]);
    return { ...sec, node, time, start: null, end: null };
  });
  const lap = el("strong", { text: "0.0" });
  const log = el("ol", { class: "run-log" });
  const live = el("span", { class: "live-dot", text: "진행 중" });
  const node = el("div", { class: "lap-board", role: "status", "aria-label": "답변 진행 상태" }, [
    el("div", { class: "board-head" }, [live, el("strong", { text: "라이브 분석" }), el("span", { class: "board-q", text: `${modeLabel}: ${question}` })]),
    el("div", { class: "sectors" }, [...cells.map((c) => c.node), el("div", { class: "lap-time" }, [el("span", { text: "경과" }), lap])]),
    log,
  ]);
  const lines = [];
  let current = -1;
  const secs = (ms) => (ms / 1000).toFixed(1);
  const close = (c, now) => {
    if (c.start === null) c.start = now;
    if (c.end !== null) return;
    c.end = now;
    c.node.className = "sector done";
    c.time.textContent = secs(c.end - c.start);
  };
  const enter = (idx) => {
    const now = performance.now();
    for (let i = 0; i < idx; i++) close(cells[i], now);
    if (cells[idx].start === null) cells[idx].start = now;
    if (cells[idx].end === null) cells[idx].node.className = "sector live";
    current = Math.max(current, idx);
  };
  const timer = setInterval(() => {
    const now = performance.now();
    lap.textContent = secs(now - t0);
    const c = cells[current];
    if (c && c.end === null) c.time.textContent = secs(now - c.start);
  }, 100);
  return {
    node,
    lines,
    update(stage, message) {
      const idx = SECTORS.findIndex((sec) => sec.stages.includes(stage));
      if (idx >= 0) enter(idx);
      if (stage === "collect_done") close(cells[0], performance.now());
      const line = { t: fmtClock(performance.now() - t0), s: idx >= 0 ? idx + 1 : 0, text: message };
      lines.push(line);
      log.append(logRow(line));
      log.scrollTop = log.scrollHeight;
      // 로그가 늘어나도 패널 아래쪽이 질문 입력란에 가리지 않게 따라간다
      node.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" });
    },
    finish(total) {
      const now = performance.now();
      cells.forEach((c) => close(c, now));
      node.classList.add("finished");
      live.textContent = "완료";
      lap.textContent = typeof total === "number" ? total.toFixed(2) : secs(now - t0);
      const line = { t: fmtClock(now - t0), s: 0, text: "답변 완료" };
      lines.push(line);
    },
    stop() { clearInterval(timer); },
  };
}

// ---------- 근거 원문 패널
let drawerReturn = null;
function openDrawer(e, opener) {
  const d = $("#drawer");
  document.querySelectorAll('.cite[aria-expanded="true"]').forEach((b) => b.setAttribute("aria-expanded", "false"));
  drawerReturn = opener || null;
  if (opener) opener.setAttribute("aria-expanded", "true");
  $("#drawer-tyre").dataset.src = e.source_type;
  $("#drawer-n").textContent = `근거 ${e.n}`;
  $("#drawer-title").textContent = e.title;
  $("#drawer-src").textContent = e.source_label + (e.location && !/^https?:/.test(e.location) ? `, ${e.location}` : "");
  $("#drawer-text").textContent = e.text;
  const link = $("#drawer-link");
  link.hidden = !/^https?:/.test(e.url);
  if (!link.hidden) link.href = e.url;
  const wasHidden = d.hidden;
  d.hidden = false;
  d.scrollTop = 0;
  if (wasHidden && !reduceMotion()) {
    const narrow = matchMedia("(max-width: 900px)").matches;
    d.animate([{ transform: narrow ? "translateY(24px)" : "translateX(24px)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 220, easing: "cubic-bezier(.2,.7,.2,1)" });
  }
  $("#drawer-title").focus({ preventScroll: true });
}
function closeDrawer(restoreFocus = true) {
  const d = $("#drawer");
  if (d.hidden) return;
  d.hidden = true;
  document.querySelectorAll('.cite[aria-expanded="true"]').forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (restoreFocus && drawerReturn && document.contains(drawerReturn)) drawerReturn.focus();
  drawerReturn = null;
}

// ---------- 첫 진입: 레이스 스타트 연출
// 세션에서 처음 열면 화면 전체에 스타트 갠트리가 나오고, 다섯 칸이 차례로 켜진 뒤
// 실제 출발처럼 잠깐(무작위 0.2~0.7초) 멈췄다가 함께 꺼지면 서비스 이름이 나온다.
// 다시 열면 상단의 작은 라이트만 짧게 깜빡이고, 움직임 줄이기 설정이면 둘 다 생략한다.
function startIntro() {
  // 캡처용: ?intro=hold 는 다섯 칸이 모두 켜진 채 멈추고, ?intro=title 은 꺼진 뒤 서비스 이름이 나온 채 멈춘다.
  // 멈춘 화면은 화면을 누르거나 Enter·Esc·Space를 누르면 넘어간다 (캡처 단축키의 Shift 등에는 반응하지 않음).
  // ?intro=replay 는 이미 본 세션에서도 한 번 더 재생한다.
  const mode = new URLSearchParams(location.search).get("intro") || "";
  const hold = mode === "hold" || mode === "title";
  let seen = false;
  try { seen = sessionStorage.getItem("f1-race-notes-intro") === "1"; sessionStorage.setItem("f1-race-notes-intro", "1"); } catch { /* 무시 */ }
  const intro = $("#intro");
  if (!mode && (reduceMotion() || seen)) {
    document.documentElement.classList.remove("intro-first");
    intro.hidden = true;
    if (seen && !reduceMotion()) flickHeaderLights();
    return;
  }
  const pods = [...intro.querySelectorAll(".pod")];
  const timers = [];
  let ended = false;
  intro.hidden = false;
  if (hold) intro.querySelector(".intro-skip").hidden = true;
  const lift = () => {
    intro.classList.add("lift");
    setTimeout(() => { intro.hidden = true; document.documentElement.classList.remove("intro-first"); revealBrand(); }, 380);
  };
  const lightsOut = () => {
    pods.forEach((p) => p.classList.remove("on"));
    intro.classList.add("lights-out");
  };
  const finish = (skipped) => {
    if (ended) return;
    ended = true;
    timers.forEach(clearTimeout);
    lightsOut();
    if (mode === "title" && !skipped) return;  // 서비스 이름이 나온 채 멈춤
    setTimeout(lift, skipped ? 120 : 520);
  };
  const advance = () => {
    if (mode === "title" && ended) lift();
    else finish(true);
  };
  pods.forEach((pod, i) => timers.push(setTimeout(() => pod.classList.add("on"), 260 + i * 330)));
  if (mode !== "hold") timers.push(setTimeout(() => finish(false), 260 + 4 * 330 + 420 + 200 + Math.random() * 500));
  const onClick = () => advance();
  intro.addEventListener("click", onClick);
  const onKey = (e) => {
    if (hold && !["Enter", "Escape", " "].includes(e.key)) return;
    advance();
  };
  addEventListener("keydown", onKey);
  // 연출이 끝나면 듣기를 멈춘다
  const stop = setInterval(() => { if (intro.hidden) { removeEventListener("keydown", onKey); intro.removeEventListener("click", onClick); clearInterval(stop); } }, 300);
}
function revealBrand() {
  const title = $("#service-name");
  title.animate([{ clipPath: "inset(0 100% 0 0)", transform: "translateX(-8px)" }, { clipPath: "inset(0 0 0 0)", transform: "none" }], { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" });
  flickHeaderLights(true);
}
function flickHeaderLights(quick = false) {
  const lights = [...document.querySelectorAll("#lights span")];
  const step = quick ? 60 : 110;
  lights.forEach((l, i) => setTimeout(() => l.classList.add("on"), i * step));
  setTimeout(() => lights.forEach((l) => l.classList.remove("on")), 5 * step + 260);
}

// ---------- 조건 기반 탐색 (F2011)
async function submitExplore(ev) {
  ev.preventDefault();
  const condition = $("#explore-input").value.trim();
  if (!condition) return;
  const box = $("#explore-results");
  box.replaceChildren(el("p", { class: "muted", text: "후보 경기를 찾고 조건에 맞는지 확인하는 중…" }));
  try {
    const res = await fetch("/api/explore", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ condition }),
    });
    const data = await res.json();
    if (!res.ok) { box.replaceChildren(el("p", { class: "muted", text: data.message || "찾지 못했습니다." })); return; }
    if (!data.matches.length) {
      // 최종전·개막전 조건이면 확인한 경기의 사실을 함께 보여 줘 0건인 이유를 직접 확인할 수 있게 한다
      const checked = (data.required || []).map((r) => el("li", { text: `${r.label}: 예선 1위 ${r.pole || "-"}, 1번 그리드 ${r.grid_one || "-"}, 우승 ${r.winner || "-"}` }));
      box.replaceChildren(
        el("p", { class: "muted", text: `후보 ${data.candidates}건 중 조건에 맞는 경기를 찾지 못했습니다. 조건을 바꿔 보세요.` }),
        ...(checked.length ? [el("p", { class: "muted", text: "확인한 경기:" }), el("ul", { class: "muted checked-list" }, checked)] : []),
      );
      return;
    }
    box.replaceChildren(
      el("p", { class: "muted", text: `후보 ${data.candidates}건 중 ${data.matches.length}건이 조건에 맞습니다.` }),
      ...data.matches.map((m) => el("button", { type: "button", class: "explore-card", onclick: () => selectRace(m.item_id) }, [
        el("strong", { text: m.label }),
        el("span", { text: m.reason }),
      ])),
    );
  } catch {
    box.replaceChildren(el("p", { class: "muted", text: "요청을 보내지 못했습니다. 서버가 켜져 있는지 확인하세요." }));
  }
}

// ---------- 시작
loadHistories();
startIntro();
$("#drawer-close").addEventListener("click", () => closeDrawer());
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#race-filter").addEventListener("input", (e) => { state.filter = e.target.value; renderRaceList(); });
$("#ask-form").addEventListener("submit", submitQuestion);
$("#question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ask-form").requestSubmit(); } });
$("#explore-form").addEventListener("submit", submitExplore);
loadStatus().then(loadRaces).catch(() => showBanner("서버에 연결하지 못했습니다. uvicorn 서버가 켜져 있는지 확인하세요.", "error"));
