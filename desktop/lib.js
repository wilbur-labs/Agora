'use strict'
// Pure logic for the desktop shell (CR-72 slice 1): no Electron, no I/O, so it is
// testable with node:test. main.js wires these to windows, processes and dialogs.
//
// The shell never decides on its own that Agora is safe to use. agora.ps1 `up` does
// that (journal refusal, the listener belongs to this install, config); the shell
// only reads what the script wrote. See design-cr72-shell.md, revision P1.

const path = require('path')

const ORIGIN = 'http://127.0.0.1:8000'

// ---------------------------------------------------------------- install folder

// `exists(p)` is injected so the check is testable without a disk.
function installDirProblem(dir, exists) {
  if (typeof dir !== 'string' || dir.trim() === '') return 'empty'
  if (!path.isAbsolute(dir)) return 'notAbsolute'
  if (!exists(path.join(dir, 'agora.ps1'))) return 'noScript'
  if (!exists(path.join(dir, 'backend'))) return 'noBackend'
  return null
}

// ---------------------------------------------------------------- launch file

// What `agora.ps1 up -LaunchUrlFile` wrote. Anything not exactly that shape is a
// failed read, not "no code": the caller then shows an error instead of guessing.
function parseLaunchFile(text) {
  let data
  try { data = JSON.parse(String(text).replace(/^﻿/, '')) } catch { return null }
  if (!data || typeof data.url !== 'string' || typeof data.code_obtained !== 'boolean') return null
  let url
  try { url = new URL(data.url) } catch { return null }
  if (url.origin !== ORIGIN) return null
  return { url: url.toString(), codeObtained: data.code_obtained }
}

// ---------------------------------------------------------------- navigation

// Main and child windows: only Agora's own origin.
function navigationAllowed(target) {
  try { return new URL(target).origin === ORIGIN } catch { return false }
}

// window.open: 'child' for Agora pages and the about:blank the terminal launcher opens
// first (it navigates it afterwards — that later navigation meets navigationAllowed);
// 'external' for other http(s), handed to the default browser; 'deny' otherwise.
function openPolicy(target) {
  if (target === 'about:blank') return 'child'
  let url
  try { url = new URL(target) } catch { return 'deny' }
  if (url.origin === ORIGIN) return 'child'
  if (url.protocol === 'http:' || url.protocol === 'https:') return 'external'
  return 'deny'
}

// ---------------------------------------------------------------- versions

const TAG = /^v(\d+)\.(\d+)\.(\d+)$/

function versionParts(v) {
  const m = TAG.exec(v.startsWith('v') ? v : `v${v}`)
  return m ? m.slice(1).map(Number) : null
}

function compareVersions(a, b) {
  const x = versionParts(a); const y = versionParts(b)
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i]
  return 0
}

// `git ls-remote --tags origin` output → release tags with the commit each names.
// An annotated tag appears twice: `<tag object>\trefs/tags/v1` and
// `<commit>\trefs/tags/v1^{}`; the peeled line is the commit and wins.
function remoteReleaseTags(output) {
  const tags = new Map()
  for (const line of String(output).split(/\r?\n/)) {
    const m = /^([0-9a-f]{40})\trefs\/tags\/(v\d+\.\d+\.\d+)(\^\{\})?$/.exec(line.trim())
    if (!m) continue
    const [, sha, tag, peeled] = m
    if (peeled || !tags.has(tag)) tags.set(tag, sha)
  }
  return tags
}

// The highest remote release above `current`, or null when there is none. `current`
// must be a readable version: the caller does not offer an upgrade from "unknown".
function newerRelease(tags, current) {
  if (!versionParts(current)) return null
  let best = null
  for (const [tag, commit] of tags) {
    if (compareVersions(tag, current) <= 0) continue
    if (!best || compareVersions(tag, best.tag) > 0) best = { tag, commit }
  }
  return best
}

// ---------------------------------------------------------------- process outcomes

// A child's exit code: 0 is success, a number is failure, anything else (killed,
// never reported) is unknown — never read as success (CR-59).
function exitOutcome(code) {
  if (code === 0) return 'ok'
  if (typeof code === 'number') return 'failed'
  return 'unknown'
}

// ---------------------------------------------------------------- strings

