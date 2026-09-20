/* Firebase 설정과 Firestore 경로.
 *
 * 여기 값은 전부 공개되어도 되는 것들이다 (pfkfks의 다른 페이지에도 같은
 * 값이 박혀 있다). 실제 보안 경계는 apiKey가 아니라 **Firestore 규칙**이다
 * — pfkfks-main/firestore.rules 의 `match /fm24/{doc=**}` 가 관리자 UID만
 * 읽고 쓰게 막는다. 규칙을 고치지 않으면 키가 알려져도 데이터는 안전하다.
 */

export const FIREBASE_CONFIG = {
  apiKey: 'AIzaSyAx10cs0puHJtRKQFICiaASA7OYhiHgldA',
  authDomain: 'pfkfks.firebaseapp.com',
  projectId: 'pfkfks',
  storageBucket: 'pfkfks.firebasestorage.app',
  messagingSenderId: '696195798658',
  appId: '1:696195798658:web:5f4d72974835c53a60c69b',
};

/** 이 UID만 /fm24 를 쓸 수 있다. firestore.rules 의 isAdmin() 과 맞춰야 한다. */
export const ALLOWED_UIDS = new Set([
  'xrUHsuQ8l2WJtB6gt0Ynp0U5VkJ3',
]);

export const SDK = 'https://www.gstatic.com/firebasejs/10.12.5';

/* Firestore 경로 —
 *   fm24/overview                  전체 요약 1건
 *   fm24/index/snapshots/{날짜}     그 시점 스쿼드 전체
 *   fm24/index/players/{선수id}     선수별 상세 이력
 *
 * 호스팅 페이지는 조인을 못 하므로, 화면이 쓸 모양 그대로 비정규화해 둔다. */
export const PATHS = {
  root: 'fm24',
  overview: ['fm24', 'overview'],
  snapshots: ['fm24', 'index', 'snapshots'],
  players: ['fm24', 'index', 'players'],
};
