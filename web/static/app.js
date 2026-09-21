/* FM24 Tracker — 화면.
 *
 * 같은 코드가 두 곳에서 돈다 (datasource.js 참고):
 *   내 PC     http://localhost:8765   — 불러오기·수정·동기화 가능
 *   pfkfks.org/fm24                   — Firestore 읽기 전용
 *
 * 프레임워크를 쓰지 않는다. 화면이 두 개(목록/상세)뿐이고 데이터도 수십 건
 * 규모라, 상태를 하나 두고 통째로 다시 그리는 편이 단순하고 빠르다.
 *
 * 핵심 화면은 "내가 데려온 어린 선수가 성장하고 있고, 주전에 가까워지고
 * 있는가" 하나에 맞춰져 있다. 다만 처음 들어왔을 때는 선수단 전체가
 * 보이는 편이 낫다고 판단해, 기본 필터는 출신·나이 모두 전체다.
 *
 * 상태 필터만 예외다. 임대 나간 선수와 떠난 선수는 지금 내가 쓸 수 있는
 * 자원이 아니므로 기본에서는 감춘다.
 */

import { DS, MODE, signIn, signOut, syncToCloud, watchAuth } from './datasource.js';

// ── 상태 ───────────────────────────────────────────────
const state = {
  overview: null,
  date: null,
  players: [],
  recommendation: null,
  formation: null,
  filters: { origin: '', age: '', status: 'squad', group: '', role: '', search: '' },
  sort: { key: 'starter_gap', asc: false },
  selectedId: null,
  pending: null,   // 불러오기 미리보기 결과
};

const $ = (id) => document.getElementById(id);

// ── 표시 헬퍼 ──────────────────────────────────────────
const dash = '<span class="dash">–</span>';

function num(value, digits = 2) {
  if (value === null || value === undefined) return dash;
  return Number(value).toFixed(digits);
}

/** +3 / -1 형태로, 부호에 따라 색을 입힌다. */
function signed(value, digits = 0) {
  if (value === null || value === undefined) return dash;
  const n = Number(value);
  if (n === 0) return '<span class="dash">0</span>';
  const cls = n > 0 ? 'pos' : 'neg';
  return `<span class="${cls}">${n > 0 ? '+' : ''}${n.toFixed(digits)}</span>`;
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function roleTag(role) {
  if (!role) return dash;
  const labels = {
    starter: '주전', rotation: '로테이션', development: '육성', fringe: '잉여',
  };
  return `<span class="tag ${role}">${labels[role] || role}</span>`;
}

/** 억 단위 한국어 금액. */
function money(won) {
  if (!won) return dash;
  const eok = won / 1e8;
  if (eok >= 10000) return `${(eok / 10000).toFixed(2)}조`;
  return `${Math.round(eok).toLocaleString()}억`;
}

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
}

// ── 초기 로딩 ──────────────────────────────────────────
async function init() {
  bindEvents();

  if (MODE === 'local') {
    // 내 PC: 로그인 없이 바로 쓴다. Firebase는 동기화할 때만 불러온다.
    showApp();
    $('btn-load').hidden = false;
    $('btn-sync').hidden = false;
    await refreshAll();
    return;
  }

  // pfkfks.org: 관리자만 들어올 수 있다.
  $('auth-gate').hidden = false;
  $('cloud-banner').hidden = false;
  try {
    await watchAuth(async ({ user, allowed }) => {
      if (allowed) {
        $('auth-gate').hidden = true;
        $('btn-signout').hidden = false;
        showApp();
        await refreshAll();
      } else {
        $('auth-gate').hidden = false;
        $('topbar').hidden = true;
        $('main').hidden = true;
        $('btn-signin').hidden = false;
        $('gate-message').textContent = user
          ? '이 계정에는 접근 권한이 없습니다.'
          : '관리자 계정으로 로그인하세요.';
      }
    });
  } catch (err) {
    $('gate-message').textContent = `로그인을 시작할 수 없습니다: ${err.message}`;
  }
}

function showApp() {
  $('topbar').hidden = false;
  $('main').hidden = false;
}

async function refreshAll() {
  await refreshOverview();
  await refreshSquad();
}

