// GBit Shell - VS Code extension v1.0.0
// Registers GBit Shell as an integrated terminal profile.
// Terminal opens CLEAN (no banner) inside VS Code.
// Tab shows "GBit Shell" instead of "python" (overrideName: true).
// Directory path in the prompt renders in BLUE.
// Ctrl+C correctly interrupts foreground processes on Windows.

const vscode = require('vscode');
const { execFile } = require('child_process');
const path = require('path');
const fs = require('fs');

const PROFILE_ID = 'gbit-shell.terminal-profile';
const PROFILE_NAME = 'GBit Shell';

// ---------------------------------------------------------------------
// Python discovery (cached: probing on every terminal open made the
// panel sit blank for seconds)
// ---------------------------------------------------------------------
let pythonCache = null;      // { bin, prefix, version, hasShell }

function platformKey() {
  if (process.platform === 'win32') return 'windows';
  if (process.platform === 'darwin') return 'osx';
  return 'linux';
}

function pythonCandidates() {
  const configured = vscode.workspace.getConfiguration('gbitShell').get('pythonPath');
  const list = [];
  if (configured) list.push(configured);

  // Reuse the Python extension's selected interpreter when available
  const pyExtPath = vscode.workspace.getConfiguration('python').get('defaultInterpreterPath');
  if (pyExtPath) list.push(pyExtPath);

  if (process.platform === 'win32') {
    list.push('py', 'python', 'python3');
  } else {
    list.push('python3', 'python');
  }
  return [...new Set(list)];
}

function probePython(bin) {
  return new Promise((resolve) => {
    const args = bin === 'py' ? ['-3', '--version'] : ['--version'];
    execFile(bin, args, { timeout: 5000 }, (err, stdout, stderr) => {
      if (err) return resolve(null);
      const match = `${stdout || ''}${stderr || ''}`.match(/Python\s+(\d+)\.(\d+)/);
      if (!match) return resolve(null);
      const major = Number(match[1]);
      const minor = Number(match[2]);
      if (major > 3 || (major === 3 && minor >= 9)) {
        resolve({ bin, prefix: bin === 'py' ? ['-3'] : [], version: `${major}.${minor}` });
      } else {
        resolve(null);
      }
    });
  });
}

function moduleInstalled(python, moduleName) {
  return new Promise((resolve) => {
    execFile(python.bin, [...python.prefix, '-c', `import ${moduleName}`],
      { timeout: 8000 }, (err) => resolve(!err));
  });
}

/**
 * Resolve the interpreter that has gbit_shell available.
 * The result is cached; pass true to force a fresh probe.
 */
async function resolvePython(force = false) {
  if (pythonCache && !force) return pythonCache;

  let fallback = null;
  for (const candidate of pythonCandidates()) {
    if (candidate.includes(path.sep) && !fs.existsSync(candidate)) continue;
    const found = await probePython(candidate);
    if (!found) continue;
    if (!fallback) fallback = { ...found, hasShell: false };
    if (await moduleInstalled(found, 'gbit_shell')) {
      pythonCache = { ...found, hasShell: true };
      return pythonCache;
    }
  }
  pythonCache = fallback;      // Python exists but without the package
  return pythonCache;
}

// ---------------------------------------------------------------------
// Environment & shell args for the terminal
// ---------------------------------------------------------------------
function terminalEnv() {
  const config = vscode.workspace.getConfiguration('gbitShell');
  const configuredTag = String(config.get('promptTag') || 'GBIT').trim();
  // Migrate the old generated label without changing custom tags.
  const promptTag = /^(GBIT-SHE|GBIT SHE|GBIT_SHELL|GBIT-SHELL)$/i.test(configuredTag)
    ? 'GBIT'
    : (configuredTag || 'GBIT');
  return {
    PYTHONIOENCODING: 'utf-8',
    // This env var tells gbit_shell to SUPPRESS the banner.
    // Terminal opens CLEAN and professional inside VS Code.
    GBIT_VSCODE: '1',
    GBIT_THEME: config.get('theme') || 'gbit',
    GBIT_TAG: promptTag,
    // Node badge off by default = clean prompt
    ...(config.get('showNodeBadge') === true ? { GBIT_SHOW_NODE: 'true' } : {}),
    ...(config.get('showGitStatus') === false ? { GBIT_SHOW_GIT: 'false' } : {}),
  };
}

function shellArgsFor(python) {
  const args = [...python.prefix, '-m', 'gbit_shell'];
  // Double guarantee: besides GBIT_VSCODE=1, we pass --no-banner
  // so the terminal NEVER shows the ASCII art inside VS Code.
  args.push('--no-banner');
  return args;
}

