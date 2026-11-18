# Simple Bookmark Service

여러 사용자의 북마크를 SQLite에 저장하는 작은 Python 실습 프로젝트입니다. 이 저장소는 코딩 에이전트와 개발 자동화 하네스를 시험하기 위해 만들었습니다. 처음에는 같은 북마크가 중복 저장되는 결함을 의도적으로 포함했으며(이슈 #1), 현재는 그 수정과 빈 URL 검증(이슈 #3)이 회귀 테스트와 함께 들어 있습니다. 운영 서비스용 코드가 아닙니다.

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

모든 테스트(현재 12개)가 통과해야 합니다. 테스트는 사용자별 저장·조회, 순차 반복 저장, 빈/공백 URL 거부, 기존 중복 데이터, 동시 저장과 잠금·재시도 정책을 다룹니다. 상세 시나리오와 에이전트 전달 예시는 [테스트 가이드](TESTING.md)를 확인하세요.

### Docker Compose 개발 환경

Docker와 Docker Compose가 설치되어 있으면 다음 명령으로 같은 단위 테스트를 컨테이너에서 실행할 수 있습니다.

```bash
docker compose -f compose.dev.yaml run --build --rm test
```

`test` 서비스는 Python 3.12의 `slim` 이미지를 사용하고 현재 저장소를 `/workspace`에 마운트합니다. 테스트는 임시 SQLite DB를 사용하며 포트나 외부 DB가 필요하지 않습니다. 첫 실행에는 이미지 다운로드가 필요합니다. 별도 pip 패키지는 설치하지 않습니다. `requirements-dev.txt`는 현재 필요한 외부 개발 패키지가 없다는 것을 기록합니다.

## 구성

- `bookmark_service.py`: SQLite 기반 북마크 추가와 사용자별 조회
- `tests/test_bookmark_service.py`: 정상 동작, 빈 URL 검증, 중복 저장·동시 저장·잠금 정책의 회귀 테스트
- `TESTING.md`: 테스트 시나리오와 완료 조건
- `AGENTS.md`: 코딩 에이전트가 저장소를 조사하고 단위 테스트를 실행할 때 따를 지침
- `Dockerfile.dev`, `compose.dev.yaml`: 컨테이너 기반 개발·테스트 환경
- `requirements-dev.txt`: 외부 개발 패키지 목록

## 프로젝트 API

`BookmarkService`는 SQLite 연결을 받아 테이블을 만들고 `add_bookmark(user_id, url)`, `list_bookmarks(user_id)`를 제공합니다. 같은 사용자가 같은 URL을 다시 저장하면 기존 북마크의 ID를 반환하고 행을 추가하지 않습니다. 다른 사용자는 같은 URL을 따로 저장할 수 있습니다.

```python
import sqlite3
from bookmark_service import BookmarkService

with sqlite3.connect(":memory:") as connection:
    service = BookmarkService(connection)
    service.add_bookmark("alice", "https://example.com/article")
    print(service.list_bookmarks("alice"))
```

### 저장 동작

- URL이 빈 문자열이거나 공백 문자(스페이스, 탭, 개행 등)로만 이루어져 있으면 `add_bookmark`는 DB에 접근하기 전에 `ValueError`를 발생시키고 아무것도 저장하지 않습니다. 호출자가 연 트랜잭션도 건드리지 않습니다.
- 그 밖의 URL은 주어진 문자열 그대로 저장합니다. 앞뒤 공백 제거, 대소문자·끝 슬래시 정규화, 형식(스킴·호스트) 검증은 하지 않습니다. 이 검증 이전에 저장된 빈/공백 URL 행은 삭제하지 않습니다.
- 같은 `(user_id, url)`은 정확한 문자열 비교로 판단합니다. 이전 버전에서 이미 중복 저장된 행은 삭제하거나 병합하지 않으며, 다시 저장하면 가장 오래된 행(`MIN(id)`)의 ID를 반환합니다. 기존 중복 때문에 `UNIQUE` 제약은 추가하지 않았으므로 서비스를 거치지 않은 직접 `INSERT`는 막지 못합니다.

### 잠금과 재시도 정책

- **열린 트랜잭션이 없는 일반 호출:** 서비스가 `BEGIN IMMEDIATE`로 쓰기 잠금을 먼저 잡고 조회 → 없으면 삽입 → SQL `COMMIT`합니다. 같은 DB 파일에 여러 연결이 동시에 저장해도 북마크는 하나만 남고 모두 같은 ID를 받습니다. 잠금을 busy timeout(`sqlite3.connect(..., timeout=)`) 안에 얻지 못하면 `sqlite3.OperationalError`가 발생하고 서비스가 롤백하므로, 같은 호출을 다시 하면 됩니다.
- **호출자가 이미 트랜잭션을 연 경우:** 그 트랜잭션 안에서 조회·삽입하고 기존처럼 `connection.commit()`을 호출합니다(호출자 트랜잭션의 앞선 작업도 함께 커밋됩니다). 호출자가 이미 읽은 뒤 다른 연결이 쓰기를 커밋하면 SQLite는 `database is locked`를 발생시키며, 서비스는 재시도하거나 롤백하지 않고 오류를 그대로 전파합니다. 호출자가 롤백한 뒤 **처음 읽기부터 전체 트랜잭션을 재시도**합니다.
- 서비스 내부에는 재시도 루프가 없습니다. 보장 범위 밖: 하나의 `Connection`을 여러 스레드가 공유하는 사용, 서비스 밖의 직접 쓰기, 네트워크 파일시스템 위의 DB 파일, Python 3.12+ `autocommit` 연결 모드.

## 주의

이 프로젝트는 인증·접근 제어, 빈/공백 URL 거부 외의 URL 검증, 운영 DB 마이그레이션을 구현하지 않았습니다. 테스트에는 가상의 사용자와 주소만 사용하세요.
