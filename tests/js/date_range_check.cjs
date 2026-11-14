// Checks the pure functions in static/date-range.js. Run by tests/test_date_range_js.py.
// The resolver must agree with date_range.py on every shared case.
const fs = require('fs');
const path = require('path');
const dr = require('../../static/date-range.js');

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'date_range_cases.json'), 'utf8'));
let failures = 0;

function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    failures += 1;
    console.error(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
  }
}

const iso = (d) => dr.toIso(d);
const day = (y, m, d) => new Date(y, m - 1, d);
const today = day(2026, 11, 13);

cases.forEach((c) => {
  const [s, e] = dr.resolveRule(c.rule, dr.parseIso(c.today));
  check(`resolveRule ${JSON.stringify(c.rule)} on ${c.today}`, [iso(s), iso(e)], [c.start, c.end]);
});

check(
  'convertRule day/absolute -> month/absolute',
  dr.convertRule({ grain: 'day', mode: 'absolute', start: '2026-08-10', end: '2027-05-03' }, 'month', 'absolute', today),
  { grain: 'month', mode: 'absolute', start: '2026-08', end: '2027-05' },
);
check(
  'convertRule month/absolute -> month/relative',
  dr.convertRule({ grain: 'month', mode: 'absolute', start: '2026-08', end: '2027-05' }, 'month', 'relative', today),
  { grain: 'month', mode: 'relative', start: -3, end: 6 },
);
check(
  'convertRule month/relative -> decade/absolute',
  dr.convertRule({ grain: 'month', mode: 'relative', start: -3, end: 6 }, 'decade', 'absolute', today),
  { grain: 'decade', mode: 'absolute', start: '2020', end: '2020' },
);
check('stepLabel month', dr.stepLabel('month', -3, today), 'Aug 2026');
check('stepLabel decade', dr.stepLabel('decade', 0, today), '2020s');
check('stepLabel year', dr.stepLabel('year', 1, today), '2027');
check('stepLabel day', dr.stepLabel('day', -3, today), 'Nov 10, 2026');
check('formatRange', dr.formatRange(day(2026, 1, 1), day(2026, 12, 31)), 'Jan 1, 2026 – Dec 31, 2026');

check('monthsTouched 3', dr.monthsTouched(day(2026, 11, 10), day(2027, 1, 20)), 3);
check('monthsTouched 12', dr.monthsTouched(day(2026, 1, 1), day(2026, 12, 31)), 12);
check('monthsTouched 24', dr.monthsTouched(day(2026, 8, 1), day(2028, 7, 31)), 24);
check('previewView 3', dr.previewView(3), 'full');
check('previewView 4', dr.previewView(4), 'mini');
check('previewView 24', dr.previewView(24), 'mini');
check('previewView 25', dr.previewView(25), 'years');
check(
  'quarterRows',
  dr.quarterRows(day(2026, 8, 1), day(2027, 7, 31)),
  [[2026, 4], [2026, 7], [2026, 10], [2027, 1], [2027, 4], [2027, 7], [2027, 10]],
);

check('parseOffset plain', dr.parseOffset('3'), 3);
check('parseOffset plus', dr.parseOffset('+6'), 6);
check('parseOffset hyphen', dr.parseOffset('-3'), -3);
check('parseOffset minus sign', dr.parseOffset('−12'), -12);
check('parseOffset spaces', dr.parseOffset('  4 '), 4);
check('parseOffset junk', dr.parseOffset('abc'), null);
check('parseOffset empty', dr.parseOffset(''), null);
check('parseOffset decimal', dr.parseOffset('1.5'), null);

const rel = { grain: 'month', mode: 'relative', start: -3, end: 3 };
check('sameRule equal', dr.sameRule(rel, { grain: 'month', mode: 'relative', start: -3, end: 3 }), true);
check('sameRule string vs number offsets', dr.sameRule(rel, { grain: 'month', mode: 'relative', start: '-3', end: '3' }), true);
check('sameRule different mode', dr.sameRule(rel, { grain: 'month', mode: 'absolute', start: '2026-08', end: '2027-02' }), false);
check('sameRule different end', dr.sameRule(rel, { grain: 'month', mode: 'relative', start: -3, end: 4 }), false);

check('stepLabel quarter', dr.stepLabel('quarter', -1, today), 'Q3 2026');
check(
  'convertRule month/absolute -> quarter/absolute',
  dr.convertRule({ grain: 'month', mode: 'absolute', start: '2026-08', end: '2027-05' }, 'quarter', 'absolute', today),
  { grain: 'quarter', mode: 'absolute', start: '2026-Q3', end: '2027-Q2' },
);
check(
  'convertRule quarter/absolute -> quarter/relative',
  dr.convertRule({ grain: 'quarter', mode: 'absolute', start: '2026-Q3', end: '2027-Q2' }, 'quarter', 'relative', today),
  { grain: 'quarter', mode: 'relative', start: -1, end: 2 },
);

if (failures) {
  process.exit(1);
}
console.log('date-range.js: all checks passed');
