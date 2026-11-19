# Simple Bookmark Service

여러 사용자의 북마크를 SQLite에 저장하는 작은 Python 실습 프로젝트입니다. 이 저장소는 코딩 에이전트와 개발 자동화 하네스를 시험하기 위해 만들었습니다. 처음에는 같은 북마크가 중복 저장되는 결함을 의도적으로 포함했으며, 이 결함은 [이슈 #1](https://github.com/sungreong/simple-bookmark-service/issues/1)에서 수정되었습니다. 현재는 수정된 동작과 회귀 테스트가 함께 있습니다. 운영 서비스용 코드가 아닙니다.

추가 실습용 기능 `bookmark_extras.py`의 페이지 조회 결함은 이슈 #8에서 수정했습니다. 삭제·검색의 대기 결함과 기본 회귀에서 분리된 시나리오 명령은 [테스트 대기 이슈 목록](EXERCISE-ISSUES.md)을 참고하세요. 실습 코드는 인증·접근 제어가 완성된 서비스가 아닙니다.

`BookmarkExtras.page(user_id, page_number, page_size)`는 1부터 시작하는 페이지 번호를 사용하고 사용자별 북마크를 ID 순으로 반환합니다. 페이지 간 중복·누락 없음은 해당 사용자의 데이터가 페이지 조회 사이에 바뀌지 않은 경우에만 보장합니다. 조회 사이에 북마크가 추가·삭제되면 고정된 스냅샷을 유지하지 않습니다.

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

모든 테스트(현재 30개)가 통과해야 합니다. 테스트는 순차 반복 저장, 정확한 문자열 비교, 사용자별 분리, 기존 중복 데이터, 서비스 밖 직접 쓰기와 다른 스레드의 연결 공유에 대한 한계, 여러 스레드·프로세스의 동시 저장, 잠금 오류와 재시도 정책, `isolation_level=None`과 Python 3.12+의 `autocommit` 연결 모드, 수정 전 코드에서 회귀 테스트가 실패하는지를 다룹니다. `autocommit` 테스트 8개는 Python 3.11 이하에서 건너뜁니다. 시나리오와 완료 조건은 [테스트 가이드](TESTING.md)를 확인하세요.

### Docker Compose 개발 환경

Docker와 Docker Compose가 설치되어 있으면 다음 명령으로 같은 단위 테스트를 컨테이너에서 실행할 수 있습니다.

```bash
docker compose -f compose.dev.yaml run --build --rm test
```

`test` 서비스는 Python 3.12의 `slim` 이미지를 사용하고 현재 저장소를 `/workspace`에 마운트합니다. 테스트는 임시 SQLite DB를 사용하며 포트나 외부 DB가 필요하지 않습니다. 첫 실행에는 이미지 다운로드가 필요합니다. 별도 pip 패키지는 설치하지 않습니다. `requirements-dev.txt`는 현재 필요한 외부 개발 패키지가 없다는 것을 기록합니다.

## 구성

- `bookmark_service.py`: SQLite 기반 북마크 추가와 사용자별 조회
- `tests/test_bookmark_service.py`: 정상 동작, 중복 저장 결함, 동시 저장과 잠금·재시도 정책의 회귀 테스트
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

- 같은 `(user_id, url)`은 정확한 문자열 비교로 판단합니다. 대소문자나 끝 슬래시는 정규화하지 않습니다.
- 수정 전 버전으로 이미 중복 저장된 행은 삭제하거나 병합하지 않습니다. 그 북마크를 다시 저장하면 가장 오래된 행(가장 작은 `id`)의 ID를 반환하고 새 행을 추가하지 않습니다. 기존 중복이 있는 DB로도 서비스를 초기화할 수 있습니다.
- 스키마와 기존 데이터는 바꾸지 않습니다. 기존 중복이 있으면 생성할 수 없는 `UNIQUE` 제약은 추가하지 않았으므로 서비스를 거치지 않은 직접 `INSERT`는 막지 못합니다. 중복 방지는 `BookmarkService.add_bookmark`를 통한 저장에만 적용됩니다.

### 잠금과 재시도 정책

`add_bookmark`는 연결에 열린 트랜잭션이 있는지에 따라 다르게 동작합니다.

| 상황 | 동작 | 잠금 오류 시 | 호출자가 할 일 |
|---|---|---|---|
| 열린 트랜잭션 없음 (일반 호출) | 서비스가 `BEGIN IMMEDIATE`로 쓰기 잠금을 먼저 잡고 조회 → 없으면 삽입 → SQL `COMMIT` (오류 시 SQL `ROLLBACK`). 연결의 트랜잭션 모드와 관계없이 반환 시 열린 트랜잭션이 남지 않음 | 연결의 busy timeout(`sqlite3.connect(..., timeout=)`, 기본 5초)까지 기다린 뒤 `sqlite3.OperationalError`(`database is locked`). 서비스가 롤백하므로 열린 트랜잭션이 남지 않고 행도 추가되지 않음 | 같은 호출을 다시 해도 됨 (멱등) |
| 호출자가 트랜잭션을 연 상태 | 그 트랜잭션 안에서 조회·삽입 후 **`connection.commit()` 호출** (기존 호환 동작). 기본(legacy)·`autocommit=False` 연결에서는 호출자 트랜잭션도 함께 커밋되어 끝나고, `autocommit=True` 연결에서는 `commit()`이 아무 일도 하지 않으므로 트랜잭션이 열린 채 호출자에게 남음 | `sqlite3.OperationalError`를 그대로 전파. 서비스는 호출자 트랜잭션을 커밋·롤백·재시도하지 않으며 트랜잭션과 그 안의 이전 작업은 커밋되지 않은 채 남음 | 롤백 후 **처음 읽기부터 전체 트랜잭션을 재시도**. `autocommit=True` 연결에서는 `connection.rollback()`이 아무 일도 하지 않으므로 `connection.execute("ROLLBACK")`을 사용 |

- 같은 DB 파일에 연결별로(같은 프로세스의 여러 스레드 또는 여러 프로세스) 동시에 저장해도 쓰기 잠금 아래에서 확인과 삽입이 직렬화되어 북마크는 하나만 남고 모두 같은 ID를 받습니다 (rollback journal과 WAL 모드, 별도 프로세스 테스트).
- 호출자 트랜잭션이 이미 읽기를 했고 그 사이 다른 연결이 쓰기를 진행하거나 커밋했다면 SQLite는 오래된 읽기에 기반한 쓰기를 허용하지 않습니다. WAL 모드에서는 오래된 스냅샷 때문에, 기본 rollback journal 모드에서는 교착을 피하려고 busy timeout과 관계없이 즉시 `database is locked`가 발생합니다. 같은 트랜잭션에서 다시 시도해도 성공하지 않으므로 반드시 롤백 후 전체를 다시 실행합니다. 서비스가 이 상황에서 호출자 트랜잭션을 먼저 커밋하고 다시 시도하지 않는 이유는, 그렇게 하면 호출자가 읽은 내용과 맞지 않을 수 있는 호출자 작업이 몰래 커밋되기 때문입니다.
- `add_bookmark`가 성공하면 호출자 트랜잭션의 그 이전 작업도 커밋됩니다(기본·`autocommit=False` 연결). 호출자 트랜잭션에서 사용할 때는 `add_bookmark`를 마지막 단계로 두세요. 그렇지 않으면 이후 단계의 잠금으로 재시도할 때 이미 커밋된 앞부분이 다시 실행될 수 있습니다.
- Python 3.12+ 연결 모드별 차이:
  - 기본값(`autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL`): 위 표 그대로입니다.
  - `autocommit=True`: `connection.commit()`/`rollback()`이 아무 일도 하지 않습니다. 일반 호출은 서비스가 SQL로 커밋·롤백하므로 다른 모드와 같습니다. 호출자가 `BEGIN`으로 연 트랜잭션은 서비스가 끝내지 않으므로 호출자가 SQL `COMMIT`/`ROLLBACK`으로 끝냅니다.
  - `autocommit=False`: sqlite3가 항상 트랜잭션을 열어 두므로 모든 호출이 "호출자가 트랜잭션을 연 상태"에 해당합니다. 성공하면 커밋되지만, 동시에 저장하면 `BEGIN IMMEDIATE`로 직렬화되지 않아 한 연결을 제외한 나머지가 `database is locked`를 받을 수 있습니다(중복은 생기지 않음). 호출자가 `rollback()` 후 다시 시도합니다. 경합이 심하면 대기 없는 즉시 재시도가 커밋 중인 연결을 굶길 수 있으므로 재시도 사이에 짧은 무작위 대기(backoff)를 둡니다.
- 서비스 내부에는 재시도 루프가 없습니다. 재시도 횟수와 대기 시간은 호출자가 정합니다.
- 보장 범위 밖: 서비스 밖의 직접 쓰기(DB 고유 제약 없음), 하나의 `Connection`을 여러 스레드가 공유하는 사용(`check_same_thread=False`; 기본값에서는 sqlite3가 다른 스레드의 사용을 `ProgrammingError`로 거부), 네트워크 파일시스템 위의 DB 파일, Python 3.12 이외 버전에서의 실행(기본 Compose 환경은 3.12만 실행).

호출자 트랜잭션에서 재시도하는 예:

```python
import random
import sqlite3
import time
from bookmark_service import BookmarkService

def save_in_transaction(connection, user_id, url, attempts=3):
    service = BookmarkService(connection)
    for attempt in range(attempts):
        try:
            connection.execute("BEGIN")
            # 호출자의 다른 읽기·쓰기를 여기에서 수행합니다.
            return service.add_bookmark(user_id, url)  # 기본 모드에서는 성공 시 전체 트랜잭션이 커밋됩니다.
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")  # autocommit=True에서도 동작합니다.
            if "locked" not in str(error) or attempt == attempts - 1:
                raise
            time.sleep(random.uniform(0.001, 0.02))
```

## 주의

이 프로젝트는 인증·접근 제어, URL 검증, 운영 DB 마이그레이션을 구현하지 않았습니다. 테스트에는 가상의 사용자와 주소만 사용하세요.
