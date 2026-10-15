import type { TreeNode } from './tree'
export function remapPath<T extends string | null>(path: T, oldPath: string, newPath: string): T {
  return (path === oldPath ? newPath : path?.startsWith(oldPath + '/') ? newPath + path.slice(oldPath.length) : path) as T
}
export function validMove(path: string, isDir: boolean, dest: string): boolean {
  return !!path && dest !== path.slice(0, Math.max(0, path.lastIndexOf('/'))) &&
    !(isDir && (dest === path || dest.startsWith(path + '/'))) &&
    !dest.split('/').some(p => p.startsWith('.') || p === '_assets' || p === 'node_modules')
}
export function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  const q = query.trim().toLocaleLowerCase()
  if (!q) return nodes
  return nodes.flatMap(n => {
    if (n.path.toLocaleLowerCase().includes(q) || n.name.toLocaleLowerCase().includes(q)) return [n]
    const dirs = filterTree(n.dirs, q), files = filterTree(n.files, q)
    return dirs.length || files.length ? [{ ...n, dirs, files }] : []
  })
}
