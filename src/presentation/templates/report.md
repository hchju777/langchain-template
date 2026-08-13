<!-- 기본 리포트 템플릿.

     문법
       <!-- block: 이름 -->   블록 구분 (이 줄만 제거되고 본문 여백은 보존됩니다)
       ${변수}                 치환 (파이썬 string.Template)

     각 블록은 자기 앞뒤 빈 줄을 스스로 들고 있습니다. 렌더러가 블록을
     그냥 이어붙이기 때문입니다. 비어버린 블록(요약 없음, 지표 없음)이
     남긴 여백은 마지막에 자동 정리됩니다.

     반복 블록(section, judgement_item)은 렌더러가 항목마다 한 번씩 채웁니다.
     서브그래프를 config에서 끄면 해당 섹션이 저절로 사라지므로 이 파일은
     손댈 필요가 없습니다. 다른 양식을 쓰려면 config의 report.template만
     바꾸세요 (예: report_brief.md).

     쓸 수 있는 변수
       document       : gbm factory as_of section_count overall sections
       overall        : critical warning normal degraded narrative top_issues
       section        : key title mark severity degraded_mark
                        narrative narrative_inline metrics judgements
       judgements     : items
       judgement_item : subject severity confidence reasoning evidence
-->

<!-- block: document -->
# 운영 상태 리포트 — ${gbm} / ${factory}

- 기준 시각: `${as_of}`
- 분석 항목: ${section_count}건
${overall}${sections}

<!-- block: overall -->

## 전체 요약

심각 ${critical}건 · 경고 ${warning}건 · 정상 ${normal}건

${narrative}
${top_issues}

<!-- block: section -->

## ${mark} ${title}${degraded_mark}

${narrative}

${metrics}

${judgements}

<!-- block: judgements -->
<details><summary>판정 근거</summary>

${items}
</details>

<!-- block: judgement_item -->
- **${subject}** (${severity}, 확신도 ${confidence})
  - ${reasoning}
  - 근거: `${evidence}`