const MESSAGES = {
  zh: {
    show: '显示 Agora',
    checkUpdates: '检查更新',
    changeFolder: '更改 Agora 安装位置…',
    quit: '退出窗口（Agora 服务继续运行）',
    pickFolder: '选择 Agora 稳定版所在的文件夹（含 agora.ps1）',
    folderProblem: '这个文件夹不是 Agora 安装位置：{reason}',
    'reason.empty': '没有选择',
    'reason.notAbsolute': '不是完整路径',
    'reason.noScript': '里面没有 agora.ps1',
    'reason.noBackend': '里面没有 backend 文件夹',
    starting: '正在启动 Agora…',
    startFailed: 'Agora 没能启动（退出码 {code}）。下面是 agora.ps1 的输出：',
    startUnknown: '没能确认 Agora 是否启动（读不到退出结果）。下面是 agora.ps1 的输出：',
    launchUnreadable: 'agora.ps1 结束了，但没有写出可读的启动地址，所以没有打开页面。下面是它的输出：',
    recoveryHint: '如果上面说上次升级没有完成，请在安装文件夹里运行 .\\agora.ps1 rollback -Yes 恢复后再打开。',
    noLaunchCode: '没有拿到新的终端启动码；终端能不能用，取决于这个窗口之前的授权。',
    checkFailed: '没能检查更新：{reason}',
    versionUnread: '读不到当前运行的版本，所以不判断有没有新版本。',
    upToDate: '当前 v{current} 已是远端最新的发布版本。',
    localTagDiffers: '本地的 {tag} 和远端指向的不是同一个提交，不升级。请先核对这个 tag。',
    upgradeTitle: '升级 Agora',
    upgradeAsk: '把 Agora 从 v{current} 升级到 {tag}？\n\n会先停止 Agora、备份数据库，再切换版本；任何一步失败都会自动回到 v{current} 和原来的数据库。',
    upgrade: '升级',
    cancel: '取消',
    upgrading: '正在升级到 {tag}…（会先备份数据库）',
    upgradeOk: '已升级到 {tag}。',
    upgradeFailed: '升级没有成功（退出码 {code}）。agora.ps1 的输出如下，其中写明是否已经回滚：',
    upgradeUnknown: '读不到升级的结果，不知道是否成功。agora.ps1 的输出如下：',
    trayHint: '窗口已收到托盘，Agora 仍在运行。',
    notStable: '（不是稳定版：{instance}）',
    ok: '确定',
  },
  ja: {
    show: 'Agora を表示',
    checkUpdates: '更新を確認',
    changeFolder: 'Agora のインストール場所を変更…',
    quit: 'ウィンドウを終了（Agora サービスは動き続けます）',
    pickFolder: 'Agora 安定版のフォルダ（agora.ps1 があるもの）を選んでください',
    folderProblem: 'このフォルダは Agora のインストール場所ではありません：{reason}',
    'reason.empty': '選択されていません',
    'reason.notAbsolute': '完全なパスではありません',
    'reason.noScript': 'agora.ps1 がありません',
    'reason.noBackend': 'backend フォルダがありません',
    starting: 'Agora を起動しています…',
    startFailed: 'Agora を起動できませんでした（終了コード {code}）。agora.ps1 の出力：',
    startUnknown: 'Agora が起動したか確認できませんでした（終了結果を読めません）。agora.ps1 の出力：',
    launchUnreadable: 'agora.ps1 は終了しましたが、読める起動アドレスを書き出さなかったため、ページは開いていません。出力：',
    recoveryHint: '前回のアップグレードが完了していないと書かれている場合は、インストールフォルダで .\\agora.ps1 rollback -Yes を実行して復旧してから開いてください。',
    noLaunchCode: '新しいターミナル起動コードは取得できませんでした。ターミナルが使えるかは、このウィンドウの以前の認可によります。',
    checkFailed: '更新を確認できませんでした：{reason}',
    versionUnread: '実行中のバージョンを読めないため、新しいバージョンの有無は判断しません。',
    upToDate: '現在の v{current} がリモートの最新リリースです。',
    localTagDiffers: 'ローカルの {tag} はリモートと別のコミットを指しているため、アップグレードしません。先にこのタグを確認してください。',
    upgradeTitle: 'Agora のアップグレード',
    upgradeAsk: 'Agora を v{current} から {tag} にアップグレードしますか？\n\nAgora を停止してデータベースをバックアップしてから切り替えます。どの段階で失敗しても v{current} と元のデータベースに自動で戻ります。',
    upgrade: 'アップグレード',
    cancel: 'キャンセル',
    upgrading: '{tag} にアップグレードしています…（先にデータベースをバックアップします）',
    upgradeOk: '{tag} にアップグレードしました。',
    upgradeFailed: 'アップグレードは成功しませんでした（終了コード {code}）。agora.ps1 の出力（ロールバックしたかどうかも書かれています）：',
    upgradeUnknown: 'アップグレードの結果を読めないため、成功したかは不明です。agora.ps1 の出力：',
    trayHint: 'ウィンドウをトレイにしまいました。Agora は動き続けています。',
    notStable: '（安定版ではありません：{instance}）',
    ok: 'OK',
  },
  en: {
    show: 'Show Agora',
    checkUpdates: 'Check for updates',
    changeFolder: 'Change Agora install folder…',
    quit: 'Quit window (the Agora service keeps running)',
    pickFolder: 'Choose the folder of the stable Agora install (the one with agora.ps1)',
    folderProblem: 'This folder is not an Agora install: {reason}',
    'reason.empty': 'nothing chosen',
    'reason.notAbsolute': 'not a full path',
    'reason.noScript': 'no agora.ps1 in it',
    'reason.noBackend': 'no backend folder in it',
    starting: 'Starting Agora…',
    startFailed: 'Agora did not start (exit code {code}). Output of agora.ps1:',
    startUnknown: 'Could not confirm whether Agora started (its exit result could not be read). Output of agora.ps1:',
    launchUnreadable: 'agora.ps1 finished but wrote no readable launch address, so no page was opened. Its output:',
    recoveryHint: 'If it says an earlier upgrade did not finish, run .\\agora.ps1 rollback -Yes in the install folder, then open Agora again.',
    noLaunchCode: 'No new terminal launch code was obtained; whether terminals work depends on this window\'s earlier authorisation.',
    checkFailed: 'Could not check for updates: {reason}',
    versionUnread: 'The running version could not be read, so no update is offered.',
    upToDate: 'v{current} is the newest release on the remote.',
    localTagDiffers: 'The local {tag} names a different commit from the remote one; not upgrading. Check that tag first.',
    upgradeTitle: 'Upgrade Agora',
    upgradeAsk: 'Upgrade Agora from v{current} to {tag}?\n\nAgora is stopped and its database backed up before switching; if any step fails it goes back to v{current} and the old database automatically.',
    upgrade: 'Upgrade',
    cancel: 'Cancel',
    upgrading: 'Upgrading to {tag}… (the database is backed up first)',
    upgradeOk: 'Upgraded to {tag}.',
    upgradeFailed: 'The upgrade did not succeed (exit code {code}). Output of agora.ps1, which says whether it rolled back:',
    upgradeUnknown: 'The upgrade\'s result could not be read; whether it succeeded is unknown. Output of agora.ps1:',
    trayHint: 'The window went to the tray; Agora keeps running.',
    notStable: '(not the stable install: {instance})',
    ok: 'OK',
  },
}