// ---------------------------------------------------------------------
// Terminal options (with overrideName so the tab reads "GBit Shell")
// ---------------------------------------------------------------------
async function buildTerminalOptions() {
  const python = await resolvePython();

  if (!python) {
    const choice = await vscode.window.showErrorMessage(
      'GBit Shell: nenhum Python 3.9+ encontrado. Defina gbitShell.pythonPath nas configuracoes.',
      'Abrir configuracoes'
    );
    if (choice === 'Abrir configuracoes') {
      vscode.commands.executeCommand('workbench.action.openSettings', 'gbitShell.pythonPath');
    }
    return null;
  }

  if (!python.hasShell) {
    const choice = await vscode.window.showWarningMessage(
      `GBit Shell nao esta instalado em ${python.bin} (Python ${python.version}).`,
      'Instalar agora', 'Cancelar'
    );
    if (choice === 'Instalar agora') {
      const terminal = vscode.window.createTerminal('Instalar GBit Shell');
      terminal.show();
      terminal.sendText(`${python.bin} -m pip install gbit-shell`);
      pythonCache = null;      // re-probe after the install
    }
    return null;
  }

  return {
    name: PROFILE_NAME,
    shellPath: python.bin,
    shellArgs: shellArgsFor(python),
    env: terminalEnv(),
    iconPath: new vscode.ThemeIcon('terminal'),
    isTransient: false,
    // CRITICAL: overrideName makes the tab show "GBit Shell"
    // instead of "python" when clicking the + button.
    overrideName: true,
  };
}

// ---------------------------------------------------------------------
// settings.json profile (needed to make GBit Shell the default:
// VS Code only accepts a profile declared in the settings)
// ---------------------------------------------------------------------
async function writeProfileToSettings() {
  const python = await resolvePython();
  if (!python) {
    vscode.window.showErrorMessage('GBit Shell: nenhum Python 3.9+ encontrado.');
    return false;
  }

  const key = platformKey();
  const config = vscode.workspace.getConfiguration('terminal.integrated');
  const profiles = { ...(config.get(`profiles.${key}`) || {}) };

  profiles[PROFILE_NAME] = {
    path: python.bin,
    args: shellArgsFor(python),
    env: terminalEnv(),
    icon: 'terminal',
    // Without overrideName: true the tab shows "python" instead of "GBit Shell".
    overrideName: true,
  };

  await config.update(`profiles.${key}`, profiles, vscode.ConfigurationTarget.Global);
  return true;
}

// ---------------------------------------------------------------------
// Activation
//
// Commands are registered FIRST and each registration is isolated.
// A single failure here (for instance a terminal profile provider id
// still held by a previous extension host) used to abort activate()
// and leave every command reported as "command not found".
// ---------------------------------------------------------------------
const startupErrors = [];

function safely(label, fn) {
  try {
    return fn();
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    startupErrors.push(`${label}: ${message}`);
    console.error(`[GBit Shell] falha em ${label}`, err);
    return undefined;
  }
}

