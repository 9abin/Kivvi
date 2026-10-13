# 261007-1544 배경 조사: 문제 규모, 제도, 기존 솔루션, 선행 연구

- 관련 TODO: 방향 결정(개정안 작성 에이전트) 전 근거, 발표 자료
- 변경 범위: 문서만. 앞선 팩트체크 [261007-1523-18](261007-1523-18-public-sector-factcheck.md)를 보강

## 요약
1. **문제는 크고 일반적이다.** 법제처·행안부가 자치법규 87,613건을 검토해 15,818건(18%)을 정비 대상으로 찾았고, 그중 **상위법령 미반영이 9,117건으로 가장 많았다**(2015). 공공기관 사규도 권익위가 506개 기관에서 4,722건 개선을 권고했다.
2. **신·구조문대비표와 규정 검색은 이미 있는 기능이다.** 상용 규정관리시스템(나라이넷), 법제처 법령안편집기, 오픈소스 alio-mcp(ALIO 355개 기관 규정 검색·비교, HWPX 개정안+대비표)가 있다.
3. **확인한 범위에서 "규정 간 인용·영향 연쇄 분석과 그 검증"을 하는 도구는 없었다.** alio-mcp는 기능이 없다고 명시돼 있고, 나라이넷 페이지에도 해당 내용이 없다. → 우리 차별점은 대비표가 아니라 **연쇄 영향 분석 + 규칙 기반 검증 + 개정 이력 근거**다.
4. 해외에서는 규정을 구조화해 컴플라이언스 에이전트로 만드는 회사(Norm Ai)가 대규모 투자를 받고 있고, 개정문 자동 적용은 연구 주제다(생성형 모델 성공률 63%).
5. 감사원 원문은 이번에도 확인하지 못했다(다운로드 필요). 중기부 감사결과 목록 1쪽에 한국벤처투자는 없다.

## 1. 문제 규모 (외부 통계)
| 자료 | 내용 | 확인 수준 |
|---|---|---|
| 행안부·법제처 자치법규 정비 (2015.12.28 서울신문) | 87,613건 검토 → 정비 대상 15,818건, 정비 완료 13,946건(88%). 유형: **상위법령 미반영 9,117**, 상위법령 위반 2,216, 근거 없는 규제 1,538, 적용 대상 부재 522, 어려운 용어 1,789 | 기사 원문 |
| 법제처 상시 「불합리한 자치법규 일제 정비」 | 정비 유형에 상위법령 개정사항 미반영 | 검색 요약 |
| 권익위 공공기관 사규 부패영향평가 (2020~2022) | 506개 기관, 사규 48,174개, 개선 권고 4,722건(기관당 9.3), 이행률 75.6% | 기사 원문 |
| 서울시 투자출연기관 감사사례집 (2024.2) | 내규에 상위 기준 미반영(p35), 근거 없는 응시자격(p28) 등 | PDF 원문 |
| 인천공항 자체감사 (2024) | 국가계약법 시행령 변경을 내부 기준이 따라가지 못해 통제 공백(p7) | PDF 원문 |

해석: 전문 법제 인력이 있는 지자체에서도 "상위 규범이 바뀌었는데 하위 규범이 따라가지 않음"이 가장 흔한 정비 유형이다. KVIC 측정 결과(확인된 인용 오류 17건 중 15건이 상대 규정 개정 뒤 미반영)와 같은 구조다.

## 2. 제도
| 근거 | 내용 |
|---|---|
| 공공기관운영법 제11조, 시행령 제15조 | 공공기관은 경영공시 항목(내부규정 포함, 약 48개)을 알리오에 공시하고 최근 5년 자료를 게시 |
| KVIC 사전정보공표 | 내부규정: 수시, 자료 발생 시, 알리오 |
| KVIC 내규관리규정 제20~21조 | 제·개정 때마다 관련 부서 협의, 내규담당부서장·준법감시인과 **법령·정관·다른 내규와의 저촉·중복 여부** 조정 |
| 제25조 | 내규담당부서장은 내규 정비에 항상 노력, 정비 기간 설정 가능 |

