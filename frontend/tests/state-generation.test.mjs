import assert from 'node:assert/strict'
import { createStateGeneration } from '../src/lib/state-generation.ts'
const gate = createStateGeneration()
let state = 'initial'
const old = gate.begin(), newer = gate.begin()
gate.apply(newer, () => { state = 'new GET' })
gate.apply(old, () => { state = 'old GET' })
assert.equal(state, 'new GET', 'reverse GET order')
for (const operation of ['answer', 'undo', 'start', 'split', 'withdrawal']) {
 const get = gate.begin(), mutation = gate.begin()
 gate.apply(mutation, () => { state = operation })
 gate.apply(get, () => { state = 'stale success' })
 gate.apply(get, () => { state = 'stale failure' })
 assert.equal(state, operation, operation + ' invalidates old success and failure')
}
const recovery = gate.begin()
gate.apply(recovery, () => { state = 'recovered' })
assert.equal(state, 'recovered')
console.log('PASS reverse GET, answer/undo/start/split/withdrawal, stale failure, recovery')

const during = gate.mutate()
const overlappingGET = gate.begin()
gate.applyMutation(during, () => { state = 'authoritative answer' })
gate.apply(overlappingGET, () => { state = 'GET begun during answer' })
assert.equal(state, 'authoritative answer')
const oldMutation = gate.mutate(), newMutation = gate.mutate()
gate.applyMutation(newMutation, () => { state = 'new mutation' })
gate.applyMutation(oldMutation, () => { state = 'old mutation' })
assert.equal(state, 'new mutation')
console.log('PASS mutation completion invalidates overlapping GET, older mutations rejected')
