"""ScanPathViewer — Laser scan-path / G-code layer viewer (SLS, SLM, laser marking, CNC)
扫描路径查看器 — 面向SLS/SLM/激光打标/CNC的逐层G代码路径查看工具

Usage / 用法:
    python scan_path_viewer.py [directory or .nc file]
    Or just run it and drag & drop a folder onto the window.
    也可直接运行后把数据文件夹拖进窗口。

Supported inputs / 支持格式:
    1) A folder of layer NC files:  1.nc, 2.nc ... or 1-0-0.nc style grouped files
    2) A "merged" JSON index folder (see README for the simple schema)
    3) A single .nc file

License: MIT
"""
import os, re, sys, json, math, argparse, locale, traceback
from collections import defaultdict

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSpinBox, QLabel, QFileDialog, QStatusBar,
    QMessageBox, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

import matplotlib
matplotlib.use('Qt5Agg')
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt


# ============================================================
# i18n
# ============================================================
STRINGS = {
    'title':        ('ScanPathViewer — 扫描路径查看器', 'ScanPathViewer'),
    'open_dir':     ('打开目录', 'Open Folder'),
    'open_file':    ('打开文件', 'Open File'),
    'layer':        ('层号:', 'Layer:'),
    'play':         ('▶ 播放', '▶ Play'),
    'stop':         ('⏸ 停止', '⏸ Stop'),
    'g01':          ('扫描线 G01', 'Scan G01'),
    'g00':          ('跳转线 G00', 'Travel G00'),
    'fit':          ('适应', 'Fit'),
    'measure':      ('📏 测量', '📏 Measure'),
    'snapshot':     ('💾 截图', '💾 Snapshot'),
    'ready':        ('就绪 — 打开目录 或 拖放文件夹到窗口',
                     'Ready — open a folder or drag & drop one onto the window'),
    'choose_dir':   ('选择G代码目录', 'Choose G-code folder'),
    'choose_file':  ('选择NC文件', 'Choose NC file'),
    'nc_filter':    ('NC (*.nc);;全部 (*)', 'NC (*.nc);;All (*)'),
    'no_data':      ('未找到数据', 'No data found'),
    'unknown_fmt':  ('未识别数据格式', 'Unrecognized data format'),
    'err':          ('错误', 'Error'),
    'load_fail':    ('加载失败', 'Load failed'),
    'render_fail':  ('渲染失败', 'Render failed'),
    'scan_fail':    ('扫描目录失败:\n', 'Failed to scan folder:\n'),
    'read_fail':    ('无法读取文件:\n', 'Cannot read file:\n'),
    'read_layer_fail': ('读取层{0}数据失败:\n', 'Failed to load layer {0}:\n'),
    'show_layer_fail': ('显示层{0}失败:\n', 'Failed to render layer {0}:\n'),
    'single_file':  ('单文件 | G01 {0} | G00 {1}', 'Single file | G01 {0} | G00 {1}'),
    'loaded':       ('{0} | L{1}~L{2} | 首层已加载', '{0} | L{1}~L{2} | first layer loaded'),
    'cache':        ('G01 {0} | G00 {1} | 缓存 {2}/{3}层', 'G01 {0} | G00 {1} | cached {2}/{3}'),
    'layers':       ('{0}层', '{0} layers'),
    'plot_title':   ('第 {0} 层  |  扫描 {1} 段  |  跳转 {2} 段',
                     'Layer {0}  |  {1} scan segs  |  {2} travel segs'),
    'measured':     ('测量: {0:.2f} mm', 'Measured: {0:.2f} mm'),
    'save_title':   ('保存截图', 'Save snapshot'),
    'saved':        ('已保存: {0}', 'Saved: {0}'),
    'load_layer_fail_sb': ('加载层{0}失败: {1}', 'Load layer {0} failed: {1}'),
    'layer_snap':   ('层 {0} 不存在，已跳到最近层 {1}', 'Layer {0} missing, snapped to {1}'),
    'lang_btn':     ('EN', '中文'),
}

