/* 데이터 출처 추상화 — 같은 화면 코드가 두 곳에서 돈다.
 *
 *   local  : 내 PC의 파이썬 서버 (http://localhost:8765)
 *            원본 SQLite가 진실. 불러오기·수정·동기화 전부 가능.
 *   cloud  : pfkfks.org/fm24
 *            Firestore를 읽기만 한다. 로컬에서 동기화한 결과를 본다.
 *
 * 왜 클라우드는 읽기 전용인가: 양쪽에서 쓰면 SQLite와 Firestore를 양방향으로
 * 맞춰야 하는데, 그건 이 도구의 목적에 비해 과한 작업이고 조용히 어긋나기
 * 쉽다. 진실은 언제나 내 PC의 SQLite 한 곳이다.
 */

import { ALLOWED_UIDS, FIREBASE_CONFIG, PATHS, SDK } from './firebase-config.js';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/** 'local' 또는 'cloud'. 주소로 판별한다. */
export const MODE = LOCAL_HOSTS.has(location.hostname) ? 'local' : 'cloud';

// ── Firebase 지연 로딩 ─────────────────────────────────────
// 로컬 모드는 인터넷 없이도 동작해야 하므로, Firebase는 실제로 필요할 때
// (동기화 버튼, 또는 클라우드 모드)만 불러온다.
let firebasePromise = null;

function loadFirebase() {
  if (!firebasePromise) {
    firebasePromise = (async () => {
      const [appMod, authMod, storeMod] = await Promise.all([
        import(`${SDK}/firebase-app.js`),
        import(`${SDK}/firebase-auth.js`),
        import(`${SDK}/firebase-firestore.js`),
      ]);
      const app = appMod.getApps().length ? appMod.getApps()[0] : appMod.initializeApp(FIREBASE_CONFIG);
      return { app, auth: authMod, store: storeMod, db: storeMod.getFirestore(app) };
    })();
  }
  return firebasePromise;
}

/** 현재 로그인 상태를 구독한다. 콜백은 {user, allowed} 를 받는다. */
export async function watchAuth(onChange) {
  const { app, auth } = await loadFirebase();
  const instance = auth.getAuth(app);
  auth.onAuthStateChanged(instance, (user) => {
    onChange({ user, allowed: Boolean(user && ALLOWED_UIDS.has(user.uid)) });
  });
  return instance;
}

export async function signIn() {
  const { app, auth } = await loadFirebase();
  const provider = new auth.GoogleAuthProvider();
  await auth.signInWithPopup(auth.getAuth(app), provider);
}

export async function signOut() {
  const { app, auth } = await loadFirebase();
  await auth.signOut(auth.getAuth(app));
}

// ── 로컬 (파이썬 서버) ─────────────────────────────────────

async function callApi(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({ error: '응답을 읽을 수 없습니다.' }));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

