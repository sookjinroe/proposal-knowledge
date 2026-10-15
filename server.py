"""제안전략 지식시스템 편집기 — 로컬 서버.

K드라이브의 md 레코드를 브라우저에서 읽고 고친다. 저장하면 K:에 바로 쓴다.
외부 의존성 없음. 127.0.0.1 에만 바인딩한다.

    python server.py            # http://127.0.0.1:8777
    python server.py --port 9000
    python server.py --root "K:/Workgroups/제안전략 지식시스템"
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import shutil
import sys
import hashlib
import subprocess
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = 'K:/Workgroups/제안전략 지식시스템'
BACKUP = os.path.join(HERE, '_백업')

CATS = [
    ('01_사업실적', '사업실적', True),
    ('02_보유자산', '보유자산', False),
    ('03_수행체계', '수행체계', False),
    ('04_회사정보', '회사정보', False),
    ('05_오퍼링', '오퍼링', False),
    ('06_인력', '인력', False),
]

# 카테고리별 필수 필드 — 저장 전에 있는지 본다
REQUIRED = {
    '01_사업실적': ['사업명', '고객사 / 발주처', '수행 기간', '진행 상태',
                 '참여 형태와 담당 범위', '계약 금액', '투입 인력', '선행·후속 사업'],
    '02_보유자산': ['자산명', '기준 시점', '소속 오퍼링', '담당자'],
    '03_수행체계': ['체계명', '기준 시점', '담당자'],
    '05_오퍼링': ['오퍼링명'],
}

ROOT = DEFAULT_ROOT


# ─────────────────────────── 파싱 ───────────────────────────

def count_q(body):
    return len(re.findall(r'\(확인 필요', body))


# K: 는 네트워크 드라이브라 파일을 열 때마다 느리다.
# (경로, 수정시각) 이 그대로면 다시 읽지 않는다.
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def read_cached(fp):
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return None, None
    with _CACHE_LOCK:
        hit = _CACHE.get(fp)
    if hit and hit[0] == mt:
        return hit[1], mt
    body = open(fp, encoding='utf-8').read()
    with _CACHE_LOCK:
        _CACHE[fp] = (mt, body)
    return body, mt


def parse(body):
    """md 를 줄 단위로 들여다본다.

    원본 줄 목록(`lines`)을 그대로 들고 있고, 필드와 섹션은 거기에 대한
    줄 번호로만 가리킨다. 저장할 때는 고친 자리만 바꿔 끼우므로
    손대지 않은 줄은 한 글자도 달라지지 않는다.
    """
    lines = body.split('\n')
    title = ''
    i = 0
    if lines and lines[0].startswith('# '):
        title = lines[0][2:].strip()
        i = 1
    pre_lo = i
    while i < len(lines) and not lines[i].startswith('## '):
        i += 1
    pre_hi = i

    secs = []
    for n, ln in enumerate(lines):
        if n >= pre_hi and ln.startswith('## '):
            if secs:
                secs[-1]['hi'] = n
            secs.append({'head': ln[3:].strip(), 'lo': n + 1, 'hi': len(lines)})

    # 정보표 — 정확히 2열이고 머리가 「항목 | 내용」인 표만 칸 편집 대상으로 본다.
    # 04_회사정보처럼 열이 더 많은 표는 건드리지 않고 본문으로 둔다.
    fields, info_idx, tbl = [], None, None
    for n, s in enumerate(secs):
        rows = [k for k in range(s['lo'], s['hi'])
                if lines[k].startswith('|')
                and set(lines[k].replace('|', '').replace('-', '').replace(':', '').strip())]
        if (len(rows) >= 2 and lines[rows[0]].count('|') == 3
                and lines[rows[0]].split('|')[1].strip() == '항목'):
            info_idx = n
            # 표는 파이프 줄이 끊기지 않고 이어지는 데까지다.
            # 섹션의 마지막 파이프 줄까지 잡으면 그 사이의 메모나 두 번째 표를
            # 저장할 때 통째로 지워 버린다.
            lo = hi = rows[0]
            while hi + 1 < s['hi'] and lines[hi + 1].startswith('|'):
                hi += 1
            for k in range(lo + 1, hi + 1):
                if lines[k].count('|') != 3:
                    continue
                if not set(lines[k].replace('|', '').replace('-', '').replace(':', '').strip()):
                    continue        # 구분선
                p = lines[k].split('|')
                fields.append({'label': p[1].strip(), 'value': p[2].strip(), 'line': k})
            tbl = (lo, hi)
            break

    out = []
    for n, s in enumerate(secs):
        out.append({'head': s['head'], 'lo': s['lo'], 'hi': s['hi'],
                    'body': '\n'.join(lines[s['lo']:s['hi']]).strip('\n'),
                    'is_info': n == info_idx})
    return {'title': title, 'lines': lines,
            'pre': '\n'.join(lines[pre_lo:pre_hi]).strip('\n'),
            'fields': fields, 'secs': out, 'tbl': tbl}


def assemble(p, fields=None, secs=None):
    """원본 줄을 바탕으로, 바뀐 자리만 갈아 끼운다."""
    lines = list(p['lines'])
    edits = []  # (lo, hi, 새 줄들)

    if fields is not None and p['tbl']:
        lo, hi = p['tbl']
        rows = [lines[lo], lines[lo + 1]] if lines[lo + 1].lstrip().startswith('|-') \
            else [lines[lo], '|---|---|']
        for f in fields:
            v = f['value']
            rows.append('| %s | %s |' % (f['label'], v) if v else '| %s | |' % f['label'])
        edits.append((lo, hi, rows))

    if secs is not None:
        # 이름이 아니라 순서로 짝지운다 — 같은 이름의 섹션이 둘이어도 엉키지 않는다
        for s in secs:
            i = s.get('i')
            o = p['secs'][i] if isinstance(i, int) and 0 <= i < len(p['secs']) else None
            if o is None or o['head'] != s['head'] or o['is_info']:
                continue
            old = '\n'.join(lines[o['lo']:o['hi']])
            lead = len(old) - len(old.lstrip('\n'))
            trail = len(old) - len(old.rstrip('\n'))
            new = ('\n' * lead) + s['body'].strip('\n') + ('\n' * trail)
            edits.append((o['lo'], o['hi'] - 1, new.split('\n')))

    for lo, hi, new in sorted(edits, key=lambda e: -e[0]):
        lines[lo:hi + 1] = new
    return '\n'.join(lines).rstrip('\n') + '\n'


# ─────────────────────────── 검사 ───────────────────────────

def validate(cat, p, text):
    errs, warns = [], []

    for f in p['fields']:
        v = f['value']
        if '\n' in v or '\r' in v:
            errs.append('필드 「%s」 값에 줄바꿈이 있습니다. 표가 그 행부터 깨집니다.' % f['label'])
        if '|' in v:
            errs.append('필드 「%s」 값에 | 가 있습니다. 표가 깨집니다.' % f['label'])
        if re.search(r'^##\s', v) or '## ' in v:
            errs.append('필드 「%s」 값에 섹션 헤딩(## )이 들어 있습니다. '
                        '예전에 이것 때문에 레코드가 깨진 적이 있습니다.' % f['label'])
        if v.count('(') != v.count(')'):
            warns.append('필드 「%s」 의 괄호 짝이 맞지 않습니다.' % f['label'])

    req = REQUIRED.get(cat, [])
    have = {f['label'] for f in p['fields']}
    for r in req:
        if r not in have:
            errs.append('필수 필드 「%s」 가 없습니다.' % r)

    # 표 열 수 정합성 (본문 포함 전체)
    ncol, blk = None, 0
    for n, ln in enumerate(text.split('\n'), 1):
        if not ln.startswith('|'):
            ncol = None
            continue
        if not set(ln.replace('|', '').replace('-', '').replace(':', '').strip()):
            continue
        c = ln.count('|')
        if ncol is None:
            ncol, blk = c, n
        elif c != ncol:
            errs.append('%d행: 표 열 수가 %d행에서 시작한 표(%d개 칸)와 다릅니다 — %s'
                        % (n, blk, ncol - 1, ln[:50]))

    heads = [s['head'] for s in p['secs']]
    if len(heads) != len(set(heads)):
        errs.append('같은 이름의 섹션이 둘 이상입니다: %s' % heads)
    if not heads:
        errs.append('섹션이 하나도 없습니다.')

    return errs, warns


# ─────────────────────────── 읽기 ───────────────────────────

def rec_path(cat, name, folder_mode):
    base = os.path.join(ROOT, cat)
    if folder_mode:
        g = glob.glob(os.path.join(base, name) + '/*.md')
        if g:
            return g[0]
        # 01 의 `_양식_*.md` 처럼 폴더 없이 낱개로 놓인 파일
        loose = os.path.join(base, name + '.md')
        return loose if os.path.exists(loose) else None
    return os.path.join(base, name + '.md')


def _one(cat, n, folder_mode, base):
    """레코드 한 건의 요약. K: 는 느려서 이걸 스레드로 한꺼번에 돌린다."""
    fp = rec_path(cat, n, folder_mode)
    if not fp:
        return None
    body, mt = read_cached(fp)
    if body is None:
        return None
    subs = []
    sibling = os.path.join(base, n)
    try:
        if os.path.isdir(sibling):
            for s in sorted(os.listdir(sibling)):
                sp = os.path.join(sibling, s)
                if os.path.isdir(sp):
                    subs.append({'name': s, 'n': len(os.listdir(sp))})
    except OSError:
        pass
    return {'name': n, 'q': count_q(body), 'tmpl': n.startswith('_'),
            'ex': '기입례' in n, 'subs': subs,
            'path': os.path.relpath(fp, ROOT).replace('/', '\\'), 'mtime': mt}


def scan():
    from concurrent.futures import ThreadPoolExecutor
    jobs, meta = [], []
    for cat, label, folder_mode in CATS:
        base = os.path.join(ROOT, cat)
        names = []
        if os.path.isdir(base):
            if folder_mode:
                names = sorted(d for d in os.listdir(base)
                               if os.path.isdir(os.path.join(base, d)))
                # 폴더 없이 놓인 양식도 레코드 목록에 올린다
                names += sorted(os.path.basename(f)[:-3] for f in glob.glob(base + '/_*.md'))
            else:
                names = sorted(os.path.basename(f)[:-3] for f in glob.glob(base + '/*.md')
                               if os.path.basename(f) != 'index.md')
        meta.append((cat, label, folder_mode, names))
        for n in names:
            jobs.append((cat, n, folder_mode, base))

    with ThreadPoolExecutor(max_workers=16) as ex:
        done = list(ex.map(lambda a: _one(*a), jobs))
    byname = {}
    for (cat, n, _, _), r in zip(jobs, done):
        if r:
            byname[(cat, n)] = r

    cats = []
    for cat, label, folder_mode, names in meta:
        recs = [byname[(cat, n)] for n in names if (cat, n) in byname]
        cats.append({'id': cat, 'label': label, 'folder': folder_mode, 'records': recs})

    tests = []
    for d in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, d)
        if d.startswith('test_') and os.path.isdir(p):
            groups = []
            for g in sorted(os.listdir(p)):
                gp = os.path.join(p, g)
                if os.path.isdir(gp):
                    groups.append({'name': g,
                                   'files': sorted(x for x in os.listdir(gp) if x.endswith('.md'))})
            tests.append({'name': d, 'groups': groups})

    cm = os.path.join(ROOT, 'CLAUDE.md')
    return {'cats': cats, 'tests': tests, 'root': ROOT.replace('/', '\\'),
            'claude': read_cached(cm)[0]}


# 스캔 결과도 들고 있는다. 「새로 읽기」 를 누르면 다시 훑는다.
_SCAN = {'data': None, 'at': 0}
_SCAN_LOCK = threading.Lock()


def scan_cached(force=False):
    with _SCAN_LOCK:
        if not force and _SCAN['data'] is not None:
            return _SCAN['data']
    d = scan()
    with _SCAN_LOCK:
        _SCAN['data'] = d
    return d


def load(cat, name):
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp:
        return None
    body, mt = read_cached(fp)
    if body is None:
        return None
    p = parse(body)
    p.update({'cat': cat, 'name': name, 'raw': body,
              'path': os.path.relpath(fp, ROOT).replace('/', '\\'),
              'full': os.path.join(ROOT, os.path.relpath(fp, ROOT)).replace('/', '\\'),
              'q': count_q(body), 'mtime': mt})
    return p


# ─────────────────────── 마크다운 렌더 ───────────────────────
# 사내망에서 CDN 이 막혀 있어도 되게, 바깥 라이브러리 없이 직접 그린다.
# 레코드가 실제로 쓰는 문법만 다룬다.

def _esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _inline(s):
    s = _esc(s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', s)
    s = re.sub(r'\[\[([^\]]+)\]\]', r'<code>\1</code>', s)
    s = re.sub(r'(\(확인 필요[^)]*\))', r'<span class="q">\1</span>', s)
    return s


def _row(ln):
    cells = ln.strip().strip('|').split('|')
    return [c.strip() for c in cells]


def md_html(text):
    out, lines, i = [], text.split('\n'), 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if not s:
            i += 1
            continue

        if s.startswith('#'):
            n = len(s) - len(s.lstrip('#'))
            out.append('<h%d>%s</h%d>' % (min(n, 6), _inline(s[n:].strip()), min(n, 6)))
            i += 1
            continue

        # 표
        if s.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[i + 1].strip()):
            head = _row(s)
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                body.append(_row(lines[i].strip()))
                i += 1
            h = ['<div class="tblwrap"><table><thead><tr>']
            h += ['<th>%s</th>' % _inline(c) for c in head]
            h.append('</tr></thead><tbody>')
            for r in body:
                h.append('<tr>' + ''.join('<td>%s</td>' % _inline(c) for c in r) + '</tr>')
            h.append('</tbody></table></div>')
            out.append(''.join(h))
            continue

        if s.startswith('>'):
            buf = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip().lstrip('>').strip())
                i += 1
            out.append('<blockquote>%s</blockquote>' % _inline(' '.join(buf)))
            continue

        # 목록 — 들여쓰기로 깊이를 잡는다
        if re.match(r'^[-*·•]\s', s):
            items, depth = [], []
            while i < len(lines):
                cur = lines[i]
                cs = cur.strip()
                if not cs:
                    if (i + 1 < len(lines) and re.match(r'^\s*[-*·•]\s', lines[i + 1])):
                        i += 1
                        continue
                    break
                if not re.match(r'^\s*[-*·•]\s', cur):
                    # 제목·표·인용은 목록의 보조 줄이 아니다. 빈 줄 없이 이어져도
                    # 삼키면 그 섹션이 통째로 사라진 것처럼 보인다.
                    if re.match(r'^\s*(#{1,6}\s|\||>)', cur):
                        break
                    if items:
                        items[-1][1].append(cs)
                        i += 1
                        continue
                    break
                ind = len(cur) - len(cur.lstrip())
                items.append([ind, [re.sub(r'^[-*·•]\s+', '', cs)]])
                i += 1
            lvl, h = [], []
            for ind, parts in items:
                while lvl and ind < lvl[-1]:
                    h.append('</ul>')
                    lvl.pop()
                if not lvl or ind > lvl[-1]:
                    h.append('<ul>')
                    lvl.append(ind)
                first = _inline(parts[0])
                rest = ''.join('<div class="sub">%s</div>' % _inline(x) for x in parts[1:])
                h.append('<li>%s%s</li>' % (first, rest))
            h += ['</ul>'] * len(lvl)
            out.append(''.join(h))
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r'^\s*([-*·•]\s|\||>|#)', lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append('<p>%s</p>' % _inline(' '.join(buf)))
        else:
            i += 1
    return '\n'.join(out)


# ─────────────────────────── 잠금 ───────────────────────────
# 여러 사람이 각자 PC 에서 같은 K: 를 본다. 그래서 잠금은 공유 폴더에 둔다.
# 레코드마다 파일 하나. 만들기(O_EXCL)가 원자적이라 동시에 둘이 못 잡는다.

LOCK_DIR = '_편집중'
LOCK_TTL = 15 * 60      # 이 시간 동안 신호가 없으면 끊긴 잠금으로 본다

# 편집자 이름은 접속자가 보낸다. 서버 계정이 아니다 —
# 서버 한 대에 여러 명이 붙으면 서버 계정은 모두 같아서 잠금이 무의미해진다.
# 이 이름은 본인이 적는 것이라 신원 증명이 아니다. "누가 보고 있는지" 를 알리는 용도다.
def clean_who(s):
    s = (s or '').strip()
    s = re.sub(r'[\x00-\x1f|/\\]', '', s)[:40]
    return s or '이름 없음'


def _lock_path(cat, name):
    d = os.path.join(ROOT, LOCK_DIR)
    key = re.sub(r'[^0-9A-Za-z가-힣._-]', '_', '%s__%s' % (cat, name))
    return d, os.path.join(d, key + '.json')


def lock_read(cat, name, who=None):
    _, fp = _lock_path(cat, name)
    try:
        d = json.loads(open(fp, encoding='utf-8').read())
    except Exception:
        return None
    age = time.time() - d.get('beat', 0)
    d['age'] = int(age)
    d['stale'] = age > LOCK_TTL
    d['mine'] = bool(who) and d.get('who') == who
    return d


def lock_take(cat, name, who, steal=False):
    who = clean_who(who)
    d, fp = _lock_path(cat, name)
    os.makedirs(d, exist_ok=True)
    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale'] and not steal:
        return {'ok': False, 'lock': cur}
    rec = {'who': who, 'at': time.time(), 'beat': time.time(),
           'cat': cat, 'name': name}
    blob = json.dumps(rec, ensure_ascii=False).encode('utf-8')

    # 끊긴 잠금을 가져올 때도 그냥 덮어쓰면, 둘이 동시에 가져갈 경우
    # 둘 다 "내 것" 으로 믿고 편집에 들어간다. 지우고 O_EXCL 로 다시 만들어
    # 한 사람만 이기게 한다.
    if cur:
        try:
            os.remove(fp)
        except OSError:
            pass
    try:
        fd = os.open(fp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        again = lock_read(cat, name, who)
        if again and not again['mine']:
            return {'ok': False, 'lock': again}   # 경쟁에서 졌다
        return {'ok': True, 'lock': again}
    with os.fdopen(fd, 'wb') as f:
        f.write(blob)

    # 쓴 뒤 다시 읽어 내가 주인인지 확인한다
    back = lock_read(cat, name, who)
    if not back or not back['mine']:
        return {'ok': False, 'lock': back}
    return {'ok': True, 'lock': back}


def lock_beat(cat, name, who):
    who = clean_who(who)
    cur = lock_read(cat, name, who)
    if not cur or not cur['mine']:
        return {'ok': False, 'lock': cur}
    _, fp = _lock_path(cat, name)
    cur2 = {k: cur[k] for k in ('who', 'at', 'cat', 'name') if k in cur}
    cur2['beat'] = time.time()
    open(fp, 'w', encoding='utf-8').write(json.dumps(cur2, ensure_ascii=False))
    return {'ok': True, 'lock': lock_read(cat, name, who)}


def lock_free(cat, name, who, force=False):
    who = clean_who(who)
    cur = lock_read(cat, name, who)
    if cur and not cur['mine'] and not cur['stale'] and not force:
        return {'ok': False, 'lock': cur}
    _, fp = _lock_path(cat, name)
    try:
        os.remove(fp)
    except OSError:
        pass
    return {'ok': True, 'lock': None}


def locks_all(who=None):
    d = os.path.join(ROOT, LOCK_DIR)
    out = {}
    if not os.path.isdir(d):
        return out
    for f in os.listdir(d):
        if not f.endswith('.json'):
            continue
        try:
            r = json.loads(open(os.path.join(d, f), encoding='utf-8').read())
        except Exception:
            continue
        age = time.time() - r.get('beat', 0)
        if age > LOCK_TTL:
            continue
        out['%s/%s' % (r.get('cat'), r.get('name'))] = {
            'who': r.get('who'), 'age': int(age), 'mine': r.get('who') == who}
    return out


# ─────────────────────────── 쓰기 ───────────────────────────

def backup(fp, cat, name):
    d = os.path.join(BACKUP, cat, re.sub(r'[^0-9A-Za-z가-힣._-]', '_', name))
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(d, stamp + '.md')
    # 초 단위라 같은 초에 두 번 저장하면 직전 백업을 덮는다. 번호를 붙인다.
    k = 1
    while os.path.exists(dst):
        dst = os.path.join(d, '%s-%d.md' % (stamp, k))
        k += 1
    shutil.copy2(fp, dst)
    keep = sorted(glob.glob(d + '/*.md'))
    for old in keep[:-30]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dst


INDEX_COL = {
    '02_보유자산': {'기준 시점': 2, '담당자': -1},
    '03_수행체계': {'기준 시점': 2},
}


def sync_index(cat, name, fields):
    """index.md 의 해당 행에서 레코드와 어긋난 칸을 맞춘다."""
    cols = INDEX_COL.get(cat)
    if not cols:
        return []
    ip = os.path.join(ROOT, cat, 'index.md')
    if not os.path.exists(ip):
        return []
    t = open(ip, encoding='utf-8').read()
    lines = t.split('\n')
    fv = {f['label']: f['value'] for f in fields}
    changed = []
    for i, ln in enumerate(lines):
        if not ln.startswith('| ') or '---' in ln:
            continue
        cells = ln.split(' | ')
        if cells[0][2:].strip() != name:
            continue
        for label, ci in cols.items():
            if label not in fv:
                continue
            new = fv[label]
            # 레코드 쪽 긴 꼬리표는 index 에서 짧게
            new = re.sub(r' \(모판·목록 두 자료가 다름\)$', '', new)
            # index 칸은 사람이 줄여 쓴 요약이다. 잘라서 덮으면 그 요약이 망가지므로
            # 통째로 들어가는 짧은 값일 때만 맞춘다.
            if len(new) > 46:
                continue
            old = cells[ci].rstrip('|').strip()
            if old != new:
                cells[ci] = new + (' |' if ci == -1 else '')
                changed.append('%s: %s → %s' % (label, old[:28], new[:28]))
        if changed:
            lines[i] = ' | '.join(cells)
        break
    if changed:
        backup(ip, cat, 'index')  # 공유 폴더에 .bak 을 남기지 않는다
        open(ip, 'w', encoding='utf-8', newline='').write('\n'.join(lines))
        with _CACHE_LOCK:
            _CACHE.pop(ip, None)
    return changed


def save(cat, name, payload, who=None):
    fm = dict((c, f) for c, _, f in CATS)[cat]
    fp = rec_path(cat, name, fm)
    if not fp or not os.path.exists(fp):
        return {'ok': False, 'errors': ['레코드 파일을 찾지 못했습니다.']}

    who = clean_who(who or payload.get('who'))
    held = lock_read(cat, name, who)
    if not held or not held['mine']:
        return {'ok': False, 'lock': held, 'errors': [
            '편집 잠금이 없습니다. %s' % ('지금은 %s 님이 편집 중입니다.' % held['who']
                                   if held else '「수정하기」를 다시 눌러 주세요.')]}

    disk = os.path.getmtime(fp)
    if payload.get('mtime') and abs(disk - payload['mtime']) > 1:
        return {'ok': False, 'stale': True, 'errors': [
            '이 파일이 편집기 밖에서 바뀌었습니다. 새로 고쳐 다시 여세요. '
            '(지금 덮어쓰면 그 변경이 사라집니다)']}

    cur = parse(read_cached(fp)[0])
    new_fields = payload.get('fields') or cur['fields']
    text = assemble(cur, fields=new_fields, secs=payload.get('secs') or None)
    chk = dict(cur)
    chk['fields'] = new_fields
    errs, warns = validate(cat, chk, text)
    if errs:
        return {'ok': False, 'errors': errs, 'warnings': warns}

    bk = backup(fp, cat, name)
    open(fp, 'w', encoding='utf-8', newline='').write(text)
    with _CACHE_LOCK:
        _CACHE[fp] = (os.path.getmtime(fp), text)
    idx = sync_index(cat, name, new_fields)
    scan_cached(True)
    lock_free(cat, name, who)
    return {'ok': True, 'warnings': warns, 'backup': os.path.relpath(bk, HERE),
            'index': idx, 'mtime': os.path.getmtime(fp), 'q': count_q(text)}


# ─────────────────────── 자동 갱신 ───────────────────────
# 아무도 손대지 않아도 최신 코드로 따라가게 한다.
# 서버가 주기적으로 git 으로 당겨 받고, 받았으면 스스로 다시 뜬다.
# 열어 둔 화면은 버전이 바뀐 것을 보고 알아서 새로 고친다.

RESTART = {'want': False, 'srv': None}
# 사내 프록시를 거치면 GitHub 응답이 몇 분씩 걸린다. 짧게 잡으면
# 자동 갱신이 매번 타임아웃으로 조용히 건너뛰어, 영영 최신이 되지 않는다.
GIT_TIMEOUT = 300
_UPD = {'fail': 0}


def version():
    """화면 파일이 바뀌면 값이 바뀐다. 브라우저가 이걸로 갱신을 안다."""
    h = hashlib.sha1()
    for f in ('app.html', 'server.py'):
        p = os.path.join(HERE, f)
        try:
            h.update(('%s:%s' % (f, os.path.getmtime(p))).encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _git(*args, timeout=None):
    return subprocess.run(['git'] + list(args), cwd=HERE,
                          timeout=timeout or GIT_TIMEOUT,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def update_once():
    """새 커밋이 있으면 당겨 받는다. 받았으면 True."""
    if not os.path.isdir(os.path.join(HERE, '.git')):
        return False
    try:
        if not _git('remote').stdout.strip():
            return False
        if _git('fetch', '--quiet').returncode != 0:
            raise RuntimeError('git fetch 실패')
        br = _git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() or 'HEAD'
        up = _git('rev-parse', '--abbrev-ref', '@{u}').stdout.strip()
        if not up:
            return False
        local = _git('rev-parse', 'HEAD').stdout.strip()
        remote = _git('rev-parse', up).stdout.strip()
        if not local or local == remote:
            return False
        if _git('status', '--porcelain').stdout.strip():
            print('  [자동 갱신] 고쳐 둔 파일이 있어 건너뜁니다.')
            return False
        r = _git('pull', '--ff-only', '--quiet')
        if r.returncode != 0:
            print('  [자동 갱신] 당기지 못했습니다: %s' % (r.stderr or '').strip()[:120])
            return False
        print('  [자동 갱신] %s → %s 로 올렸습니다.' % (local[:7], remote[:7]))
        _UPD['fail'] = 0
        return True
    except subprocess.TimeoutExpired:
        _UPD['fail'] += 1
        print('  [자동 갱신] GitHub 응답이 %d초 안에 안 왔습니다 (연속 %d회).'
              % (GIT_TIMEOUT, _UPD['fail']))
        if _UPD['fail'] == 3:
            print('  ** 자동 갱신이 계속 실패합니다. 프록시나 자격증명을 확인하세요.')
            print('     --git-timeout 으로 더 늘리거나, --no-auto-update 로 끄고 직접 받으세요.')
        return False
    except Exception as e:
        _UPD['fail'] += 1
        print('  [자동 갱신] 건너뜀 (연속 %d회): %s' % (_UPD['fail'], e))
        if _UPD['fail'] == 3:
            print('  ** 자동 갱신이 계속 실패합니다. 서버에서 git pull 이 되는지 확인하세요.')
        return False


def updater(every):
    blocked = 0
    while True:
        time.sleep(every)
        try:
            held = locks_all()
            if held:
                # 누가 편집 중이면 다음 차례에. 다만 편집 화면을 열어 둔 채
                # 자리를 비우면 하트비트가 잠금을 계속 살려서 갱신이 영영 막힌다.
                # 한 시간 넘게 막히면 누가 잡고 있는지 알린다.
                blocked += every
                if blocked >= 3600:
                    who = ', '.join(sorted({v.get('who') or '?' for v in held.values()}))
                    print('  ** 자동 갱신이 %d분째 막혀 있습니다 — %s 님이 편집 중입니다.'
                          % (blocked // 60, who))
                    print('     편집 화면을 열어 둔 채 자리를 비운 것일 수 있습니다.')
                    blocked = 0
                continue
            blocked = 0
            if update_once():
                print('  [자동 갱신] 새 코드로 다시 뜹니다.')
                RESTART['want'] = True
                if RESTART['srv']:
                    threading.Thread(target=RESTART['srv'].shutdown, daemon=True).start()
                return
        except Exception:
            pass


# ─────────────────────────── HTTP ───────────────────────────

SAVE_LOCK = threading.Lock()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 브라우저가 탭을 닫으면 끊긴 연결로 보인다. 서버 문제가 아니라
        # 매번 통째로 찍히면 진짜 오류를 가린다.
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)
    # Windows 에서는 이걸 켜 두면 이미 떠 있는 편집기가 있어도 또 뜬다.
    # 그러면 요청이 옛 서버로 가 버려서, 고친 게 반영 안 된 것처럼 보인다.
    allow_reuse_address = False


class H(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        # send_error 는 여기에 HTTPStatus 를 넘긴다. 문자열로 단정하면
        # 404 가 날 때마다 TypeError 로 응답 없이 연결이 끊긴다.
        first = args[0] if args else ''
        if isinstance(first, str) and '/api/' in first:
            sys.stderr.write('  %s\n' % first)

    def who(self):
        import urllib.parse as _u
        raw = self.headers.get('X-Editor-Who') or ''
        try:
            raw = _u.unquote(raw)
        except Exception:
            pass
        return clean_who(raw)

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self):
        # 서버를 새 버전으로 올렸는데 브라우저가 옛 화면을 들고 있으면
        # 고친 게 반영 안 된 것처럼 보인다. 화면 파일은 캐시하지 않는다.
        if self.path.split('?')[0] in ('/', '/app.html'):
            self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs, unquote
        u = urlparse(self.path)
        if u.path == '/':
            self.path = '/app.html'
            return super().do_GET()
        if u.path == '/api/tree':
            try:
                force = parse_qs(u.query).get('force', ['0'])[0] == '1'
                return self._json(scan_cached(force))
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        if u.path == '/api/record':
            try:
                q = parse_qs(u.query)
                cat = unquote(q.get('cat', [''])[0])
                name = unquote(q.get('name', [''])[0])
                if cat not in dict((c, f) for c, _, f in CATS):
                    return self._json({'error': '없는 카테고리: %s' % cat}, 404)
                r = load(cat, name)
                if r:
                    r['html'] = md_html(r['raw'])
                    r['lock'] = lock_read(cat, name, self.who())
                    r['me'] = self.who()
                return self._json(r or {'error': '없는 레코드'}, 200 if r else 404)
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._json({'error': '레코드를 읽지 못했습니다: %s' % e}, 500)
        if u.path == '/api/locks':
            try:
                me = self.who()
                return self._json({'me': me, 'locks': locks_all(me), 'ver': version()})
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        if u.path.startswith('/api/'):
            return self._json({'error': 'unknown'}, 404)
        return super().do_GET()

    def do_POST(self):
        from urllib.parse import urlparse
        u = urlparse(self.path)
        if u.path not in ('/api/save', '/api/lock', '/api/unlock', '/api/beat'):
            return self._json({'error': 'unknown'}, 404)
        n = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception as e:
            return self._json({'ok': False, 'errors': ['요청을 읽지 못했습니다: %s' % e]}, 400)

        if u.path != '/api/save':
            c, nm = body.get('cat'), body.get('name')
            try:
                me = self.who()
                if u.path == '/api/lock':
                    return self._json(lock_take(c, nm, me, bool(body.get('steal'))))
                if u.path == '/api/unlock':
                    return self._json(lock_free(c, nm, me, bool(body.get('force'))))
                return self._json(lock_beat(c, nm, me))
            except Exception as e:
                return self._json({'ok': False, 'errors': ['잠금 처리 중 오류: %s' % e]}, 500)
        try:
            with SAVE_LOCK:
                return self._json(save(body['cat'], body['name'], body, self.who()))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json({'ok': False, 'errors': ['저장 중 오류: %s' % e]}, 500)


def main():
    global ROOT
    # 한글 윈도우 콘솔은 기본이 cp949 라서 — 「」 ※ 같은 글자를 못 찍고 죽는다.
    # .bat 로 켜면 chcp 65001 이 깔리지만, 직접 띄우거나 서비스로 돌리면 그게 없다.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8777)
    ap.add_argument('--host', default='127.0.0.1',
                    help='여러 명이 쓰는 서버면 0.0.0.0')
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--no-auto-update', action='store_true',
                    help='git 으로 스스로 최신 코드를 받지 않는다')
    ap.add_argument('--update-every', type=int, default=600,
                    help='자동 갱신 확인 주기(초). 기본 10분')
    ap.add_argument('--git-timeout', type=int, default=300,
                    help='git 한 번에 기다릴 시간(초). 프록시가 느리면 늘린다')
    a = ap.parse_args()
    ROOT = a.root.replace('\\', '/').rstrip('/')
    global GIT_TIMEOUT
    GIT_TIMEOUT = a.git_timeout

    if not os.path.isdir(ROOT):
        print('지식시스템 폴더를 찾지 못했습니다: %s' % ROOT)
        print('K드라이브가 연결돼 있는지 확인하세요. 다른 경로면 --root 로 지정하세요.')
        sys.exit(1)

    os.makedirs(BACKUP, exist_ok=True)
    s = scan_cached(True)
    tot = sum(len([r for r in c['records'] if not r['tmpl']]) for c in s['cats'])
    shown = '127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host
    url = 'http://%s:%d/' % (shown, a.port)
    print('제안전략 지식시스템 편집기')
    print('  원본  %s' % ROOT.replace('/', '\\'))
    print('  백업  %s' % BACKUP)
    print('  레코드 %d건' % tot)
    print('  주소  %s' % url)
    if a.host not in ('127.0.0.1', 'localhost', '::1'):
        print()
        print('  ** %s 로 열려 있습니다 — 이 PC 밖에서도 들어옵니다.' % a.host)
        print('     로그인이 없습니다. 망에서 포트에 닿는 사람은 누구나 레코드를 고칠 수 있습니다.')
        print('     사내망 안에서만 쓰거나, 앞에 인증 프록시를 두세요.')
    print('  멈추려면 Ctrl+C')
    if not a.no_open and a.host in ('127.0.0.1', 'localhost'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv = Server((a.host, a.port), H)
    except OSError:
        print('\n%d번 포트를 이미 쓰고 있습니다.' % a.port)
        print('편집기가 이미 켜져 있는지 보세요 — %s' % url)
        print('다른 포트로 켜려면: python server.py --port 8778')
        sys.exit(1)
    RESTART['srv'] = srv
    if not a.no_auto_update:
        if os.path.isdir(os.path.join(HERE, '.git')):
            update_once()
            threading.Thread(target=updater, args=(a.update_every,), daemon=True).start()
            print('  자동 갱신 %d분마다 (끄려면 --no-auto-update)' % max(1, a.update_every // 60))
        else:
            print('  자동 갱신 없음 — git 으로 받은 폴더가 아닙니다')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n멈췄습니다.')
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    if RESTART['want']:
        time.sleep(1)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])


if __name__ == '__main__':
    main()