## 3. 기존 솔루션·선행 사례
| 이름 | 무엇 | 우리와 겹침 | 없는 것 (확인 범위) | 확인 수준 |
|---|---|---|---|---|
| **alio-mcp** (GitHub, 오픈소스) | ALIO 355개 기관 내부규정(약 36,000건) 검색·조문 읽기·기관 간 비교, 정부 지침 최신성 확인, 마크다운 → HWPX 개정안(신·구조문대비표 「현행·개정안·사유」) | 규정 읽기, 대비표, HWPX 출력 | 규정 간 인용 의존·영향 분석, 오류 점검 없음(README 명시). 개정안은 "AI 초안, 사람 확인 필요". 비공식 내부 API 사용 | README |
| 나라이넷 규정관리시스템 (상용) | 규정 DB, 입법예고 관리, 「신구법령」, AI 검색 | 대비표·연혁류 | 페이지에 인용·영향 분석, 오류 점검 언급 없음 (요약 모델이 있다고 했으나 원문 재확인 결과 없음) | 제품 페이지 |
| 법제처 법령안편집기 | 정부 입법 문안 편집, 신·구조문대비표 자동 생성 | 대비표 | 내규 대상 아님 | 이용자 문의 글 |
| 강남구 자치법규 스마트 정비시스템 (2026) | 법제처 API로 상위법 개정 수집 → 구 자치법규와 비교 → 정비 필요 안내 | 법령 영향(필수 ③) | 자치법규 대상, 내규 간 인용 분석 아님 | 구청 보도자료 |
| korean-law-mcp (GitHub) | 법제처 국가법령정보 조회, 인용 검증 | 법령 쪽 | 온라인 API (폐쇄망 불가) | 검색 결과 |
| Norm Ai (미국) | 규정·사내 정책을 결정 트리 형태의 자체 언어로 표현 → 컴플라이언스 에이전트가 업무 산출물 점검. 2024 $27M, 2025 $48M(누적 $87M), 2026 $120M(보도) | 규정을 구조화해 에이전트로 | 한국 공공 내규·개정 연쇄 아님 | 보도자료·기사 |
| Definely, Litera (계약) | 계약서 상호참조 오류 5유형, 정의 용어 점검 | 인용 오류 점검 | 규정 체계·개정 이력 아님 | 앞선 벤치마킹 |

## 4. 선행 연구
| 연구 | 내용 | 시사점 |
|---|---|---|
| Etcheverry 외, "Algorithm for Automatic Legislative Text Consolidation" (arXiv 2501.16794, 2025.1) | 개정 조항을 기존 조문에 적용해 통합본 생성. 경량 생성형 모델+LoRA. 어려운 법안에서 성공률 63% 이상, 데이터 HuggingFace 공개 | 생성형 적용은 실패가 잦음 → 우리는 개정문 적용을 **규칙**으로 하고 LLM은 문안 작성만. 비교 대상으로 인용 가능 |
| CLiC-it 2021 "A First Step Towards Automatic Consolidation of Legal Acts" (이탈리아) | 개정 유형 분류 + 통합 | 개정문 문법 분류 방식 참고 |
| MetaVex (ACM) | XML 입법 편집기에서 개정 문서 자동 생성과 자동 통합 | Akoma Ntoso 계열 구조화 방식 |

## 5. 감사원 (미확인)
- 감사결과 목록(1,231건)은 제목 검색만 되고 내용은 보고서 파일에 있음. 다운로드 버튼이 스크립트로 동작해 파일 주소를 얻지 못했고, 브라우저 다운로드는 사전 허락 대상이라 하지 않음.
- 웹 검색으로 "감사원이 내규의 법령 미반영을 지적"한 보도를 찾지 못함.
- 중기부 감사결과 목록 1쪽(173건 중 10건)에 한국벤처투자 없음.
- 발표에서 "감사원" 근거는 쓰지 않는다 (리포트 18과 같음).

## 시사점: 차별점 재정의
| 기존에 있음 (차별점 아님) | 우리만의 것 (확인 범위) |
|---|---|
| 규정 검색, 신·구조문대비표, HWPX 출력, 연혁 | **규정 간 인용·영향의 연쇄 분석** (번호 이동·삭제·값 변경 → 깨지는 곳·해소·확인 필요) |
| 상위법 개정 탐지(강남구, 자치법규) | **내규끼리** 엮인 관계 + 개정 이력으로 "따라가지 않은 곳" 찾기 |
| 생성형 개정 적용(연구, 63%) | **규칙 기반 적용·검증**: 틀린 개정문은 멈추고, 결과는 재현·역추적 |
| 클라우드 API 기반 도구 | 폐쇄망·로컬 모델 전제 (KVIC 자체 NPU 챗봇 방향과 일치) |

- 대비표는 "기본 기능"으로 보여 주고, 발표의 중심은 **연쇄 영향 + 검증**에 둔다.
- alio-mcp의 "타 기관 같은 주제 규정 비교"는 확장 아이디어로 참고(예: 다른 공공기관은 육아휴직 분할을 몇 회로 정했나). 다만 비공식 API 의존은 피한다.
- Norm Ai의 "규정을 구조화해 에이전트로"는 아이디어 3(규정을 데이터로)의 해외 근거.