const postJSON = (path, body) => callApi(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const localSource = {
  mode: 'local',
  editable: true,
  overview: () => callApi('/api/overview'),
  squad: (date) => callApi(`/api/squad${date ? `?date=${encodeURIComponent(date)}` : ''}`),
  player: (id) => callApi(`/api/player?id=${encodeURIComponent(id)}`),
  files: () => callApi('/api/files'),
  inspect: (path) => postJSON('/api/inspect', { path }),
  import: (path, gameDate) => postJSON('/api/import', { path, game_date: gameDate }),
  setRole: (playerId, gameDate, role) => postJSON('/api/role', { player_id: playerId, game_date: gameDate, role }),
  setOrigin: (playerId, origin) => postJSON('/api/origin', { player_id: playerId, origin }),
  bundle: () => callApi('/api/bundle'),
  upload: async (file) => callApi(`/api/upload?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: await file.arrayBuffer(),
  }),
};

// ── 클라우드 (Firestore, 읽기 전용) ────────────────────────

function notAvailable(what) {
  return () => {
    throw new Error(`${what}는 내 PC의 로컬 앱에서만 할 수 있습니다.`);
  };
}

const cloudSource = {
  mode: 'cloud',
  editable: false,

  async overview() {
    const { db, store } = await loadFirebase();
    const snap = await store.getDoc(store.doc(db, ...PATHS.overview));
    if (!snap.exists()) {
      // 아직 한 번도 동기화하지 않은 상태. 빈 화면이 에러보다 낫다.
      return {
        dates: [], latest_date: null, players: 0, signed: 0, youth: 0, snapshots: 0,
        formula_version: '-', roles: ['starter', 'rotation', 'development', 'fringe'],
        position_groups: {}, can_compare_growth: false, synced_at: null,
      };
    }
    return snap.data();
  },

  async squad(date) {
    const { db, store } = await loadFirebase();
    let target = date;
    if (!target) {
      const overview = await cloudSource.overview();
      target = overview.latest_date;
    }
    if (!target) return { game_date: null, players: [] };
    const snap = await store.getDoc(store.doc(db, ...PATHS.snapshots, target));
    return snap.exists() ? snap.data() : { game_date: target, players: [] };
  },

  async player(id) {
    const { db, store } = await loadFirebase();
    const snap = await store.getDoc(store.doc(db, ...PATHS.players, id));
    if (!snap.exists()) throw new Error(`선수를 찾을 수 없습니다: ${id}`);
    return snap.data();
  },

  files: notAvailable('파일 목록 보기'),
  inspect: notAvailable('파일 미리보기'),
  import: notAvailable('데이터 불러오기'),
  setRole: notAvailable('역할 수정'),
  setOrigin: notAvailable('출신 수정'),
  bundle: notAvailable('내보내기'),
  upload: notAvailable('업로드'),
};

export const DS = MODE === 'local' ? localSource : cloudSource;

// ── 동기화 (로컬 → Firestore) ──────────────────────────────

/** Firestore 쓰기 한 묶음의 최대 문서 수. 실제 한도는 500이라 여유를 둔다. */
const BATCH_LIMIT = 400;

/**
 * 로컬 데이터를 Firestore에 통째로 덮어쓴다.
 *
 * 증분이 아니라 전량 덮어쓰기다. 로컬이 항상 진실이므로 그쪽에 맞추는 것이
 * 가장 단순하고, 42명 규모에서는 비용도 무시할 만하다. 다만 로컬에서 사라진
 * 선수(이적 등)는 Firestore에 남으므로, 사라진 문서는 지워 준다.
 *
 * @param {object} bundle /api/bundle 응답
 * @param {(msg: string) => void} onProgress 진행 상황 콜백
 * @returns {Promise<{written: number, removed: number}>}
 */
export async function syncToCloud(bundle, onProgress = () => {}) {
  const { db, store } = await loadFirebase();
  const { doc, setDoc, writeBatch, collection, getDocs, deleteDoc, serverTimestamp } = store;

  const syncedAt = new Date().toISOString();
  onProgress('요약 올리는 중…');
  await setDoc(doc(db, ...PATHS.overview), {
    ...bundle.overview,
    synced_at: syncedAt,
    synced_server_at: serverTimestamp(),
  });

  let written = 1;

  const pushAll = async (pathParts, entries, label) => {
    const items = Object.entries(entries);
    for (let i = 0; i < items.length; i += BATCH_LIMIT) {
      const chunk = items.slice(i, i + BATCH_LIMIT);
      const batch = writeBatch(db);
      for (const [id, value] of chunk) {
        batch.set(doc(db, ...pathParts, id), { ...value, synced_at: syncedAt });
      }
      await batch.commit();
      written += chunk.length;
      onProgress(`${label} ${Math.min(i + chunk.length, items.length)}/${items.length}`);
    }
  };

  await pushAll(PATHS.snapshots, bundle.snapshots, '스냅샷');
  await pushAll(PATHS.players, bundle.players, '선수');

  // 로컬에 없어진 문서 정리
  onProgress('정리 중…');
  let removed = 0;
  for (const [pathParts, keep] of [
    [PATHS.snapshots, new Set(Object.keys(bundle.snapshots))],
    [PATHS.players, new Set(Object.keys(bundle.players))],
  ]) {
    const existing = await getDocs(collection(db, ...pathParts));
    for (const found of existing.docs) {
      if (!keep.has(found.id)) {
        await deleteDoc(found.ref);
        removed += 1;
      }
    }
  }

  return { written, removed };
}
