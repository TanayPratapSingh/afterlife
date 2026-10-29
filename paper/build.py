"""Typeset paper/draft.md as paper/paper.pdf with tectonic.

    .venv/bin/python paper/build.py

The draft stays the single source. This converts the small Markdown subset it
uses (headings, paragraphs, lists, pipe tables, figures, bold, italic, code,
bare URLs) to LaTeX. Unfinished items in brackets (AUDIT, VERIFY, PENDING) are
printed in red so a preview cannot pass for a finished paper.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{ebgaramond}
\usepackage{microtype}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage{xurl}
\usepackage[hidelinks]{hyperref}
\urlstyle{same}
\usepackage{caption}
\captionsetup{font=small,labelformat=empty}
\setlist{itemsep=2pt,topsep=4pt}
\setlength{\parskip}{4pt}
\setlength{\parindent}{0pt}
\renewcommand{\arraystretch}{1.15}
\newcommand{\todo}[1]{\textcolor{red!75!black}{#1}}
"""

_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\^{}",
}


def _escape(s: str) -> str:
    return "".join(_SPECIAL.get(c, c) for c in s)


def inline(text: str) -> str:
    """One line or paragraph of Markdown to LaTeX."""
    keep: list[str] = []

    def stash(latex: str) -> str:
        keep.append(latex)
        return f"\x00{len(keep) - 1}\x00"

    def code(m: re.Match) -> str:
        s = m.group(1)
        # Paths and identifiers with no spaces may break anywhere.
        if " " not in s and "{" not in s:
            return stash(rf"\nolinkurl{{{s}}}")
        return stash(rf"\texttt{{{_escape(s)}}}")

    def url(m: re.Match) -> str:
        u, tail = m.group(1), ""
        while u and u[-1] in ".,;)":
            u, tail = u[:-1], u[-1] + tail
        return stash(rf"\url{{{u}}}") + tail

    text = re.sub(r"`([^`]+)`", code, text)
    text = re.sub(r"(https?://\S+)", url, text)
    text = _escape(text)
    text = re.sub(r"(?<![\w-])-(?=\d)", "\u2212", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", text)
    text = re.sub(r'"(.+?)"', "\u201c\\1\u201d", text)
    text = re.sub(r"\[((?:AUDIT|VERIFY|PENDING)[^\]]*)\]", r"\\todo{[\1]}", text)
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], text)


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, rule, body = cells[0], cells[1], cells[2:]
    left = ">{\\raggedright\\arraybackslash}X"
    spec = "".join("r" if c.endswith(":") else left for c in rule)
    out = [
        # Lining, tabular figures so columns of numbers line up.
        r"\begin{center}\small\addfontfeatures{Numbers={Lining,Monospaced}}",
        rf"\begin{{tabularx}}{{\linewidth}}{{{spec}}}",
        r"\toprule",
    ]
    out.append(" & ".join(inline(c) for c in head) + r" \\ \midrule")
    out += [" & ".join(inline(c) for c in r) + r" \\" for r in body]
    out += [r"\bottomrule", r"\end{tabularx}", r"\end{center}"]
    return "\n".join(out)


def _list(lines: list[str]) -> str:
    env = "enumerate" if re.match(r"\d+\.\s", lines[0]) else "itemize"
    items: list[str] = []
    for line in lines:
        if re.match(r"(\d+\.|-)\s", line):
            items.append(re.sub(r"^(\d+\.|-)\s+", "", line))
        else:
            items[-1] += " " + line.strip()
    body = "\n".join(rf"\item {inline(i)}" for i in items)
    return f"\\begin{{{env}}}\n{body}\n\\end{{{env}}}"


def convert(md: str, lines: bool = False) -> str:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", md) if b.strip()]
    title_block = blocks.pop(0).splitlines()
    title, authors = title_block[0].lstrip("# "), title_block[1:]
    if not authors and not blocks[0].startswith("#"):
        authors = blocks.pop(0).splitlines()
    out = [
        # QSS suggests line numbers on submitted manuscripts.
        PREAMBLE
        + ("\\usepackage{lineno}\n\\linenumbers\n" if lines else "")
        + "\\begin{document}\n",
        rf"\title{{{inline(title)}}}",
        r"\author{" + r" \\ ".join(inline(a) for a in authors) + "}",
        r"\date{}",
        r"\maketitle",
    ]
    in_abstract = False
    i = 0
    while i < len(blocks):
        b = blocks[i]
        first = b.splitlines()[0]
        if first.startswith("## "):
            if in_abstract:
                out.append(r"\end{abstract}")
                in_abstract = False
            name = first[3:]
            if name == "Abstract":
                out.append(r"\begin{abstract}")
                in_abstract = True
            else:
                out.append(rf"\section*{{{inline(name)}}}")
        elif first.startswith("### "):
            out.append(rf"\subsection*{{{inline(first[4:])}}}")
        elif b.startswith("!["):
            path = re.match(r"!\[[^\]]*\]\(([^)]+)\)", b).group(1)
            caption = ""
            if i + 1 < len(blocks) and blocks[i + 1].startswith("**Figure"):
                i += 1
                caption = rf"\caption{{{inline(blocks[i])}}}"
            out.append(
                r"\begin{figure}[ht]\centering"
                rf"\includegraphics[width=\linewidth]{{{(HERE / path).as_posix()}}}"
                f"{caption}\\end{{figure}}"
            )
        elif first.startswith("|"):
            out.append(_table(b.splitlines()))
        elif re.match(r"(\d+\.|-)\s", first):
            out.append(_list(b.splitlines()))
        else:
            out.append(inline(" ".join(line.strip() for line in b.splitlines())))
        i += 1
    if in_abstract:
        out.append(r"\end{abstract}")
    out.append(r"\end{document}")
    return "\n\n".join(out) + "\n"


def _selfcheck() -> None:
    assert inline("9.1% & `a_b/c.json`") == r"9.1\% \& \nolinkurl{a_b/c.json}"
    assert inline('**Bold** and *d* "q"') == "\\textbf{Bold} and \\emph{d} \u201cq\u201d"
    assert inline("see https://x.org/a_b.") == r"see \url{https://x.org/a_b}."
    assert inline("[AUDIT] left") == r"\todo{[AUDIT]} left"
    assert inline("-100% on 2024-09-02") == "\u2212100\\% on 2024-09-02"
    assert r"\arraybackslash}Xr}" in _table(["| a | b |", "| --- | ---: |", "| x | 1 |"])
    assert _list(["1. one", "   more", "2. two"]).count(r"\item") == 2


if __name__ == "__main__":
    import sys

    _selfcheck()
    qss = "--qss" in sys.argv  # line numbered copy for journal submission
    name = "paper-qss" if qss else "paper"
    BUILD.mkdir(exist_ok=True)
    tex = BUILD / f"{name}.tex"
    tex.write_text(convert((HERE / "draft.md").read_text(), lines=qss))
    subprocess.run(["tectonic", "--keep-logs", str(tex)], check=True, cwd=BUILD)
    shutil.copy(BUILD / f"{name}.pdf", HERE / f"{name}.pdf")
    print(f"wrote {(HERE / f'{name}.pdf').relative_to(HERE.parent)}")