class I18N:
    def __init__(self):
        try:
            lang = (locale.getlocale()[0] or '')
        except Exception:
            lang = ''
        low = lang.lower()
        self.zh = low.startswith('zh') or 'chinese' in low
    def tr(self, key):
        pair = STRINGS[key]
        return pair[0] if self.zh else pair[1]
    def toggle(self):
        self.zh = not self.zh

TR = I18N()


# ============================================================
# Data types
# ============================================================
class GCodeLayer:
    __slots__ = ('n', 'g00', 'g01')
    def __init__(self, n):
        self.n = n
        self.g00 = []
        self.g01 = []
    def c00(self): return len(self.g00)
    def c01(self): return len(self.g01)


# ============================================================
# NC Parser
# ============================================================
RX_CMD = re.compile(r'(?:N\d+\s*)?(G0?[0-3])(?!\d)\s*(.*)', re.IGNORECASE)
RX_X = re.compile(r'X([\-\d.]+)')
RX_Y = re.compile(r'Y([\-\d.]+)')
RX_I = re.compile(r'I([\-\d.]+)')
RX_J = re.compile(r'J([\-\d.]+)')
RX_R = re.compile(r'R([\-\d.]+)')


def _arc_segments(x0, y0, x1, y1, i, j, cw, n_seg=48):
    """Discretize G02/G03 (I/J center form) into line segments.
    Center = (x0+i, y0+j); cw=True → G02 clockwise."""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    if r < 1e-12:
        return []
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if cw:
        sweep = a0 - a1
        while sweep < 0: sweep += 2 * math.pi
    else:
        sweep = a1 - a0
        while sweep < 0: sweep += 2 * math.pi
    if sweep < 1e-9:
        sweep = 2 * math.pi      # full circle
    n = max(2, int(math.ceil(sweep / (2 * math.pi) * n_seg)))
    pts = []
    for k in range(n + 1):
        t = a0 + (-sweep if cw else sweep) * k / n
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) for k in range(n)]


def _arc_segments_r(x0, y0, x1, y1, r, cw, n_seg=48):
    """Discretize G02/G03 (R form) into line segments."""
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d < 1e-12:
        return []
    if abs(r) < d / 2:
        r = math.copysign(d / 2, r)   # clamp impossible radius
    h = math.sqrt(max(r * r - (d / 2) ** 2, 0.0))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    s = (-1.0 if cw else 1.0) * (1.0 if r >= 0 else -1.0)   # R sign picks side; flip for G02 (cw) per CNC convention
    ox, oy = -dy / d, dx / d          # unit normal to chord
    cx, cy = mx + s * h * ox, my + s * h * oy
    return _arc_segments(x0, y0, x1, y1, cx - x0, cy - y0, cw, n_seg)


def parse_nc(filepath):
    g00, g01 = [], []
    lx = ly = None
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.split(';', 1)[0].strip()   # strip trailing comments
            if not line: continue
            m = RX_CMD.match(line)
            if not m: continue
            cmd = 'G0' + m.group(1).upper()[-1]   # normalize G1/G2/G3 -> G01/G02/G03
            rest = m.group(2)
            if cmd in ('G02', 'G03'):
                xm = RX_X.search(rest)
                ym = RX_Y.search(rest)
                if not xm or not ym or lx is None or ly is None: continue
                try:
                    x, y = float(xm.group(1)), float(ym.group(1))
                except ValueError: continue
                im, jm = RX_I.search(rest), RX_J.search(rest)
                if im and jm:
                    try:
                        g01.extend(_arc_segments(lx, ly, x, y,
                                                 float(im.group(1)), float(jm.group(1)),
                                                 cmd == 'G02'))
                    except (ValueError, ZeroDivisionError):
                        pass
                else:
                    rm = RX_R.search(rest)
                    if rm:
                        try:
                            g01.extend(_arc_segments_r(lx, ly, x, y,
                                                       float(rm.group(1)), cmd == 'G02'))
                        except (ValueError, ZeroDivisionError):
                            pass
                lx, ly = x, y
                continue
            xm = RX_X.search(rest)
            ym = RX_Y.search(rest)
            if not xm or not ym: continue
            try:
                x, y = float(xm.group(1)), float(ym.group(1))
            except ValueError: continue
            if lx is not None and ly is not None and (lx != x or ly != y):
                seg = (lx, ly, x, y)
                if cmd == 'G00': g00.append(seg)
                else: g01.append(seg)
            lx, ly = x, y
    return g00, g01


