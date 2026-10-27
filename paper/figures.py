"""Figures for the paper, drawn only from the newest runs/*-findings.json.

    .venv/bin/python paper/figures.py

Writes paper/figures/*.pdf (for typesetting) and *.png (for preview).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "figures"
BLUE, ORANGE = "#2a78d6", "#eb6834"  # dataviz reference palette, slots 1 and 2
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
THOUSANDS = FuncFormatter(lambda v, _: f"{v:,.0f}")

plt.rcParams.update(
    {
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 8,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": MUTED,
        "axes.titlesize": 8.5,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": INK,
        "legend.frameon": False,
    }
)


def _findings() -> tuple[str, dict]:
    path = sorted((ROOT / "runs").glob("*-findings.json"))[-1]
    return path.name, json.loads(path.read_text())["report"]


def _save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # No creation date in the PDF, so the same findings give the same bytes.
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", metadata={"CreationDate": None})
    fig.savefig(OUT / f"{name}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def snapshots(rep: dict) -> None:
    """Four questions about a fixed past, asked of every snapshot."""
    series = rep["as_of"]["series"]
    xs = [date.fromisoformat(r["snapshot_date"]) for r in series]
    panels = [
        ("Retractions dated 2023 or earlier", "retractions_through_2023"),
        ('Of those, citing "Paper Mill"', "paper_mill"),
        ('Of those, citing "Fake Peer Review"', "fake_peer_review"),
        ("Median days, publication to retraction", "median_lag_days"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(6.5, 4.0), sharex=True)
    for ax, (title, key) in zip(axes.flat, panels, strict=True):
        ys = [r[key] for r in series]
        ax.plot(xs, ys, color=BLUE, linewidth=1.6, solid_capstyle="round")
        ax.scatter(
            [xs[0], xs[-1]],
            [ys[0], ys[-1]],
            s=14,
            color=BLUE,
            zorder=3,
            edgecolor="white",
            linewidth=1,
        )
        # First value below and right of its point, last value above it, clear of the line.
        for x, y, ha, off in ((xs[0], ys[0], "left", (4, -11)), (xs[-1], ys[-1], "right", (0, 6))):
            ax.annotate(
                f"{y:,.0f}",
                (x, y),
                xytext=off,
                textcoords="offset points",
                ha=ha,
                fontsize=7.5,
            )
        ax.set_title(title)
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.18 or 1
        ax.set_ylim(max(lo - pad * 1.4, 0), hi + pad * 1.6)
        ax.yaxis.set_major_formatter(THOUSANDS)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.supxlabel("Retraction Watch snapshot", fontsize=8, color=MUTED)
    fig.tight_layout()
    _save(fig, "fig1_same_question")


def late_records(rep: dict) -> None:
    """When the records added after the first snapshot first appeared."""
    late = rep["as_of"]["late_records"]["by_month_first_seen"]
    xs = [date.fromisoformat(r["month"] + "-01") for r in late]
    ys = [r["records"] for r in late]
    fig, ax = plt.subplots(figsize=(6.5, 2.2))
    ax.bar(xs, ys, width=22, color=BLUE, edgecolor="white", linewidth=0.8)
    ax.set_ylabel("Records first seen")
    ax.yaxis.set_major_formatter(THOUSANDS)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.grid(axis="x", visible=False)
    ax.set_title("Records dated 2023 or earlier that were added after 2024-09-02")
    _save(fig, "fig2_late_records")


def lag_by_year(rep: dict) -> None:
    """Median lag per notice year, with and without the year's largest publisher."""
    rows = rep["retraction_lag"]["by_year_top_publisher"]
    years = [r["retraction_year"] for r in rows]
    allv = [r["median_days"] for r in rep["retraction_lag"]["by_retraction_year"]]
    without = [r["median_days_without_top"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    ax.plot(years, allv, color=BLUE, linewidth=1.6, marker="o", markersize=3.2, label="All notices")
    ax.plot(
        years,
        without,
        color=ORANGE,
        linewidth=1.6,
        marker="o",
        markersize=3.2,
        label="Without that year's largest publisher",
    )
    i = years.index(2011)
    ax.annotate(
        "IEEE conference batches",
        (years[i], allv[i]),
        xytext=(8, -2),
        textcoords="offset points",
        fontsize=7.5,
        color=MUTED,
    )
    j = years.index(2023)
    ax.annotate(
        "Hindawi",
        (years[j], allv[j]),
        xytext=(0, -14),
        textcoords="offset points",
        fontsize=7.5,
        color=MUTED,
        ha="center",
    )
    ax.set_ylabel("Median days to retraction")
    ax.set_xticks(years[::3])
    ax.yaxis.set_major_formatter(THOUSANDS)
    ax.legend(loc="upper left", fontsize=7.5)
    _save(fig, "fig3_lag_by_year")


if __name__ == "__main__":
    name, rep = _findings()
    snapshots(rep)
    late_records(rep)
    lag_by_year(rep)
    print(f"figures from runs/{name} in {OUT.relative_to(ROOT)}")
