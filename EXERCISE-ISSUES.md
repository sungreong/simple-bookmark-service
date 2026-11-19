# 테스트 대기 이슈 목록

이 저장소는 개발 에이전트 실습용입니다. 아래 기능에는 의도적인 결함이 있으며 운영에 사용하면 안 됩니다. 이 문서는 **이슈 후보**이고 GitHub 이슈가 자동 생성되지는 않습니다. 사용자가 항목을 선택해 요청하면 관련 기존 이슈와 PR을 먼저 확인합니다.

기본 회귀(`tests/`)는 통과해야 합니다. 결함별 재현 테스트(`exercise_tests/`)는 선택 실행하며 수정 전에는 각 항목이 실패합니다. 전체 선택 테스트는 여러 항목이 동시에 실패하므로 한 이슈를 수정할 때는 해당 클래스와 기본 회귀를 실행하고, 나머지 실패는 다른 대기 항목으로 구분해 보고합니다. 실제 테스트 실행 환경과 종료 코드를 기록합니다.

## 1. 다른 사용자의 북마크 삭제 요청

- 상황: Alice가 Bob의 북마크 ID로 삭제를 요청합니다.
- 기대 결과: 삭제 결과는 `False`이고 Bob의 북마크는 그대로 남습니다. Alice의 본인 북마크 삭제는 정상 동작해야 합니다.
- 재현: `python -m unittest exercise_tests.test_scenarios.DeleteOwnershipTests -v`
- 추가 검증: 존재하지 않는 ID, 다른 사용자, 본인의 삭제와 삭제 후 목록을 확인합니다.

## 2. 검색어의 특수 문자를 일반 문자로 처리

- 상황: 사용자가 `_100%`을 포함하는 URL을 검색합니다.
- 기대 결과: 검색어를 문자 그대로 포함하는 본인의 북마크만 반환합니다. `_`나 `%`가 임의 문자를 뜻하지 않습니다.
- 재현: `python -m unittest exercise_tests.test_scenarios.LiteralSearchTests -v`
- 추가 검증: `%`, `_`, 빈 검색어, 다른 사용자의 결과와 일반 문자열 검색을 확인합니다. 대소문자 정책은 기존 SQLite 동작을 유지합니다.

## 3. 첫 번째 페이지가 첫 항목부터 시작

- 상황: 북마크가 세 개일 때 페이지 크기 2로 1페이지를 요청합니다.
- 기대 결과: 첫 번째와 두 번째 북마크가 ID 순으로 반환됩니다. 페이지 번호는 1부터 시작합니다.
- 재현: `python -m unittest exercise_tests.test_scenarios.PaginationTests -v`
- 추가 검증: 2페이지, 마지막 빈 페이지, 사용자별 분리, 0 이하 페이지와 크기 오류를 확인합니다.

## Docker Compose에서 실행

기본 전체 회귀:

```bash
docker compose -f compose.dev.yaml run --build --rm test
```

예를 들어 첫 번째 결함만 재현하려면:

```bash
docker compose -f compose.dev.yaml run --build --rm test python -m unittest exercise_tests.test_scenarios.DeleteOwnershipTests -v
```

다른 항목은 해당 테스트 클래스 이름을 바꿉니다. 전체 시나리오를 함께 확인하려면 `python -m unittest discover -s exercise_tests -v`를 같은 방식으로 실행합니다.