async function refreshOverview() {
  state.overview = await DS.overview();
  const o = state.overview;

  if (MODE === 'cloud' && o.synced_at) {
    $('synced-at').textContent = `마지막 동기화: ${o.synced_at.slice(0, 16).replace('T', ' ')}`;
  }

  $('db-summary').textContent = o.players
    ? `선수 ${o.players}명 · 스냅샷 ${o.snapshots}건 · 영입 ${o.signed} / 유스 ${o.youth}`
    : '아직 데이터가 없습니다. “데이터 불러오기”로 시작하세요.';

  // 시점 선택
  const select = $('date-select');
  select.innerHTML = o.dates.map((d) => `<option value="${d}">${d}</option>`).join('');
  if (!state.date || !o.dates.includes(state.date)) state.date = o.latest_date;
  if (state.date) select.value = state.date;
  select.disabled = o.dates.length === 0;

  // 자리 / 역할 필터 채우기
  const groupSelect = $('group-select');
  groupSelect.innerHTML = '<option value="">전체</option>'
    + Object.entries(o.position_groups)
      .map(([key, label]) => `<option value="${key}">${escapeHtml(label)}</option>`).join('');

  const roleLabels = { starter: '주전', rotation: '로테이션', development: '육성', fringe: '잉여' };
  $('role-select').innerHTML = '<option value="">전체</option>'
    + o.roles.map((r) => `<option value="${r}">${roleLabels[r] || r}</option>`).join('');

  $('growth-banner').hidden = o.can_compare_growth || o.snapshots === 0;
}

async function refreshSquad() {
  if (!state.date) { state.players = []; state.recommendation = null; render(); renderFormation(); return; }
  const data = await DS.squad(state.date);
  state.players = data.players || [];
  state.recommendation = data.recommendation || null;
  const formations = state.recommendation?.formations || [];
  if (!formations.some((item) => item.id === state.formation)) {
    state.formation = state.recommendation?.formation || formations[0]?.id || null;
  }
  render();
  renderFormation();
}

