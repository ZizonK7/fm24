"""로컬 웹 UI.

``python scripts/serve.py`` 로 띄운다. 표준 라이브러리만 쓰고 외부 CDN도
쓰지 않으므로, 설치나 인터넷 연결 없이 동작한다.

    api.py       DB 조회/가공 (HTTP와 분리 → 테스트 가능)
    server.py    http.server 기반 JSON API + 정적 파일
    static/      화면 (프레임워크 없음)
"""
