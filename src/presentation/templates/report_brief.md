<!-- 요약본 템플릿.

     같은 데이터로 짧은 리포트를 냅니다. 지표 표와 판정 근거를 빼고
     전체 요약과 섹션 한 줄 서술만 남깁니다. 메일 본문이나 모바일에서
     보기 좋은 형태입니다.

     쓰려면 config에 한 줄만 넣으면 됩니다:
         "report": { "template": "report_brief.md" }

     judgements / judgement_item 블록을 아예 정의하지 않으면 렌더러가
     판정 근거를 통째로 생략합니다.

     application 계층은 이 파일의 존재를 모릅니다. 노드는 ReportSection
     객체를 만들 뿐이고, 어떤 양식으로 찍을지는 presenter의 몫입니다.
-->

<!-- block: document -->
# ${gbm} / ${factory} 운영 요약

`${as_of}` 기준 · ${section_count}개 분석
${overall}
${sections}

<!-- block: overall -->

> **심각 ${critical} · 경고 ${warning}**
>
> ${narrative}
${top_issues}

<!-- block: section -->
- ${mark} **${title}**${degraded_mark} — ${narrative_inline}
