"""Recoverable same-filesystem relocation, not a multi-resource atomic transaction.

Caller holds its business-state lock. flock serializes relocation/recovery across
processes; SQLite's marker commits with path updates. Journal roll-forward never
restores a whole database/flow snapshot and never overwrites destination data.
"""
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path
from contextlib import contextmanager

class RelocationConflict(Exception):
    pass


def fingerprint(path):
    path = Path(path)
    if not path.exists():
        return None
    rows = []
    for p in [path, *sorted(path.rglob('*'))] if path.is_dir() else [path]:
        if p.is_symlink():
            raise RelocationConflict('不支持移动包含符号链接的路径')
        st = p.stat()
        rows.append([str(p.relative_to(path)), st.st_dev, st.st_ino,
                     'dir' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()])
    return rows


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_no_replace(src, dst, root):
    # Walk from the corpus descriptor without following symlinks, including
    # destination parents created by the file-management move API.
    fn = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    if fn is None:
        raise RelocationConflict('当前系统不支持安全的无覆盖移动')
    def parent_fd(path, create=False):
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in Path(path).parent.relative_to(root).parts:
                if create:
                    try:
                        os.mkdir(part, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise
    source_fd = parent_fd(src)
    try:
        destination_fd = parent_fd(dst, create=True)
        try:
            if os.fstat(source_fd).st_dev != os.fstat(destination_fd).st_dev:
                raise RelocationConflict('不支持跨文件系统移动')
            if fn(source_fd, os.fsencode(Path(src).name), destination_fd, os.fsencode(Path(dst).name), 1) != 0:
                code = ctypes.get_errno()
                if code in (errno.EEXIST, errno.ENOTEMPTY, errno.EXDEV, errno.ENOSYS, errno.EINVAL):
                    raise RelocationConflict('目标已存在或不支持同文件系统无覆盖移动')
                raise OSError(code, os.strerror(code))
            os.fsync(source_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


class Relocator:
    def __init__(self, root, state, preflight, migrate, committed, signature):
        self.root, self.directory = Path(root).resolve(), Path(state) / 'file-relocations'
        self.preflight, self.migrate, self.committed, self.signature = preflight, migrate, committed, signature
        self.fault = lambda phase: None  # deterministic fixture-only injection

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        sync_dir(self.directory.parent)
        with (self.directory / 'lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def needs_recovery(self):
        # No completed journal JSON on the request hot path. Legacy indexing is
        # performed once under flock; journals themselves are never removed.
        return self.directory.exists() and (not (self.directory / 'indexed-v1').exists()
            or any((self.directory / 'pending').iterdir()))

    def index_unlocked(self):
        pending = self.directory / 'pending'
        pending.mkdir(exist_ok=True)
        if (self.directory / 'indexed-v1').exists():
            return
        for path in sorted(self.directory.glob('*.json')):
            j = json.loads(path.read_text())
            if j['phase'] not in ('complete', 'cancelled'):
                self.pending_marker(j['id'])
            if j.get('undo_of') and j['phase'] != 'cancelled':
                self.undo_marker(j['undo_of'], j['id'])
        marker = self.directory / 'indexed-v1'
        with marker.open('w') as stream:
            stream.write('1'); stream.flush(); os.fsync(stream.fileno())
        sync_dir(self.directory)

    def pending_marker(self, operation):
        pending = self.directory / 'pending'
        pending.mkdir(exist_ok=True)
        with (pending / operation).open('a') as stream:
            stream.flush(); os.fsync(stream.fileno())
        sync_dir(pending); sync_dir(self.directory)

    def undo_marker(self, operation, inverse):
        directory = self.directory / 'undo-index'
        directory.mkdir(exist_ok=True)
        path = directory / operation
        temp = path.with_suffix('.tmp')
        with temp.open('w') as stream:
            stream.write(inverse); stream.flush(); os.fsync(stream.fileno())
        temp.replace(path); sync_dir(directory); sync_dir(self.directory)

    def write(self, j):
        if j['phase'] not in ('complete', 'cancelled'):
            self.pending_marker(j['id'])
        path = self.directory / (j['id'] + '.json')
        temp = path.with_suffix('.tmp')
        with temp.open('w') as stream:
            json.dump(j, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        sync_dir(self.directory)
        if j['phase'] in ('complete', 'cancelled'):
            marker = self.directory / 'pending' / j['id']
            marker.unlink(missing_ok=True)
            sync_dir(marker.parent)

    def paths(self, j):
        if j.get('root') != str(self.root):
            raise RelocationConflict('迁移日志语料根目录不匹配')
        paths = []
        for key in ('old', 'new'):
            p = (self.root / j[key]).resolve()
            if p == self.root or not p.is_relative_to(self.root):
                raise RelocationConflict('迁移日志路径无效')
            paths.append(p)
        return paths

    def finish(self, j):
        src, dst = self.paths(j)
        if j['phase'] == 'prepared':
            if src.exists() and not dst.exists():
                if fingerprint(src) != j['fingerprint']:
                    raise RelocationConflict('源文件已变化，迁移暂停')
                self.preflight(j['old'], j['new'], j['is_dir'])
                self.fault('fs_before')
                rename_no_replace(src, dst, self.root)
                self.fault('fs')
            elif src.exists() or not dst.exists() or fingerprint(dst) != j['fingerprint']:
                raise RelocationConflict('迁移恢复路径冲突，未覆盖任何文件')
            j['phase'] = 'moved'
            self.write(j)
        if fingerprint(dst) != j['fingerprint'] or src.exists():
            raise RelocationConflict('迁移中的文件已变化，恢复暂停')
        # migrate is idempotent: marker and DB remap commit together; flow aliases
        # are replayed separately after commit, preserving unrelated state.
        self.migrate(j['old'], j['new'], j['is_dir'], j['id'], self.fault)
        j['phase'] = 'complete'
        j['expected'] = self.signature(j['new'], j['is_dir'])
        self.write(j)
        return {'ok': True, 'path': j['new'], 'old_path': j['old'], 'operationId': j['id']}

    def recover_unlocked(self):
        self.index_unlocked()
        for marker in sorted((self.directory / 'pending').iterdir()):
            path = self.directory / (marker.name + '.json')
            if not path.exists():
                # Crash before journal flush cannot have moved filesystem data.
                marker.unlink(); sync_dir(marker.parent); continue
            j = json.loads(path.read_text())
            if j.get('undo_of') and j['phase'] != 'cancelled': self.undo_marker(j['undo_of'], j['id'])
            if j['phase'] not in ('complete', 'cancelled'):
                self.finish(j)
            else:
                marker.unlink(missing_ok=True); sync_dir(marker.parent)

    def recover(self):
        with self.locked():
            self.recover_unlocked()

    def validate_id(self, value):
        if len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
            raise RelocationConflict('无效的迁移请求标识')

    def current_result(self, j):
        src, dst = self.paths(j)
        if src.exists() or fingerprint(dst) != j['fingerprint'] or self.signature(j['new'], j['is_dir']) != j['expected']:
            raise RelocationConflict('操作曾完成，但文件或阅读状态已变化；不能重放旧路径')
        return dict(ok=True, status='complete', path=j['new'], old_path=j['old'], operationId=j.get('result_of', j['id']))

    def request_result(self, request_id, request=None):
        # Only an explicitly supplied opaque client ID can retrieve its result.
        # No history enumeration; payload reuse cannot turn into another move.
        self.validate_id(request_id)
        with self.locked():
            self.recover_unlocked()
            path = self.directory / (request_id + '.json')
            if not path.exists():
                return dict(status='not_found')
            j = json.loads(path.read_text())
            if not j.get('request') or (request is not None and j['request'] != request):
                raise RelocationConflict('请求标识与操作内容不匹配')
            if j['phase'] == 'cancelled':
                return dict(status='cancelled')
            return self.current_result(j)

    def move(self, old, new, is_dir, undo_of=None, request_id=None, request=None):
        with self.locked():
            self.recover_unlocked()
            if request_id:
                self.validate_id(request_id)
                path = self.directory / (request_id + '.json')
                if path.exists():
                    j = json.loads(path.read_text())
                    if j.get('request') != request:
                        raise RelocationConflict('请求标识与操作内容不匹配')
                    if j['phase'] != 'complete':
                        raise RelocationConflict('该请求已取消，请发起新的操作')
                    return self.current_result(j)
            return self.move_unlocked(old, new, is_dir, undo_of, request_id, request)

    def move_unlocked(self, old, new, is_dir, undo_of=None, request_id=None, request=None):
        self.preflight(old, new, is_dir)
        src, dst = self.root / old, self.root / new
        if dst.exists():
            raise RelocationConflict('目标已存在')
        j = dict(id=request_id or uuid.uuid4().hex, root=str(self.root), old=old, new=new,
                 is_dir=is_dir, phase='prepared', fingerprint=fingerprint(src), undo_of=undo_of, request=request)
        self.write(j)
        if undo_of: self.undo_marker(undo_of, j['id'])
        try:
            return self.finish(j)
        except Exception:
            # A pre-FS failure can cancel safely. After rename keep the journal
            # pending for roll-forward recovery; do not guess a partial rollback.
            if src.exists() and not dst.exists():
                j['phase'] = 'cancelled'
                self.write(j)
            raise

    def undo(self, operation_id, request_id=None):
        if len(operation_id) != 32 or any(c not in '0123456789abcdef' for c in operation_id):
            raise RelocationConflict('无效的撤销操作')
        with self.locked():
            self.recover_unlocked()
            path = self.directory / (operation_id + '.json')
            if not path.exists():
                raise RelocationConflict('找不到迁移操作')
            j = json.loads(path.read_text())
            self.paths(j)
            if j.get('result_of'):
                raise RelocationConflict('确认记录不能作为新的撤销操作')
            if request_id:
                self.validate_id(request_id)
                requested = self.directory / (request_id + '.json')
                if requested.exists():
                    result_j = json.loads(requested.read_text())
                    if result_j.get('request') != dict(action='undo', operationId=operation_id):
                        raise RelocationConflict('请求标识与操作内容不匹配')
                    if result_j['phase'] != 'complete':
                        raise RelocationConflict('撤销已取消，请重新操作')
                    return self.current_result(result_j)
            index = self.directory / 'undo-index' / operation_id
            previous = None
            if index.exists():
                previous_path = self.directory / (index.read_text() + '.json')
                if previous_path.exists():
                    candidate = json.loads(previous_path.read_text())
                    if candidate['phase'] == 'complete': previous = candidate
            if previous:
                result = self.current_result(previous)
                if request_id:
                    # Durable acknowledgement of a guarded historical result,
                    # not another FS/DB/flow migration. Keep canonical operation
                    # identity so the client cannot apply the mapping twice.
                    receipt = dict(previous, id=request_id,
                                   request=dict(action='undo', operationId=operation_id),
                                   result_of=previous.get('result_of', previous['id']))
                    self.write(receipt)
                result.pop('status')
                return result
            if j['phase'] != 'complete' or fingerprint(self.root / j['new']) != j['fingerprint'] or self.signature(j['new'], j['is_dir']) != j['expected']:
                raise RelocationConflict('文件或阅读状态已变化，不能撤销')
            return self.move_unlocked(j['new'], j['old'], j['is_dir'], operation_id, request_id,
                                      dict(action='undo', operationId=operation_id) if request_id else None)
