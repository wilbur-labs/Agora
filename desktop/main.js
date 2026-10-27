'use strict'
// Agora desktop shell (CR-72 slice 1): a window, a tray and an installer around the
// existing stable install. Starting and upgrading go through agora.ps1, which owns
// every safety check (journal, listener ownership, backup and rollback). Decision and
// alternatives: task adr/0003; design: design-cr72-shell.md.

const { app, BrowserWindow, Tray, Menu, dialog, shell, Notification } = require('electron')
const { spawn, execFile } = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')
const lib = require('./lib')

let mainWindow = null
let tray = null
let lang = 'zh'
let busy = false            // a start, an upgrade or an install-folder change is running
let quitting = false
let toldAboutTray = false

// ---------------------------------------------------------------- config

const configPath = () => path.join(app.getPath('userData'), 'agora-desktop.json')

function readConfig() {
  try { return JSON.parse(fs.readFileSync(configPath(), 'utf8')) } catch { return {} }
}

function writeConfig(config) {
  fs.mkdirSync(path.dirname(configPath()), { recursive: true })
  fs.writeFileSync(configPath(), JSON.stringify(config, null, 2))
}

async function chooseInstallDir() {
  while (true) {
    const picked = await dialog.showOpenDialog(mainWindow, {
      title: lib.t(lang, 'pickFolder'),
      properties: ['openDirectory'],
    })
    if (picked.canceled) return null
    const dir = picked.filePaths[0]
    const problem = lib.installDirProblem(dir, fs.existsSync)
    if (problem === null) {
      writeConfig({ ...readConfig(), installDir: dir })
      return dir
    }
    await dialog.showMessageBox(mainWindow, {
      type: 'warning',
      message: lib.t(lang, 'folderProblem', { reason: lib.t(lang, `reason.${problem}`) }),
      buttons: [lib.t(lang, 'ok')],
    })
  }
}

async function installDir() {
  const dir = readConfig().installDir
  if (dir && lib.installDirProblem(dir, fs.existsSync) === null) return dir
  return chooseInstallDir()
}

// ---------------------------------------------------------------- processes

// Runs agora.ps1 with output to a file, never a pipe: `up` leaves the server running
// in the background, and a server that inherits a pipe keeps it open, so the caller
// would wait forever (RESUME pitfall, CR-31).
function runAgoraScript(dir, args) {
  const logFile = path.join(app.getPath('userData'), `agora-${args[0]}.log`)
  const fd = fs.openSync(logFile, 'w')
  return new Promise((resolve) => {
    let child
    try {
      child = spawn('powershell.exe', [
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'agora.ps1'), ...args,
      ], { cwd: dir, windowsHide: true, stdio: ['ignore', fd, fd] })
    } catch (err) {
      fs.closeSync(fd)
      resolve({ code: undefined, log: String(err) })
      return
    }
    const finish = (code) => {
      try { fs.closeSync(fd) } catch { /* already closed */ }
      let log = ''
      try { log = fs.readFileSync(logFile, 'utf8') } catch { /* stays empty */ }
      resolve({ code, log })
    }
    child.once('error', (err) => finish(undefined, err))
    child.once('exit', (code) => finish(code))
  })
}

function git(dir, args, timeout = 30000) {
  return new Promise((resolve) => {
    execFile('git', args, { cwd: dir, timeout, windowsHide: true }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: String(stdout), stderr: String(stderr || (err && err.message) || '') })
    })
  })
}

async function readJson(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(5000) })
    if (!response.ok) return null
    return await response.json()
  } catch { return null }
}

// ---------------------------------------------------------------- start

// Start, upgrade and changing the install folder are one critical section: each of
// them reads or replaces "which install", so none may run while another is under way
// (codex review rounds 1 and 2, CR-72). A click while busy is ignored, not queued.
async function exclusive(work) {
  if (busy) return
  busy = true
  try {
    await work()
  } finally {
    busy = false
  }
}

const startAgora = () => exclusive(doStart)

async function doStart() {
  mainWindow.loadURL(lib.statusPage({ title: lib.t(lang, 'starting'), lang }))
  const dir = await installDir()
  if (!dir) { app.quit(); return }
  const launchFile = path.join(os.tmpdir(), `agora-launch-${process.pid}-${Date.now()}.json`)
  const { code, log } = await runAgoraScript(dir, ['up', '-LaunchUrlFile', launchFile])
  let launch = null
  try { launch = lib.parseLaunchFile(fs.readFileSync(launchFile, 'utf8')) } catch { /* unread */ }
  try { fs.unlinkSync(launchFile) } catch { /* not written */ }

  const outcome = lib.exitOutcome(code)
  if (outcome !== 'ok' || launch === null) {
    const title = outcome === 'failed' ? lib.t(lang, 'startFailed', { code })
      : outcome === 'unknown' ? lib.t(lang, 'startUnknown')
        : lib.t(lang, 'launchUnreadable')
    mainWindow.loadURL(lib.statusPage({ title, detail: lib.t(lang, 'recoveryHint'), log: lib.tail(log), lang }))
    return
  }
  await refreshLanguage()
  await mainWindow.loadURL(launch.url)
  await describeInstance()
  if (!launch.codeObtained) notify(lib.t(lang, 'noLaunchCode'))
}

async function refreshLanguage() {
  const answer = await readJson(`${lib.ORIGIN}/api/settings/display-language`)
  lang = lib.languageOf(answer && answer.language)
  buildTrayMenu()
}