function activate(context) {
  const register = (id, handler) =>
    safely(`comando ${id}`, () => {
      const wrapped = async (...args) => {
        try {
          return await handler(...args);
        } catch (err) {
          vscode.window.showErrorMessage(
            `GBit Shell: ${err && err.message ? err.message : String(err)}`);
        }
      };
      context.subscriptions.push(vscode.commands.registerCommand(id, wrapped));
    });

  // Command: new terminal
  register('gbit-shell.newTerminal', async () => {
    const options = await buildTerminalOptions();
    if (!options) return;
    vscode.window.createTerminal(options).show();
  });

  // Command: new terminal split beside the active one
  register('gbit-shell.splitTerminal', async () => {
    const options = await buildTerminalOptions();
    if (!options) return;
    const active = vscode.window.activeTerminal;
    if (active) options.location = { parentTerminal: active };
    vscode.window.createTerminal(options).show();
  });

  // Command: publish current workspace to GitHub
  register('gbit-shell.publishToGitHub', async () => {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      vscode.window.showWarningMessage('GBit Shell: abra uma pasta primeiro.');
      return;
    }
    const message = await vscode.window.showInputBox({
      prompt: 'Mensagem do commit',
      value: 'chore: update via GBit Shell',
      ignoreFocusOut: true,
    });
    if (message === undefined) return;

    const options = await buildTerminalOptions();
    if (!options) return;
    options.cwd = folders[0].uri.fsPath;
    const terminal = vscode.window.createTerminal(options);
    terminal.show();
    terminal.sendText(`ghpush -m "${message.replace(/"/g, '\\"')}"`);
  });

  // Command: add the profile to settings.json (without changing the default)
  register('gbit-shell.addProfile', async () => {
    if (!(await writeProfileToSettings())) return;
    const choice = await vscode.window.showInformationMessage(
      `Perfil "${PROFILE_NAME}" adicionado ao settings.json.`,
      'Ver no settings.json', 'Abrir terminal'
    );
    if (choice === 'Ver no settings.json') {
      await vscode.commands.executeCommand('workbench.action.openSettingsJson');
    } else if (choice === 'Abrir terminal') {
      vscode.commands.executeCommand('gbit-shell.newTerminal');
    }
  });

  // Command: make GBit Shell the default terminal
  register('gbit-shell.setDefault', async () => {
    // The default profile must exist in the settings, an extension
    // contributed profile alone is not enough.
    if (!(await writeProfileToSettings())) return;
    try {
      await vscode.workspace.getConfiguration('terminal.integrated').update(
        `defaultProfile.${platformKey()}`, PROFILE_NAME,
        vscode.ConfigurationTarget.Global
      );
    } catch (err) {
      vscode.window.showErrorMessage(`GBit Shell: nao consegui gravar a preferencia (${err.message}).`);
      return;
    }
    const choice = await vscode.window.showInformationMessage(
      'GBit Shell definido como terminal padrao.',
      'Abrir terminal agora', 'Ver no settings.json'
    );
    if (choice === 'Abrir terminal agora') {
      vscode.commands.executeCommand('gbit-shell.newTerminal');
    } else if (choice === 'Ver no settings.json') {
      vscode.commands.executeCommand('workbench.action.openSettingsJson');
    }
  });

  // Command: doctor
  register('gbit-shell.doctor', async () => {
    const python = await resolvePython(true);
    if (!python) {
      vscode.window.showErrorMessage('GBit Shell: nenhum Python 3.9+ encontrado.');
      return;
    }
    const hasPtk = await moduleInstalled(python, 'prompt_toolkit');
    const hasRich = await moduleInstalled(python, 'rich');
    const lines = [
      `Python: ${python.bin} (${python.version})`,
      `gbit_shell: ${python.hasShell ? 'ok' : 'ausente'}`,
      `prompt_toolkit: ${hasPtk ? 'ok' : 'ausente'}`,
      `rich: ${hasRich ? 'ok' : 'ausente'}`,
      `perfil no settings: ${profileInSettings() ? 'ok' : 'ausente'}`,
      `extensao: v${extensionVersion(context)}`,
    ];
    if (startupErrors.length) lines.push(`avisos: ${startupErrors.join(' | ')}`);
    const allGood = python.hasShell && hasPtk && hasRich && !startupErrors.length;
    const show = allGood ? vscode.window.showInformationMessage
      : vscode.window.showWarningMessage;
    show(lines.join('  \u00b7  '));
  });

  // --- non-critical wiring, isolated from the commands above --------

  // Warm the cache so the first terminal opens without a pause
  safely('deteccao do Python', () => {
    resolvePython()
      .then((python) => {
        // Auto-register profile in settings on first activation
        if (python && python.hasShell && !profileInSettings()) {
          return writeProfileToSettings();
        }
        return undefined;
      })
      .catch(() => {});
  });

  // Re-probe when the user points at another interpreter
  safely('observador de configuracao', () => {
    context.subscriptions.push(
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (
          event.affectsConfiguration('gbitShell.pythonPath') ||
          event.affectsConfiguration('gbitShell.theme') ||
          event.affectsConfiguration('gbitShell.showBanner') ||
          event.affectsConfiguration('gbitShell.showNodeBadge') ||
          event.affectsConfiguration('gbitShell.showGitStatus') ||
          event.affectsConfiguration('gbitShell.promptTag')
        ) {
          pythonCache = null;
        }
      })
    );
  });

  // Terminal profile provider (the entry in the + dropdown)
  // This is what makes "GBit Shell" appear in the terminal profile picker.
  safely('perfil de terminal', () => {
    context.subscriptions.push(
      vscode.window.registerTerminalProfileProvider(PROFILE_ID, {
        async provideTerminalProfile() {
          const options = await buildTerminalOptions();
          if (!options) return undefined;
          return new vscode.TerminalProfile(options);
        },
      })
    );
  });

  // Status bar entry
  safely('barra de status', () => {
    const status = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right, 100
    );
    status.text = '$(terminal) GBit';
    status.tooltip = 'Abrir um terminal GBit Shell';
    status.command = 'gbit-shell.newTerminal';
    status.show();
    context.subscriptions.push(status);
  });

  if (startupErrors.length) {
    console.warn('[GBit Shell] ativado com avisos:', startupErrors.join(' | '));
  }
}

function profileInSettings() {
  const profiles = vscode.workspace.getConfiguration('terminal.integrated')
    .get(`profiles.${platformKey()}`) || {};
  const entry = profiles[PROFILE_NAME];
  return Boolean(entry && entry.overrideName);
}

function extensionVersion(context) {
  try {
    const pkg = require(path.join(context.extensionPath, 'package.json'));
    return pkg.version;
  } catch (err) {
    return '?';
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
