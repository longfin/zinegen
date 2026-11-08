# zinegen 사전 조사 (2026-11-08)

lr-mcp로 가져온 Lightroom 앨범을 바탕으로, AI 에이전트가 조판하고 출판용(인쇄 입고 가능) PDF를 만드는 앱의 사전 조사 메모.

## 1. lr-mcp가 지금 주는 것 / 못 주는 것

소스: github.com/longfin/lr-mcp `src/lr_mcp/server.py`, `client.py` (2026-11-08 기준 main)

제공:
- 앨범 목록/검색, 앨범 내 사진 필터(날짜, 시간대, 별점, 플래그, 카메라/렌즈/조리개/ISO/파일명)
- 사진 요약: id, 파일명, 촬영시각, 별점, 플래그, 카메라, 렌즈, 노출값, 원본 픽셀 크기(`importSource.originalWidth/Height`)
- 현상 설정 요약(XMP develop의 주요 crs:* 키, `HasCrop`, `CropAngle` 포함)
- 렌디션 JPEG: thumbnail2x / 640 / 1280 / 2048 / 2560 / fullsize. 2560·fullsize는 `X-Generate-Renditions`로 생성 요청 후 2초 간격 폴링(최대 60초)
- 프로젝트 앨범 생성/추가/삭제(서버가 만든 앨범만)

미제공(확장 후보):
- 앨범의 사용자 정렬 순서(custom order). 현재 `lr_list_photos`는 촬영시각 순으로 정렬
- 제목/캡션(xmp dc:title, dc:description), 키워드, 색 라벨, GPS/위치
- 크롭 적용 후 실제 가로세로비(원본 크기만 노출. 렌디션 JPEG 픽셀 크기를 직접 읽어야 함)
- 원본(RAW/마스터) 다운로드. Partner API에 원본 다운로드 엔드포인트가 없다는 Adobe 측 답변(2024-07)이 커뮤니티에 인용됨. PUT /master(업로드)만 존재
- 렌디션 색공간/ICC. 문서에서 확인 못 함. 실측 필요(exiftool로 ICC 확인)

운영 제약:
- 생성된 2560/fullsize 렌디션은 1일 후 자동 삭제 → 로컬 캐시 필수
- 2560/fullsize는 "basic API access에서는 불가"라는 문구가 검색 결과에 인용됨(lr-mcp는 이미 사용 중이므로 현재 자격으로 되는지 확인)
- 크레덴셜당 베타 사용자 25명 제한 → 타인에게 배포하는 제품이면 Adobe 심사 필요

## 2. 인쇄 입고 사양(일반)

- 재단 여백(도련) 사방 3mm. A4 완성 210×297 → 작업 216×303mm
- 이미지 300dpi(최종 인쇄 크기 기준)
- 국내 인쇄소 대부분 CMYK 요구. RGB 입고 시 색 변환은 인쇄소가 임의로 함
- PDF/X-1a: CMYK/별색만, 투명도 불가. PDF/X-4: ICC 태그된 RGB·투명도 허용. 해외 POD(Blurb 등)와 디지털 인쇄는 X-4 또는 일반 PDF 수용이 많음
- 무선제본 책등 = (총 페이지 ÷ 2) × 종이 1장 두께(mm). 평량(g)이 아닌 두께(㎛)가 필요. 인쇄소/제지사 자료 필요
- 표지는 보통 내지와 별도 PDF(펼침: 뒷표지+책등+앞표지+도련)
- 검증: PDF/X 프리플라이트는 Acrobat Pro 또는 callas pdfToolbox. veraPDF는 PDF/A·UA만

해상도 환산(300dpi):
| 렌디션 | 긴 변 최대 인쇄 길이 |
|---|---|
| 2048px | 173mm |
| 2560px | 217mm (A5 세로 216mm 도련 포함 딱 맞음) |
| 24MP 6000px | 508mm (A4 펼침 426mm 가능) |
| 12MP 4000px | 339mm (A4 펼침은 240dpi) |

## 3. 렌더링 엔진 후보 (PDF/X 지원 관점)

| 엔진 | PDF/X | 이미지 RGB→CMYK | 도련/재단선 | 비고 |
|---|---|---|---|---|
| Typst | 없음(PDF/A, PDF/UA만) | 없음 | 수동 | 빠름, 텍스트 DSL이라 에이전트 친화적. PDF/X는 Ghostscript 후처리 필요 |
| WeasyPrint | pdf/x-1a, x-3, x-4, x-5g + output intent | 불명확 | @page bleed/marks | Python(lr-mcp와 동일). "유효성 보장 안 함" 명시 |
| Prince | X-1a/X-3/X-4 공식, 오류 보고 | 자동 변환, 이미지 ICC 존중 | @page bleed/marks | 상용. 가장 확실 |
| Chromium/Paged.js | 없음 | 없음 | Paged.js로 marks/bleed | 미리보기용으로는 최적(HTML 그대로 GUI) |
| 직접 생성(pikepdf/pdf-lib) | 직접 구현 | Pillow+LittleCMS | 직접 | 자유도 최대, 공수 최대 |
| InDesign(설치됨, MCP 있음) | 네이티브 | 네이티브 | 네이티브 | Adobe 라이선스 의존 |
| Ghostscript 후처리 | -dPDFX + PDFX_def.ps | -sColorConversionStrategy=CMYK | - | 버전별 동작 차이·버그 보고 있음 |

로컬 확인: gs 10.05, ImageMagick 7.1, exiftool, uv, node 24, InDesign 2026, Lightroom(Classic 포함) 설치됨.

## 4. 아키텍처 초안(에이전트 중심)

1. 수집: lr-mcp로 선별 → 로컬 캐시(렌디션 + 메타 JSON)
2. 북 스펙(선언적 파일, git 친화): 판형/도련/그리드/서체/시퀀스/스프레드별 템플릿/이미지 배치(초점·크롭)/캡션
3. 결정적 렌더러: 스펙 → PDF(인쇄용) + 저해상 PNG 프루프(스프레드 단위)
4. 에이전트 루프: 프루프 이미지를 보고 비평 → 스펙 수정 → 재렌더
5. GUI: 스프레드 뷰어 + 코멘트/승인만. 편집 기능 없음(또는 최소)
6. 출고: 내지 PDF/X + 표지 PDF/X + 프리플라이트 리포트