const LANGUAGES = Object.keys(MESSAGES)

function languageOf(value) {
  return LANGUAGES.includes(value) ? value : 'zh'
}

function t(lang, key, vars = {}) {
  const table = MESSAGES[languageOf(lang)]
  const text = key in table ? table[key] : MESSAGES.zh[key]
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m))
}

// ---------------------------------------------------------------- local pages

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

// A self-contained status page (starting / error). Rendered as a data: URL, so it
// has no Agora origin and can make no request to the backend.
function statusPage({ title, detail = '', log = '', lang = 'zh' }) {
  const body = [
    `<h2>${escapeHtml(title)}</h2>`,
    detail ? `<p>${escapeHtml(detail)}</p>` : '',
    log ? `<pre>${escapeHtml(log)}</pre>` : '',
  ].join('')
  const html = `<!doctype html><html lang="${lang === 'zh' ? 'zh-Hans' : lang}"><meta charset="utf-8"><style>
body{margin:0;padding:32px;font-family:'Segoe UI','Yu Gothic UI','Microsoft YaHei UI',sans-serif;background:#fafafa;color:#1f2937}
h2{font-size:18px;font-weight:600;margin:0 0 12px}p{font-size:14px;line-height:1.6;margin:0 0 12px}
pre{font:12px/1.5 Consolas,monospace;background:#fff;border:1px solid #e5e7eb;padding:12px;white-space:pre-wrap;max-height:60vh;overflow:auto}
@media (prefers-color-scheme:dark){body{background:#111827;color:#e5e7eb}pre{background:#1f2937;border-color:#374151}}
</style><body>${body}</body></html>`
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html)
}

function tail(text, lines = 40) {
  return String(text).split(/\r?\n/).slice(-lines).join('\n').trim()
}

module.exports = {
  ORIGIN, installDirProblem, parseLaunchFile, navigationAllowed, openPolicy,
  compareVersions, remoteReleaseTags, newerRelease, exitOutcome,
  MESSAGES, LANGUAGES, languageOf, t, statusPage, tail,
}
