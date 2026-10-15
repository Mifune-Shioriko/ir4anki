/** Single authority for asynchronous study state successes and failures. */
export function createStateGeneration() {
  let generation = 0
  let mutation = 0
  return {
    begin: () => ++generation,
    mutate: () => { ++generation; return ++mutation },
    mutationCurrent: (ticket: number) => ticket === mutation,
    applyMutation(ticket: number, action: () => void) {
      if (this.mutationCurrent(ticket)) { ++generation; action() }
    },
    current: (ticket: number) => ticket === generation,
    apply(ticket: number, action: () => void) { if (this.current(ticket)) action() },
  }
}
