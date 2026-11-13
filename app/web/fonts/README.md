# 화면 글꼴 (폐쇄망)

화면은 KRDS(정부 디자인 시스템)가 쓰는 Pretendard GOV를 쓰도록 만들었다. 폐쇄망에서는 외부 주소(CDN)를 부를 수 없어 글꼴 파일을 내부에 두어야 한다.

1. 인터넷이 되는 PC에서 Pretendard의 `PretendardGOVVariable.woff2`를 내려받는다 (오픈소스, SIL OFL 라이선스: https://github.com/orioncactus/pretendard 의 `packages/pretendard-gov/dist/public/variable/`).
2. 이 폴더(`app/web/fonts/`)에 같은 이름으로 넣는다. 서버를 다시 켤 필요는 없다.
3. 파일이 없으면 화면은 맑은 고딕(Windows 기본)으로 보인다. 기능에는 영향이 없다.

글꼴 파일은 저장소에 넣지 않는다 (`.gitignore`의 `*.woff2`).

## 새 화면(edit-review)

`edit-review.html`은 Pretendard(일반판)를 쓴다. `PretendardVariable.woff2`를 이 폴더에 같은 이름으로 넣는다(npm `pretendard` 패키지의 `dist/web/variable/woff2/PretendardVariable.woff2`, SIL OFL). 없으면 맑은 고딕으로 보인다.
