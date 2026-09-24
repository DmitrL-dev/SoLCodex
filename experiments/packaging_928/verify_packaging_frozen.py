"""Private behavior verifier for the historical packaging SPDX incident."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve() / 'src'))
from packaging.licenses import canonicalize_license_expression, InvalidLicenseExpression
from packaging.metadata import Metadata, InvalidMetadata

cases = [
    ('nested-single', lambda: canonicalize_license_expression('((MIT))'), '((MIT))'),
    ('nested-whitespace', lambda: canonicalize_license_expression('(( MIT ))'), '((MIT))'),
    ('nested-and-or', lambda: canonicalize_license_expression('((MIT AND (Apache-2.0 OR BSD-2-Clause)))'), '((MIT AND (Apache-2.0 OR BSD-2-Clause)))'),
    ('nested-with', lambda: canonicalize_license_expression('((GPL-2.0-only WITH Classpath-exception-2.0))'), '((GPL-2.0-only WITH Classpath-exception-2.0))'),
    ('nested-ref', lambda: canonicalize_license_expression('((LicenseRef-Custom))'), '((LicenseRef-Custom))'),
    ('metadata-nested', lambda: Metadata.from_raw({'license_expression':'((MIT))'}, validate=False).license_expression, '((MIT))'),
    ('simple', lambda: canonicalize_license_expression('MIT'), 'MIT'),
    ('single-parens', lambda: canonicalize_license_expression('(MIT)'), '(MIT)'),
]
invalid = ['()', 'MIT Apache-2.0', '(MIT', 'MIT OR', 'Unknown-License', 'MIT (Apache-2.0)']
for index, expression in enumerate(invalid, 1):
    def expect_rejection(value=expression):
        try:
            canonicalize_license_expression(value)
        except InvalidLicenseExpression:
            return 'rejected'
        return 'accepted'
    cases.append((f'invalid-{index}', expect_rejection, 'rejected'))

passed = 0
for name, run, expected in cases:
    try:
        actual = run()
        ok = actual == expected
    except Exception as error:
        actual = type(error).__name__
        ok = False
    print(f'{name}: {"PASS" if ok else "FAIL"} ({actual})')
    passed += ok
print(f'TOTAL {passed}/{len(cases)}')
sys.exit(0 if passed == len(cases) else 1)
