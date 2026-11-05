# Sprint

외우기 쉬운 설치 문장 하나:

```text
Jinseong1126/sprint 스킬 설치
```

Codex **대화창**에 입력하십시오. 설치 후에는 `$product-sprint`로 시작합니다.

## Codex 설치 안내

이 저장소는 앱이나 전체 개발 프로젝트가 아니라 **스킬 하나**입니다.
- GitHub 저장소: `Jinseong1126/sprint`
- 스킬 루트: `.` (`SKILL.md`가 저장소 최상위에 있음)
- 설치할 스킬 이름: `product-sprint`
- 제공된 `skill-installer`의 설치 helper를 사용하십시오. 필요한 인수는 `--repo Jinseong1126/sprint --path . --name product-sprint --method download`입니다.
- 실제 Codex의 사용자 스킬 경로를 먼저 확인하고 필요한 경우 helper의 `--dest`로 지정하십시오. 현대식 사용자 경로는 `~/.agents/skills`; 구형 경로는 실제 지원이 확인될 때만 사용하십시오.
- `product-sprint`가 이미 설치되어 있으면 `SOURCE.json`의 18개 파일 해시와 비교하십시오. 같으면 기존 설치를 유지하고, 다르면 덮어쓰거나 중복 설치하지 말고 차이를 보고하십시오.
- Codex 버전·보안 설정·전역 지침 파일은 변경하지 마십시오. 다운로드나 설치가 금지된 환경에서는 제한을 우회하지 마십시오.
- 공개 아카이브 다운로드로 설치하며 Git 이력을 스킬 안에 복사하지 마십시오. 다운로드 실패를 보안 제한 우회의 근거로 삼지 마십시오.
- 설치 후 실제 Codex 스킬 목록에서 `product-sprint` 인식을 확인하십시오. 목록에 나타나지 않으면 현재 작업을 저장한 뒤 새 세션에서 확인하십시오.

## 출처

스킬 파일 18개는 [원본 준비물의 고정 커밋](https://github.com/Jinseong1126/codex-product-sprint-kit/tree/ad517ddd37cf447c6206636656eba2a4d57263aa/skills/product-sprint)에서 내용 변경 없이 가져왔습니다.
전체 준비·검증·게시 도구는 [원본 저장소](https://github.com/Jinseong1126/codex-product-sprint-kit)에 있습니다.
원본 커밋과 파일 해시는 [SOURCE.json](SOURCE.json), 외부 참고문서 출처는 [THIRD_PARTY.md](THIRD_PARTY.md)에 있습니다.

MIT 라이선스이며, 외부 참고문서는 `licenses/`의 해당 라이선스를 따릅니다.
이 짧은 설치 문장은 이 저장소를 읽을 수 있고 스킬 설치가 허용된 Codex 환경에서 사용합니다.
