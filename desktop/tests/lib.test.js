'use strict'
const assert = require('node:assert/strict')
const { test } = require('node:test')
const path = require('path')

const lib = require('../lib')

const SHA_A = 'a'.repeat(40)
const SHA_B = 'b'.repeat(40)
const SHA_C = 'c'.repeat(40)

test('an install folder needs agora.ps1 and backend/, and a full path', () => {
  const root = path.resolve('/agora-stable')
  const present = new Set([path.join(root, 'agora.ps1'), path.join(root, 'backend')])
  const exists = (p) => present.has(p)
  assert.equal(lib.installDirProblem(root, exists), null)
  assert.equal(lib.installDirProblem('', exists), 'empty')
  assert.equal(lib.installDirProblem('relative/dir', exists), 'notAbsolute')
  assert.equal(lib.installDirProblem(path.resolve('/elsewhere'), exists), 'noScript')
  assert.equal(lib.installDirProblem(root, (p) => p.endsWith('agora.ps1')), 'noBackend')
})

test('the launch file is read only in the exact shape agora.ps1 writes', () => {
  assert.deepEqual(
    lib.parseLaunchFile('{"url":"http://127.0.0.1:8000/?terminal_code=x","code_obtained":true}'),
    { url: 'http://127.0.0.1:8000/?terminal_code=x', codeObtained: true },
  )
  // PowerShell may write a BOM.
  assert.equal(lib.parseLaunchFile('\uFEFF{"url":"http://127.0.0.1:8000/","code_obtained":false}').codeObtained, false)
  // Unreadable is not "no code": it is a failed read.
  assert.equal(lib.parseLaunchFile(''), null)
  assert.equal(lib.parseLaunchFile('{"url":"http://127.0.0.1:8000/"}'), null)
  // Another origin is never loaded, whatever the file says.
  assert.equal(lib.parseLaunchFile('{"url":"http://127.0.0.1:8001/","code_obtained":true}'), null)
  assert.equal(lib.parseLaunchFile('{"url":"https://evil.example/","code_obtained":true}'), null)
})

test('windows navigate only within Agora; popups are Agora, about:blank, or the browser', () => {
  assert.equal(lib.navigationAllowed('http://127.0.0.1:8000/workspace'), true)
  assert.equal(lib.navigationAllowed('http://127.0.0.1:8001/'), false)
  assert.equal(lib.navigationAllowed('https://github.com/'), false)
  assert.equal(lib.navigationAllowed('not a url'), false)
  // The terminal launcher opens about:blank first, then navigates it.
  assert.equal(lib.openPolicy('about:blank'), 'child')
  assert.equal(lib.openPolicy('http://127.0.0.1:8000/terminal?id=1'), 'child')
  assert.equal(lib.openPolicy('https://github.com/x'), 'external')
  assert.equal(lib.openPolicy('file:///C:/Windows/system32/calc.exe'), 'deny')
  assert.equal(lib.openPolicy('javascript:alert(1)'), 'deny')
})

test('release tags come from ls-remote, with the peeled commit for annotated tags', () => {
  const output = [
    `${SHA_A}\trefs/tags/v1.5.1`,
    `${SHA_B}\trefs/tags/v1.6.0`,
    `${SHA_C}\trefs/tags/v1.6.0^{}`,
    `${SHA_A}\trefs/tags/archive/2026-08`,
    `${SHA_A}\trefs/tags/v1.6.0-rc1`,
  ].join('\n')
  const tags = lib.remoteReleaseTags(output)
  assert.deepEqual([...tags.keys()].sort(), ['v1.5.1', 'v1.6.0'])
  assert.equal(tags.get('v1.6.0'), SHA_C)
})

test('only a strictly newer remote release is offered, and never from an unread version', () => {
  const tags = new Map([['v1.5.1', SHA_A], ['v1.10.0', SHA_B], ['v1.9.3', SHA_C]])
  assert.deepEqual(lib.newerRelease(tags, '1.5.1'), { tag: 'v1.10.0', commit: SHA_B })
  assert.equal(lib.newerRelease(tags, '1.10.0'), null)
  assert.equal(lib.newerRelease(tags, 'unknown'), null)
})

test('an exit code that was not read is unknown, never success', () => {
  assert.equal(lib.exitOutcome(0), 'ok')
  assert.equal(lib.exitOutcome(1), 'failed')
  assert.equal(lib.exitOutcome(null), 'unknown')
  assert.equal(lib.exitOutcome(undefined), 'unknown')
})

test('every message exists in zh, ja and en with the same placeholders', () => {
  const holes = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')
  for (const key of Object.keys(lib.MESSAGES.zh)) {
    for (const lang of ['ja', 'en']) {
      assert.ok(key in lib.MESSAGES[lang], `${lang} is missing ${key}`)
      assert.equal(holes(lib.MESSAGES[lang][key]), holes(lib.MESSAGES.zh[key]), `${lang}.${key}`)
    }
  }
  assert.equal(lib.t('fr', 'starting'), lib.MESSAGES.zh.starting)
  assert.equal(lib.t('ja', 'upgradeOk', { tag: 'v2.0.0' }), 'v2.0.0 にアップグレードしました。')
})

test('status pages escape what they show', () => {
  const page = decodeURIComponent(lib.statusPage({ title: '<b>x</b>', log: '<script>alert(1)</script>' }))
  assert.ok(!page.includes('<script>alert'))
  assert.ok(page.includes('&lt;script&gt;'))
})
