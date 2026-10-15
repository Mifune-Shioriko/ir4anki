// No server or production URL: every fixture request is fulfilled from scratch.
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
const result=spawnSync(process.env.STUDY_PYTHON||'python3',[fileURLToPath(new URL('study-browser.py',import.meta.url)),...process.argv.slice(2)],{stdio:'inherit'})
process.exit(result.status??1)
