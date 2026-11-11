import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Run with node src/csv.test.mjs; no browser or additional test dependency needed.
const source = readFileSync(new URL('./csv.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
})
const { rowsToCsv } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

assert.equal(rowsToCsv([{ value: '=1+1' }]), "value\n'=1+1")
assert.equal(rowsToCsv([{ value: '\t@SUM(1)' }]), "value\n'\t@SUM(1)")
assert.equal(rowsToCsv([{ value: '+123' }]), "value\n'+123")
assert.equal(rowsToCsv([{ value: '-123' }]), "value\n'-123")
assert.equal(rowsToCsv([{ value: -123 }]), 'value\n-123')
assert.equal(rowsToCsv([{ '=header': 'normal' }]), "'=header\nnormal")
assert.equal(rowsToCsv([{ value: 'a,"b"\nc' }]), 'value\n"a,""b""\nc"')
assert.equal(rowsToCsv([]), '')
console.log('CSV security tests passed (8 assertions)')
