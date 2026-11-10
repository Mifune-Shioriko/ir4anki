import {batch} from 'solid-js'

/** Single authority for asynchronous study state successes and failures.
 * A response may set batch defaults before its final flow phase. Apply it
 * atomically so mounted readers/editors never observe that temporary phase. */
export function createStateGeneration() {
  let generation = 0
  let mutation = 0
  return {
    begin: () => ++generation,
    mutate: () => { ++generation; return ++mutation },
    mutationCurrent: (ticket: number) => ticket === mutation,
    applyMutation(ticket: number, action: () => void) {
      if (this.mutationCurrent(ticket)) { ++generation; batch(action) }
    },
    current: (ticket: number) => ticket === generation,
    apply(ticket: number, action: () => void) { if (this.current(ticket)) batch(action) },
  }
}
