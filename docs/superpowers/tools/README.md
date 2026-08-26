# 문서 도구

검토 메모의 그림과 배포 파일을 만드는 스크립트입니다. 표준 라이브러리만 쓰고, PNG·PDF 변환에는 시스템에 설치된 Chrome을 씁니다.

## gen_diagrams.py — 그림 생성

`docs/images/`에 SVG 세 장을 만듭니다. 좌표를 손으로 맞추면 어긋나기 쉬워서 헬퍼 함수로 그립니다.

```bash
python3 docs/superpowers/tools/gen_diagrams.py

# SVG → PNG (마크다운에서 참조하는 쪽)
cd docs/images
for f in arch-current:1200:646 arch-target:1240:842 subgraph-process:1200:476; do
  n=${f%%:*}; r=${f#*:}
  google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --force-device-scale-factor=2 --default-background-color=ffffff \
    --window-size=${r%%:*},${r##*:} --screenshot="$n.png" "file://$PWD/$n.svg"
done
```

`--force-device-scale-factor=2`가 2배 해상도를 만듭니다. `--window-size`는 각 SVG의 `width`·`height`와 같아야 하며, SVG 크기를 바꾸면 이 값도 함께 바꿔야 합니다.

한글은 Noto Sans CJK KR로 렌더링됩니다. 폰트가 없는 환경에서는 글자가 깨지므로 `fc-list :lang=ko`로 먼저 확인하세요.

## build_dist.py — 배포 파일 생성

메모 마크다운에서 배포용 세 가지를 만듭니다.

```bash
python3 docs/superpowers/tools/build_dist.py

# HTML → PDF
cd docs/superpowers/specs
google-chrome --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="2026-08-26-llm-autonomy-review.pdf" \
  "file://$PWD/2026-08-26-llm-autonomy-review.html"
```

| 산출물 | 그림이 들어가는 방식 |
|---|---|
| `*.html` | SVG를 문서 안에 직접 삽입. 단일 파일이고 확대해도 안 깨짐 |
| `*.pdf` | 위 HTML을 A4로 출력. 그림이 벡터로 들어감 |
| `*.embedded.md` | PNG를 base64로 인라인. **GitHub에서는 안 보임** (`data:` URI가 제거됨) |

세 파일 모두 생성물이라 `.gitignore`에 있습니다. 저장소에는 마크다운 원본과 `docs/images/`만 들어갑니다.

내장 마크다운 변환기는 이 메모가 쓰는 문법(제목, 문단, 표, 목록, 인용, 굵게·기울임, 인라인 코드, 링크, 이미지, 구분선)만 다룹니다. 다른 문법을 쓰면 `render()`에 규칙을 추가해야 합니다.
