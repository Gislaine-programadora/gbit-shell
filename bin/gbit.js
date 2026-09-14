#!/usr/bin/env node
// GBit Shell v1.0.0 — Node.js launcher
// Detects Python and runs: py -3 -m gbit_shell (Windows) or python3 -m gbit_shell (POSIX)
// This file is referenced by package.json "bin" and is required for npm/npx to work.

'use strict';

var cp = require('child_process');
var path = require('path');
var isWin = process.platform === 'win32';

function findPython() {
  var candidates = isWin
    ? ['py', 'py -3', 'python', 'python3']
    : ['python3', 'python'];

  for (var i = 0; i < candidates.length; i++) {
    var bin = candidates[i];
    var args = bin.split(' ');
    var base = args[0];
    var extra = args.slice(1);
    try {
      var result = cp.spawnSync(
        base,
        extra.concat(['-c', 'import gbit_shell; print("ok")']),
        { encoding: 'utf-8', timeout: 8000, stdio: ['pipe','pipe','pipe'] }
      );
      if (result.status === 0 && (result.stdout || '').trim() === 'ok') {
        return { bin: base, prefix: extra };
      }
    } catch (e) {
      // not available
    }
  }
  return null;
}

function main() {
  var py = findPython();
  if (!py) {
    console.error(
      'GBit Shell: nenhum Python 3.9+ com gbit_shell encontrado.\n' +
      'Instale com:  pip install gbit-shell'
    );
    process.exit(1);
  }

  var userArgs = process.argv.slice(2);
  var allArgs = py.prefix.concat(['-m', 'gbit_shell'], userArgs);

  var child = cp.spawn(py.bin, allArgs, {
    stdio: 'inherit',
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' })
  });

  child.on('error', function (err) {
    console.error('GBit Shell: erro ao iniciar:', err.message);
    process.exit(1);
  });

  child.on('exit', function (code, signal) {
    if (signal) {
      process.kill(process.pid, signal);
    } else {
      process.exit(code || 0);
    }
  });

  // Forward signals to the child process
  process.on('SIGINT', function () { child.kill('SIGINT'); });
  process.on('SIGTERM', function () { child.kill('SIGTERM'); });
}

main();
