# 테스트 가이드

## 시나리오: 같은 북마크가 두 번 저장됩니다

Alice가 같은 게시물의 북마크 추가 버튼을 두 번 눌렀다고 가정합니다. 현재 저장소에는 이 요청이 애플리케이션의 `add_bookmark`를 두 번 호출하는 형태로 재현되어 있습니다. 브라우저와 HTTP 서버는 포함하지 않습니다.

1. `BookmarkService.add_bookmark("alice", "https://example.com/a")`를 호출합니다.
2. 같은 사용자와 URL로 다시 호출합니다.
3. Alice의 목록을 확인합니다.

**기대 결과:** 두 호출은 같은 ID를 반환하고, 목록에는 해당 북마크가 하나만 있습니다.

**수정 전 결과 (기준 커밋 `f07bdd2`):** 새로운 ID가 두 번 반환되고 목록에 같은 URL이 중복됩니다.

**현재 결과:** [이슈 #1](https://github.com/sungreong/simple-bookmark-service/issues/1)의 수정으로 기대 결과와 같습니다. `test_saving_the_same_bookmark_twice_is_idempotent`가 이를 확인합니다.

## 기준 테스트

Docker Compose를 사용할 수 있다면 다음 명령을 우선합니다.

```bash
docker compose -f compose.dev.yaml run --build --rm test
```

로컬 Python을 사용하는 대안은 `python -m unittest discover -s tests -v`입니다. 어느 환경에서 실행했는지, 실제 출력과 종료 코드를 기록합니다.

수정 후에는 아래 19개 테스트가 모두 통과해야 합니다(Python 3.11 이하에서는 `AutocommitModeTests` 8개를 건너뜁니다). 수정 전 기준 커밋 `f07bdd2`에서는 테스트가 세 개였고 그중 `test_saving_the_same_bookmark_twice_is_idempotent`가 `AssertionError: 1 != 2`로 실패했습니다.

| 테스트 클래스 | 테스트 | 확인하는 조건 |
|---|---|---|
| `BookmarkServiceTests` | `test_save_and_list_bookmarks_for_one_user` | 저장과 사용자별 조회 |
| | `test_same_url_can_be_saved_by_different_users` | 다른 사용자의 같은 URL 허용, 조회 분리 |
| | `test_saving_the_same_bookmark_twice_is_idempotent` | 순차 반복 저장 시 같은 ID, 행 1개 |
| | `test_saving_twice_inside_a_caller_transaction_is_idempotent` | 호출자 트랜잭션 안의 반복 저장 |
| | `test_saving_inside_a_caller_transaction_commits_it` | 호환 동작: 성공 시 호출자 트랜잭션과 그 이전 작업도 커밋 |
| `ExistingDuplicateDataTests` | `test_existing_duplicates_are_kept_and_no_new_duplicate_is_added` | 수정 전 DB의 중복 행 유지, 가장 오래된 ID 반환, 신규 중복 없음 |
| `ConcurrentSaveTests` | `test_concurrent_saves_leave_one_row_with_rollback_journal` | 8개 연결 동시 저장 × 5회: 같은 ID, 행 1개 (기본 journal) |
| | `test_concurrent_saves_leave_one_row_with_wal` | 같은 조건 (WAL) |
| | `test_lock_in_an_already_read_caller_transaction_is_raised_for_full_retry` | WAL: 이미 읽은 호출자 트랜잭션의 잠금 오류 전파, 트랜잭션 유지, 롤백 후 전체 재시도 시 기존 ID |
| | `test_lock_in_an_already_read_caller_transaction_is_raised_for_full_retry_with_rollback_journal` | 기본 journal: 다른 연결이 커밋 대기 중이면 busy timeout과 관계없이 즉시 잠금 오류, 롤백 후 전체 재시도 시 기존 ID |
| | `test_busy_timeout_in_a_service_owned_transaction_raises_without_leaving_it_open` | 일반 호출의 busy timeout 초과: 오류 전파, 열린 트랜잭션·행 없음, 같은 호출 재시도 가능 |
| `AutocommitModeTests` (Python 3.12+) | `test_saving_does_not_leave_a_transaction_open_in_memory` | `connect(":memory:", autocommit=True)`에서 저장 후 `in_transaction`이 False, 반복 저장 같은 ID |
| | `test_saved_bookmark_is_committed_and_the_write_lock_released` | `autocommit=True` 저장이 커밋되어 다른 연결에 보이고 쓰기 잠금이 풀림 |
| | `test_concurrent_saves_leave_one_committed_row` | `autocommit=True` 8개 연결 동시 저장 × 5회: 같은 ID, 커밋된 행 1개 |
| | `test_busy_timeout_in_a_service_owned_transaction_raises_without_leaving_it_open` | `autocommit=True` 일반 호출의 busy timeout: 오류 전파, 열린 트랜잭션 없음 |
| | `test_error_after_begin_rolls_back_the_service_owned_transaction` | `BEGIN IMMEDIATE` 이후 오류(NOT NULL)에서 SQL `ROLLBACK`, 열린 트랜잭션 없음 |
| | `test_lock_in_an_already_read_caller_transaction_needs_sql_rollback_and_full_retry` | `autocommit=True`, WAL: 잠금 오류 전파, `rollback()`은 no-op, SQL `ROLLBACK` 후 전체 재시도 시 기존 ID |
| | `test_caller_transaction_is_left_for_the_caller_to_end` | `autocommit=True` 호출자 트랜잭션은 서비스가 끝내지 않음: 호출자 `COMMIT`이면 반영, `ROLLBACK`이면 미반영 |
| | `test_autocommit_false_commits_and_concurrent_callers_retry_without_duplicates` | `autocommit=False`: 저장이 커밋됨, 동시 저장에서 잠금 오류 시 호출자 `rollback()`·재시도로 같은 ID, 행 1개 |

## 잠금과 재시도 정책 시나리오

정책의 전체 설명은 [README의 잠금과 재시도 정책](README.md#잠금과-재시도-정책)에 있습니다. 테스트는 다음을 확인합니다.

1. **일반 호출 (열린 트랜잭션 없음):** 서비스가 `BEGIN IMMEDIATE`로 확인과 삽입을 직렬화합니다. 다른 연결이 쓰기 잠금을 busy timeout보다 오래 잡고 있으면 `sqlite3.OperationalError`가 발생하고 서비스가 롤백합니다. 호출자는 같은 호출을 다시 할 수 있습니다.
2. **이미 읽은 호출자 트랜잭션:** 다른 연결이 먼저 쓰기를 진행하거나 커밋하면 `sqlite3.OperationalError`가 전파되고 트랜잭션은 열린 채 남습니다. 서비스는 재시도하지 않습니다. 호출자가 롤백(`autocommit=True` 연결에서는 SQL `ROLLBACK`) 후 읽기부터 전체 트랜잭션을 재시도하면 다른 연결이 저장한 ID를 받고 행은 하나로 유지됩니다.
3. **호출자 트랜잭션의 커밋:** `add_bookmark`는 성공 시 `connection.commit()`을 호출합니다. 기본 모드에서는 호출자 트랜잭션의 앞선 작업도 함께 커밋되므로 `add_bookmark`는 마지막 단계에 둡니다.
4. **Python 3.12+ `autocommit=True`:** `connection.commit()`/`rollback()`이 아무 일도 하지 않습니다. 일반 호출은 서비스가 SQL `COMMIT`/`ROLLBACK`으로 끝내므로 열린 트랜잭션이 남지 않습니다. 호출자가 연 트랜잭션은 호출자가 SQL `COMMIT`/`ROLLBACK`으로 끝냅니다.
5. **Python 3.12+ `autocommit=False`:** 항상 트랜잭션이 열려 있으므로 모든 호출이 2·3번 정책을 따릅니다. 동시 저장에서는 잠금 오류가 날 수 있으며 호출자가 `rollback()` 후 재시도합니다.

동시성 테스트는 한 프로세스 안에서 스레드마다 별도의 연결을 사용합니다. 잠금은 DB 파일 단위이므로 같은 원리가 적용되지만 다중 프로세스, 연결을 공유하는 스레드, 네트워크 파일시스템은 테스트하지 않습니다.

## 에이전트에 줄 요청 예시

```text
제목: 같은 북마크를 반복 추가하면 중복 저장됩니다.

기대 동작:
- 같은 사용자가 같은 URL을 반복 추가해도 북마크는 하나만 남습니다.
- 반복 호출은 기존 북마크의 ID를 반환합니다.
- 다른 사용자가 같은 URL을 저장하는 것은 허용합니다.

완료 조건:
- 수정 전 실패를 확인하고 원인을 설명합니다.
- 전체 unittest가 통과합니다.
- 사용자별 조회가 다른 사용자의 데이터를 포함하지 않습니다.
- 동시 요청이나 기존 DB의 중복 데이터에 대한 처리 범위는 검토하고,
  구현하거나 검증하지 않은 항목은 보고서에 명시합니다.
- 기존 API 이름과 인자를 유지합니다.

제외: UI, 로그인, 외부 DB, 운영 데이터 이전, 배포
```

## 사람의 검토 기준

- [ ] 작업 브랜치와 기준 커밋을 기록했나요?
- [ ] 수정 전 실패와 수정 후 결과가 명령과 함께 남았나요?
- [ ] 같은 사용자의 반복 요청과 다른 사용자의 같은 URL을 구분했나요?
- [ ] 수정 범위, 기존 DB에 미치는 영향, 동시 요청에서의 한계를 설명했나요?
- [ ] diff와 테스트를 사람이 확인할 때까지 PR 생성이나 병합을 기다리나요?

위 시나리오는 **순차 반복 요청**을 재현하고, 동시 저장·기존 중복 데이터·잠금 재시도는 `ConcurrentSaveTests`와 `ExistingDuplicateDataTests`가 다룹니다. 기존 DB의 중복 정리(삭제·병합)는 하지 않으며 별도 설계가 필요합니다. 여러 프로세스의 동시 요청은 테스트하지 않습니다.
