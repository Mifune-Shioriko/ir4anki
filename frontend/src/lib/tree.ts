// Folder-tree builder shared by the 阅读清单 picker and the 文件 browser
// (extracted from ReadingListScreen 2026-09-19 round 3 — both screens fold
// flat corpus paths (year/科目/文件.md) into the same TreeNode shape).

export interface TreeFile {
  path: string
  title: string
  /** reading-list membership (picker only; the file browser omits it) */
  in_list?: boolean
  /** previously removed with parked progress — picker offers 重新播种
   * (fresh add, whole-file seeding escape hatch 2026-09-24) */
  archived?: boolean
}

export interface TreeNode {
  /** display name: folder segment or file title (without .md) */
  name: string
  /** full relative path for FILES; joined dir path for folders */
  path: string
  kind: 'dir' | 'file'
  file?: TreeFile
  dirs: TreeNode[]
  files: TreeNode[]
}

export function buildTree(files: TreeFile[]): TreeNode[] {
  const roots: TreeNode[] = []
  const dirIndex = new Map<string, TreeNode>()
  for (const f of files) {
    const parts = f.path.split('/')
    let container = roots
    let prefix = ''
    for (let i = 0; i < parts.length - 1; i++) {
      prefix = prefix ? `${prefix}/${parts[i]}` : parts[i]
      let dir = dirIndex.get(prefix)
      if (!dir) {
        dir = { name: parts[i], path: prefix, kind: 'dir', dirs: [], files: [] }
        dirIndex.set(prefix, dir)
        container.push(dir)
      }
      container = dir.dirs
    }
    const fileNode: TreeNode = {
      name: parts[parts.length - 1].replace(/\.md$/, ''),
      path: f.path,
      kind: 'file',
      file: f,
      dirs: [],
      files: [],
    }
    // a file lives in its parent dir's `files` list; top-level files in roots
    const parentDir =
      parts.length > 1 ? dirIndex.get(parts.slice(0, -1).join('/')) : undefined
    if (parentDir) parentDir.files.push(fileNode)
    else roots.push(fileNode)
  }
  const sortRec = (nodes: TreeNode[]) => {
    nodes.sort((a, b) =>
      a.kind !== b.kind
        ? a.kind === 'dir'
          ? -1
          : 1
        : a.name.localeCompare(b.name, 'zh-Hans-CN'),
    )
    nodes.forEach(n => {
      if (n.kind === 'dir') {
        sortRec(n.dirs)
        sortRec(n.files)
      }
    })
  }
  sortRec(roots)
  dirIndex.forEach(d => {
    sortRec(d.dirs)
    sortRec(d.files)
  })
  return roots
}

/** every dir path in the tree — used to pre-expand all folders */
export function allDirPaths(nodes: TreeNode[]): Set<string> {
  const out = new Set<string>()
  const walk = (ns: TreeNode[]) =>
    ns.forEach(n => {
      if (n.kind === 'dir') {
        out.add(n.path)
        walk(n.dirs)
      }
    })
  walk(nodes)
  return out
}
