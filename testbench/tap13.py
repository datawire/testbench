import re
from typing import Any, Dict, List, Optional, TextIO, Tuple, cast

from .tap import TestCase, TestStatus, peek_line, trim_prefix


def parse(reader: TextIO) -> Tuple[Dict[int, TestCase], List[str]]:
    lineno = 0
    errs: List[str] = []

    def error(msg: str) -> None:
        errs.append("%s:%s: Invalid TAP13: %s" % (reader.name, lineno, msg))

    firstline = reader.readline().rstrip("\n")
    lineno += 1
    if firstline != "TAP version 13":
        error("First line must be %s" % repr("TAP version 13"))
        return ({}, errs)

    plan: Optional[int] = None
    tests: Dict[int, TestCase] = {}
    at_end = False
    prev_test = 0

    line = reader.readline().rstrip("\n")
    lineno += 1
    while line:
        if line.startswith("#"):
            pass
        elif at_end:
            error("Cannot have more output after trailing test plan")
            break
        elif line.startswith("1.."):
            if plan is not None:
                error("Test plan can only be given once")
                break
            strplan = trim_prefix(line, "1..")
            if not strplan.isdigit():
                error("Not an integer number of tests: %s" % repr(strplan))
                break
            if len(tests) > 0:
                at_end = True
            plan = int(strplan)
        elif re.match(r"^(not )?ok\b", line):
            m = cast(Dict[int, str], re.match(r"^(ok|not ok)\b\s*([0-9]+\b)?([^#]*)(#.*)?", line))
            #                                    1               2          3      4
            #
            # 1: status (required)
            # 2: test number (recommended)
            # 3: description (recommended)
            # 4: comment (when necessary)
            status = TestStatus.OK if m[1] == "ok" else TestStatus.NOT_OK
            test_number = int(m[2]) if m[2] is not None else (prev_test + 1)
            description = m[3]
            comment = m[4]

            # Parse directives
            if re.match(r"^# TODO( .*)?$", comment or "", flags=re.IGNORECASE):
                status = {
                    TestStatus.OK: TestStatus.TODO_OK,
                    TestStatus.NOT_OK: TestStatus.TODO_NOT_OK,
                }[status]
            if re.match(r"^# SKIP", comment or "", flags=re.IGNORECASE):
                status = TestStatus.SKIP

            yaml: Optional[Any] = None
            if re.match(r"^\s+---$", peek_line(reader).rstrip("\n")):
                yaml = ""
                for line in reader:
                    lineno += 1
                    line = line.rstrip("\n")
                    yaml += line + "\n"
                    if re.match(r"^\s+\.\.\.$", line):
                        break
                # Don't bother parsing the YAML; we'd have to pull in
                # something outside of the stdlib, and we don't intend
                # to do anytihng with it anyway.

            tests[test_number] = TestCase(
                status=status,
                n=test_number,
                description=description,
                comment=comment,
                yaml=yaml)
            prev_test = test_number
        elif line.startswith("Bail out!"):
            error(line)
            break
        else:
            error("Invalid line: %s" % repr(line))
            break
        line = reader.readline().rstrip("\n")
        lineno += 1

    if plan is not None:
        for i in range(1, plan+1):
            if i not in tests:
                tests[i] = TestCase(status=TestStatus.MISSING, n=i)
        if len(tests) > plan:
            error("More test results than test plan indicated, truncating: %d > %d" % (len(tests), plan))
            trunc: Dict[int, TestCase] = {}
            for i in range(1, plan+1):
                trunc[i] = tests[i]
            tests = trunc
    return tests, errs
