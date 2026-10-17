import { build } from 'vite'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { sourceScrollFixture } from './source-scroll-fixture.config.mjs'

const args = process.argv.slice(2)
const option = name => args[args.indexOf(name) + 1]
if (!args.includes('--outDir') || !args.includes('--data')) throw Error('Use --outDir SCRATCH/dist --data DATA.json')
const outDir = path.resolve(option('--outDir'))
const repo = path.resolve(fileURLToPath(new URL('../..', import.meta.url)))
const relative = path.relative(repo, outDir)
if (!relative.startsWith('..' + path.sep)) throw Error('Fixture outDir must be outside repository')
await build(sourceScrollFixture(outDir, path.resolve(option('--data'))))