// The window title says which install answered; the shell never presents a dev
// instance as the stable one.
async function describeInstance() {
  const health = await readJson(`${lib.ORIGIN}/health`)
  const instance = health && health.instance
  mainWindow.setTitle(instance === 'stable' ? 'Agora' : `Agora ${lib.t(lang, 'notStable', { instance: instance || '?' })}`)
}

// ---------------------------------------------------------------- upgrade

// The lock is taken before the first await and held to the end: two "check for
// updates" clicks must never become two `agora.ps1 upgrade` processes, which would
// fight over the journal, the backup and the checkout (codex review, CR-72).
// The restart after a successful upgrade stays inside the same section, so nothing
// can change the install folder between the upgrade and the start that follows it.
const checkForUpdates = () => exclusive(async () => {
  if (await checkAndUpgrade()) await doStart()
})

const changeInstallFolder = () => exclusive(async () => {
  if (await chooseInstallDir()) await doStart()
})

// Returns true only when an upgrade ran and reported success.
async function checkAndUpgrade() {
  const dir = await installDir()
  if (!dir) return false
  const say = async (message, type = 'info') => {
    await dialog.showMessageBox(mainWindow, { type, message, buttons: [lib.t(lang, 'ok')] })
    return false
  }

  const health = await readJson(`${lib.ORIGIN}/health`)
  const current = health && typeof health.version === 'string' ? health.version : null
  if (!current) return say(lib.t(lang, 'versionUnread'), 'warning')

  const remote = await git(dir, ['ls-remote', '--tags', 'origin'])
  if (!remote.ok) return say(lib.t(lang, 'checkFailed', { reason: lib.tail(remote.stderr, 5) }), 'warning')
  const newer = lib.newerRelease(lib.remoteReleaseTags(remote.stdout), current)
  if (!newer) return say(lib.t(lang, 'upToDate', { current }))

  // A local tag of the same name that names another commit is not what the remote
  // released; agora.ps1 would check it out as-is.
  const local = await git(dir, ['rev-parse', '--verify', '--quiet', `refs/tags/${newer.tag}^{commit}`])
  if (local.ok && local.stdout.trim() !== newer.commit) return say(lib.t(lang, 'localTagDiffers', { tag: newer.tag }), 'warning')

  const choice = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    title: lib.t(lang, 'upgradeTitle'),
    message: lib.t(lang, 'upgradeAsk', { current, tag: newer.tag }),
    buttons: [lib.t(lang, 'upgrade'), lib.t(lang, 'cancel')],
    defaultId: 1,
    cancelId: 1,
  })
  if (choice.response !== 0) return false

  mainWindow.loadURL(lib.statusPage({ title: lib.t(lang, 'upgrading', { tag: newer.tag }), lang }))
  const result = await runAgoraScript(dir, ['upgrade', '-To', newer.tag])
  const outcome = lib.exitOutcome(result.code)
  if (outcome === 'ok') {
    notify(lib.t(lang, 'upgradeOk', { tag: newer.tag }))
    return true
  }
  const title = outcome === 'failed' ? lib.t(lang, 'upgradeFailed', { code: result.code }) : lib.t(lang, 'upgradeUnknown')
  mainWindow.loadURL(lib.statusPage({ title, log: lib.tail(result.log, 60), lang }))
  return false
}

// ---------------------------------------------------------------- windows

function notify(body) {
  if (Notification.isSupported()) new Notification({ title: 'Agora', body }).show()
}

// One policy for every window, main and child, applied to every later navigation and
// redirect too — not only the first URL (codex design review P2).
function guard(contents) {
  const block = (event, url) => { if (!lib.navigationAllowed(url)) event.preventDefault() }
  contents.on('will-navigate', block)
  contents.on('will-redirect', block)
  contents.setWindowOpenHandler(({ url }) => {
    const policy = lib.openPolicy(url)
    if (policy === 'external') shell.openExternal(url)
    if (policy !== 'child') return { action: 'deny' }
    return { action: 'allow', overrideBrowserWindowOptions: { autoHideMenuBar: true, webPreferences: secureWebPreferences() } }
  })
  contents.on('did-create-window', (child) => guard(child.webContents))
}

function secureWebPreferences() {
  return { contextIsolation: true, sandbox: true, nodeIntegration: false }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    title: 'Agora',
    icon: path.join(__dirname, 'icon.ico'),
    autoHideMenuBar: true,
    webPreferences: secureWebPreferences(),
  })
  guard(mainWindow.webContents)
  mainWindow.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    mainWindow.hide()
    if (!toldAboutTray) { toldAboutTray = true; notify(lib.t(lang, 'trayHint')) }
  })
}

function showWindow() {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function buildTrayMenu() {
  if (!tray) return
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: lib.t(lang, 'show'), click: showWindow },
    { label: lib.t(lang, 'checkUpdates'), click: () => { showWindow(); checkForUpdates() } },
    { label: lib.t(lang, 'changeFolder'), click: () => { showWindow(); changeInstallFolder() } },
    { type: 'separator' },
    { label: lib.t(lang, 'quit'), click: () => { quitting = true; app.quit() } },
  ]))
}

// ---------------------------------------------------------------- app

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showWindow)
  app.whenReady().then(() => {
    createWindow()
    tray = new Tray(path.join(__dirname, 'icon.ico'))
    tray.setToolTip('Agora')
    tray.on('click', showWindow)
    buildTrayMenu()
    startAgora()
  })
  app.on('before-quit', () => { quitting = true })
  // The tray keeps the shell alive when the window is hidden.
  app.on('window-all-closed', (event) => event.preventDefault())
}
