"""Turn the report numbers into plotly charts and one self-contained HTML file."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import plotly.graph_objects as go
from jinja2 import Environment, PackageLoader, select_autoescape
from plotly.offline import get_plotlyjs

from gb_power_data.report import data
from gb_power_data.report.data import AGENTS, DAY_TYPES, PROMISED, ROLLING_DAYS, LedgerData

COLOURS = dict(zip(AGENTS.values(), ("#7f7f7f", "#1f77b4", "#d62728"), strict=True))
CHART_HEIGHT = "440px"


def _layout(fig: go.Figure, title: str, y_title: str, **yaxis) -> go.Figure:
    fig.update_layout(
        title=title,
        template="plotly_white",
        legend={"orientation": "h", "y": -0.15},
        margin={"t": 60, "b": 60, "l": 70, "r": 20},
        yaxis={"title": y_title, **yaxis},
    )
    return fig


def _in_agent_order(by_agent: dict) -> list:
    return [(agent, by_agent[agent]) for agent in AGENTS.values() if agent in by_agent]


def _promise_line(fig: go.Figure) -> None:
    fig.add_hline(
        y=PROMISED,
        line_dash="dash",
        line_color="black",
        annotation_text=f"promised {PROMISED:.0%}",
        annotation_position="bottom right",
    )


def coverage_by_day_type_chart(df) -> go.Figure:
    table = data.coverage_by_day_type(df)
    fig = go.Figure()
    for agent in AGENTS.values():
        rows = table.loc[agent]
        cov = [None if math.isnan(v) else v for v in rows["coverage"]]
        fig.add_bar(
            name=agent,
            x=list(DAY_TYPES),
            y=cov,
            marker_color=COLOURS[agent],
            text=[f"n={n}" for n in rows["claims"]],
            textposition="outside",
            hovertemplate="%{x}: %{y:.1%} (%{text})<extra>" + agent + "</extra>",
        )
    fig.update_layout(barmode="group")
    fig.update_xaxes(categoryorder="array", categoryarray=list(DAY_TYPES), title="day type")
    _promise_line(fig)
    return _layout(fig, "Coverage by day type", "coverage", tickformat=".0%", range=[0, 1.1])


def rolling_coverage_chart(df) -> go.Figure:
    fig = go.Figure()
    series = data.rolling_coverage(df)
    for agent, s in _in_agent_order(series):
        fig.add_scatter(
            name=agent,
            x=s.index,
            y=s.to_numpy(),
            mode="lines",
            line_color=COLOURS[agent],
            hovertemplate="%{x|%Y-%m-%d}: %{y:.1%}<extra>" + agent + "</extra>",
        )
    _promise_line(fig)
    return _layout(
        fig,
        f"Rolling coverage ({ROLLING_DAYS}-calendar-day window over target date)",
        "coverage",
        tickformat=".0%",
    )


def daily_score_chart(df) -> go.Figure:
    fig = go.Figure()
    series = data.daily_score(df)
    for agent, s in _in_agent_order(series):
        fig.add_scatter(
            name=agent,
            x=s.index,
            y=s.to_numpy(),
            mode="lines+markers",
            marker_size=4,
            line_width=1,
            line_color=COLOURS[agent],
            hovertemplate="%{x|%Y-%m-%d}: %{y:,.0f}<extra>" + agent + "</extra>",
        )
    return _layout(fig, "Daily interval score (log scale)", "interval score (GBP)", type="log")


def _gbp(value: float) -> str:
    return "—" if value is None or math.isnan(value) else f"{value:,.0f}"


def _pct(value: float) -> str:
    return "—" if value is None or math.isnan(value) else f"{value:.1%}"


def _fig_html(fig: go.Figure) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, default_height=CHART_HEIGHT)


def render(ledger: LedgerData, generated_at: datetime | None = None) -> str:
    df = ledger.claims
    env = Environment(
        loader=PackageLoader("gb_power_data.report"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["gbp"] = _gbp
    env.filters["pct"] = _pct
    template = env.get_template("report.html.j2")
    return template.render(
        plotly_js=get_plotlyjs(),
        start=ledger.start,
        end=ledger.end,
        promised=PROMISED,
        headline=data.headline(df),
        charts={
            "day_type": _fig_html(coverage_by_day_type_chart(df)),
            "rolling": _fig_html(rolling_coverage_chart(df)),
            "daily": _fig_html(daily_score_chart(df)),
        },
        top_days=data.top_days(df),
        agents=list(AGENTS.values()),
        ledger_path=ledger.path.resolve(),
        commits=data.code_versions(df),
        unresolved=ledger.unresolved,
        generated_at=(generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


def write_report(ledger: LedgerData, out: str | Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ledger), encoding="utf-8")
    return out
