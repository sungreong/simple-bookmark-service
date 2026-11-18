# Simple Bookmark Service

여러 사용자의 북마크를 SQLite에 저장하는 작은 Python 실습 프로젝트입니다. 이 저장소는 코딩 에이전트와 개발 자동화 하네스를 시험하기 위해 만들었습니다. **의도적으로 버그가 포함되어 있으며, 첫 상태에서 테스트 한 개가 실패합니다.** 운영 서비스용 코드가 아닙니다.

## 준비 사항

- Python 3.9 이상
- Python 표준 라이브러리에 포함된 SQLite와 unittest
- 외부 패키지, API 키, DB 서버 없음

## 실행 및 테스트

```bash
git clone https://github.com/sungreong/simple-bookmark-service.git
cd simple-bookmark-service
python -m unittest discover -s tests -v
```

Windows에서 `python` 명령이 없으면 `py -m unittest discover -s tests -v`를 사용할 수 있습니다. 테스트는 임시 SQLite DB를 사용하고 종료 후 삭제합니다.

처음 상태에서는 사용자별 저장과 조회를 확인하는 테스트는 통과하고, 중복 저장을 막는 테스트 **한 개가 실패**해야 합니다. 상세 요구사항과 에이전트 전달 예시는 [테스트 가이드](TESTING.md)를 확인하세요.

### Docker Compose 개발 환경

Docker와 Docker Compose가 설치되어 있으면 다음 명령으로 같은 단위 테스트를 컨테이너에서 실행할 수 있습니다.

```bash
docker compose -f compose.dev.yaml run --build --rm test
```

`test` 서비스는 Python 3.12의 `slim` 이미지를 사용하고 현재 저장소를 `/workspace`에 마운트합니다. 테스트는 임시 SQLite DB를 사용하며 포트나 외부 DB가 필요하지 않습니다. 첫 실행에는 이미지 다운로드가 필요합니다. 별도 pip 패키지는 설치하지 않습니다. `requirements-dev.txt`는 현재 필요한 외부 개발 패키지가 없다는 것을 기록합니다.

## 구성

- `bookmark_service.py`: SQLite 기반 북마크 추가와 사용자별 조회
- `tests/test_bookmark_service.py`: 정상 동작과 알려진 버그의 회귀 테스트
- `TESTING.md`: 테스트 시나리오와 완료 조건
- `AGENTS.md`: 코딩 에이전트가 저장소를 조사하고 단위 테스트를 실행할 때 따를 지침
- `Dockerfile.dev`, `compose.dev.yaml`: 컨테이너 기반 개발·테스트 환경
- `requirements-dev.txt`: 외부 개발 패키지 목록

## 프로젝트 API

`BookmarkService`는 SQLite 연결을 받아 테이블을 만들고 `add_bookmark(user_id, url)`, `list_bookmarks(user_id)`를 제공합니다. 같은 북마크를 다시 저장할 때는 기존 데이터의 ID를 반환하고 행을 추가하지 않는 것이 목표입니다. 현재 구현에는 이 목표를 만족하지 않는 결함이 있습니다.

```python
import sqlite3
from bookmark_service import BookmarkService

with sqlite3.connect(":memory:") as connection:
    service = BookmarkService(connection)
    service.add_bookmark("alice", "https://example.com/article")
    print(service.list_bookmarks("alice"))
```

## 주의

이 프로젝트는 인증·접근 제어, URL 검증, 운영 DB 마이그레이션을 구현하지 않았습니다. 테스트에는 가상의 사용자와 주소만 사용하세요.
