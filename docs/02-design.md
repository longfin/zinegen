# zinegen 설계 (v0, 2026-11-08)

## 결정 사항
- 출력: 오프셋(PDF/X-1a, CMYK 변환)과 POD(PDF/X-4, P3 JPEG 무손실 통과) 모두 지원
- 판형: 프리셋 + 임의 치수. 제본: 중철/무선/양장/레이플랫 모두(여백·거터 규칙으로 구분)
- 이미지 소스: lr-mcp 렌디션 JPEG(fullsize). 로컬 캐시
- 실행 형태: Python 패키지 하나 = CLI + MCP 서버 + 스킬. Claude Code와 Codex 양쪽에서 같은 MCP/스킬 사용
- Adobe 앱 비의존. lr-mcp 패키지는 라이브러리로 의존(인증·HTTP 재사용)
- lr-mcp에 없는 API(앨범 순서, 캡션, 캐시)는 `zinegen.lr.compat`에 호환 구현 후 업스트림(lr-mcp 이슈 #9 앨범 순서, #10 캡션·크롭, #11 캐시)
- 텍스트: 에이전트가 초안, 사용자는 북 스펙(YAML)을 직접 편집

## 파이프라인
```
album ──fetch──▶ cache/<asset>.jpg + meta.json
               ──init───▶ book.yaml (스펙 초안: 사진 순서 = 앨범 순서, 템플릿 자동 배정)
book.yaml ──render──▶ out/interior.pdf (PDF/X-1a 또는 X-4) + out/report.json
           ──proof───▶ proof/spread-NN.png (에이전트·사용자 확인용)
           ──check───▶ 박스/출력의도/색공간/서체/해상도 검사
```

## 북 스펙(book.yaml) 핵심
```yaml
title: 단풍
trim: { preset: A5 }            # 또는 { width_mm: 148, height_mm: 210 }
bleed_mm: 3
binding: perfect                # saddle | perfect | hardcover | layflat
paper: { caliper_um: 110 }      # 책등 계산용(선택)
margins_mm: { top: 14, bottom: 16, inside: 16, outside: 12 }
fonts: { body: "Nanum Myeongjo", caption: "Nanum Gothic" }
output: { standard: pdf/x-1a, icc: /path/JapanColor2001Coated.icc, intent: perceptual }
source: { album_id: ..., size: fullsize }
pages:
  - { template: title, title: 단풍, subtitle: 2026 }
  - { template: blank }
  - { template: full-bleed, photo: <asset>, focal: [0.5, 0.4] }
  - { template: margin, photo: <asset>, caption: "..." }
  - { template: spread-full, photo: <asset> }       # 2페이지 점유, 왼쪽 페이지에서 시작
  - { template: text, text: "..." }
  - { template: colophon, text: "..." }
```

템플릿: title, blank, text, full-bleed, margin, pair, spread-full, spread-margin, colophon.
페이지 번호는 1부터, 홀수 = 오른쪽(recto). 스프레드 템플릿은 왼쪽(짝수) 페이지에서 시작해야 하며, 아니면 빈 페이지를 자동 삽입.

## 색 처리
- X-1a: 캐시 JPEG(P3 ICC) → LittleCMS 지각적+흑점보정 → CMYK JPEG(q95, 4:4:4) 캐시(프로파일별). 텍스트/괘선은 device-cmyk
- X-4: 원본 P3 JPEG를 재압축 없이 임베드, 출력 의도는 선택한 CMYK 프로파일
- X-1a 후처리: 미사용 Lab/ICCBased 색공간 리소스 제거(pikepdf)

## 해상도 규칙
배치 크기 대비 유효 dpi를 report.json에 기록. 300dpi 미만 경고, 200 미만 오류.

## GUI(다음 단계)
로컬 웹 뷰어: proof PNG 스프레드 넘기기 + 코멘트(JSON) + 승인 표시. 편집 기능 없음.
