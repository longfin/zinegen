# 실측 결과: Lightroom 렌디션 JPEG 적합성과 WeasyPrint PDF/X (2026-11-08)

## 1. Lightroom Partner API 렌디션 실측

대상: Ricoh GR IV DNG(25MP) 2장, 앨범 "20261107 단풍을찌것습니다", lr-mcp `lr_download_photos`로 수신.

| 항목 | fullsize | 2560 | 2048 |
|---|---|---|---|
| 픽셀 | 6192×4128 (원본 동일) | 2560×1706 | 2048×1365 |
| 용량 | 5.0MB | 0.8MB | 0.6MB |
| 비트 | 8bit | 8bit | 8bit |
| ICC | Display P3 임베드 | Display P3 | Display P3 |
| JPEG 품질 | 92 | 92 | 92 |
| 크로마 서브샘플링 | 없음(4:4:4) | 없음 | 없음 |
| 해상도 태그 | 300dpi | - | - |
| 생성 엔진 | Adobe Imagecore (Linux) | | |
| 메타데이터 | 제목/캡션/저작자 없음. EXIF 대부분 제거 | | |

판정: 출판 입고용으로 충분하다.
- 8bit/채널은 인쇄 RIP 입력의 업계 표준. 16bit 이점은 추가 보정 여지뿐이며, 보정은 Lightroom에서 끝내고 다시 받으면 된다.
- 품질 92 + 4:4:4는 시각적으로 무손실 수준. 재압축만 피하면 된다.
- Display P3는 sRGB보다 넓어 코티드 CMYK 색역 대부분을 담는다. 변환 시 임베드 ICC를 LittleCMS가 그대로 사용했다.
- 단점: 캡션·제목 메타데이터 없음(API로 따로 받아야 함), 출력 샤프닝 제어 불가, 생성 렌디션 1일 후 만료.

## 2. CMYK 변환 실측 (Pillow + LittleCMS)

Display P3 → 각 프로파일, 지각적 의도 + 흑점 보정. 풀해상도 변환 0.5초.

| 목적 프로파일 | 색역 밖 픽셀(상대색도 기준, 1/8 축소 샘플) |
|---|---|
| Japan Color 2001 Coated | 약 28% |
| Coated FOGRA39 | 약 25% |
| US Web Coated SWOP | 약 31% |

이 사진은 채도 +28, 포지티브 필름 프로파일의 단풍 사진이라 색역 밖 비율이 높다. JPEG 문제가 아니라 CMYK 색역의 한계이므로 소프트프루프(CMYK→sRGB 시뮬레이션)를 에이전트가 만들어 보여주고 Lightroom에서 조정하는 루프가 필요하다.

산출 CMYK JPEG(품질 95, 4:4:4): 약 22MB/장. CMYK TIFF LZW: 85~92MB/장.

## 3. WeasyPrint PDF/X 실측

환경: WeasyPrint 최신(uv), macOS에서 `brew install pango` 필요, `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
입력: A5(148×210) 2페이지 HTML, `@page { bleed: 3mm; marks: crop cross }`, `@color-profile --press { src: url(JapanColor2001Coated.icc); components: c,m,y,k }`, `output_intent="--press"`, CMYK JPEG 전면 배치 + 한글 캡션.

| 검사 항목 | PDF/X-1a | PDF/X-4 |
|---|---|---|
| PDF 버전 | 1.4 | 1.6 |
| OutputIntent GTS_PDFX + DestOutputProfile | ✓ Japan Color 2001 Coated | ✓ |
| GTS_PDFXVersion | PDF/X-1a:2003 (Info + XMP) | PDF/X-4 (XMP) |
| TrimBox / BleedBox | 419.5×595.3pt / +8.5pt(3mm) | 동일 |
| 이미지 | DCTDecode, DeviceCMYK, 6192×4128 재압축 없음 | 동일 |
| 서체 | Apple SD Gothic Neo 서브셋 임베드 | 동일 |
| 투명도/ExtGState alpha | 없음 | 없음 |
| 텍스트·괘선 색 | device-cmyk → DeviceCMYK 연산자 | 동일 |
| 재단선 | 렌더 확인됨 | 동일 |

주의: 미사용 `/Lab`, `/ICCBased` ColorSpace 리소스가 페이지 리소스에 남는다. 엄격한 X-1a 프리플라이트에서 걸릴 수 있으니 pikepdf 후처리로 제거하거나 Acrobat 프리플라이트로 확인 필요. 공식 PDF/X 검증 도구(Acrobat Pro Preflight, callas)로의 최종 검증은 아직 안 했다.

재현: `probe/cmyk_probe.py`, `probe/page.html`.
