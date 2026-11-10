// A page with durable local bytes may unmount and flush/restore. Without those
// bytes it owns navigation until a successful save or an informed discard.
const guards=new Set<()=>boolean>()
export function registerDraftLeave(guard:()=>boolean){guards.add(guard);return ()=>guards.delete(guard)}
export function requestDraftLeave(){for(const guard of [...guards])if(!guard())return false;return true}