# ============================================================
# Layer index & loading
# ============================================================
class LayerIndex:
    def __init__(self, source_type, entries, bounds=None):
        self.source_type = source_type  # 'merged', 'nc', 'grouped-nc'
        self.entries = entries          # {ln: info}
        self.bounds = bounds
    def __len__(self): return len(self.entries)
    def lnums(self): return sorted(self.entries.keys())

    def load_layer(self, ln):
        if ln not in self.entries: return None
        info = self.entries[ln]
        if self.source_type == 'merged':
            return self._load_merged(info)
        else:
            return self._load_nc(info)

    def _load_merged(self, info):
        ly = GCodeLayer(info['num'])
        with open(info['path'], 'r', encoding='utf-8') as f:
            data = json.load(f)['data']
        for seg in data:
            tup = (float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3]))
            if seg[4] == 0: ly.g00.append(tup)
            else: ly.g01.append(tup)
        return ly

    def _load_nc(self, info):
        ly = GCodeLayer(info['num'])
        seen00, seen01 = set(), set()
        for fp in info['files']:
            g00s, g01s = parse_nc(fp)
            for s in g00s:
                if s not in seen00: seen00.add(s); ly.g00.append(s)
            for s in g01s:
                if s not in seen01: seen01.add(s); ly.g01.append(s)
        return ly


def build_index(path):
    """Scan directory, return (LayerIndex, label) or (None, error)."""
    try:
        # 1) merged JSON index (fast path, optional)
        midx = os.path.join(path, 'merged', 'index.json')
        if os.path.isfile(midx):
            base = os.path.join(path, 'merged')
            with open(midx, 'r', encoding='utf-8') as f:
                idx = json.load(f)
            entries = {}
            for e in idx['layers']:
                entries[e['num']] = {
                    'num': e['num'],
                    'path': os.path.normpath(os.path.join(base, e['file'])),
                }
            if not entries:
                return None, TR.tr('no_data')
            b = idx.get('bounds')
            bounds = (b['minX'], b['minY'], b['maxX'], b['maxY']) if b else None
            return LayerIndex('merged', entries, bounds), "merged — " + TR.tr('layers').format(len(entries))

        # 2) grouped NC files:  layer-part-part.nc  (e.g. 12-0-1.nc)
        grouped = [f for f in os.listdir(path) if re.match(r'\d+-\d+-\d+\.nc$', f, re.IGNORECASE)]
        if grouped:
            grp = defaultdict(list)
            for f in grouped:
                n = os.path.splitext(f)[0].split('-')
                try: grp[int(n[0])].append(os.path.join(path, f))
                except ValueError: pass
            entries = {ln: {'num': ln, 'files': fps} for ln, fps in grp.items()}
            return LayerIndex('grouped-nc', entries), "grouped NC — " + TR.tr('layers').format(len(entries))

        # 3) plain NC files:  1.nc, 2.nc ...  (optionally under G/<sub>/)
        ncf = []
        for f in os.listdir(path):
            if f.lower().endswith('.nc'): ncf.append(os.path.join(path, f))
        gd = os.path.join(path, 'G')
        if os.path.isdir(gd):
            for sub in os.listdir(gd):
                sp = os.path.join(gd, sub)
                if os.path.isdir(sp):
                    for f in os.listdir(sp):
                        if f.lower().endswith('.nc'): ncf.append(os.path.join(sp, f))
        if ncf:
            grp = defaultdict(list)
            for fp in ncf:
                m = re.match(r'(\d+)', os.path.splitext(os.path.basename(fp))[0])
                if m: grp[int(m.group(1))].append(fp)
            entries = {ln: {'num': ln, 'files': fps} for ln, fps in grp.items()}
            return LayerIndex('nc', entries), "NC — " + TR.tr('layers').format(len(entries))

        return None, TR.tr('unknown_fmt')
    except Exception as e:
        return None, f"index failed: {e}\n{traceback.format_exc()}"


