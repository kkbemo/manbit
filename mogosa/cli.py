"""모의고사 변환기 명령줄 도구.

    python -m mogosa pdf2hwpx 기출.pdf -o 기출.hwpx
    python -m mogosa pdf2json 기출.pdf -o 기출.json      # 손보고 싶을 때
    python -m mogosa json2hwpx 출제.json -o 시험지.hwpx --answers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .hwpx_writer import Style, render_exam
from .model import Exam


def _style_from_args(args) -> Style:
    style = Style(columns=args.columns)
    if getattr(args, "font", None):
        style.body_font = args.font
    if getattr(args, "size", None):
        style.stem_size = args.size
        style.choice_size = args.size
    return style


def _report_problems(label: str, problems: list[str]) -> None:
    if not problems:
        return
    print(f"\n[{label}] 확인이 필요한 곳 {len(problems)}건", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def cmd_pdf2json(args) -> int:
    from .pdf_reader import parse_pdf

    exam, report = parse_pdf(args.input, columns=args.columns,
                             subject=args.subject, title=args.title)
    out = Path(args.output or Path(args.input).with_suffix(".json"))
    exam.to_json(out)
    print(f"{report.summary()} -> 문항 {len(exam.questions)}개")
    print(f"저장: {out}")
    _report_problems("파싱", report.warnings)
    return 0


def cmd_pdf2hwpx(args) -> int:
    from .pdf_reader import parse_pdf

    exam, report = parse_pdf(args.input, columns=args.columns,
                             subject=args.subject, title=args.title)
    out = Path(args.output or Path(args.input).with_suffix(".hwpx"))
    render_exam(exam, out, style=_style_from_args(args), include_answers=args.answers)

    print(f"{report.summary()} -> 문항 {len(exam.questions)}개")
    print(f"저장: {out}")
    if args.json:
        exam.to_json(args.json)
        print(f"중간 JSON: {args.json}")
    _report_problems("파싱", report.warnings)
    if report.warnings:
        print("\n한글에서 열어 확인한 뒤 고쳐 쓰십시오.", file=sys.stderr)
    return 0


def cmd_json2hwpx(args) -> int:
    exam = Exam.from_json(args.input)
    problems = exam.validate()
    out = Path(args.output or Path(args.input).with_suffix(".hwpx"))
    render_exam(exam, out, style=_style_from_args(args), include_answers=args.answers)
    print(f"문항 {len(exam.questions)}개 -> {out}")
    _report_problems("검증", problems)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mogosa",
        description="평가원 모의고사 PDF를 한글 파일로 바꾸고, 새 문항을 같은 형식으로 찍어낸다.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, *, with_render: bool = True):
        p.add_argument("input", help="입력 파일")
        p.add_argument("-o", "--output", help="출력 파일 (생략하면 입력과 같은 이름)")
        p.add_argument("--columns", type=int, default=2, help="단 수 (기본 2)")
        if with_render:
            p.add_argument("--answers", action="store_true", help="정답·해설을 뒤에 붙인다")
            p.add_argument("--font", help="본문 글꼴 (기본 함초롬바탕)")
            p.add_argument("--size", type=float, help="본문 크기 pt (기본 10)")

    p_pdf_json = sub.add_parser("pdf2json", help="PDF에서 문항을 뽑아 JSON으로 저장")
    add_common(p_pdf_json, with_render=False)
    p_pdf_json.add_argument("--subject", default="", help="과목명 (예: 생활과 윤리)")
    p_pdf_json.add_argument("--title", default="", help="시험명")
    p_pdf_json.set_defaults(func=cmd_pdf2json)

    p_pdf_hwpx = sub.add_parser("pdf2hwpx", help="PDF를 바로 한글 파일로 변환")
    add_common(p_pdf_hwpx)
    p_pdf_hwpx.add_argument("--subject", default="", help="과목명 (예: 생활과 윤리)")
    p_pdf_hwpx.add_argument("--title", default="", help="시험명")
    p_pdf_hwpx.add_argument("--json", help="중간 JSON도 함께 저장할 경로")
    p_pdf_hwpx.set_defaults(func=cmd_pdf2hwpx)

    p_json_hwpx = sub.add_parser("json2hwpx", help="문항 JSON을 시험지 한글 파일로 조판")
    add_common(p_json_hwpx)
    p_json_hwpx.set_defaults(func=cmd_json2hwpx)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
