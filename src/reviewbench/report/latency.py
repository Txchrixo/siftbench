"""Offline plot of observed publication latency, one dot per review."""

from collections import defaultdict
from html import escape
from math import ceil, floor, log10
import re
from statistics import median
from typing import NamedTuple

from ..analysis import _elapsed
from ..stats import median_interval
from .info import info
from . import _case_context, _case_title, _link, _pull_number, _reviewer, _reviewer_display


_LEFT, _RIGHT = 10, 550


class Sample(NamedTuple):
    """One observed review: its elapsed time, what it reviewed and, when shareable, where to follow it."""
    seconds: float
    title: str
    context: str = ""
    href: str | None = None


def _tick_step(maximum: float) -> float:
    target = max(1, maximum / 4)
    magnitude = 10 ** floor(log10(target))
    return next(factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= target)


def _plot_row(samples: list[Sample], limit: float, ticks: list[float]) -> str:
    def x(seconds):
        return _LEFT + (_RIGHT - _LEFT) * seconds / limit

    lanes: list[float] = []
    positions = []
    for sample in samples:
        position = x(sample.seconds)
        lane = next((index for index, last in enumerate(lanes) if position - last >= 13), len(lanes))
        if lane == len(lanes):
            lanes.append(position)
        else:
            lanes[lane] = position
        positions.append((sample, lane))
    height = max(34, 22 + len(lanes) * 13)
    grid = ''.join(f'<line class="latency-gridline" x1="{x(tick):g}" x2="{x(tick):g}" y1="4" y2="{height - 4}"/>'
                   for tick in ticks)
    middle = x(median(sample.seconds for sample in samples))
    marks = [f'<line class="latency-median" x1="{middle:g}" x2="{middle:g}" y1="4" y2="{height - 4}"/>']
    for sample, lane in positions:
        description = " · ".join(filter(None, (sample.title, sample.context, f"{sample.seconds:g} seconds")))
        # A point without a destination stays focusable so keyboard users still get its tooltip.
        tag, target = ("a", f'href="{escape(sample.href, quote=True)}"') if sample.href else ("g", 'tabindex="0" role="img"')
        marks.append(f'<{tag} class="latency-point" {target} aria-label="{escape(description, quote=True)}" '
                     f'data-tip-title="{escape(sample.title, quote=True)}" data-tip-context="{escape(sample.context, quote=True)}" '
                     f'data-tip-value="{sample.seconds:g} seconds">'
                     f'<circle class="latency-hit" cx="{x(sample.seconds):g}" cy="{17 + lane * 13}" r="10"/>'
                     f'<circle class="latency-dot" cx="{x(sample.seconds):g}" cy="{17 + lane * 13}" r="4.5"/></{tag}>')
    return (f'<svg class="latency-track" viewBox="0 0 560 {height}" aria-label="Individual review times">'
            f'{grid}{"".join(marks)}</svg>')


def latency_figure(by_reviewer: dict[str, list[Sample]], *, explanation: str, link_hint: str, note: str = "") -> str:
    """The dot plot, its axis and, when the samples link somewhere, the list of every plotted review.

    `explanation` is an info key.

    Ties keep their input order, so callers pass samples in a stable order."""
    if not by_reviewer:
        return ""

    maximum = max(sample.seconds for samples in by_reviewer.values() for sample in samples)
    step = _tick_step(maximum)
    intervals = max(1, ceil(maximum / step))
    ticks = [step * index for index in range(intervals + 1)]
    axis = "".join(f'<li>{tick:g}</li>' for tick in ticks)
    html = ['<figure class="latency-figure" aria-labelledby="latency-title"><figcaption>'
            '<h3><span id="latency-title">Review publication times</span>' + info(explanation) + '</h3>'
            f'<p class="subtle">Each dot is an observed review. {escape(link_hint)}</p>'
            '<p class="latency-key subtle"><span aria-hidden="true"></span>Median</p></figcaption>']
    reviewers = sorted(by_reviewer.items())
    for provider, samples in reviewers:
        samples.sort(key=lambda sample: sample.seconds)
        seconds = [sample.seconds for sample in samples]
        interval = median_interval(seconds)
        spread = f'<span class="subtle latency-interval">Likely {interval[0]:g}-{interval[1]:g} s</span>' if interval else ""
        html.append(f'<div class="latency-row"><div class="latency-reviewer">{_reviewer_display(provider)}'
                    f'<span class="subtle">{median(seconds):g} s median · n={len(samples)}</span>{spread}</div>'
                    f'{_plot_row(samples, ticks[-1], ticks)}</div>')
    html.extend(('<div class="latency-row latency-axis"><span class="subtle">Elapsed seconds</span>'
                 f'<ol class="latency-ticks subtle" aria-hidden="true">{axis}</ol></div>',
                 '<p class="latency-context subtle">Measured from the recorded review start to the verified GitHub review submission '
                 f'or completed check. Comments can appear before or after this signal.{note}</p>'))
    if any(sample.href for _, samples in reviewers for sample in samples):
        html.append('<details class="analysis-disclosure latency-data"><summary>Review times and sources</summary><ul>')
        for _, samples in reviewers:
            for sample in samples:
                title = (f'<a href="{escape(sample.href, quote=True)}">{escape(sample.title)}</a>' if sample.href
                         else f'<strong>{escape(sample.title)}</strong>')
                html.append(f'<li><div>{title}<small class="subtle">{escape(sample.context)}</small></div>'
                            f'<span class="latency-duration">{sample.seconds:g} s</span></li>')
        html.append('</ul></details>')
    return "".join(html) + '<div class="latency-tip" role="presentation" hidden></div></figure>'


def _evidence_link(pair: dict, fixture: bool) -> str:
    if not fixture:
        if check := _link(pair.get("capture_check_url")) or _link((pair.get("publication") or {}).get("check_url")):
            return check
        if pr := _link(pair.get("pr_url")):
            if signal := re.fullmatch(r"review:([0-9]+)", pair.get("capture_signal") or ""):
                return f'{pr.split("#", 1)[0]}#pullrequestreview-{signal[1]}'
            return pr
    return f'#pair-{pair["pair_id"]}'


def _source_labels(pair: dict, fixture: bool) -> tuple[str, str]:
    title = _case_title(pair)
    context = [_reviewer(pair["provider_id"])]
    if not fixture and (number := _pull_number(pair.get("pr_url"))):
        context.append(f"PR #{number}")
    context.append(_case_context(pair, title))
    return title, " · ".join(context)


def publication_latency(bundle: dict) -> str:
    fixture = bundle["fixture"]
    by_provider = defaultdict(list)
    for pair in sorted(bundle["pairs"], key=lambda pair: pair["pair_id"]):
        if pair["status"] == "completed":
            timing = pair.get("timing", {})
            if (duration := _elapsed(timing.get("review_started_at"), timing.get("published_at"))) is not None:
                by_provider[pair["provider_id"]].append(Sample(duration, *_source_labels(pair, fixture),
                                                               _evidence_link(pair, fixture)))
    return latency_figure(by_provider, explanation="latency",
                          link_hint="Select it to open the original evidence.",
                          note=" The source campaigns ran separately." if len(bundle.get("source_campaigns", [])) > 1 else "")
