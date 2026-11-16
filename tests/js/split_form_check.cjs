// Checks the pure functions in static/split-form.js. Run by tests/test_split_form_js.py.
// Amounts are in cents; index 0 is the original line, which keeps the rest.
const split = require('../../static/split-form.js');

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    failures += 1;
    console.error(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
  }
}

// Changing a line moves the difference to the original.
check('a line changes the original', split.balance(10000, [5000, 3000], 1), [7000, 3000]);
check('with three lines the original absorbs', split.balance(10000, [5000, 2000, 1000], 2), [7000, 2000, 1000]);
// Changing the original moves the difference to the final line.
check('the original changes the final line', split.balance(10000, [6000, 5000], 0), [6000, 4000]);
check('the original changes only the final one of three', split.balance(10000, [5000, 2000, 1000], 0), [5000, 2000, 3000]);
check('one line alone is left as it is', split.balance(10000, [10000], 0), [10000]);

// What stops a save, first problem first; '' when it can be saved.
check('valid', split.problem(10000, [6000, 4000], [true]), '');
check('a line over the transaction', split.problem(10000, [-2000, 12000], [true]), 'a line is more than the transaction');
check('the original over the transaction', split.problem(10000, [12000, -2000], [true]), 'a line is more than the transaction');
check('a line at zero', split.problem(10000, [10000, 0], [true]), 'every line needs an amount');
check('a line with no category', split.problem(10000, [6000, 4000], [false]), 'choose a category for each line');
check('the original may keep nothing', split.problem(10000, [0, 10000], [true]), '');

// Cents in and out of an input.
check('cents from text', [split.cents('12.34'), split.cents(''), split.cents('7')], [1234, 0, 700]);
check('text from cents', [split.money(1234), split.money(-50)], ['12.34', '-0.50']);

if (failures) { process.exit(1); }