function renderFormation() {
  const recommendation = state.recommendation;
  const formations = recommendation?.formations?.length
    ? recommendation.formations
    : (recommendation?.squads ? [{
      id: recommendation.formation || '4-2-3-1',
      label: recommendation.formation || '4-2-3-1',
      squads: recommendation.squads,
    }] : []);
  if (!formations.length) {
    state.formation = null;
    $('formation-name').textContent = '–';
    $('formation-select').innerHTML = '';
    $('formation-squads').innerHTML = '<p class="muted">추천할 선수 데이터가 없습니다.</p>';
    return;
  }
  const selected = formations.find((item) => item.id === state.formation) || formations[0];
  state.formation = selected.id;
  $('formation-name').textContent = selected.label;
  $('formation-select').innerHTML = formations.map((item) => (
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`
  )).join('');
  $('formation-select').value = selected.id;
  const squads = selected.squads;
  const labels = { starter: '주전', rotation: '로테이션', development: '육성' };
  $('formation-squads').innerHTML = Object.entries(labels).map(([kind, label]) => {
    const slots = squads[kind] || [];
    const missing = slots.filter((item) => !item.player).length;
    return `<section class="formation-card"><h3>${label} 스쿼드</h3>
      ${missing ? `<p class="muted">배치 가능한 선수가 부족해 ${missing}자리가 비어 있습니다.</p>` : ''}
      <div class="formation-pitch" aria-label="${escapeHtml(selected.label)} ${label} 배치">${slots.map((item) => {
        const p = item.player;
        const x = Math.max(0, Math.min(100, Number(item.x) || 50));
        const y = Math.max(0, Math.min(100, Number(item.y) || 50));
        return `<div class="formation-slot ${p ? '' : 'vacant'}" style="--slot-x:${x}%;--slot-y:${y}%">
          <span class="slot-name">${item.slot}</span>
          ${p ? `<button type="button" data-player-id="${escapeHtml(p.player_id)}" title="${escapeHtml(`${p.name} · ${p.age ?? '–'}세 · ${p.position}${p.primary ? '' : ' · 가능 포지션'}`)}">
            <span class="player-name">${escapeHtml(p.name)}</span><small>${p.age ?? '–'}세 · ${escapeHtml(p.position)}${p.primary ? '' : ' · 가능 포지션'}</small>
          </button>` : '<span>선수 없음</span>'}</div>`;
      }).join('')}</div></section>`;
  }).join('');
}

/** 상태 꼬리표. 정상(active)이면 아무것도 붙이지 않는다 — 대부분이 정상이라 소음이 된다. */
function statusTag(player) {
  if (player.status === 'loaned_out') {
    const where = player.club ? `${player.club}(으)로 ` : '';
    return `<span class="tag loan" title="${escapeHtml(where)}임대 나가 있습니다.">임대</span>`;
  }
  if (player.status === 'released') {
    return `<span class="tag released" title="${escapeHtml(player.last_seen_date || '')} 명단을 마지막으로 사라졌습니다. 방출·이적·계약만료를 FM 데이터로는 구분할 수 없고, 아래 숫자는 그때 기준입니다.">방출</span>`;
  }
  return '';
}

// ── 필터 + 정렬 ────────────────────────────────────────

/**
 * 상태 필터를 통과하는가. 'squad'=우리 팀만, 'loan'=임대까지, ''=전부.
 *
 * 상태를 모르면 정상으로 본다. 클라우드에는 이 기능 이전에 동기화한 데이터가
 * 남아 있어 `status` 가 없는데, 그걸 감추면 목록이 통째로 비어 보인다.
 */
function statusAllows(player) {
  const mode = state.filters.status;
  const status = player.status || 'active';
  if (mode === 'squad') return status === 'active';
  if (mode === 'loan') return status !== 'released';
  return true;
}

/**
 * 조건에 맞는 선수들.
 *
 * 상태 필터를 마지막에 적용하고 걸러진 선수를 따로 돌려준다. 몇 명이
 * 감춰졌는지 화면에 적어주기 위해서다 — 조용히 빠지면 "왜 안 보이지" 가 된다.
 *
 * @returns {{rows: object[], hidden: object[]}}
 */
function visiblePlayers() {
  const f = state.filters;
  const needle = f.search.trim().toLowerCase();

  const matched = state.players.filter((p) => {
    if (f.origin && p.origin !== f.origin) return false;
    if (f.age && (p.age === null || p.age > Number(f.age))) return false;
    if (f.group && p.group !== f.group) return false;
    if (f.role && p.role !== f.role) return false;
    if (needle && !(p.name || '').toLowerCase().includes(needle)) return false;
    return true;
  });
  const rows = matched.filter(statusAllows);

  const { key, asc } = state.sort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    // 값이 없는 행은 방향과 무관하게 항상 뒤로 보낸다.
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return asc ? x.localeCompare(y) : y.localeCompare(x);
    return asc ? x - y : y - x;
  });
  return { rows, hidden: matched.filter((p) => !statusAllows(p)) };
}

/** "3명 · 임대 2 숨김" 처럼, 상태 때문에 빠진 인원을 알린다. */
function hiddenNote(hidden) {
  if (!hidden.length) return '';
  const labels = { loaned_out: '임대', released: '방출' };
  const counts = new Map();
  hidden.forEach((p) => counts.set(p.status, (counts.get(p.status) || 0) + 1));
  const parts = [...counts].map(([status, n]) => `${labels[status] || status} ${n}`);
  return ` · ${parts.join(' · ')} 숨김`;
}

// ── 목록 렌더 ──────────────────────────────────────────
function render() {
  const { rows, hidden } = visiblePlayers();
  const body = $('squad-body');

  body.innerHTML = rows.map((p) => {
    const rank = (p.rank && p.depth) ? `${p.rank}<span class="muted">/${p.depth}</span>` : dash;
    const origin = p.origin === 'signed'
      ? `<span class="tag signed">영입</span>` : `<span class="tag youth">유스</span>`;

    return `<tr data-id="${escapeHtml(p.player_id)}" ${p.player_id === state.selectedId ? 'class="selected"' : ''}>
      <td>
        <div class="player-name">${escapeHtml(p.name)}</div>
        <div class="player-sub">${origin} ${statusTag(p)} ${escapeHtml([p.primary_position, ...(p.other_positions || [])].filter(Boolean).join(', ') || p.position || '')}</div>
      </td>
      <td class="num">${p.age ?? dash}</td>
      <td>${escapeHtml(p.group_label || '')}</td>
      <td class="num">${rank}</td>
      <td class="num">${num(p.quality)}</td>
      <td class="num">${gapBar(p.starter_gap)}</td>
      <td class="num">${signed(p.growth_total, 0)}</td>
      <td class="num">${p.usage === null ? dash : Math.round(p.usage * 100) + '%'}</td>
      <td>${roleTag(p.role)}</td>
    </tr>`;
  }).join('');

  $('squad-empty').hidden = rows.length > 0;
  $('result-count').textContent = `${rows.length}명${hiddenNote(hidden)}`;

  document.querySelectorAll('.squad th.sortable').forEach((th) => {
    const active = th.dataset.sort === state.sort.key;
    th.classList.toggle('sorted', active);
    th.classList.toggle('asc', active && state.sort.asc);
  });
}

/** 주전과의 격차를 0 기준 좌우 막대로 보여준다. */
function gapBar(gap) {
  if (gap === null || gap === undefined) return dash;
  const scale = 5;  // ±5 포인트를 막대 폭의 끝으로 본다
  const width = Math.min(Math.abs(gap) / scale, 1) * 50;
  const fill = gap >= 0
    ? `<span class="fill pos" style="width:${width}%"></span>`
    : `<span class="fill neg" style="width:${width}%"></span>`;
  const cls = gap >= 0 ? 'pos' : 'neg';
  return `<span class="gapbar">
    <span class="track">${fill}</span>
    <span class="val ${cls}">${gap > 0 ? '+' : ''}${gap.toFixed(1)}</span>
  </span>`;
}

// ── 상세 패널 ──────────────────────────────────────────
async function openPlayer(playerId) {
  state.selectedId = playerId;
  render();

  const data = await DS.player(playerId);
  const { player, origin, timeline, overall_growth: overall, attributes } = data;
  const latest = timeline[timeline.length - 1];

  $('d-name').textContent = player.name;
  $('d-sub').innerHTML = [
    origin.origin === 'signed'
      ? `<span class="tag signed">영입</span> ${escapeHtml(origin.signed_from || '')} ${origin.signed_fee ? money(origin.signed_fee) : '자유이적'}`
      : `<span class="tag youth">유스</span>`,
    statusTag(player),
    escapeHtml(player.nationality || ''),
    escapeHtml(player.primary_position || latest?.position || player.position || ''),
  ].filter(Boolean).join(' · ');

  $('drawer-body').innerHTML = [
    renderHeadline(latest),
    renderVerdict(timeline, overall),
    renderQualityChart(timeline),
    renderTimeline(timeline),
    renderChanges(overall, timeline),
    renderAttributes(attributes),
    renderPositionPicker(player),
    // 수정은 진실이 있는 로컬에서만. 클라우드는 읽기 전용이다.
    DS.editable ? renderRolePicker(player.player_id, latest) : '',
    DS.editable ? renderOriginPicker(player.player_id, origin) : '',
  ].join('');

  $('drawer').hidden = false;
  $('scrim').hidden = false;
  $('drawer-body').scrollTop = 0;
}

function renderPositionPicker(player) {
  const options = state.overview?.position_options || [];
  const primary = player.primary_position || '';
  const others = player.other_positions || [];
  if (!options.length) return '';
  const label = (token) => token.replaceAll('(', ' (');
  return `<h3>포지션 설정</h3>
    <p class="muted">FM 포지션: ${escapeHtml(player.position || '–')} · ${player.manual_positions ? '직접 지정한 포지션' : 'FM 데이터에서 가져온 기본값'}</p>
    ${DS.editable ? `<form id="position-form" data-player="${escapeHtml(player.player_id)}">
      <label class="field"><span>주 포지션</span><select name="primary" required>
        ${options.map((token) => `<option value="${escapeHtml(token)}" ${token === primary ? 'selected' : ''}>${escapeHtml(label(token))}</option>`).join('')}
      </select></label>
      <span class="field-label">다른 가능 포지션</span>
      <div class="position-options">${options.map((token) => `<label><input type="checkbox" name="other" value="${escapeHtml(token)}" ${others.includes(token) && token !== primary ? 'checked' : ''} ${token === primary ? 'disabled' : ''}>${escapeHtml(label(token))}</label>`).join('')}</div>
      <button type="submit" class="btn primary">포지션 저장</button>
    </form>` : `<p>주 포지션: ${escapeHtml(label(primary) || '–')} · 가능 포지션: ${escapeHtml(others.map(label).join(', ') || '없음')}</p>`}`;
}

function renderHeadline(latest) {
  if (!latest) return '<p class="muted">스냅샷이 없습니다.</p>';
  const rank = (latest.rank && latest.depth) ? `${latest.rank}위 / ${latest.depth}명` : '–';
  return `<div class="stat-row">
    <div class="stat"><div class="k">나이</div><div class="v">${latest.age ?? '–'}</div></div>
    <div class="stat"><div class="k">실력</div><div class="v">${num(latest.quality)}</div>
      <div class="n">${escapeHtml(latest.group || '')} 기준</div></div>
    <div class="stat"><div class="k">주전까지</div>
      <div class="v ${latest.starter_gap >= 0 ? 'pos' : 'neg'}">${latest.starter_gap === null ? '–' : (latest.starter_gap > 0 ? '+' : '') + latest.starter_gap.toFixed(1)}</div>
      <div class="n">${rank}</div></div>
    <div class="stat"><div class="k">출전</div>
      <div class="v">${latest.minutes ?? '–'}<span class="n">분</span></div>
      <div class="n">${escapeHtml(latest.appearances || '')}</div></div>
  </div>`;
}

/** 목적에 대한 한 줄 답. 데이터가 부족하면 부족하다고 말한다. */
function renderVerdict(timeline, overall) {
  if (timeline.length < 2) {
    return `<h3>판단</h3><p class="muted">스냅샷이 1개뿐이라 성장을 판단할 수 없습니다.
      게임을 진행한 뒤 다시 export해서 불러오세요.</p>`;
  }
  const first = timeline[0], last = timeline[timeline.length - 1];
  const gapDelta = (last.starter_gap ?? 0) - (first.starter_gap ?? 0);
  const grew = overall ? overall.total : 0;

  const growthText = grew > 0
    ? `능력치가 <span class="pos">+${grew}</span> 올랐습니다`
    : grew < 0 ? `능력치가 <span class="neg">${grew}</span> 내렸습니다`
    : '능력치 변화가 없습니다';
  const gapText = gapDelta > 0.05
    ? `주전과의 격차가 <span class="pos">${gapDelta.toFixed(1)}만큼 좁혀졌습니다</span>`
    : gapDelta < -0.05
      ? `주전과의 격차가 <span class="neg">${Math.abs(gapDelta).toFixed(1)}만큼 벌어졌습니다</span>`
      : '주전과의 격차는 거의 그대로입니다';

  return `<h3>판단</h3>
    <p>${first.game_date} → ${last.game_date} 사이에 ${growthText}. ${gapText}.
    역할은 ${roleTag(first.role)} → ${roleTag(last.role)} 입니다.</p>`;
}

/** 날짜 간격을 유지하며 스냅샷의 실력 점수를 직선으로 잇는다. */
function renderQualityChart(timeline) {
  const values = timeline
    .filter((point) => point.quality !== null && Number.isFinite(Number(point.quality)))
    .map((point) => ({ date: point.game_date, value: Number(point.quality) }));
  if (!values.length) return '';

  const width = 520, height = 230;
  const left = 43, right = 18, top = 24, bottom = 43;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const dates = values.map((point) => Date.parse(`${point.date}T00:00:00Z`));
  const validDates = dates.every(Number.isFinite);
  const start = validDates ? Math.min(...dates) : 0;
  const end = validDates ? Math.max(...dates) : values.length - 1;
  const low = Math.min(...values.map((point) => point.value));
  const high = Math.max(...values.map((point) => point.value));
  const padding = Math.max(0.5, (high - low) * 0.2);
  const yMin = Math.max(0, Math.floor(low - padding));
  const yMax = Math.min(20, Math.max(yMin + 1, Math.ceil(high + padding)));
  const xAt = (index) => left + (end === start ? plotWidth / 2 :
    ((validDates ? dates[index] : index) - start) / (end - start) * plotWidth);
  const yAt = (value) => top + (yMax - value) / (yMax - yMin) * plotHeight;
  const coordinates = values.map((point, index) => ({
    ...point, x: xAt(index), y: yAt(point.value),
  }));
  const ticks = [yMin, (yMin + yMax) / 2, yMax];
  const grid = ticks.map((value) => `<g>
    <line class="quality-grid" x1="${left}" y1="${yAt(value)}" x2="${width - right}" y2="${yAt(value)}" />
    <text class="quality-axis" x="${left - 8}" y="${yAt(value) + 4}" text-anchor="end">${value.toFixed(1)}</text>
  </g>`).join('');

  const labelIndices = values.length <= 4
    ? values.map((_, index) => index)
    : [0, Math.floor((values.length - 1) / 2), values.length - 1];
  const dateLabels = labelIndices.map((index) => `<text class="quality-axis"
    x="${coordinates[index].x}" y="${height - 12}"
    text-anchor="${index === 0 && values.length > 1 ? 'start' : index === values.length - 1 ? 'end' : 'middle'}">${escapeHtml(values[index].date.slice(2))}</text>`).join('');
  const path = coordinates.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
  const points = coordinates.map((point, index) => {
    const previous = coordinates[index - 1];
    const delta = previous ? point.value - previous.value : null;
    const detail = delta === null ? '' : ` · 이전 대비 ${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`;
    return `<circle class="quality-point" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="5">
      <title>${escapeHtml(`${point.date} · 실력 ${point.value.toFixed(2)}${detail}`)}</title>
    </circle>`;
  }).join('');
  const change = values.length > 1 ? values.at(-1).value - values[0].value : null;
  const summary = change === null ? '첫 기록입니다.' :
    `${values[0].value.toFixed(2)} → ${values.at(-1).value.toFixed(2)} (${change >= 0 ? '+' : ''}${change.toFixed(2)})`;

  return `<section class="quality-chart-section"><h3>실력 그래프</h3>
    <p class="quality-chart-summary">${summary}</p>
    <svg class="quality-chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="날짜별 실력 그래프: ${escapeHtml(values.map((point) => `${point.date} ${point.value.toFixed(2)}`).join(', '))}">
      ${grid}
      <path class="quality-line" d="${path}" />
      ${points}
      ${dateLabels}
    </svg>
    <p class="muted quality-chart-note">점에 마우스를 올리면 날짜와 실력 점수를 볼 수 있습니다. 아래 표에서도 정확한 값을 확인할 수 있습니다.</p>
  </section>`;
}

function renderTimeline(timeline) {
  if (!timeline.length) return '';
  const rows = timeline.map((t) => `<tr>
    <td>${t.game_date}</td>
    <td class="num">${t.age ?? '–'}</td>
    <td class="num">${num(t.quality)}</td>
    <td class="num">${t.starter_gap === null ? dash : (t.starter_gap > 0 ? '+' : '') + t.starter_gap.toFixed(1)}</td>
    <td class="num">${t.minutes ?? dash}</td>
    <td class="num">${signed(t.growth_total, 0)}</td>
    <td>${roleTag(t.role)}</td>
  </tr>`).join('');

  return `<h3>시점별 추이</h3><div class="scroll-x"><table class="mini">
    <thead><tr><th>시점</th><th class="num">나이</th><th class="num">실력</th>
      <th class="num">주전까지</th><th class="num">출장</th><th class="num">성장</th><th>역할</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function renderChanges(overall, timeline) {
  const source = overall
    || (timeline.length ? { changes: timeline[timeline.length - 1].changes, from: timeline[timeline.length - 1].prev_game_date, to: timeline[timeline.length - 1].game_date } : null);
  if (!source || !source.changes || !source.changes.length) return '';

  const rows = source.changes.map((c) => `<tr>
    <td>${escapeHtml(c.label)}</td>
    <td class="num">${c.previous} → ${c.current}</td>
    <td class="num">${signed(c.delta, 0)}</td>
  </tr>`).join('');

  return `<h3>능력치 변화 <span class="muted">(${source.from} → ${source.to})</span></h3>
    <table class="mini"><tbody>${rows}</tbody></table>`;
}

function renderAttributes(groups) {
  const titles = {
    technical: '기술', mental: '정신', physical: '신체', goalkeeping: '골키퍼',
  };
  const blocks = Object.entries(groups).map(([group, entries]) => `
    <h3>${titles[group] || group} <span class="muted">· 강조 = 이 포지션의 핵심</span></h3>
    <div class="attr-grid">${entries.map((a) => `
      <span class="attr ${a.core ? 'core' : ''}">
        <span>${escapeHtml(a.label)}</span><span class="v">${a.value}</span>
      </span>`).join('')}</div>`);
  return blocks.join('');
}

function renderRolePicker(playerId, latest) {
  if (!latest) return '';
  const roles = ['starter', 'rotation', 'development', 'fringe'];
  const buttons = roles.map((r) => `<button class="btn" data-role="${r}"
    ${r === latest.role ? 'disabled' : ''}>${escapeHtml(roleTag(r).replace(/<[^>]*>/g, ''))}</button>`).join('');
  return `<h3>역할 직접 지정 <span class="muted">(${latest.game_date})</span></h3>
    <p class="muted">FM의 "${escapeHtml(latest.fm_status || '–')}" 에서 자동으로 붙인 라벨입니다.
      직접 고르면 이후 import가 덮어쓰지 않습니다.</p>
    <div class="role-picker" data-player="${escapeHtml(playerId)}" data-date="${latest.game_date}">${buttons}</div>`;
}

/** 영입/유스 판정은 FM 데이터 추론이라 틀릴 수 있다. 직접 고칠 수단을 준다. */
function renderOriginPicker(playerId, origin) {
  const current = origin.origin || 'unknown';
  const labels = { signed: '영입', youth: '유스' };
  const buttons = ['signed', 'youth'].map((value) => `<button class="btn" data-origin="${value}"
    ${value === current ? 'disabled' : ''}>${labels[value]}</button>`).join('');

  const note = origin.manual
    ? '직접 지정한 값입니다. import가 덮어쓰지 않습니다.'
    : '이적료와 이전 구단으로 자동 판정했습니다. 임대 이력이 있으면 틀릴 수 있습니다.';

  return `<h3>출신 구분</h3>
    <p class="muted">${escapeHtml(note)}</p>
    <div class="role-picker" data-player="${escapeHtml(playerId)}">${buttons}</div>`;
}

function closeDrawer() {
  $('drawer').hidden = true;
  $('scrim').hidden = true;
  state.selectedId = null;
  render();
}

// ── 데이터 불러오기 ────────────────────────────────────
async function openLoadDialog() {
  $('preview').hidden = true;
  $('load-error').hidden = true;
  $('btn-import').disabled = true;
  state.pending = null;

  try {
    const { files } = await DS.files();
    $('file-list').innerHTML = files.length
      ? files.map((f) => `<li>
          <span>
            <strong>${escapeHtml(f.name)}</strong>
            <span class="meta">${f.size_kb}KB${f.inferred_date ? ' · ' + f.inferred_date : ''}${f.already_imported ? ' · 이미 들어감' : ''}</span>
          </span>
          <button class="btn" data-path="${escapeHtml(f.path)}">선택</button>
        </li>`).join('')
      : '<li class="meta">data/ 폴더에 HTML 파일이 없습니다.</li>';
  } catch (err) {
    $('file-list').innerHTML = `<li class="meta">${escapeHtml(err.message)}</li>`;
  }

  $('load-dialog').showModal();
}

function showPreview(result) {
  state.pending = result;

  $('preview-grid').innerHTML = `
    <dt>파일</dt><dd>${escapeHtml(result.name)}</dd>
    <dt>선수</dt><dd>${result.players}명 (${result.youngest ?? '?'}~${result.oldest ?? '?'}세)</dd>
    <dt>컬럼</dt><dd>${result.columns}개 · 중복 이름 ${result.duplicate_headers}종</dd>
    <dt>인식</dt><dd>필드 ${result.resolved_fields}/${result.total_fields} · 능력치 ${result.attributes_found}개</dd>
    ${result.fallback_ids ? `<dt>경고</dt><dd class="neg">ID 없는 선수 ${result.fallback_ids}명</dd>` : ''}
    <dt>예시</dt><dd class="muted">${escapeHtml(result.sample.slice(0, 5).join(', '))}</dd>`;

  const dateInput = $('date-input');
  dateInput.value = result.inferred_date || '';
  $('date-hint').textContent = result.inferred_date
    ? `파일명 "${result.name}" 에서 추측했습니다. 세이브 화면과 다르면 고쳐주세요.`
    : '파일명에서 날짜를 찾지 못했습니다. 세이브 화면의 날짜를 입력하세요.';

  $('preview-warnings').innerHTML = (result.warnings || [])
    .map((w) => `<li>${escapeHtml(w)}</li>`).join('');

  $('preview').hidden = false;
  $('load-error').hidden = true;
  $('btn-import').disabled = !dateInput.value;
}

async function uploadFile(file) {
  $('load-error').hidden = true;
  try {
    showPreview(await DS.upload(file));
  } catch (err) {
    showError(err);
  }
}

function showError(err) {
  const el = $('load-error');
  el.textContent = err.message;
  el.hidden = false;
  $('btn-import').disabled = true;
}

async function runImport() {
  if (!state.pending) return;
  const gameDate = $('date-input').value;
  if (!gameDate) return;

  const button = $('btn-import');
  button.disabled = true;
  button.textContent = '가져오는 중…';

  try {
    const summary = await DS.import(state.pending.path, gameDate);
    $('load-dialog').close();
    state.date = summary.game_date;
    await refreshOverview();
    $('date-select').value = state.date;
    await refreshSquad();
    toast(`${summary.imported}명 저장 · 성장 계산 ${summary.growth_calculated}명`
      + ' — Firestore에 올리려면 "동기화"를 누르세요.');
  } catch (err) {
    showError(err);
  } finally {
    button.textContent = '가져오기';
    button.disabled = false;
  }
}

// ── Firestore 동기화 (로컬에서만) ──────────────────────
async function runSync() {
  const button = $('btn-sync');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '동기화 중…';

  try {
    // 로그인부터. 팝업이 막히면 여기서 바로 실패한다.
    const auth = await new Promise((resolve) => {
      watchAuth(resolve).catch(() => resolve({ user: null, allowed: false }));
    });
    if (!auth.allowed) {
      await signIn();
    }

    toast('데이터를 준비하는 중…');
    const bundle = await DS.bundle();
    const result = await syncToCloud(bundle, (message) => { button.textContent = message; });
    toast(`Firestore 동기화 완료 — 문서 ${result.written}건`
      + (result.removed ? `, 정리 ${result.removed}건` : ''));
  } catch (err) {
    toast(`동기화 실패: ${err.message}`);
  } finally {
    button.textContent = label;
    button.disabled = false;
  }
}

// ── 이벤트 ─────────────────────────────────────────────
function bindEvents() {
  $('tab-players').addEventListener('click', () => {
    $('players-view').hidden = false;
    $('formation-view').hidden = true;
    $('tab-players').classList.add('active');
    $('tab-formation').classList.remove('active');
  });
  $('tab-formation').addEventListener('click', () => {
    $('players-view').hidden = true;
    $('formation-view').hidden = false;
    $('tab-players').classList.remove('active');
    $('tab-formation').classList.add('active');
  });
  $('formation-squads').addEventListener('click', (e) => {
    const button = e.target.closest('button[data-player-id]');
    if (button) openPlayer(button.dataset.playerId);
  });
  $('formation-select').addEventListener('change', (e) => {
    state.formation = e.target.value;
    renderFormation();
  });
  $('btn-load').addEventListener('click', openLoadDialog);
  $('btn-sync').addEventListener('click', runSync);
  $('btn-signin').addEventListener('click', () => {
    signIn().catch((err) => { $('gate-message').textContent = err.message; });
  });
  $('btn-signout').addEventListener('click', () => {
    signOut().then(() => location.reload());
  });
  $('btn-cancel').addEventListener('click', () => $('load-dialog').close());
  $('btn-import').addEventListener('click', runImport);
  $('date-input').addEventListener('input', (e) => {
    $('btn-import').disabled = !e.target.value;
  });

  $('file-input').addEventListener('change', (e) => {
    if (e.target.files[0]) uploadFile(e.target.files[0]);
  });

  // data/ 안의 파일 선택
  $('file-list').addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-path]');
    if (!button) return;
    try {
      showPreview(await postJSON('/api/inspect', { path: button.dataset.path }));
    } catch (err) { showError(err); }
  });

  // 드래그 앤 드롭
  const zone = $('dropzone');
  ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (e) => {
    e.preventDefault(); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, (e) => {
    e.preventDefault(); zone.classList.remove('over');
  }));
  zone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });

  // 시점 변경
  $('date-select').addEventListener('change', (e) => {
    state.date = e.target.value;
    closeDrawer();
    refreshSquad();
  });

  // 필터 칩
  $('filters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    chip.parentElement.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
    chip.classList.add('active');
    state.filters[chip.dataset.filter] = chip.dataset.value;
    render();
  });

  $('group-select').addEventListener('change', (e) => { state.filters.group = e.target.value; render(); });
  $('role-select').addEventListener('change', (e) => { state.filters.role = e.target.value; render(); });
  $('search').addEventListener('input', (e) => { state.filters.search = e.target.value; render(); });

  // 정렬
  document.querySelector('.squad thead').addEventListener('click', (e) => {
    const th = e.target.closest('th.sortable');
    if (!th) return;
    const key = th.dataset.sort;
    // 같은 열을 다시 누르면 방향만 뒤집는다. 이름은 오름차순이 자연스럽다.
    state.sort = (state.sort.key === key)
      ? { key, asc: !state.sort.asc }
      : { key, asc: key === 'name' };
    render();
  });

  // 선수 선택 — 이름을 입력하지 않고 목록에서 고른다
  $('squad-body').addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-id]');
    if (row) openPlayer(row.dataset.id);
  });

  $('drawer-close').addEventListener('click', closeDrawer);
  $('scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('drawer').hidden) closeDrawer();
  });

  // 역할 / 출신 직접 지정 — 자동 판정이 틀렸을 때 사람이 바로잡는다
  $('drawer-body').addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-role], button[data-origin]');
    if (!button) return;
    const picker = button.closest('.role-picker');
    const playerId = picker.dataset.player;

    try {
      if (button.dataset.role) {
        await DS.setRole(playerId, picker.dataset.date, button.dataset.role);
        toast('역할을 저장했습니다.');
      } else {
        await DS.setOrigin(playerId, button.dataset.origin);
        toast('출신 구분을 저장했습니다.');
      }
      await refreshOverview();
      await refreshSquad();
      await openPlayer(playerId);
    } catch (err) { toast(err.message); }
  });
  $('drawer-body').addEventListener('change', (e) => {
    const form = e.target.closest('#position-form');
    if (!form || e.target.name !== 'primary') return;
    form.querySelectorAll('input[name="other"]').forEach((input) => {
      if (input.value === e.target.value) input.checked = false;
      input.disabled = input.value === e.target.value;
    });
  });
  $('drawer-body').addEventListener('submit', async (e) => {
    if (e.target.id !== 'position-form') return;
    e.preventDefault();
    const form = e.target;
    const primary = form.elements.primary.value;
    const others = [...form.querySelectorAll('input[name="other"]:checked')]
      .map((input) => input.value).filter((value) => value !== primary);
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await DS.setPositions(form.dataset.player, primary, others);
      await refreshSquad();
      await openPlayer(form.dataset.player);
      toast('포지션을 저장했습니다.');
    } catch (err) {
      toast(err.message);
      button.disabled = false;
    }
  });
}

init().catch((err) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<p class="error" style="margin:20px">${escapeHtml(err.message)}</p>`);
});
