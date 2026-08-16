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


def _load_standards(path: str | None):
    if not path:
        return None
    from .standards import StandardSet

    standards = StandardSet.load(path)
    if not len(standards):
        print(f"경고: {path} 에서 성취기준을 하나도 읽지 못했습니다.", file=sys.stderr)
        return None
    return standards


def _apply_standards(exam, standards, *, verbose: bool = True) -> None:
    """성취기준을 붙이고 검토가 필요한 문항을 알린다."""
    if standards is None:
        return
    results = standards.tag_exam(exam)
    mapped = sum(1 for q in exam.questions if q.standard)
    print(f"성취기준 판정: {mapped}/{len(exam.questions)}문항 (기준 {len(standards)}개)")

    if not verbose:
        return

    unmapped = exam.unmapped_questions()
    low = exam.low_confidence_questions()
    if not unmapped and not low:
        return

    print("\n[성취기준] 사람이 확인해야 할 문항", file=sys.stderr)
    by_number = {r.number: r for r in results}
    for number in sorted(set(unmapped) | set(low)):
        result = by_number.get(number)
        state = "미판정" if number in unmapped else "확신 약함"
        print(f"  - {number}번 ({state})", file=sys.stderr)
        if result:
            for candidate in result.candidates[:3]:
                standard = standards.get(candidate.code)
                summary = standard.short(30) if standard else ""
                hits = ", ".join(candidate.matched[:5])
                print(
                    f"      후보 {candidate.code} (점수 {candidate.score:.2f}) {summary}"
                    + (f"  ← {hits}" if hits else ""),
                    file=sys.stderr,
                )


def cmd_pdf2json(args) -> int:
    from .pdf_reader import parse_pdf

    exam, report = parse_pdf(args.input, columns=args.columns,
                             subject=args.subject, title=args.title)
    _apply_standards(exam, _load_standards(args.standards))
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
    standards = _load_standards(args.standards)
    _apply_standards(exam, standards)

    out = Path(args.output or Path(args.input).with_suffix(".hwpx"))
    render_exam(exam, out, style=_style_from_args(args),
                include_answers=args.answers,
                include_spec_table=args.spec_table,
                standards=standards)

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
    standards = _load_standards(args.standards)
    _apply_standards(exam, standards)

    problems = exam.validate()
    out = Path(args.output or Path(args.input).with_suffix(".hwpx"))
    render_exam(exam, out, style=_style_from_args(args),
                include_answers=args.answers,
                include_spec_table=args.spec_table,
                standards=standards)
    print(f"문항 {len(exam.questions)}개 -> {out}")
    _report_problems("검증", problems)
    return 0


def cmd_analyze(args) -> int:
    """문항에 성취기준만 붙이고 결과를 보고한다. 조판은 하지 않는다."""
    exam = Exam.from_json(args.input)
    standards = _load_standards(args.standards)
    if standards is None:
        print("성취기준 파일이 필요합니다: --standards <파일>", file=sys.stderr)
        return 2

    results = standards.tag_exam(exam, overwrite=args.overwrite)
    out = Path(args.output or args.input)
    exam.to_json(out)

    print(f"\n{'문항':>4}  {'성취기준':<14} {'확신':>5}  근거")
    print("-" * 68)
    by_number = {r.number: r for r in results}
    for question in exam.questions:
        result = by_number.get(question.number)
        hits = ", ".join(result.best.matched[:4]) if result and result.best else ""
        code = question.standard or "(미판정)"
        confidence = f"{(question.standard_confidence or 0):.2f}"
        print(f"{question.number:>4}  {code:<14} {confidence:>5}  {hits}")

    from .standards import coverage

    print("\n성취기준별 출제 분포")
    for code, numbers in coverage(exam, standards).items():
        marker = "  " if numbers else "! "
        listing = ", ".join(f"{n}번" for n in numbers) if numbers else "출제 없음"
        print(f"{marker}{code:<14} {listing}")

    print(f"\n저장: {out}")
    unmapped = exam.unmapped_questions()
    if unmapped:
        print(f"미판정 {len(unmapped)}문항: {', '.join(str(n) for n in unmapped)}", file=sys.stderr)
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
        p.add_argument(
            "--standards",
            help="성취기준 파일 (.json 또는 한 줄에 하나씩 붙여넣은 .txt)",
        )
        if with_render:
            p.add_argument("--answers", action="store_true", help="정답·해설을 뒤에 붙인다")
            p.add_argument("--spec-table", action="store_true",
                           help="이원목적표(문항 정보표)를 뒤에 붙인다")
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

    p_analyze = sub.add_parser(
        "analyze", help="문항에 성취기준을 붙이고 출제 분포를 보고 (조판 없음)"
    )
    p_analyze.add_argument("input", help="문항 JSON")
    p_analyze.add_argument("-o", "--output", help="저장 경로 (생략하면 입력 파일에 덮어씀)")
    p_analyze.add_argument("--standards", required=True, help="성취기준 파일")
    p_analyze.add_argument("--overwrite", action="store_true",
                           help="이미 붙어 있는 성취기준도 다시 판정한다")
    p_analyze.set_defaults(func=cmd_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
