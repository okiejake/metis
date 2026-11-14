// Checks the pure functions in static/theme.js. Run by tests/test_theme_js.py.
const t = require('../../static/theme.js');
let failures = 0;
function check(label, actual, expected) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    failures += 1;
    console.error(`FAIL ${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
check('light -> dark', t.nextTheme('light'), 'dark');
check('dark -> system', t.nextTheme('dark'), 'system');
check('system -> light', t.nextTheme('system'), 'light');
check('unknown counts as system', t.nextTheme(null), 'light');
check('resolve light', t.resolveTheme('light', true), 'light');
check('resolve dark', t.resolveTheme('dark', false), 'dark');
check('resolve system, dark OS', t.resolveTheme('system', true), 'dark');
check('resolve system, light OS', t.resolveTheme('system', false), 'light');
check('resolve unknown follows OS', t.resolveTheme('purple', true), 'dark');
check('icon light', t.themeIcon('light'), 'sun');
check('icon dark', t.themeIcon('dark'), 'moon');
check('icon system', t.themeIcon('system'), 'sun-moon');
check('normalize', [t.normalizeTheme('dark'), t.normalizeTheme(''), t.normalizeTheme('x')], ['dark', 'system', 'system']);
if (failures) process.exit(1);
console.log('theme.js: all checks passed');