def auto_find_data():
    """Try to find scan-path data near the executable or current directory."""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in [exe_dir, script_dir, os.getcwd()]:
        try:
            idx, label = build_index(d)
            if idx is not None:
                return idx, label, d
        except Exception:
            continue
    return None, "", ""


# ============================================================
# Background preloader
# ============================================================
class PreloadThread(QThread):
    layerReady = pyqtSignal(int, object)

    def __init__(self, idx, lnums):
        super().__init__()
        self.idx = idx
        self.lnums = lnums
        self._cancelled = False

    def cancel(self): self._cancelled = True

    def run(self):
        for ln in self.lnums:
            if self._cancelled: break
            try:
                ly = self.idx.load_layer(ln)
                self.layerReady.emit(ln, ly)
            except Exception:
                pass


# ============================================================
# Matplotlib Canvas
# ============================================================
class Canvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(10, 8), dpi=100, facecolor='#1a1a2e')
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#16213e')
        super().__init__(self.fig)
        self.fig.tight_layout(pad=0.5)

    def draw_layer(self, ly, bounds=None, show_g01=True, show_g00=True):
        self.ax.clear()
        self.ax.set_facecolor('#16213e')
        if ly.g01 and show_g01:
            xs, ys = [], []
            for x1,y1,x2,y2 in ly.g01: xs.extend([x1,x2,None]); ys.extend([y1,y2,None])
            self.ax.plot(xs, ys, color='#ff4444', lw=0.4, solid_capstyle='round',
                         label=f'G01 ({ly.c01()})')
        if ly.g00 and show_g00:
            xs, ys = [], []
            for x1,y1,x2,y2 in ly.g00: xs.extend([x1,x2,None]); ys.extend([y1,y2,None])
            self.ax.plot(xs, ys, color='#4488ff', lw=0.3, ls=':', alpha=0.5,
                         label=f'G00 ({ly.c00()})')
        if bounds:
            self.ax.set_xlim(bounds[0]-2, bounds[2]+2)
            self.ax.set_ylim(bounds[1]-2, bounds[3]+2)
        self.ax.set_xlabel('X (mm)', color='#aaa')
        self.ax.set_ylabel('Y (mm)', color='#aaa')
        self.ax.set_title(TR.tr('plot_title').format(ly.n, ly.c01(), ly.c00()),
                          color='#fff', fontsize=11)
        self.ax.tick_params(colors='#aaa', labelsize=8)
        self.ax.set_aspect('equal')
        self.ax.grid(True, color='#333', lw=0.3, alpha=0.5)
        for sp in self.ax.spines.values(): sp.set_color('#444')
        if ly.g00 or ly.g01:
            self.ax.legend(loc='upper right', facecolor='#1a1a2e',
                           edgecolor='#444', labelcolor='#ccc', fontsize=8)
        self.fig.tight_layout(pad=0.5)
        self.draw()


