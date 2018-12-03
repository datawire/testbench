import html
import pkgutil
import sys
from typing import Dict, List, Optional

from .tap import TestCase, TestStatus
from .tap import parse as tap_parse

HEAD = pkgutil.get_data(__package__, 'head.html').decode('utf-8')
TAIL = pkgutil.get_data(__package__, 'tail.html').decode('utf-8')

# mypy doesn't like html.escape()
def html_escape(i: str, quote: bool) -> str:
    return html.escape(i, quote=quote)

erred = False

def print_cell(s: TestStatus) -> None:
    global erred
    classes, text, passed = {
        TestStatus.OK: ("ok", "✔", True),
        TestStatus.NOT_OK: ("not_ok", "✘", False),
        TestStatus.TODO_OK: ("todo_ok", "✔", True),
        TestStatus.TODO_NOT_OK: ("todo_not_ok", "✘", True),
        TestStatus.SKIP: ("skip", "-", True),
        TestStatus.MISSING: ("missing", "❗", False),
    }[s]
    if not passed:
        erred = True
    print('    <td class="%s">%s</td>' % (classes, text))


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: %s FILE_1.tap [FILE_2.tap...]" % sys.argv[0])

    filenames = sys.argv[1:]
    file_cases: Dict[str, Dict[int, TestCase]] = {}
    file_errs: Dict[str, List[str]] = {}
    for filename in filenames:
        with open(filename, mode="rt", encoding="utf-8") as file:
            file_cases[filename], file_errs[filename] = tap_parse(file)

    # Decide what we'll pretend the canonical list of testcase names is
    longest_len = max(len(cases) for cases in file_cases.values())
    for filename in filenames:
        if len(file_cases[filename]) == longest_len:
            break
    testcase_names: List[Optional[str]] = [file_cases[filename][i].description for i in range(1, longest_len+1)]
    # Check if everything agrees with that
    for filename in filenames:
        prepend: List[str] = []
        for i in range(1, longest_len+1):
            if i in file_cases[filename]:
                expected = testcase_names[i-1]
                actual = file_cases[filename][i].description
                if actual != expected:
                    prepend.append("%s: test %d: mismatched description: expected=%s actual=%s" % (filename, i, repr(expected), repr(actual)))
            else:
                file_cases[filename][i] = TestCase(status=TestStatus.MISSING, n=i)
                prepend.append("%s: test %d: missing" % (filename, i))
        if len(prepend) > 0:
            file_errs[filename] = prepend + file_errs[filename]

    # Now print everything
    print(HEAD)
    print("<table>")
    # The table header
    print("  <tr>")
    print("    <td></td>")
    for filename in filenames:
        print('    <th><div><a href="%s">%s</a></div></th>' % (
            html_escape(filename, quote=False),
            html_escape(filename, quote=True)))
    print("  </tr>")
    # Print whether there are problems with this TAP
    print("  <tr>")
    print("    <th>Tests suite ran</th>")
    for filename in filenames:
        ok = (
            (len(file_errs[filename]) == 0) and
            all(tc.status != TestStatus.MISSING for tc in file_cases[filename].values())
        )
        print_cell(TestStatus.OK if ok else TestStatus.NOT_OK)
    print("  </tr>")
    # Print the test suite status
    print("  <tr>")
    print("    <th>Tests suite passed</th>")
    for filename in filenames:
        ok = (
            (len(file_errs[filename]) == 0) and
            all(tc.status != TestStatus.MISSING and tc.status != TestStatus.NOT_OK for tc in file_cases[filename].values())
        )
        print_cell(TestStatus.OK if ok else TestStatus.NOT_OK)
    print("  </tr>")
    # Print each test case
    for i in range(1, longest_len+1):
        print("  <tr>")
        print("    <th>%d: %s</th>" % (i, html_escape(testcase_names[i-1] or "", quote=False)))
        for filename in filenames:
            print_cell(file_cases[filename][i].status)
        print("  </tr>")
    # End table
    print("</table>")
    # Display any errors
    if any(len(errs) > 0 for _, errs in file_errs.items()):
        print("<pre>")
        for filename in filenames:
            for err in file_errs[filename]:
                print(html_escape(err, quote=False))
        print("</pre>")
    # End document
    print(TAIL)
    print("<!-- exit: {} -->".format(1 if erred else 0))
