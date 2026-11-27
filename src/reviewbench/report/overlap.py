"""Detection combinations for the frozen references in a report's shared scoring scope."""

from collections import defaultdict
from html import escape

from . import _case_condition, _case_key, _case_title, _grading_pending, _reviewer


def reference_overlap(pairs: list[dict], samples: set, providers: list[str]) -> str:
    if len(providers) < 2:
        return ""

    by_sample = defaultdict(list)
    for pair in pairs:
        key = pair["case_id"], pair["condition"], pair["repeat_index"]
        if key in samples:
            by_sample[key].append(pair)

    groups = defaultdict(list)
    for sample in sorted(by_sample):
        attempts = by_sample[sample]
        if any(_grading_pending(pair) for pair in attempts):
            continue
        references = {finding["expected_id"]: finding["matching_criteria"]
                      for pair in attempts for finding in pair["expected"]}
        detected = defaultdict(set)
        for pair in attempts:
            for claim in pair["claims"]:
                if claim["decision"] == "confirmed_expected":
                    detected[claim["expected_id"]].add(pair["provider_id"])
        for expected_id, criterion in sorted(references.items()):
            combination = tuple(provider for provider in providers if provider in detected[expected_id])
            groups[combination].append((attempts[0], criterion))

    if not groups:
        return ""

    total = sum(len(references) for references in groups.values())
    maximum = max(map(len, groups.values()))
    scrollable = " is-scrollable" if len(groups) > 8 else ""
    html = ['<details class="additional-scores interval-details" id="reference-overlap">'
            '<summary id="overlap-title">Reference finding overlap</summary>'
            '<figure class="interval-figure" aria-labelledby="overlap-title"><figcaption>'
            f'<p class="subtle">{total} reference {"finding" if total == 1 else "findings"} across fully graded shared reviews. '
            'Each row shows an exact reviewer combination. Open it to see the cases.</p></figcaption>',
            f'<div class="overlap-rows{scrollable}">']
    for combination, references in sorted(groups.items(), key=lambda item: (-len(item[0]), -len(item[1]), item[0])):
        count = len(references)
        if not combination:
            label = "Detected by no reviewer"
        elif len(combination) == len(providers):
            label = f"All {len(providers)} reviewers"
        elif len(combination) == 1:
            label = f"{_reviewer(combination[0])} only"
        else:
            names = [_reviewer(provider) for provider in combination]
            label = f'{", ".join(names[:-1])} and {names[-1]}'
        row_class = "overlap-row" if combination else "overlap-row overlap-missed"
        html.append(f'<details class="{row_class}"><summary><span class="overlap-label">{escape(label)}</span>'
                    f'<meter min="0" max="{maximum}" value="{count}" aria-hidden="true"></meter>'
                    f'<span class="overlap-count">{count}<span class="visually-hidden"> of {total} reference findings</span></span>'
                    '</summary><ul class="overlap-cases">')
        for pair, criterion in sorted(references, key=lambda item: (_case_title(item[0]).casefold(), _case_key(item[0]), item[1])):
            destination = escape(_case_key(pair), quote=True)
            html.append(f'<li><a href="#problems-{destination}">{escape(_case_title(pair))}</a>'
                        f'<span class="subtle">{escape(_case_condition(pair))}</span>'
                        f'<p class="subtle">{escape(criterion)}</p></li>')
        html.append('</ul></details>')
    html.append('</div></figure></details>')
    return "".join(html)
