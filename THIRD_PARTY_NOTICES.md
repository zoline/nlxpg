# 서드파티 고지

nlxpg 자체는 [Apache License 2.0](LICENSE)으로 배포한다. 이 문서는 nlxpg 저장소에 **함께 들어 있는** 남의 소프트웨어·데이터와, 설치 시 **내려받는** 의존성의 라이선스를 정리한다.

## 1. 함께 배포하는 소프트웨어

### Mermaid 11.4.1 — `nlxpg/api/static/vendor/mermaid.min.js`

웹 UI의 ERD를 그린다. 오프라인 환경에서도 동작하도록 파일을 저장소에 포함했다.

- Mermaid 본체: MIT, Copyright (c) 2014 - 2022 Knut Sveidqvist — [mermaid.LICENSE.txt](nlxpg/api/static/vendor/mermaid.LICENSE.txt)
- `mermaid.min.js`는 하위 의존성 140개를 한 파일로 묶은 것이다. 각 패키지의 저작권 표시와 라이선스 전문: [mermaid.THIRD_PARTY_LICENSES.txt](nlxpg/api/static/vendor/mermaid.THIRD_PARTY_LICENSES.txt)

| 라이선스 | 패키지 수 | 대표 패키지 |
| --- | --- | --- |
| MIT | 93 | mermaid, cytoscape, dagre-d3-es, katex, lodash-es, marked, dayjs, khroma, langium, roughjs |
| ISC | 33 | d3 및 d3-* 모듈, delaunator |
| BSD-3-Clause | 6 | d3-sankey, d3-array@2, d3-path@1, d3-shape@1, d3-ease, rw |
| Apache-2.0 | 7 | chevrotain 및 @chevrotain/*, DOMPurify(선택) |
| Unlicense | 1 | robust-predicates |

- **DOMPurify**는 MPL-2.0 또는 Apache-2.0 이중 라이선스다. nlxpg는 **Apache-2.0**을 선택한다.
- **khroma**는 npm 메타데이터에 라이선스가 비어 있지만 패키지에 포함된 라이선스 파일이 MIT다.
- GPL·LGPL·AGPL 같은 카피레프트 라이선스는 없다.

## 2. 함께 배포하는 데이터

### 공공데이터 공통표준 — `data/standards/`

| 파일 | 출처 |
| --- | --- |
| 공통표준용어_20251101.csv | 행정안전부, [공공데이터포털 15156379](https://www.data.go.kr/data/15156379/fileData.do) |
| 공통표준단어_20251101.csv | 행정안전부, [공공데이터포털 15156439](https://www.data.go.kr/data/15156439/fileData.do) |
| 공통표준도메인_20251101.csv | 행정안전부, [공공데이터포털 15156442](https://www.data.go.kr/data/15156442/fileData.do) |

공공데이터포털 표기: **이용허락범위 제한 없음**. 원본 그대로 두고 출처를 밝힌다.

## 3. 설치할 때 내려받는 의존성 (저장소에 포함하지 않음)

`pyproject.toml`에 적힌 파이썬 패키지는 pip이 설치할 때 각 배포처에서 내려받는다. nlxpg 저장소가 재배포하지 않는다.

| 패키지 | 라이선스 |
| --- | --- |
| pydantic, pydantic-settings, fastapi, typer | MIT |
| python-dotenv, httpx, uvicorn, starlette, click | BSD-3-Clause |
| openai, asyncpg, python-multipart | Apache-2.0 |
| cryptography (LLM 키 암호화) | Apache-2.0 OR BSD-3-Clause |
| certifi (httpx 하위 의존성) | MPL-2.0 |
| anthropic, docstring-parser (선택 설치 `[claude]`, ADR-0008) | MIT |
| docling (선택 설치 `[parsing]`) | MIT. 실행 시 내려받는 모델 가중치는 각 모델의 라이선스를 따른다 |

하위 의존성까지 모두 허용형(MIT·BSD·Apache·ISC·PSF)이고, certifi와 pathspec(개발 도구 mypy의 하위 의존성)만 MPL-2.0이다. MPL-2.0은 해당 파일을 **고쳐서** 배포할 때만 수정본 공개 의무가 생기며, nlxpg는 고치지 않고 설치해 쓴다.

## 4. 배포하지 않는 자료

| 자료 | 이유 |
| --- | --- |
| `docs/reference/*.pdf` (공공데이터베이스 표준화 관리 매뉴얼, 행정안전부, 2026.4) | 문서에 공공누리 등 이용 조건 표시가 없어 재배포하지 않는다(`.gitignore`). 원문은 한국지능정보사회진흥원(NIA)·공공데이터포털 배포본을 받는다. 정리 노트(`docs/reference/public-db-standard-manual-notes.md`)는 요약·인용만 담는다 |
| `data/private/`, `out/` | 사내 문서·업로드·실행 산출물 |

BIRD(CC BY-SA 4.0) 등 공개 벤치마크에서 만든 테스트 문서·정답을 저장소에 넣게 되면, 그 폴더에 원 데이터셋의 라이선스를 표시하고 같은 조건으로 배포한다.

## 고지 갱신 방법

Mermaid 버전을 바꾸면 `mermaid.THIRD_PARTY_LICENSES.txt`를 다시 만든다.

```bash
mkdir /tmp/mm && cd /tmp/mm && npm init -y && npm i --omit=dev --ignore-scripts mermaid@<버전>
# node_modules의 각 package.json(license)과 LICENSE 파일을 모아 같은 문구끼리 묶는다
```

파이썬 의존성은 `importlib.metadata`로 설치된 패키지의 `License-Expression`을 확인한다.