# ============================================================
# Main Window
# ============================================================
class Viewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setGeometry(100, 100, 1400, 900)
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            QMainWindow{background:#1a1a2e}
            QLabel{color:#ccc;font-size:13px}
            QPushButton{background:#0f3460;color:#e0e0e0;border:1px solid #1a508b;border-radius:4px;padding:6px 14px;font-size:13px}
            QPushButton:hover{background:#1a508b}
            QPushButton:disabled{background:#1a1a2e;color:#555}
            QPushButton:checked{background:#e94560;border-color:#e94560}
            QSpinBox{background:#16213e;color:#ccc;border:1px solid #1a508b;border-radius:4px;padding:4px;font-size:14px;min-width:80px}
            QCheckBox{color:#ccc;font-size:12px;spacing:6px}
        """)

        self._idx = None
        self._cache = {}
        self._lnums = []
        self._pos = 0
        self._bounds = None
        self._show_g01 = True
        self._show_g00 = True
        self._anim_timer = None
        self._preloader = None
        self._pan_start = None
        self._pan_xlim = self._pan_ylim = None
        self._measuring = False
        self._meas_pts = []
        self._accum_bounds = None
        self._user_view = False

        self._init_ui()
        self._apply_lang()

    def _init_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        lo = QVBoxLayout(cw); lo.setContentsMargins(8,8,8,8)

        tb = QHBoxLayout()
        self.btn_open = QPushButton(); self.btn_open.clicked.connect(self._open_dir)
        tb.addWidget(self.btn_open)
        self.btn_file = QPushButton(); self.btn_file.clicked.connect(self._open_file)
        tb.addWidget(self.btn_file)

        tb.addSpacing(16)
        self.lbl_layer = QLabel()
        tb.addWidget(self.lbl_layer)
        self.spin = QSpinBox(); self.spin.setMinimum(1); self.spin.setMaximum(1)
        self.spin.setEnabled(False); self.spin.valueChanged.connect(self._on_spin)
        tb.addWidget(self.spin)

        self.btn_prev = QPushButton("◀"); self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(self._prev); tb.addWidget(self.btn_prev)
        self.btn_next = QPushButton("▶"); self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(self._next); tb.addWidget(self.btn_next)

        tb.addSpacing(16)
        self.btn_anim = QPushButton(); self.btn_anim.setCheckable(True)
        self.btn_anim.toggled.connect(self._on_anim); tb.addWidget(self.btn_anim)

        tb.addSpacing(16)
        self.chk_g01 = QCheckBox(); self.chk_g01.setChecked(True)
        self.chk_g01.toggled.connect(lambda: self._refresh()); tb.addWidget(self.chk_g01)
        self.chk_g00 = QCheckBox(); self.chk_g00.setChecked(True)
        self.chk_g00.toggled.connect(lambda: self._refresh()); tb.addWidget(self.chk_g00)

        tb.addSpacing(16)
        self.btn_fit = QPushButton(); self.btn_fit.clicked.connect(self._fit)
        tb.addWidget(self.btn_fit)
        self.btn_meas = QPushButton(); self.btn_meas.setCheckable(True)
        self.btn_meas.toggled.connect(self._on_measure); tb.addWidget(self.btn_meas)
        self.btn_save = QPushButton(); self.btn_save.clicked.connect(self._save)
        tb.addWidget(self.btn_save)

        tb.addSpacing(16)
        self.btn_lang = QPushButton(); self.btn_lang.setFixedWidth(52)
        self.btn_lang.clicked.connect(self._on_lang)
        tb.addWidget(self.btn_lang)

        tb.addStretch()
        self.lbl_info = QLabel("")
        tb.addWidget(self.lbl_info)
        lo.addLayout(tb)

        self.canvas = Canvas()
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        self.canvas.mpl_connect('button_press_event', self._on_click)
        self.canvas.mpl_connect('button_release_event', self._on_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_move)
        lo.addWidget(self.canvas)

        self.sbar = QStatusBar()
        self.sbar.setStyleSheet("QStatusBar{background:#0f3460;color:#aaa}")
        self.setStatusBar(self.sbar)

    def _apply_lang(self):
        self.setWindowTitle(TR.tr('title'))
        self.btn_open.setText(TR.tr('open_dir'))
        self.btn_file.setText(TR.tr('open_file'))
        self.lbl_layer.setText(TR.tr('layer'))
        self.btn_anim.setText(TR.tr('stop') if self.btn_anim.isChecked() else TR.tr('play'))
        self.chk_g01.setText(TR.tr('g01'))
        self.chk_g00.setText(TR.tr('g00'))
        self.btn_fit.setText(TR.tr('fit'))
        self.btn_meas.setText(TR.tr('measure'))
        self.btn_save.setText(TR.tr('snapshot'))
        self.btn_lang.setText(TR.tr('lang_btn'))
        self.sbar.showMessage(TR.tr('ready'))

    def _on_lang(self):
        TR.toggle()
        self._apply_lang()
        self._refresh()

    # ---- keyboard shortcuts ----
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left: self._prev()
        elif e.key() == Qt.Key_Right: self._next()
        elif e.key() == Qt.Key_Space: self.btn_anim.toggle()
        elif e.key() == Qt.Key_F: self._fit()
        elif e.key() == Qt.Key_G: self.chk_g00.toggle()
        elif e.key() == Qt.Key_H: self.chk_g01.toggle()
        else: super().keyPressEvent(e)

    # ---- Data loading ----
    def _open_dir(self, path=None):
        if path is None:
            path = QFileDialog.getExistingDirectory(self, TR.tr('choose_dir'))
        if not path: return
        self._load_path(path)

    def _open_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, TR.tr('choose_file'), "", TR.tr('nc_filter'))
        if not path: return
        try:
            ly = GCodeLayer(0)
            g00, g01 = parse_nc(path)
            ly.g00.extend(g00); ly.g01.extend(g01)
            self._cache = {0: ly}
            self._idx = None
            self._lnums = [0]; self._pos = 0
            self._bounds = None
            self._accum_bounds = None
            self._user_view = False
            self._accumulate_bounds(ly)
            self.spin.setEnabled(False)
            self.btn_prev.setEnabled(False); self.btn_next.setEnabled(False)
            self.lbl_info.setText(os.path.basename(path))
            self.sbar.showMessage(TR.tr('single_file').format(ly.c01(), ly.c00()))
            self._display(ly)
        except Exception as e:
            QMessageBox.critical(self, TR.tr('load_fail'), TR.tr('read_fail') + str(e))

    def _load_path(self, path):
        self._stop_preloader()
        self._cache.clear()
        try:
            idx, label = build_index(path)
        except Exception as e:
            QMessageBox.critical(self, TR.tr('err'), TR.tr('scan_fail') + str(e))
            return

        if idx is None:
            QMessageBox.warning(self, TR.tr('no_data'), label)
            return

        self._idx = idx
        self._lnums = idx.lnums()
        self._pos = 0
        self._bounds = idx.bounds
        self._accum_bounds = None
        self._user_view = False
        if not self._lnums:
            QMessageBox.warning(self, TR.tr('no_data'), TR.tr('no_data'))
            return

        self.spin.setEnabled(True)
        self.spin.blockSignals(True)
        self.spin.setMinimum(self._lnums[0])
        self.spin.setMaximum(self._lnums[-1])
        self.spin.setValue(self._lnums[0])
        self.spin.blockSignals(False)
        self.btn_prev.setEnabled(len(self._lnums) > 1)
        self.btn_next.setEnabled(len(self._lnums) > 1)

        ln0 = self._lnums[0]
        try:
            ly = idx.load_layer(ln0)
            if ly is None:
                raise RuntimeError(f"load_layer({ln0}) returned None")
            self._cache[ln0] = ly
            self._accumulate_bounds(ly)
        except Exception as e:
            QMessageBox.critical(self, TR.tr('load_fail'), TR.tr('read_layer_fail').format(ln0) + str(e))
            return

        try:
            self._display(ly)
            self.sbar.showMessage(TR.tr('loaded').format(label, self._lnums[0], self._lnums[-1]))
        except Exception as e:
            QMessageBox.critical(self, TR.tr('render_fail'), TR.tr('show_layer_fail').format(ln0) + str(e))
            return

        if len(self._lnums) > 1:
            self._preloader = PreloadThread(idx, self._lnums[1:])
            self._preloader.layerReady.connect(self._on_preloaded)
            self._preloader.start()

    def _on_preloaded(self, ln, ly):
        if ly:
            self._cache[ln] = ly
            self._accumulate_bounds(ly)

    def _accumulate_bounds(self, ly):
        """Accumulate min/max XY across loaded layers (for Fit without merged bounds)."""
        b = self._accum_bounds
        for segs in (ly.g00, ly.g01):
            for x1, y1, x2, y2 in segs:
                for x, y in ((x1, y1), (x2, y2)):
                    if b is None:
                        b = [x, y, x, y]
                    else:
                        if x < b[0]: b[0] = x
                        if y < b[1]: b[1] = y
                        if x > b[2]: b[2] = x
                        if y > b[3]: b[3] = y
        self._accum_bounds = b

    def _stop_preloader(self):
        if self._preloader and self._preloader.isRunning():
            self._preloader.cancel()
            self._preloader.wait(1000)

    def _display(self, ly):
        self._show_g01 = self.chk_g01.isChecked()
        self._show_g00 = self.chk_g00.isChecked()
        keep = None
        if self._user_view:
            keep = (self.canvas.ax.get_xlim(), self.canvas.ax.get_ylim())
        self.canvas.draw_layer(ly, self._bounds, self._show_g01, self._show_g00)
        if keep:
            self.canvas.ax.set_xlim(keep[0])
            self.canvas.ax.set_ylim(keep[1])
            self.canvas.draw()
        self.lbl_info.setText(TR.tr('cache').format(ly.c01(), ly.c00(), len(self._cache), len(self._lnums)))
        self.spin.blockSignals(True)
        self.spin.setValue(ly.n)
        self.spin.blockSignals(False)

    def _refresh(self):
        if self._lnums and self._lnums[self._pos] in self._cache:
            self._display(self._cache[self._lnums[self._pos]])

    # ---- Navigation ----
    def _goto(self, ln):
        if ln not in self._lnums: return
        self._pos = self._lnums.index(ln)
        if ln in self._cache:
            self._display(self._cache[ln])
        else:
            try:
                ly = self._idx.load_layer(ln)
                self._cache[ln] = ly
                self._display(ly)
            except Exception as e:
                self.sbar.showMessage(TR.tr('load_layer_fail_sb').format(ln, e))

    def _on_spin(self, v):
        if v in self._lnums:
            self._goto(v)
        else:
            closest = min(self._lnums, key=lambda x: abs(x - v))
            self.sbar.showMessage(TR.tr('layer_snap').format(v, closest))
            self.spin.blockSignals(True)
            self.spin.setValue(closest)
            self.spin.blockSignals(False)
            self._goto(closest)
    def _prev(self):
        if self._pos > 0: self._pos -= 1; self._goto(self._lnums[self._pos])
    def _next(self):
        if self._pos < len(self._lnums) - 1: self._pos += 1; self._goto(self._lnums[self._pos])

    def _on_anim(self, checked):
        if checked:
            self.btn_anim.setText(TR.tr('stop'))
            self._anim_timer = self.startTimer(200)
        else:
            self.btn_anim.setText(TR.tr('play'))
            if self._anim_timer: self.killTimer(self._anim_timer); self._anim_timer = None

    def timerEvent(self, e):
        if self._pos < len(self._lnums) - 1: self._next()
        else: self.btn_anim.setChecked(False)

    # ---- Zoom & Pan ----
    def _fit(self):
        ax = self.canvas.ax
        self._user_view = False
        b = self._bounds or self._accum_bounds
        if b:
            ax.set_xlim(b[0] - 2, b[2] + 2)
            ax.set_ylim(b[1] - 2, b[3] + 2)
        else:
            ax.relim()
            ax.autoscale()
        ax.set_aspect('equal')
        self.canvas.draw()

    def _on_scroll(self, e):
        if e.xdata is None: return
        self._user_view = True
        ax = self.canvas.ax; f = 0.7 if e.button == 'up' else 1/0.7
        xl, xr = ax.get_xlim(); yb, yt = ax.get_ylim()
        ax.set_xlim(e.xdata - (e.xdata-xl)*f, e.xdata + (xr-e.xdata)*f)
        ax.set_ylim(e.ydata - (e.ydata-yb)*f, e.ydata + (yt-e.ydata)*f)
        self.canvas.draw()

    def _on_click(self, e):
        if e.inaxes != self.canvas.ax: return
        if e.button == 2:
            self._user_view = True
            self._pan_start = (e.xdata, e.ydata)
            self._pan_xlim = self.canvas.ax.get_xlim()
            self._pan_ylim = self.canvas.ax.get_ylim()
        elif e.button == 1 and self._measuring:
            self._meas_pts.append((e.xdata, e.ydata)); self._draw_meas()

    def _on_release(self, e): self._pan_start = None

    def _on_move(self, e):
        if self._pan_start and e.xdata:
            dx = self._pan_start[0]-e.xdata; dy = self._pan_start[1]-e.ydata
            self.canvas.ax.set_xlim(self._pan_xlim[0]+dx, self._pan_xlim[1]+dx)
            self.canvas.ax.set_ylim(self._pan_ylim[0]+dy, self._pan_ylim[1]+dy)
            self.canvas.draw()
        elif e.xdata:
            self.sbar.showMessage(f"X={e.xdata:.2f}  Y={e.ydata:.2f}")

    # ---- Measurement ----
    def _on_measure(self, checked):
        self._measuring = checked
        if not checked: self._meas_pts = []; self._refresh()

    def _draw_meas(self):
        self._refresh()
        ax = self.canvas.ax; pts = self._meas_pts
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        ax.plot(xs, ys, 'o-', color='#00ff00', lw=1.5, ms=6, mec='#00ff00', mfc='none')
        if len(pts) >= 2:
            dx = pts[-1][0]-pts[-2][0]; dy = pts[-1][1]-pts[-2][1]
            d = (dx*dx+dy*dy)**0.5
            ax.annotate(f'{d:.2f} mm', ((pts[-1][0]+pts[-2][0])/2, (pts[-1][1]+pts[-2][1])/2),
                        color='#00ff00', fontsize=9, ha='center',
                        bbox=dict(boxstyle='round', facecolor='#1a1a2e', edgecolor='#00ff00', alpha=0.8))
            self.sbar.showMessage(TR.tr('measured').format(d))
        self.canvas.draw()

    # ---- Screenshot ----
    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, TR.tr('save_title'), "layer.png", "PNG (*.png)")
        if p:
            self.canvas.fig.savefig(p, dpi=150, facecolor='#1a1a2e')
            self.sbar.showMessage(TR.tr('saved').format(p))

    # ---- Drag & Drop ----
    def dragEnterEvent(self, e):
        e.accept() if e.mimeData().hasUrls() else e.ignore()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p): self._load_path(p)
            elif os.path.isfile(p): self._open_file(p)
            break

    def closeEvent(self, e):
        self._stop_preloader()
        if self._anim_timer:
            self.killTimer(self._anim_timer)
        super().closeEvent(e)


def main():
    ap = argparse.ArgumentParser(description="ScanPathViewer — layer-by-layer scan path viewer")
    ap.add_argument('path', nargs='?', default=None)
    ap.add_argument('--lang', choices=['zh', 'en'], default=None, help='UI language')
    args = ap.parse_args()

    if args.lang:
        TR.zh = (args.lang == 'zh')

    plt.style.use('dark_background')
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    viewer = Viewer()
    viewer.show()

    path = args.path
    if not path:
        idx, label, p = auto_find_data()
        if idx is not None:
            path = p

    if path:
        if os.path.isdir(path):
            viewer._load_path(path)
        elif os.path.isfile(path):
            viewer._open_file(path)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
