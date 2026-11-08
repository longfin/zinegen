# zinegen

Lightroom(cloud) 앨범을 AI 에이전트가 조판해 출판용 PDF/X(오프셋·POD)로 내보내는 도구.
CLI + MCP 서버 + 에이전트 스킬 한 패키지. Adobe 앱 의존 없음. 이미지는 [lr-mcp](https://github.com/longfin/lr-mcp)의 렌디션을 쓴다.

```
album ─fetch─▶ cache/<asset>.jpg ─┐
book.yaml (에이전트가 편집) ───────┴─render─▶ out/interior.pdf (PDF/X-1a | X-4) + report.json
                                            ─proof──▶ proof/spread-NN.png
                                            ─check──▶ out/check.json
```

## 설치

```bash
brew install pango ghostscript          # WeasyPrint 런타임, 프루프 렌더
uv tool install lr-mcp && lr-mcp doctor # Lightroom 인증 (README 참고)
uv tool install .                       # 또는 개발용: uv venv && uv pip install -e ".[dev]"
zinegen doctor
```

CMYK ICC 프로파일은 재배포가 안 되므로 로컬에 있는 것을 찾는다(Adobe 설치본, ColorSync). 없으면 `output.icc`에 경로를 적는다.
ECI(eci.org)의 ISO Coated v2 / PSO Coated v3는 무료로 받을 수 있다.

## 에이전트 등록

```bash
# Claude Code
claude mcp add zinegen -s user -- "$(which zinegen)" mcp
cp -r skills/zinegen ~/.claude/skills/zinegen
# Codex
codex mcp add zinegen -- "$(which zinegen)" mcp
cp -r skills/zinegen ~/.codex/skills/zinegen
```

## 사용

```bash
zinegen albums 단풍
zinegen init --album <album id> --dir books/danpung      # book.yaml 초안 + photos.json
$EDITOR books/danpung/book.yaml                           # 순서·템플릿·캡션
zinegen build books/danpung                               # fetch + render + proof + check
zinegen spine --pages 48 --caliper 110                    # 책등 두께
```

book.yaml 레퍼런스: `zinegen presets`, MCP `zg_spec_help`, `docs/02-design.md`.

## 문서

- `docs/00-research.md` 사전 조사
- `docs/01-probe-results.md` 렌디션 JPEG 실측, WeasyPrint PDF/X 실측
- `docs/02-design.md` 설계