## 추가: "연결·영향 분석"은 어디까지 있나 (같은 날 보강)
| 연결 대상 | 사례 | 우리와 다른 점 |
|---|---|---|
| 법령 ↔ 법령 | 국가법령정보센터 위임조문·참조조문 링크, 법령체계도, 3단비교, 신구법비교 | 법령만. 링크 탐색이지 변경 시 깨짐 알림 아님 |
| 상위법 → 하위 규범 | 강남구·강동구 자치법규 시스템 | 법령 ↔ 조례 |
| 외부 규제 → 사내 정책·통제 | CUBE, Regology, Ascent (해외, 금융권 중심) | 시장 성숙. 단위가 규제 의무 ↔ 정책·통제, 내규 간 조문 인용 아님 |
| 사내 정책 ↔ 통제·리스크·교육 | NAVEX, LogicManager, Riskonnect, MetricStream | 문서 단위, 사람이 매핑한 관계 |
| 문서 안 상호참조 | Definely(계약서) | 문서 한 건 안 |
| 법령 조문 간 누락 연결(연구) | KCI 「특례제한규정의 효력·법체계성… 자동화된 텍스트 분석」 | 법령 대상, 방법은 가까움 |

**찾지 못한 것 = 우리 자리:** 한 기관의 내규 수십 개를 조문 번호 단위로 원문에서 자동 연결하고, 개정안 하나에 대해 깨지는 곳·해소·확인 필요를 찾아 연쇄 수정안까지 만들고 규칙으로 검증하는 것.
발표 표현: "공개된 제품·연구를 조사한 범위에서는 찾지 못했다" (단정 금지 — 기관 내부 시스템은 확인 불가).
외부 규제 → 내부 정책 매핑 시장의 존재는 영향 분석 수요가 검증됐다는 근거로 쓴다.

추가 출처: [CUBE policy and controls mapping](https://cube.global/sectors/requirement/policy-and-controls-mapping), [Regology Regulatory Change Management](https://help.regology.com/en/rug/p/regulatory-change-management), [Ascent RegTech](https://www.ascentregtech.com/blog/regulatory-change-managements-achilles-heel-is-fast-execution/), [Riskonnect policy management](https://riskonnect.com/policy-management-software/), [MetricStream regulatory change management](https://www.metricstream.com/products/regulatory-change-management.htm), [국가법령정보센터 활용법(로톡)](https://www.lawtalk.co.kr/posts/77938), [KCI 특례제한규정 텍스트 분석 연구](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002497111)

## 출처
- [행자부·법제처, '법령위반·유명무실' 지자체 법규 1만4천건 정비완료 (서울신문, 2015.12.28)](https://www.seoul.co.kr/news/society/2015/12/28/20151228800103)
- [권익위 "506개 공공기관 사규 부패영향평가…위험요인 4700여건 개선" (이데일리)](https://edaily.co.kr/News/Read?mediaCodeNo=257&newsId=02473126635476736)
- [최근 4년치 공공기관 공시자료 안 알리는 '알리오' (매일노동뉴스)](https://www.labortoday.co.kr/news/articleView.html?idxno=218840)
- [chromehearts79/alio-mcp (GitHub)](https://github.com/chromehearts79/alio-mcp)
- [나라이넷 규정관리 서비스 페이지](https://narainet.co.kr/lims/front/page/service.html?pAct=judicialAffairsSystem)
- [법제처 국민참여입법센터 — 신구조문대비표 문의](https://opinion.lawmaking.go.kr/advc/stmInq/13210?pageIndex=3)
- [chrisryugj/korean-law-mcp (GitHub)](https://github.com/chrisryugj/korean-law-mcp)
- [Norm Ai Secures $48 million (PR Newswire)](https://www.prnewswire.com/news-releases/norm-ai-secures-48-million-to-transform-regulations-into-compliance-ai-agents-302398351.html)
- [Norm Ai raises $27M (SiliconANGLE)](https://siliconangle.com/2024/06/26/norm-ai-raises-27m-help-businesses-handle-regulatory-compliance-ai-agents/)
- [Norm AI Raises $120M (KuCoin 뉴스, 2차 출처)](https://www.kucoin.com/news/flash/norm-ai-raises-120m-to-automate-legal-services-and-transform-billing-models)
- [Algorithm for Automatic Legislative Text Consolidation (arXiv 2501.16794)](https://arxiv.org/abs/2501.16794)
- [A First Step Towards Automatic Consolidation of Legal Acts (CLiC-it 2021)](https://aclanthology.org/2021.clicit-1.24.pdf)
- [Automated Handling of Amending Documents and Resulting Consolidations (ACM)](https://dl.acm.org/doi/10.5555/1671082.1671097)
- [강남구 AI기반 자치법규 스마트 정비시스템](https://www.gangnam.go.kr/board/B_000031/1074984/view.do?mid=ID01)
- [중소벤처기업부 감사결과 목록](https://www.mss.go.kr/site/smba/ex/bbs/List.do?cbIdx=133)
- [감사원 분야·종류별 감사결과](https://www.bai.go.kr/bai/result/branch/list/)

## 후속 작업
- [ ] (허락 시) 감사원 보고서 원문 확인
- [ ] 발표: 문제 규모(법제처 9,117건, 권익위 4,722건) → KVIC 측정 → 기존 솔루션과 차별점 표
- [ ] 방향 확정 후 03-product-plan 갱신
