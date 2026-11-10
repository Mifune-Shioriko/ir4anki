import { render } from 'solid-js/web'
import './theme'
import './index.css'
import './study.css'
import { App } from './App'

const root = document.getElementById('app')

render(() => <App />, root!)
